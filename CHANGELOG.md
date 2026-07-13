# Changelog

All notable changes to Meridian are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [4.0.0] - Unreleased

### Remnawave compatibility matrix
This release pins a single tested version tuple. Upgrading any one component
without the rest is not supported — move the whole set together.

| Component | Pinned version |
|---|---|
| `remnawave/backend` | `2.7.4` |
| `remnawave/node` | `2.7.0` |
| `remnawave/subscription-page` | `7.2.1` |
| `remnawave` Python SDK | `2.7.1` |
| Xray-core client (test binary) | `26.3.27` |
| Realm (relay forwarder) | `2.9.3` |
| Pebble (system-lab ACME CA) | `2.10.0` |

### Changed
- **`meridian plan --json`** now emits the shared envelope shape with plan details under `data`; `status` is `no_changes` or `changed`, and the process exit code remains `0` for converged and `2` for changes pending
- **`meridian fleet status --json`** and **`meridian fleet inventory --json`** now emit the shared envelope shape while preserving stable command fields under `data`
- **Remnawave replaces 3x-ui** — modern panel with panel/node separation (NestJS + PostgreSQL + Valkey), proper REST API, built-in subscriptions, native multi-node support
- **Single `cluster.yml` replaces per-server `proxy.yml`** — fleet-wide manifest with panel URL, API token, nodes, relays. Client state lives in Remnawave's database, not locally
- **`client add` is one API call** — was ~400 LOC of SSH-tunneled curl, credential sync, and per-inbound client insertion
- **Relay = Remnawave Host entry** — enable/disable host toggles subscription inclusion automatically

### Added
- **meridian-core JSON contract foundation** — Pydantic-backed `meridian.output/v1` envelopes, structured summary/error/event models, JSONL event primitives, JSON Schema export, and centralized secret redaction for automation and future UI clients
- **`meridian api schemas` / `meridian api schema NAME`** — discover and export JSON Schemas for the public meridian-core contracts used by CLI JSON output and future UI clients
- **`meridian node add/list/remove/check`** — multi-node fleet management
- **`meridian fleet status/recover`** — panel health, node connectivity, relay status, user count, and reconstruct-from-panel when local state is lost
- **`MeridianPanel` REST client** — wraps the official `remnawave` Python SDK (v2.7.1) with retries, credential redaction, and thread-local event loops for parallel workers
- **Config reliability** — corrupt YAML handling, version check, backup before mutations, disk-full error messages, external-edit guard (`cluster.save()` refuses to clobber if the file mtime advanced during a long-running apply), snapshot type validation
- **Reality keys persisted** — public_key and short_id saved in cluster.yml for connection testing
- **Declarative plan/apply workflow** — `cluster.yml` becomes desired state (`desired_nodes`, `desired_relays`, `desired_clients`, `subscription_page`); `meridian plan` prints a Terraform-style diff; `meridian apply` converges. Imperative commands (`deploy`, `node add`, `client add`) mirror their effect into `desired_*` when the list is non-null — hybrid sync, mixing the two modes is safe
- **Applied-state tracking** — every successful `apply` snapshots desired state into the typed `cluster.applied_state` model. The next plan distinguishes intentional removals (in applied → executes under `--yes`) from drift (not in applied → requires `--prune-extras=yes`). Closes the subtle bug where `--yes` silently skipped deliberate removals
- **`meridian plan --json`** — structured output for CI consumption; exit 0 = converged, 2 = changes pending, 1 = error. Stable JSON shape with typed `actions[].kind` values
- **`--prune-extras=ask|yes|no`** — explicit control over drift handling. Under `--yes`, `ask` downgrades to `no` (safety default); destructive actions still require one confirmation unless `--yes`
- **Parallel node provisioning** — `ThreadPoolExecutor` with `--parallel N` (default 4); per-worker `MeridianPanel` SDK instance, `threading.local()` event loops, and serialized cluster saves
- **SSH multiplexing (`ControlMaster`)** — connection reuse across all SSH operations
- **Warp tri-state** — `DesiredNode.warp: None | False | True` (keep-current / disable / enable) with correct YAML round-trip (explicit `null`, not dropped; loader defaults missing key to `None`)
- **YAML null semantics** for `desired_*` and `subscription_page` — `null` means "unmanaged" (as documented); previously `desired_clients: null` collapsed to `[]` with `manage=True` and `subscription_page: null` loaded as `enabled=True`
- **Duplicate node-name validator** — `compute_plan`'s name→IP map and `find_node()` disagreed on duplicates, which could misroute relay `exit_node` references. Caught at load time now
- **Real-VM test harness** — optional `tests/realvm/` provisions real cloud VMs (Hetzner via `hcloud-python` SDK), runs full deploy + tier-α verification, tears down. Local-only (never in CI), opt-in via `make real-lab`. Foundation for a future `meridian deploy --create-vm <provider>` feature. Per-cloud `CloudProvider` abstract class under `src/meridian/infra/providers/`
- **NET_ADMIN capability** on remnawave-node container — required by panel 2.6.2+ for Torrent Blocker, IP Control, and related plugins. Without it, those features silently no-op
- **Subscription-page lifecycle via cluster.yml** — enable/disable the Remnawave subscription container declaratively; `docker compose up -d --no-recreate` on subpage-only apply to avoid incidental panel restart
- **Hysteria2 UDP/443 fallback** — available after the TCP transports in subscription ordering, with matching host and firewall configuration
- **`ConfigureFail2ban` + fail2ban package wired in both pipelines** — fail2ban is now actually installed and started on every hardened deploy. Previously `build_setup_steps` had an operator-precedence trap (`InstallPackages(REQUIRED_PACKAGES + ["fail2ban"] if harden else None)` parsed as `(REQUIRED + fail2ban) if harden else None`, skipping all packages on `--no-harden`) and `build_node_steps` (used for redeploy) lacked the step entirely
- **Smart xray readiness polling** in system-lab — polls node API port 3010 + 5s grace for Reality inbound init, replacing a blind `sleep 30`

### Fixed
- **Safe fleet recovery** — recovered inbounds now use stable protocol keys and the current cluster schema; Reality public keys are derived from preserved private keys, and redeploy refuses incomplete key material instead of silently rotating credentials

### Removed
- **3x-ui panel** — PanelClient, ConfigurePanel, CreateInbound, all SSH-tunneled curl API calls
- **All pre-v4 migration and compatibility paths** — v4 starts from typed `cluster.yml` and `servers.json` state; the old migrate command, installer/setup shims, per-server `proxy.yml` credentials, legacy registry import, and 3x-ui cleanup step are gone
- **Legacy local connection-page renderer** — the server-hosted PWA is the only connection-page implementation
- **Local client state** — no more UUID storage, credential sync, SCP rollback
- **Legacy HAProxy + Caddy code paths** — replaced entirely by nginx (stream SNI routing + http TLS + reverse proxy)

## [3.17.0] - 2026-04-11

### Added
- **Multi-node system lab** — Docker Compose-based test harness deploys Meridian across separate exit and relay containers via real SSH, verifying Reality tunnel connectivity (direct + via relay). Catches port mismatches, SNI routing errors, and relay forwarding bugs that mocked tests cannot. Run with `make system-lab`
- **Custom SSH port** — `--ssh-port` flag for servers with non-standard SSH ports. Port is stored in the server registry and reused for all subsequent commands (#22)
- **Configurable ACME server** — `MERIDIAN_ACME_SERVER` env var overrides the certificate authority (default: letsencrypt). Enables future local CA testing with Pebble
- **Configurable connectivity test URL** — `MERIDIAN_CONNECT_TEST_URL` env var overrides the IP check endpoint (default: ifconfig.me)
- **Disable update check** — `MERIDIAN_DISABLE_UPDATE_CHECK=1` skips PyPI version check for CI/automation
- **Bootstrap TLS cert includes SAN** — self-signed bootstrap certificates now include `subjectAltName` (IP or DNS), improving client compatibility

### Fixed
- **Relay with same SNI as exit no longer crashes nginx** — when relay and exit share the same SNI, Meridian skips creating a duplicate nginx map entry (#17)
- **Relay credential sync fails loudly** — `relay remove` followed by `relay deploy` no longer gets stuck on "already attached" when SCP sync fails (#19)
- **Manual 3x-ui inbounds no longer crash client commands** — `list_inbounds()` handles empty or malformed JSON from manually-created panel inbounds (#16)
- **DebianBanner no longer blocks deploy on some OpenSSH builds** — verification is skipped when `sshd -T` doesn't recognize the directive (#20)
- **BBR no longer blocks deploy on containers** — `sysctl` failures due to missing kernel tunables (containers, old kernels) return a warning instead of failing the entire deploy. Other sysctl errors still fail
- **SSH drop-in priority** — `00-meridian.conf` ensures Meridian's sshd hardening takes precedence over cloud-init overrides
- **SCP directory copy** — fixed `-r` flag compatibility with OpenSSH ≥ 9.0 (SFTP protocol default)

## [3.16.1] - 2026-04-10

### Fixed
- **Redeploy no longer blocked by missing server credentials** — servers deployed before credential sync had no `/etc/meridian/proxy.yml`, causing redeploy to fail with "stale local cache". Now gracefully proceeds with the local copy and syncs after deploy
- **Deploy syncs credentials to server** — `proxy.yml` is pushed to `/etc/meridian/` after provisioning so `update-stats.py` cron and client mutation refresh work correctly
- **E2E acme.sh stub** — stub now returns rc=0 for `--install-cronjob`/`--install-cert`/`--info` to match new return-code checks

### Added
- **Reproducible redeploy command** — deploy success output shows the exact `meridian deploy` command to redo the same configuration without the wizard

## [3.16.0] - 2026-04-10

### Added
- **Cloudflare setup guidance** — domain-mode deploy success output now prints DNS, SSL/TLS, and WebSocket configuration steps
- **Deploy page fanout** — deploy/redeploy regenerates all saved client handoff pages so branding, domain, and SNI changes reach existing clients
- **SSH hardening via drop-in** — sshd hardening uses an authoritative `/etc/ssh/sshd_config.d/99-meridian.conf` with `sshd -T` validation, overriding cloud-init defaults
- **Nested credential round-tripping** — unknown fields under panel, server, protocols, clients, relays, and branding sections are preserved across CLI versions via `_extra`
- **Relay SSH user persistence** — `relay check` and `relay remove` reuse the stored SSH user from the registry instead of defaulting to root

### Fixed
- **XHTTP nginx path mismatch** — nginx now routes both `/<path>` and `/<path>/` to the XHTTP upstream
- **Firewall cleanup safety** — `ConfigureFirewall` no longer deletes arbitrary user-managed TCP rules; cleanup is limited to Meridian-owned ports
- **Handoff page self-containment** — generated HTML/PWA pages no longer depend on `getmeridian.org/ping`; troubleshooting is self-contained
- **Relay SNI fail-closed** — new relay deploys fail closed when no relay-local SNI is available instead of silently falling back to the default
- **Client credential mutations fail-closed** — `client add` refreshes from server before mutating and rolls back local state if sync fails
- **Release workflow pinning** — `release.yml` now checks out `workflow_run.head_sha` so untested commits can't be released
- **PWA canonical subscription URL** — frontend honors server-provided `subscription_url` instead of reconstructing from `location.pathname`

### Changed
- **No silent patch auto-upgrade** — patch releases are surfaced as a notification with a link to releases; `meridian update` + `meridian deploy` is now explicit

## [3.15.2] - 2026-04-10

### Fixed
- **False local mode detection via TUN** — users connected to a VPN server via TUN mode had their public IP match the server IP, causing `_is_on_server()` to wrongly activate local mode. This made `client add` fail with "No credentials found" and `teardown` run destructively on the local machine. Detection now uses file/directory checks (`/etc/meridian/`) instead of IP matching.

### Added
- **`--geo-block` / `--no-geo-block` flag** — geo-blocking is now optional. Use `--no-geo-block` to skip country-based firewall rules during deploy.

## [3.15.1] - 2026-04-09

### Added
- **IPv6 support** — deploy to IPv6-only or dual-stack servers with `meridian deploy 2001:db8::1`. IPv6 addresses are accepted everywhere: deploy, relay, probe, ping, server commands. Connection URLs bracket IPv6 per RFC 3986. Credential directories use sanitized paths. SCP, TCP connectivity, and public IP detection all handle IPv6 natively.

### Fixed
- **mypy type errors** — `ProvisionContext.credentials` nullable access now guarded in `CreateInbound` and `DeployConnectionPage` steps.

## [3.15.0] - 2026-04-09

### Added
- **Per-relay SNI** — each relay gets its own geographically-appropriate SNI target with a dedicated Xray Reality inbound on the exit server. Passive observation sees plausible domestic traffic; active probes get the correct certificate. Use `--sni` to specify manually or let the scanner pick one automatically.
- **`meridian test` relay support** — ping section checks relay TCP reachability, connection section tests Reality + XHTTP through each relay end-to-end.
- **PWA SNI indicator** — connection cards now show "Appears as: domain" so users understand what their traffic looks like to censors.

## [3.14.4] - 2026-04-09

### Fixed
- **`--server-name` flag drift** — deploy docs (all 4 locales) and README still referenced `--server-name` after it was renamed to `--display-name` in v3.9.0. FA/ZH flag tables also had stale `--email`, `--xhttp`, `--name` flags.
- **PyPI sdist bloat** — source distribution was 38MB because it included blog hero images, website/, tests/, and build artifacts. Now 176KB.

### Added
- **CI drift validators** — `validate_cli_docs.py` checks all 19 commands' flags are documented in cli-reference.md (replaces deploy-only check). `validate_apps_sync.py` validates `_PWA_APPS` in render.py matches apps.json.
- **Global flags in cli-reference** — `--user`, `--server`, `--sni`, `--domain` documented once in Global flags section instead of repeated per command.
- **CLAUDE.md coverage** — updated all 15 existing files, created 3 new ones (layouts, i18n, content/docs). Added missing features: WARP, post-quantum, relay routing, color palettes, clock skew detection.

## [3.14.3] - 2026-04-09

### Fixed
- **Relay not found in local mode** — `relay list`, `relay check`, and `relay remove` now find relays when running on the exit server itself (`deploy local`). Previously they only checked `~/.meridian/credentials/` and missed `/etc/meridian/` where local-mode credentials are stored. (#9)
- **False client page warnings during relay deploy** — `upload_client_files()` returns empty string on success, but the check was inverted (`if not result`), causing every successful upload to show "Could not update connection page". (#9)

## [3.14.2] - 2026-04-09

### Fixed
- **Socket leaks** — `tcp_connect()` and `_get_cert_der()` now use context managers to ensure cleanup on exception paths.
- **nginx `return 444` in HTTP block** — invalid stream directive replaced with `return 403;` for IP-mode deployments.
- **WARP deploy silent failures** — `systemctl enable`, `set-proxy-port`, and `warp-cli connect` return codes now checked; step fails instead of claiming success.
- **Firewall policy silent failures** — `ufw default deny/allow` return codes now checked in both exit and relay firewall steps.
- **Reality port conflict** — standalone mode now pre-checks port 443 availability before creating Xray inbound.
- **SSRF in icon download** — `_process_image_url()` now blocks private/loopback/reserved IP addresses.
- **IPv6 URL formatting** — protocol URL builders now wrap IPv6 addresses in brackets per RFC 3986.
- **Fragile tmp_zip cleanup** — `ensure_xray_binary()` uses reliable `None` init instead of `"in dir()"` check.
- **Broad exception handlers** — narrowed `except Exception` to specific types in QR generation, HTTP date parsing, and version checking.
- **Redundant branding condition** — removed duplicate check in deploy setup.
- **`SystemExit` catch anti-pattern** — added `try_resolve_server()` wrapper; uninstall command uses the safer pattern.
- **E2E Docker image** — added missing `fail2ban` package that was breaking CI.

### Improved
- **Type annotations** — `_deploy_client_page()` now has typed `list[ProtocolURL]` and `list[RelayURLSet]` parameters.
- **Test coverage** — added 80 new tests: branding module (37), xray_client module (35), render template content assertions (8). Total: 721 tests.

## [3.14.1] - 2026-04-08

### Improved
- **Panel URL in deploy output** — post-deploy now shows the 3x-ui panel URL and credentials for advanced users.

## [3.14.0] - 2026-04-08

### Added
- **Cloudflare WARP outbound** — new `--warp` flag routes server egress through Cloudflare's network. Destination sites see a Cloudflare IP instead of the VPS IP. Useful when sites block datacenter IP ranges. Installs `cloudflare-warp` in SOCKS5 proxy mode; incoming connections (SSH, nginx) are unaffected. Connection failure is a warning, not a hard fail — WARP endpoints are blocked in some regions.
- **Panel URL in credentials** — `proxy.yml` now includes a `panel.url` field with the full panel URL, so users don't have to assemble it from parts.
- **Camouflage vs. domain explainer** — deploy docs (EN/RU) now have a "Camouflage target vs. domain" section explaining the difference and why you can't use your own domain for both.

### Fixed
- **Web panel proxy headers** — nginx panel location block was missing WebSocket upgrade headers needed by 3x-ui web UI. Added `proxy_http_version 1.1`, `Host`, `Upgrade`, and `Connection` headers.
- **`meridian test` with WARP** — when WARP is enabled, exit IP check now expects a Cloudflare IP instead of the server IP.

### Improved
- **Wizard camouflage text** — expanded to explain it's any popular site you don't own, not a domain you control.
- **Install location clarity** — getting-started docs (EN/RU) clarify that both local and VPS work, recommend local for `meridian test` and credential safety.

## [3.13.0] - 2026-04-08

### Added
- **Post-quantum encryption** — opt-in ML-KEM-768 + X25519 hybrid encryption for the Reality protocol. Enable with `--pq` flag or interactive wizard. Generates keys via `xray vlessenc`. Only affects Reality; XHTTP and WSS use TLS and are unaffected.
- **End-to-end connection testing** — `meridian test` now verifies actual proxy traffic, not just reachability. Downloads a local xray client, connects through the proxy for each active protocol (Reality, XHTTP, WSS), and confirms traffic flows end-to-end.
- **Server name in URL fragments** — when `--display-name` is set, client app connection names show "alice @ My VPN" instead of just "alice". Applies to all protocols and relay URLs.
- **OG meta tags** — PWA connection pages now include OpenGraph meta tags for social media link previews.
- **Relay connectivity retry** — relay verification retries the service check up to 4 times with 2s delay, preventing false failures when Realm needs time to bind after start.

## [3.12.0] - 2026-04-07

### Added
- **Server-side geo-blocking** — all deployments now block Russian traffic (`geosite:category-ru` + `geoip:ru`) at the Xray routing level via blackhole outbound. No manual panel configuration needed.
- **Enhanced `meridian doctor`** — new diagnostic sections: Xray process status, nginx error log, TLS certificate expiry with warnings, geo-blocking verification, deployment context (mode, protocols, client count). Every remote section now shows the exact command that produced the output.
- **Panel recovery in `client show`** — if a client is missing from local credentials (e.g. added from a different machine), `show` now recovers the data from the live panel automatically and syncs back.

### Fixed
- **`client show` missing XHTTP URLs** — console output skipped XHTTP connection links while the HTML share page worked. The `xhttp_path` in credentials is now the sole signal for XHTTP availability.
- **`meridian doctor` PermissionError** — crashed when run as non-root on the server. Now handles permission errors gracefully.
- **Stale container on deploy** — deploying to a server with a leftover 3x-ui container (custom credentials from a previous deployment) failed with "Empty response from login endpoint". Now resets the container DB and retries.
- **First client missing from XHTTP/WSS** — the deploy-time client was only added to the Reality inbound, not XHTTP or WSS. Now added to all active protocol inbounds.
- **UFW hard-failure on relay** — relay deployment crashed if `ufw` wasn't pre-installed. Now installs it automatically before configuring firewall rules.
- **Probe false positive on relay nodes** — SNI consistency warning now clarifies that certificate variation is expected for relay nodes (TCP forwarders).

### Changed
- **CLI flag renames** — `--name` → `--client-name`, `--server-name` → `--display-name`. Branding flags grouped in `--help`. Removed `--email` and `--xhttp` (wizard handles these).
- **Teardown output** — now suggests `meridian deploy IP` as a next step.

## [3.11.2] - 2026-04-07

### Added
- **Subscription QR code** — connection page now shows a QR code for the subscription URL. Scanning it imports all protocols at once instead of requiring per-protocol QR scans.
- **Version-level update warnings** — patch updates install silently, minor versions note possible behavior changes, major versions highlight breaking changes and recommend redeploying.

### Fixed
- **Docker compose on pre-installed Docker** — servers with `docker.io` (not `docker-ce`) lacked the compose plugin. Now detected and installed automatically.

## [3.11.1] - 2026-04-07

### Fixed
- **Docker credsStore on headless servers** — `docker compose pull` failed on old Ubuntu servers where Docker defaulted to the `secretservice` credential helper (D-Bus unavailable). Now stripped automatically after installation using `jq`. (#6, #7)
- **Domain in connection page URLs** — share links and deploy output now use the configured domain instead of raw server IP. SNI target shown in deploy summary when non-default. (#2, #3)

### Changed
- **nginx auto-upgrade** — if the distro nginx is too old (< 1.16), Meridian adds the official nginx.org repo and upgrades automatically, mirroring the Docker installation pattern. Previously failed with a manual upgrade message. (#8)

## [3.11.0] - 2026-04-03

### Added
- **Deep link overhaul** — 5 → 13 proxy client apps with one-tap import. Fixed v2RayTun deep link (the #1 user complaint). Added ShadowRocket, V2Box, FoXray, Happ, Karing, sing-box, FlClash, NekoBox. Three URL encoding modes: percent-encoded, raw path, base64.
- **Platform-specific download URLs** — cross-platform apps show App Store links on iOS, Google Play on Android, instead of generic GitHub/website links. Non-matching platforms collapsed under "Other platforms."
- **Numbered choices in deploy wizard** — all Y/n prompts replaced with numbered options. Manual SNI entry without scan. Rich spinner during scan. Deploy summary shows hardening status.
- **`choose()` helper** — new console utility for numbered selection prompts, documented in commands/CLAUDE.md.

### Fixed
- **SNI scan using private VPC IP** — servers with VPC addresses (e.g. 10.129.x.x) scanned a private /24 subnet instead of the public IP network. Now excludes all RFC 1918 ranges.
- **Partial deploy recovery** — if a previous deploy failed mid-"Configure panel", re-deploy detected stale credentials, nukes the panel DB, and reconfigures from scratch automatically.
- **Panel wait before login** — both "Configure panel" and "Log in to panel" steps now wait for the panel to become responsive before attempting login. Fixes "Empty response from login endpoint" on slow servers.
- **Panel health check** — checks `/login/` endpoint instead of `/` (which returns 404 when webBasePath is set). Accepts any HTTP response, not just 200.
- **Relay error messages** — "relay cannot reach exit" now includes a 3-point checklist and manual test command.
- **sudo detection** — non-root SSH to servers without sudo gives actionable hint instead of generic SSH failure.
- **Scan junk filtering** — filters Kubernetes ingress fake certs, self-signed placeholders from scan results.

## [3.10.9] - 2026-04-03

### Fixed
- **nginx stream `map_hash_bucket_size`** — long SNI hostnames (e.g. `learn.microsoft.com`) exceeded the default 32-byte bucket, failing `nginx -t` on deploy. Set to 128.

### Changed
- **Backlog cleanup** — 171 → 78 items. Dropped theoretical threats, overengineered fixes, all P3 items, and refactoring debt. Added community feedback from vas3k.club launch.
- **README** — added Community section with launch post link.

## [3.10.8] - 2026-04-01

### Changed
- **Disable Xray logs on deploy** — new `DisableXrayLogs` step patches the Xray config template to set `access="none"` and `error="none"`. Runs on every deploy (idempotent), so existing servers get logs disabled on re-deploy.

### Fixed
- **Probe SNI consistency false positive** — the check tested 3 different unknown SNIs which get TCP-proxied to the Reality dest; the dest CDN returns different certs for different SNIs (expected behavior). Now tests the same SNI repeatedly to verify deterministic routing.

## [3.10.7] - 2026-04-01

### Changed
- **nginx stream idle timeout** — increased `proxy_timeout` from 10m (default) to 30m so idle VPN sessions aren't killed prematurely. Added `proxy_socket_keepalive` to keep relay→exit TCP connections alive through NATs/firewalls.
- **XHTTP upstream keepalive** — XHTTP sub-requests now reuse TCP connections to Xray via an upstream keepalive pool (`keepalive 32`) instead of opening a new connection per request. Reduces latency, especially in `packet-up` mode and through relay nodes.

## [3.10.6] - 2026-04-01

### Fixed
- **Relay re-bind after exit teardown** — `relay deploy` now detects a previous Realm process on the port and stops it instead of failing with "port already in use". Fixes the teardown → re-deploy → relay bind flow.
- **Teardown leaves zombie relays** — `teardown` now stops and disables relay nodes before uninstalling the exit server, preventing orphaned Realm processes forwarding to a dead exit.

## [3.10.5] - 2026-04-01

### Fixed
- **Probe false positives on Meridian-deployed servers** — suspicious port scan now verifies with an HTTPS request before flagging. Cloud middleboxes that complete TCP handshakes but serve no content are reported as informational, not issues.
- **Probe SNI consistency false positives** — certificates are now compared by subject+issuer identity (via openssl) instead of raw bytes. CDN cert rotation (same origin, different serial numbers) no longer triggers warnings.
- **XHTTP upstream timeouts** — nginx reverse proxy `proxy_read_timeout` increased from 360s to 86400s for XHTTP location blocks. XHTTP `mode=auto` lets clients negotiate streaming modes (`stream-one`/`stream-up`) with long-lived connections that previously timed out. Added `proxy_send_timeout` and `proxy_request_buffering off`.

### Changed
- **Probe verdict** — removed circular "Meridian handles these automatically: `meridian deploy`" suggestion. Replaced with neutral diagnostic messaging.

## [3.10.4] - 2026-04-01

### Fixed
- **SSH timeout crashes** — `conn.run()` now catches `TimeoutExpired` and returns a graceful failure (rc=124) instead of crashing with a raw Python traceback. Every provisioning step benefits.
- **Misleading "ufw not found"** — a timeout on `which ufw` was reported as "ufw not found" instead of showing the actual timeout error.
- **Opaque PWA upload errors** — `"Failed to upload shared PWA assets"` now includes the filename and stderr (e.g. `"Failed to upload pwa/app.js: Command timed out after 30s"`).

### Changed
- **Increased all SSH timeouts for low-end machines** — simple commands 5s→15s, file writes 15s→30s, Docker/systemctl 30s→60s, apt/acme.sh 120s→180s, Docker pull 180s→300s. Accounts for SSH handshake overhead on cheap VPS providers.

## [3.10.3] - 2026-03-31

### Fixed
- **XHTTP URL missing `sni` and `fp` parameters** — Shadowrocket (and potentially other clients) failed to connect via XHTTP because the URL lacked TLS parameters. Reality URLs had them; XHTTP URLs did not. Fixed in all 4 URL generation locations.
- **Rich markup eating `[y/N]` prompt hints** — the relay offer prompt showed no hint about how to decline because Rich parsed `[y/N]` as a markup tag. All y/n prompts now display correctly.

### Changed
- **Replaced `qrencode` binary with Python `segno` package** — QR codes are now generated in pure Python. No more broken QR codes when system `qrencode` is not installed. Removes the `qrencode` apt dependency from server provisioning too.

## [3.10.2] - 2026-03-31

### Fixed
- **`meridian probe`** — no longer aborts when port 443 is closed. Remaining checks skip gracefully, and the port scan still reports other open ports.

## [3.10.1] - 2026-03-31

### Fixed
- **`--no-harden` port 443 bug** — deploying with `--no-harden` on a server with an active firewall left port 443 blocked. New `EnsurePort443` step adds the rule when ufw is already active, without touching SSH or other firewall settings.

## [3.10.0] - 2026-03-31

### Added
- **`meridian probe`** — censor-perspective server analysis. Runs 9 checks from the client side (no SSH): port surface, HTTP response, TLS certificate, SNI consistency, proxy path probing, WebSocket upgrade, reverse DNS, HTTP/2 ALPN, and legacy TLS versions. Works on any server, not just Meridian deployments. Accepts IPs and domain names.
- **Non-LTS Ubuntu detection** — preflight warns on non-LTS Ubuntu releases (only 9 months of support). Provisioner detects EOL package repos and gives a clear error.

## [3.9.0] - 2026-03-30

### Added
- **Decoy mode** (`--decoy 403`) — unknown paths return a stock nginx 403 page instead of aborting the connection. Server header, Content-Type, and response body match real nginx. CONNECT method handled. HTTP/3 disabled in decoy mode to prevent alt-svc header leak.
- **Firewall cleanup** — deploy now removes stale UFW rules from previous setups, keeping only ports 22, 443, and optionally 80.

### Changed
- **Vision refresh** — README, website hero, docs, and CLAUDE.md updated around "deploy it right, share it easily" messaging. Emphasizes airtight deployment over protocol choice.
- **Hero kicker** — "Open-source privacy tool" → "Open-source internet freedom tool" (English and Chinese aligned with existing Russian/Farsi translations).
- **PyPI description** updated to reflect hardened-by-default messaging.

## [3.8.3] - 2026-03-25

### Added
- **One-tap subscription import** — deep link buttons on connection pages let users import auto-updating subscriptions with a single tap. Supports v2rayNG, Hiddify, and Streisand URI schemes. Replaces the hidden manual-copy subscription flow.
- **Streisand app** — added as an iOS client option (popular in censored regions, supports deep links).
- **Branding flags in CLI reference** — `--server-name`, `--icon`, `--color` now documented in cli-reference across all 4 locales.

## [3.8.2] - 2026-03-25

### Added
- **Server branding** — personalize connection pages with `--server-name`, `--icon` (emoji or image URL), and `--color` (6 curated palettes: ocean, sunset, forest, lavender, rose, slate). Interactive wizard prompts for all three. Branding stored in credentials and rendered in PWA.

### Fixed
- **Connection pages unreachable in IP-only mode** — browsers don't send SNI for bare IP addresses (RFC 6066), so HAProxy dropped every connection before reaching Caddy. Added no-SNI routing rule. Also added Caddy `abort` catch-all so censors probing unknown paths see nothing.

## [3.8.1] - 2026-03-25

### Fixed
- **Blank demo page** — `jinja2` was only a dev dependency but needed at runtime for PWA template rendering. Moved to main dependencies. Added `qrencode` to CI for QR code generation
- **PyPI README** — replaced relative image paths with absolute URLs, replaced Mermaid diagram (unsupported on PyPI) with architecture SVG image
- **CI demo validation** — fixed `find` command that incorrectly excluded all `index.html` files

### Changed
- **Command Builder elevated on homepage** — moved from section 7 to section 4 (after "How it works") for better discoverability
- **Trust bar updated** — 290+ → 480+ tests across all 4 locales
- **SECURITY.md** — documented update check transparency (reads PyPI only, no telemetry)

### Added
- **Backlog items** — `meridian client export` for offline HTML sharing, live GitHub stars in trust bar

## [3.8.0] - 2026-03-24

### Security
- **XSS in inline onclick handlers** — replaced all `onclick="shareUrl('...')"` patterns with `data-url` attributes + delegated event listener. URLs no longer interpolated into inline JS strings
- **Content-Security-Policy** — added CSP header to Caddy connection page block (`default-src 'self'; img-src 'self' data:`)
- **Remove VPN identifiers from client-facing pages** — share titles no longer say "VPN", footer "Powered by Meridian" and GitHub link removed, manifest name changed to "Setup"
- **Service worker cache anonymity** — cache key changed from `meridian-pwa-v1` to `pwa-v1`

### Added
- **RTL CSS support for Farsi** — all directional properties converted to CSS logical properties (`margin-inline-start/end`, `text-align: start/end`, `border-inline-start`)
- **i18n: 15+ previously hardcoded strings now translated** — toast "Copied", stats "Active now/ago", page title, relay "via {name}", error messages, noscript fallback (RU/FA/ZH)
- **config.json error handling** — retry button + 10-second loading timeout with translated messages
- **Keyboard accessibility** — `tabindex`, `role="button"`, Enter/Space handlers on clickable URL divs; `role="alert"` + `aria-live` on toast
- **40 new tests** — upload pipeline, Caddy XHTTP block, `handle_path` structure, config.json schema, `_PWA_APPS`↔`apps.json` sync, Unicode client names, `_render_stats_script`

### Changed
- **Landing page restructured** — added trust bar (MIT, 290+ tests, 4 languages, open source), final CTA, removed redundant Reference section; reordered: Technology before Command Builder
- **README improved** — "Why Meridian?" comparison table, architecture diagram promoted above CLI table, CLI reference trimmed to 8 essentials
- **Architecture diagram** — hand-crafted SVG replacing missing PNG (was 404)
- **Locale-aware docs links** — `updateDocsLinks()` rewrites `/docs/en/` to active locale on language switch
- **GitHub Discussions** enabled
- **GitHub topics** — removed stale `ansible`, added `anti-censorship`, `vpn`, `censorship-circumvention`, `python`, `cli`

### Fixed
- **install.sh deprecated commands** — `setup`→`deploy`, `check`→`preflight`, `ping`→`test`, `version`→`--version`
- **`prefers-reduced-motion`** — scroll-reveal animations and smooth scrolling now respect user preference
- **Stats files unreadable by Caddy** — cron script wrote files as root with `0o600`; changed to `0o644` (directory is already access-controlled)
- **Service worker `networkFirst` returned `undefined`** — now returns 503 Response on full cache+network miss
- **Service worker stale assets** — replaced `cacheFirst` with stale-while-revalidate pattern (serve cached, refresh in background)
- **Silent template failures** — `render.py` bare `except` now logs warning instead of silently returning empty string
- **`upload_client_files` could exceed ARG_MAX** — switched from `printf '%s'` to base64 transport matching `upload_pwa_assets`
- **`mkdir -p` return codes unchecked** — both upload functions now check and return False on failure
- **Caddy config duplication** — extracted shared connection-page block into `_render_connection_page_block()` helper
- **Farsi question mark** — ASCII `?` → Persian `؟` (U+061F)
- **Deep links** — Android uses `vless://` scheme instead of Hiddify-specific `intent://`; iOS no longer silently overwrites clipboard
- **Focus outline** — `summary:focus{outline:none}` → `:focus:not(:focus-visible)` to preserve keyboard focus ring

## [3.7.4] - 2026-03-24

### Fixed
- **Caddy PWA cache headers** — `handle` + `uri strip_prefix` caused path matchers to evaluate before prefix stripping, so `@pwa_assets path /pwa/*` never matched; switched to `handle_path` which strips at the handle level
- **Mypy type errors in dev.py** — fix bytes/str variable shadowing and SimpleHTTPRequestHandler init typing
- **Ruff formatting** — fix import order in dev.py, formatting in services.py

## [3.7.2] - 2026-03-24

### Added
- **`meridian deploy local`** — run directly on the server without SSH. Accepts `local` or `locally` wherever an IP is expected (`deploy`, `check`, `client add`, etc.)
- **Interactive wizard local detection** — auto-detects root on VPS and offers local deployment, skipping IP/SSH prompts
- **Service health watchdog** — 5-minute cron checks Xray, Caddy, HAProxy and restarts crashed services (logged via syslog)
- **Disk space pre-check** — provisioner fails early if <2GB free, before pulling Docker images
- **Systemd restart policies** — HAProxy and Caddy get `Restart=on-failure` drop-in overrides; relay gets `StartLimitBurst=5`
- **Realm SHA256 verification** — binary checksum verified after download (pinned digests in `config.py`)
- **Provisioner test suite** — 64+ new tests across provisioner steps, relay, and pipeline

### Fixed
- **Host key verification** — write only the verified key type to `known_hosts` (was writing all scanned types); refuse to auto-accept in non-interactive mode
- **Cookie file race window** — panel login cookie created with `umask 077` in subshell (was world-readable until separate chmod)
- **Realm config permissions** — `realm.toml` set to `chmod 600` (was 644, contained exit IP in plaintext)
- **Stats files permissions** — per-client JSON stats set to `chmod 600` (was 644, revealed traffic patterns)
- **CORS on private pages** — removed `Access-Control-Allow-Origin: *` from connection page Caddy config
- **Credential dir permissions** — explicitly enforce `chmod 0o700` after mkdir (was ignored on existing dirs)
- **Shell injection in sysctl** — BBR persistence uses `shlex.quote()` + `printf` instead of unquoted `echo`
- **Stats script auth** — panel password URL-encoded in generated cron script
- **Stats cron logging** — output piped to `logger -t meridian-stats` (was silent on failure)
- **Teardown cleanup** — removes health watchdog cron, systemd restart overrides, and runs `daemon-reload`
- **E2E test infrastructure** — pre-populate `known_hosts` for port-2222 sshd, fix misleading error message
- **Mypy** — fix `_check_ports` type annotation (`object` → `ServerConnection`), add `StepStatus` literal type

### Changed
- `detect_public_ip()` moved from `setup.py` to `resolve.py` (shared across wizard and server resolution)

## [3.7.1] - 2026-03-23

### Fixed
- **HAProxy port mismatch** — HAProxy hardcoded Reality backend port 10443 instead of using the actual IP-derived port, causing VPN to connect but produce no internet traffic

## [3.7.0] - 2026-03-22

### Added — Website (getmeridian.org)
- **New Astro website** replacing old static HTML docs — 45 pages, fully static
- **Full i18n**: 30 translated doc pages (10 each for Russian, Farsi, Chinese) + landing page UI translations (~250 keys)
- **CommandBuilder**: 5-tab interactive CLI builder (Deploy, Preflight, SNI Scan, Doctor, Teardown) with real-time command generation, localStorage persistence, IPv4 validation
- **Pagefind search**: static full-text search across all 40 doc pages in 4 languages
- **Scroll reveal animations**: IntersectionObserver fade-in-up on landing page sections
- **Mobile navigation**: hamburger menu at <640px with slide-down drawer
- **404 page**: branded with noindex meta tag
- **Sitemap** (`@astrojs/sitemap`) and `robots.txt`
- **Mermaid diagram rendering** via `rehype-mermaid` (build-time inline SVGs)
- **llms.txt endpoints**: `/llms.txt`, `/llms-full.txt`, `/md/{locale}/{slug}` for AI consumption
- **Terminal deploy SVG**: recorded from live server deploy, sanitized with RFC 5737 example IPs
- **Locale-aware docs**: sidebar filters by locale, locale switcher buttons, `<link rel="alternate" hreflang>` for SEO
- **hreflang links** on all doc pages for proper multi-language indexing
- **CSS sync script** (`sync-template-css.mjs`): extracts tokens from Astro and injects into Jinja2 template (ready for SYNC markers)
- **App links single source of truth**: `src/data/apps.json` validated by CI and pre-push hook against Jinja2 template

### Changed — Website
- **Self-hosted fonts**: Fraunces, Source Sans 3, JetBrains Mono served from `/fonts/` — zero external requests to Google (critical for users in censored regions)
- **Demo page dark mode**: all hardcoded hex colors replaced with CSS custom properties
- Protocol card colors (`--blue`, `--amber`) added to design token system with dark mode variants
- Early `<script>` in `<head>` sets `lang`/`dir` from localStorage before paint (prevents RTL layout shift)
- Docs redirect (`/docs/`) detects stored locale preference before redirecting

### Changed — CI/CD
- **Release workflow** builds Astro site instead of copying old `docs/`
- **Website Build** job added to CI pipeline
- Actions upgraded: `setup-node` v6 (Node 24, npm caching), `setup-python` v6, `setup-uv` v7, `upload-pages-artifact` v4
- Playwright Chromium installed in CI/release for Mermaid rendering
- Pre-push hook validates app links against `apps.json` (was `docs/demo.html`)
- AI docs source moved to `website/src/content/ai/` (Makefile, hooks, workflows updated)

### Changed — Repository
- `docs/` directory removed entirely — all content consolidated under `website/`
- README.md image paths updated to `website/public/img/`

### Security
- `<meta name="referrer" content="no-referrer">` prevents origin leaks for at-risk users
- Ping page XSS fix: HTML-escape all localStorage-rendered values
- Demo page uses RFC 5737 documentation IPs (was real-ish subnet)
- Terminal SVG uses example IPs and redacted paths (was live server data)
- All `target="_blank"` links have `rel="noopener"`

### Accessibility
- `aria-current="page"` on active sidebar link
- `aria-label` on search input
- `for=` attributes linking CommandBuilder labels to inputs
- Translatable timeline steps, code block labels, callouts, TOC label, "then" separator

## [3.6.0] - 2026-03-22

### Changed
- **BREAKING:** XHTTP now routed through port 443 via Caddy (no extra port exposed)
  - URLs simplified: `security=tls`, path-based routing, no Reality params
  - Old XHTTP URLs (with separate port + Reality) no longer work
  - XHTTP remains enabled by default — it's now a free fallback with zero cost
- Deploy wizard redesigned with protocol explanation, Rich Panel summary
- Inline SNI scan: wizard offers to scan for optimal camouflage target (~30s)
- "Camouflage target" terminology replaces "SNI" in user-facing text
- Flag descriptions improved for non-VPN-expert users

### Added
- `scan_for_sni()` extracted as reusable function for wizard integration
- `xhttp_path` field in credentials (random 16-char path for Caddy routing)
- Caddy reverse proxy for XHTTP in both IP and domain modes

### Fixed
- XHTTP no longer opens external firewall port (localhost-only)

## [3.5.0] - 2026-03-22

### Added
- Codified architecture patterns in CLAUDE.md for consistent development
- Light mode support for website and ping tool
- WCAG accessibility improvements (color contrast, keyboard focus, ARIA labels)
- Step counter in provisioner output ([1/14] format)
- SNI and XHTTP visibility in setup wizard summary
- qrencode missing warning
- Mermaid architecture diagrams replacing ASCII art
- CHANGELOG.md

### Fixed
- Documentation accuracy for v3.4.0 architecture (HAProxy+Caddy in all modes)
- XHTTP checkbox default in website command builder
- base64 encoding cross-platform consistency in services.py
- render.py template code duplication
- HTML escaping in minimal fallback renderer

### Changed
- Renamed CLI commands for clarity: `setup` → `deploy`, `check` → `preflight`, `ping` → `test`, `diagnostics` → `doctor` (alias: `rage`), `uninstall` → `teardown`, `self-update` → `update`
- Removed `version` subcommand (use `--version` / `-v` flag instead)
- Improved CLI help text for deploy, preflight, test, doctor commands
- Improved connection page QR code guidance for non-technical users
- Reordered client add terminal output (shareable URL first)

## [3.4.0] - 2025-03-21

### Added
- Server-hosted connection pages via Let's Encrypt IP certificates
- HAProxy + Caddy deployment in standalone mode (all modes now use this architecture)
- Per-client hosted pages with shareable URLs
- Usage stats on server-hosted connection pages
- `render_hosted_html()` for server-hosted page rendering

### Changed
- Architecture: all modes now deploy HAProxy (port 443) + Caddy (port 8443) + Xray
- Connection pages automatically deployed during `client add`

## [3.3.1] - 2025-03-20

### Fixed
- Uninstall cron grep pattern never matched actual cron entry
- i18n textContent stripped ping test link for RU/FA/ZH users
- test_panel.py body format test always passed due to `assert X or Y`

### Changed
- Completed output.py migration to urls.py/render.py/display.py
- Centralized magic values in config.py
- Normalized provisioner step names to human-readable format

## [3.3.0] - 2025-03-18

### Fixed
- `--xhttp` flag was always True, now proper `--xhttp/--no-xhttp` toggle
- `InstallHAProxy` status always "changed" (copy-paste bug)
- `ConfigureFirewall` idempotency was fake

### Removed
- Complete Ansible purge (88 files, -3,785 lines)

### Changed
- Provisioner refactored: timed decorator deduplicated, PanelClient methods public
- Setup success celebration message
- Better error messages throughout

## [3.2.0] - 2025-03-15

### Added
- Python provisioner engine (15 steps replacing Ansible)
- Protocol foundation (ProtocolURL, dict registry)
- Output split into urls.py / render.py / display.py
- Error taxonomy and sudo escalation
- PanelClient context manager

### Removed
- Ansible fully deleted (-2,825 lines)

## [3.1.0] - 2025-03-12

### Added
- Uninstall provisioner
- E2E test pipeline
