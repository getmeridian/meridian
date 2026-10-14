# provision — Pure-Python step pipeline
## Design decisions
**Steps over scripts** — each step is a class with `run(conn, ctx) → StepResult` (ok/changed/skipped/failed); the pipeline stops on the first failure.
**Recipes are ordered lists** — `build_server_baseline_steps()` (OS, SSH-safe UFW, optional Docker; paired with read-only `build_server_baseline_checks()`) and `build_setup_steps()` (control plane, nginx, TLS) return steps in execution order, gated by plain `if`; keep each step after what it depends on. WARP installs through the reconciler's node-runtime driver.
**Typed context** — `ProvisionContext` has explicit fields for configuration and inter-step data; no dictionary escape hatches.
**Remnawave containers** — the panel stack runs in a bridge network on `127.0.0.1:3000` behind nginx; the node (`node_deploy.py`) runs host-network with `cap_add: NET_ADMIN`, without which the panel's node plugins and IP Control silently do nothing.
**SSH for infrastructure, REST for configuration** — steps deploy containers; panel, user, and profile setup happens over the API once they run.
**nginx and TLS split** — `nginx_render.py` renders config strings, `nginx.py` holds the steps, `tls.py` issues acme.sh certificates.
**Semantic ensure helpers** — `ensure.py` wraps package, file, service, and UFW operations; use it plus `ServerFacts` instead of new check/act shell snippets.
**Renderer-free steps** — `Provisioner.run()` emits typed core events; Rich lives only in `progress.py` (`RichStepRenderer` for the CLI, `NoopStepRenderer` for Engine and tests).
**Cooperative cancellation** — `Provisioner.run(cancellation=...)` polls only before each step; a running step and its SSH commands always finish.
**Relay steps are panel-agnostic** — `RelayContext` and `InstallRealm` back the reconciler's Realm driver; the binary installs as `RELAY_BINARY_PATH` and the sshd drop-in is `00-hardening.conf`.
## What's done well
- **Idempotent containers** — panel steps check `docker inspect` before deploying and poll the backend's health endpoint after.
- **Secrets generated once** — the PostgreSQL password and `APP_SECRET` come from `secrets.token_hex` on first install; restarts reuse `.env` (API tokens depend on `APP_SECRET`).
## Pitfalls
- **Panel sub-path rewrites follow the bundle** — Remnawave has no base path, so `sub_filter` must match the shipped frontend's literals (3.4 uses backticks); re-grep on every backend bump and never rewrite `` `/api ``.
- **nginx `add_header` inheritance** — a child `location` with `add_header` drops parent headers; use `map`.
- **acme.sh short-lived IP certs** — 6-day certs need `--days 5` and an explicit renew window.
- **nginx stream is a dynamic module** — install `libnginx-mod-stream`.
- **nginx ≥ 1.25 comes from nginx.org** — `http2 on;` needs it; keep old conffiles (`--force-confdef --force-confold`) and add the `sites-enabled` include. The system-lab image pre-installs 1.27 and hides all three.
- **Internal TLS servers need `absolute_redirect off`** — behind the 443 stream router on 127.0.0.1:8443, automatic slash redirects point browsers at `:8443`.
- **Internal TLS servers need `http2 on`** — Xray's fingerprinted XHTTP client speaks HTTP/2 whatever ALPN says.
- **XHTTP locations need `client_max_body_size 0`** — nginx counts streamed bodies against its 1m default; serve packet-up only.
- **Never `return 444` in HTTPS blocks** — use 403/404 (less fingerprintable).
- **Legacy stream config yields to V4** — `ConfigureNginx` skips `stream.d/meridian.conf` once a `meridian-v4-*` stream file listens on 443, or `nginx -t` fails on a duplicate listener.
- **Panel data is the directory, never a named volume** — PostgreSQL uses the `./data` bind mount and the Compose `name:` is pinned; `runtime_migration.py` moves `/opt/remnawave` by one rename after stopping the old stack, refusing mounts or two directories first.
- **`docker compose restart` keeps the old environment** — after an `env_file` change, `up -d --no-deps --force-recreate`.
- **Teardown never removes the `meridian` CLI** — an operator-installed copy is how they redeploy.
- **Teardown keeps what may predate Meridian** — fail2ban stays on (host hardening, like SSH settings), and nginx is stopped only when no operator config remains.
- **One unit per `systemctl` call when a unit may be absent** — multi-unit `disable --now` aborts on a missing unit and stops nothing.
- **Firewall follows the effective sshd port** — never assume `22/tcp`.
- **Recipes never own public ports** — compiled, tagged rules open them; `ConfigureFirewall` deletes only the bare, comment-less `443/udp` an old bootstrap added.
- **Baseline checks exit 1 for absence** — a bare `docker` or `ufw` exits 127 (stock Debian has no ufw), which observation treats as unavailable evidence.
- **fail2ban reads sshd from the journal** — Debian 12 has no `auth.log`; `ConfigureFail2ban` writes `jail.d/sshd-journal.local` (needs `python3-systemd`) and re-checks the service after a pause.
- **"Installed" means `install ok installed`** — `ServerFacts.installed_packages()` reads `${Status}`; `dpkg-query -W` also lists removed packages.
- **Ubuntu and Debian both** — use `bind9-dnsutils` (Debian 13's `dnsutils` is virtual) and read the codename from `ServerFacts.os_release()`, not `lsb_release`.
- **Generated file content stays off shell commands** — use `conn.put_text()`/`put_bytes()` with mode/owner/sensitive flags, not heredocs or `printf`.
