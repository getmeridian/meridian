# provision — Pure-Python step pipeline
## Design decisions
**Steps over scripts** — each step is a class with `run(conn, ctx) → StepResult` (ok/changed/skipped/failed); the pipeline stops on the first failure.
**Recipes are ordered lists** — `build_server_baseline_steps()` (with read-only `build_server_baseline_checks()`) and `build_setup_steps()` return steps in execution order, gated by plain `if`; keep each step after what it depends on.
**Typed context** — `ProvisionContext` has explicit fields for configuration and inter-step data; no dictionary escape hatches.
**SSH for infrastructure, REST for configuration** — steps install the service and nginx; users, endpoints, and settings go over its admin API.
**nginx and TLS split** — `nginx_render.py` renders config strings, `nginx.py` holds the steps, `tls.py` issues acme.sh certificates.
**Semantic ensure helpers** — `ensure.py` wraps package, file, service, and UFW operations; use it with `ServerFacts` instead of new check/act shell snippets.
**Renderer-free steps** — `Provisioner.run()` emits typed core events; Rich lives only in `progress.py`. Cancellation is polled only between steps.
**Control service install** — `InstallControlService` installs the exact `meridian-vpn` on a sha256-pinned uv under `/opt/ctld`, and never over a newer recorded package (`core/version_skew.py`).
## What's done well
- **Check before acting** — steps read package, file, and unit state first, so a rerun reports `ok`, not `changed`.
## Pitfalls
- **nginx `add_header` inheritance** — a child `location` with `add_header` drops the parent's headers; use maps for cache and robots policies and keep page headers together.
- **acme.sh short-lived IP certificates** need `--days 5` and an explicit renew window; report acme.sh's last lines (its first are timestamps), and name a rate limit's retry time instead of port advice.
- **nginx stream is a dynamic module** (`libnginx-mod-stream`), and `http2 on;` needs nginx ≥ 1.25 from nginx.org; keep old conffiles and add the `sites-enabled` include on every run, since a preinstalled nginx.org build never had it.
- **Internal TLS servers need `absolute_redirect off` and `http2 on`** — behind the 443 stream router, redirects otherwise point at the internal port, and Xray's XHTTP client speaks HTTP/2 whatever ALPN says.
- **XHTTP locations need `client_max_body_size 0`** — nginx counts streamed bodies against its default limit.
- **Never `return 444` in HTTPS blocks** — use 403/404, which are less fingerprintable.
- **Legacy stream config yields to V4** — skip `stream.d/meridian.conf` once a `meridian-v4-*` file listens on 443, or `nginx -t` fails on a duplicate listener.
- **Every apt-get goes through `ensure.run_apt()`** — it waits for unattended-upgrades or cloud-init to release the lock and names the holder; never silence apt's stderr, or a lock reads as an ordinary failure. Install only after a check says something is missing.
- **Teardown keeps what the operator may own** — the `meridian` CLI, fail2ban, and nginx while operator config remains; `panel_stack_removal()` is the one list teardown and `migrate` share.
- **One unit per `systemctl` call when a unit may be absent** — multi-unit `disable --now` aborts on a missing unit and stops nothing.
- **Realm installs only a pinned static musl tarball, run before it replaces anything** — a glibc build that couldn't start once overwrote a working binary; a target without a digest fails.
- **Firewall follows the effective sshd port**, never an assumed `22/tcp`; recipes never open public ports, compiled tagged rules do.
- **Baseline checks exit 1 for absence** — a missing `ufw` or `docker` exits 127, which observation treats as unavailable evidence.
- **fail2ban reads sshd from the journal** — Debian 12 has no `auth.log`.
- **"Installed" means `install ok installed`** — `dpkg-query -W` also lists removed packages.
- **Ubuntu and Debian both** — use `bind9-dnsutils` and read the codename from `ServerFacts.os_release()`, not `lsb_release`.
- **An unread install record stops the control-service install** — only an absent marker means fresh; a lost reply once let a forced install downgrade a newer service.
- **Restart `ctld` when it started before its marker, config, or unit last changed** — a run whose reply was lost after writing them never restarted.
- **Connection pages are service responses** — remove legacy page files only after nginx validates and reloads; a failed reload keeps them.
- **Generated file content stays off shell commands** — use `conn.put_text()`/`put_bytes()` with mode, owner, and sensitive flags, not heredocs.
