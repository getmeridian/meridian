# provision — Pure-Python step pipeline

## Design decisions

**Steps over monolithic script** — each step is a class with `run(conn, ctx) → StepResult` (ok/changed/skipped/failed). Composable, independently testable. Pipeline stops on first failure.

**Order matters**: packages → Docker → panel config → Xray inbounds → nginx → connection page. Each step depends on artifacts from earlier steps.

**Hybrid context** — `ProvisionContext` has typed fields for configuration (IP, domain, ports) and a dict for inter-step data (panel client, UUIDs). Typed fields are self-documenting; dict keeps steps loosely coupled.

**Idempotency by convention** — every step checks state before acting. Re-running `deploy` on a configured server is fast and safe.

**Protocol-generic assembly** — `build_setup_steps()` loops over `PROTOCOLS` registry. Adding a protocol doesn't require editing pipeline code.

**Relay pipeline is separate** — relay nodes use `RelayContext` (not `ProvisionContext`) and a completely different step sequence. No Docker, no 3x-ui — just Realm TCP forwarding. IP validation prevents shell/config injection.

**WARP provisioning** — installs Cloudflare WARP client for server egress. Manages APT repository, systemd service, and CLI registration. warp-cli syntax differs between versions — code tries both.

## What's done well

- **Credential lockout prevention** — save locally BEFORE changing remote password. If API fails, user has recovery data.
- **`deployed_with` updated on re-deploy** — not just fresh deploys. Enables downstream version mismatch warnings.
- **Mutable fields on redeploy** — `ConfigurePanel` skips credential generation on redeploy but updates `ip`, `domain`, `hosted_page`, `sni`, `panel.url`, and `deployed_with`. Any user-facing field that can change between deploys must be in this block. Note: SNI change updates credentials but does NOT rebuild Xray inbounds (avoids disrupting active clients).
- **`ufw limit` requires SSH multiplexing** — each `conn.run()` opens a separate SSH connection. `ufw limit 22/tcp` (6 connections/30s) would lock us out mid-deploy. Don't enable until `ControlMaster` multiplexing is added to `ServerConnection`.
- **nginx = genuine identity** — the server IS nginx. No decoy headers, no fingerprinting leaks. `server_tokens off` is all that's needed.

## Pitfalls

- **JSON string quirk** — 3x-ui API requires `settings`/`streamSettings` as JSON *strings*, not objects. Tests verify this explicitly.
- **nginx SNI catch-all** — all unrecognized SNIs route to nginx_https (same response as direct IP). This eliminates the routing differential that a blackhole (RST vs certificate) would create — censors see identical behavior regardless of SNI.
- **Realm hash verification** — SHA256 mismatch = hard failure. This is supply chain defense, not a bug.
- **nginx `add_header` inheritance** — child `location` blocks with `add_header` suppress parent headers entirely. Use `map` directives for variable headers to avoid duplication.
- **acme.sh bootstrap** — nginx needs a cert to start SSL, but acme.sh needs nginx on port 80. Solution: self-signed bootstrap cert, then issue real cert, then reload.
- **acme.sh empty email** — `email=''` breaks the installer (`shift` error). Omit the arg when no email: `sh -s --` not `sh -s email=''`.
- **acme.sh shortlived IP certs need explicit renew days** — acme.sh's default renew window is 30 days, which is fine for 90-day domain certs and catastrophically wrong for Let's Encrypt's 6-day IP certs. IP mode must issue with `--days 5`, redeploy must force-renew once when migrating stale acme state, `InstallNginx` must always re-install the acme cron job because teardown removes it but leaves `/root/.acme.sh/` behind, and `InstallPackages` must guarantee `cron` because renewal, stats, and the watchdog all depend on `crontab`.
- **nginx.conf stream block** — the default nginx.conf only has `http {}`. The stream block for SNI routing must be injected at the top level. Check idempotently before inserting.
- **nginx stream = dynamic module** — on Ubuntu, stream is compiled as dynamic (`--with-stream=dynamic`). `nginx -V` shows compile flags but the `.so` isn't installed until `libnginx-mod-stream` is installed. Always install the package, don't trust compile flags.
- **nginx version ≥1.25 required** — `http2 on;` (1.25+), `keepalive_requests` (1.15.3+), TLSv1.3 (1.13+). Distro packages often lag (Ubuntu 24.04 ships 1.24); provisioner auto-upgrades from nginx.org official repo. E2E and system lab Dockerfiles must also use the official repo. The official repo builds stream as a static module (no `libnginx-mod-stream` needed), but the `; true` on that install handles it.
- **`return 444` is banned from HTTPS blocks** — blind censor assessment rated 444 (silent close after TLS handshake) as 9/10 suspicious. 403/404 is always used instead — nginx generates these response bodies itself, identical across all installations. Custom HTML (like a placeholder page) would be fingerprintable: one known Meridian server would let censors scan for the same content hash on all IPs. The `--decoy` flag is deprecated.
- **IP mode port 80: no HTTP→HTTPS redirect** — redirecting to HTTPS that serves a placeholder is a contradiction signal for API-like profiles. IP mode serves ACME challenges only on port 80, returns 444 for everything else. Domain mode keeps the redirect since it has real content.
- **Unknown SNIs → TCP proxy to Reality dest** — all unrecognized SNIs are TCP-proxied to the Reality dest site (e.g. www.cloudflare.com:443). This eliminates the SNI routing differential — a censor probing with random SNIs sees the dest site's real cert, not nginx's. Only server IP, domain, and no-SNI connections route to nginx (for connection pages).
- **HTTP/2 via `http2 on;`** — nginx 1.25+ replaced `listen ... http2` with a separate `http2 on;` directive in the server block. The provisioner enforces >= 1.25, so this is always safe. Without it, ALPN only negotiates HTTP/1.1 — a fingerprinting vector.
- **Port 443 allowed list** — `docker.py` and `setup.py` both check port 443 occupancy. Both must include `haproxy`/`caddy` for upgrade-from-old-stack to work.
- **Step constructor defaults must be `None` for context-resolved fields** — `InstallNginx` uses `None` defaults with `if x is not None else ctx.Y` resolution. Never use truthy defaults (like `10443` or `DEFAULT_PANEL_PORT`) for fields that fall back to context — they silently mask the fallback. Fixed defaults (like `nginx_internal_port=8443`) that are never resolved from context are fine as-is.
- **Xray `listen` field** — omitting `listen` from 3x-ui API defaults to all interfaces. When nginx fronts Xray, set `"listen": "127.0.0.1"`.
- **nginx stream `map_hash_bucket_size`** — default is 32 bytes, too small for long SNI hostnames. Set `map_hash_bucket_size 128;` in the stream config before the `map` block.
- **Partial deploy recovery** — `panel_configured: true` is saved before `_apply_panel_settings` completes. If deploy fails mid-apply, re-deploy sees the flag and skips configuration, but the panel has stale/different credentials. Fix: Configure panel verifies saved creds actually work before skipping; on mismatch, nukes the container and reconfigures from scratch.
- **Config changes vs existing servers** — changes to `_render_nginx_stream_config()` only affect new deploys. Existing servers keep their already-written config. Any feature that adds new directives to rendered configs must also handle migration: check if the directive exists on the live server and inject it if missing. The per-relay SNI feature hit this: the `include relay-maps/*.conf;` line was generated correctly but never reached servers deployed before the feature. Fix: `_deploy_relay_nginx` patches the existing config with sed as a one-time migration.
- **Per-relay nginx files** — relay SNI routing uses per-file config (`relay-maps/*.conf` for map entries, `meridian-relay-*.conf` for upstreams) instead of rewriting the monolithic `meridian.conf`. This makes relay add/remove a file create/delete + reload, not a full config regeneration. The main config has `include /etc/nginx/stream.d/relay-maps/*.conf;` inside its `map` block — nginx supports `include` inside `map`.
- **Xray Reality `dest` is single-target** — Reality's fallback (`realitySettings.dest`) proxies non-Reality probes to one fixed destination. VLESS `settings.fallbacks` only apply after Reality has verified the connection, so they're irrelevant for probe resistance. To have different fallback targets per SNI (e.g., per-relay), you need separate Xray inbounds — each with its own `dest` matching its `serverNames`.
- **XHTTP nginx location must match both forms** — client URLs use `/<path>` but some requests arrive as both `/<path>` and `/<path>/`. If nginx only proxies the slash form, exact-path XHTTP falls through to the generic 404 and looks broken.
- **Firewall cleanup must not touch unknown ports** — Meridian owns only the ports it opened itself. Deleting arbitrary `ufw allow <port>/tcp` rules breaks user SSH/monitoring stacks; limit cleanup to explicitly Meridian-managed ports only.
- **Firewall must follow the effective sshd port** — never assume `22/tcp`. Exit and relay provisioning must allow the live SSH port(s), or custom-port users can lock themselves out mid-deploy.
