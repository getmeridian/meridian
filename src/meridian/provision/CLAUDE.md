# provision — Pure-Python step pipeline
## Design decisions
**Steps over scripts** — each step is a class with `run(conn, ctx) → StepResult` (ok/changed/skipped/failed); the pipeline stops on the first failure.
**Recipes are ordered lists** — `build_server_baseline_steps()` (OS, SSH-safe UFW, optional Docker; paired with read-only `build_server_baseline_checks()`) and `build_setup_steps()` (control service, nginx, TLS; it refuses a context without the service's install) return steps in execution order, gated by plain `if`; keep each step after what it depends on. WARP installs through the reconciler's agent-runtime driver.
**Typed context** — `ProvisionContext` has explicit fields for configuration and inter-step data; no dictionary escape hatches.
**SSH for infrastructure, REST for configuration** — steps install the service and nginx; users, endpoints, and settings go over its admin API once it runs.
**nginx and TLS split** — `nginx_render.py` renders config strings, `nginx.py` holds the steps, `tls.py` issues acme.sh certificates.
**Semantic ensure helpers** — `ensure.py` wraps package, file, service, and UFW operations; use it plus `ServerFacts` instead of new check/act shell snippets.
**Renderer-free steps** — `Provisioner.run()` emits typed core events; Rich lives only in `progress.py` (`RichStepRenderer` for the CLI, `NoopStepRenderer` for Engine and tests).
**Cooperative cancellation** — `Provisioner.run(cancellation=...)` polls only before each step; a running step and its SSH commands always finish.
**Relay steps are panel-agnostic** — `RelayContext` and `InstallRealm` back the reconciler's Realm driver; the binary installs as `RELAY_BINARY_PATH` and the sshd drop-in is `00-hardening.conf`.
**Control service install** — `InstallControlService` puts a sha256-pinned uv under `/opt/ctld`, installs the exact `meridian-vpn` (or `control_artifacts`' dev wheel) on uv-managed Python 3.12, and restarts `ctld` only after a change, never over a newer recorded package (`core/version_skew.py`).
## What's done well
- **Check before acting** — steps read package status, file content, and unit state first, so a rerun reports `ok`, not `changed`.
## Pitfalls
- **nginx `add_header` inheritance** — a child `location` with `add_header` drops parent headers; use maps for cache/robots policies and keep all PWA headers together.
- **acme.sh short-lived IP certs** — 6-day certs need `--days 5` and an explicit renew window; report acme.sh's last lines (`_acme_reason`), since its first ones are only timestamps.
- **nginx stream is a dynamic module** — install `libnginx-mod-stream`.
- **nginx ≥ 1.25 comes from nginx.org** — `http2 on;` needs it; keep old conffiles (`--force-confdef --force-confold`) and add the `sites-enabled` include on every run, not only after Meridian's own install (a preinstalled nginx.org build never got it, and the ACME listener failed). The system-lab image pre-installs a stock 1.27.
- **Internal TLS servers need `absolute_redirect off` and `http2 on`** — behind the 443 stream router on 127.0.0.1:8443, slash redirects point browsers at `:8443`, and Xray's fingerprinted XHTTP client speaks HTTP/2 whatever ALPN says.
- **XHTTP locations need `client_max_body_size 0`** — nginx counts streamed bodies against its 1m default; serve packet-up only.
- **Never `return 444` in HTTPS blocks** — use 403/404 (less fingerprintable).
- **Legacy stream config yields to V4** — `ConfigureNginx` skips `stream.d/meridian.conf` once a `meridian-v4-*` stream file listens on 443, or `nginx -t` fails on a duplicate listener.
- **Every apt-get goes through `ensure.run_apt()`** — it waits up to 5 minutes while unattended-upgrades or cloud-init holds the lock, and `apt_failure()` names the holder; never `2>/dev/null` apt, or a lock reads as an ordinary failure (the nginx install then fell through to the nginx.org path). Update and install are separate calls (`apt_update_and_install()`), and install only after a check says something is missing: `InstallNginx` runs on every certificate and nginx apply.
- **Teardown never removes the `meridian` CLI** — an operator-installed copy is how they redeploy.
- **Teardown keeps what may predate Meridian** — fail2ban stays on (host hardening, like SSH settings), and nginx is stopped only when no operator config remains.
- **Teardown never deletes `/var/backups/meridian-migration`** — it holds the only copy of a migrated panel's database; `panel_stack_removal()` is the one panel-removal list teardown and `migrate` share.
- **One unit per `systemctl` call when a unit may be absent** — multi-unit `disable --now` aborts on a missing unit and stops nothing.
- **Realm installs only a pinned tarball** — `RelayContext.realm_sha256` comes from the reviewed plan; a target without a digest fails (it once skipped verification silently).
- **Realm is the static musl build, run before it replaces anything** — upstream's 2.9.6 gnu build needs glibc 2.38, so `fleet upgrade` on Ubuntu 22.04 overwrote a working binary with one that could not start. A binary that runs and fails observes as drift (reinstall), not unavailable evidence.
- **Firewall follows the effective sshd port** — never assume `22/tcp`.
- **Recipes never own public ports** — compiled, tagged rules open them; `ConfigureFirewall` deletes only the bare, comment-less `443/udp` an old bootstrap added.
- **Baseline checks exit 1 for absence** — a bare `docker` or `ufw` exits 127 (stock Debian has no ufw), which observation treats as unavailable evidence.
- **fail2ban reads sshd from the journal** — Debian 12 has no `auth.log`; `ConfigureFail2ban` writes `jail.d/sshd-journal.local` (needs `python3-systemd`) and re-checks the service after a pause.
- **"Installed" means `install ok installed`** — `ServerFacts.installed_packages()` reads `${Status}`; `dpkg-query -W` also lists removed packages.
- **Ubuntu and Debian both** — use `bind9-dnsutils` (Debian 13's `dnsutils` is virtual) and read the codename from `ServerFacts.os_release()`, not `lsb_release`.
- **An unread install record stops the control-service install** — only an absent marker means a fresh server; read as empty, a lost reply let `uv tool install --force` downgrade a newer service.
- **Restart `ctld` when it started before its marker, config, or unit last changed** — a run whose reply was lost after writing them never restarted, and the retry found every file current.
- **Generated file content stays off shell commands** — use `conn.put_text()`/`put_bytes()` with mode/owner/sensitive flags, not heredocs or `printf`.
