# provision — Pure-Python step pipeline
## Design decisions
**Steps over monolithic script** — each step is a class with `run(conn, ctx) → StepResult` (ok/changed/skipped/failed). Composable, independently testable. Pipeline stops on first failure.

**Composable server baseline** — `build_server_baseline_steps()` owns OS setup and optional Docker; V4 disables legacy public-port ownership and pairs it with `build_server_baseline_checks()`.

**Recipe graph** — builders wrap steps in `Operation` objects with explicit `requires`/`provides` resources. Add graph edges before relying on declaration order for new conditional chunks.

**Typed context** — `ProvisionContext` has explicit typed fields for configuration and inter-step data (`ctx.panel_api`, `ctx.cluster`, generated paths). Do not add dictionary-style escape hatches.

**Remnawave containers** — Panel (backend + PostgreSQL) in bridge network, node in host network. Panel on `127.0.0.1:3000`, reverse-proxied by nginx. The node container carries `cap_add: NET_ADMIN` — mandatory per upstream panel 2.6.2+ / 2.7.0+ docs. It enables the node plugin system (Torrent Blocker, Ingress/Egress Filter, Connection Drop) and the IP Control panel feature; without it operators can activate those features in the panel UI and see nothing happen (kernel EPERM on nftables syscalls, swallowed). System lab Stage 3 asserts the capability is present on the live container.

**Post-provisioner API setup** — Container deployment is SSH-based (provisioner steps). Panel/user/profile configuration happens via direct REST API calls AFTER containers are running. This separates infrastructure (SSH) from configuration (REST).

**nginx + TLS extracted** — `nginx_render.py` owns pure config string rendering (stream SNI, http server blocks). `nginx.py` owns step classes (InstallNginx, ConfigureNginx, DeployPWAAssets) that call `conn.run()`. `tls.py` owns acme.sh cert issuance. Connection page deployment stays in `services.py`.

**Semantic ensure helpers** — `ensure.py` wraps package, file, service, and UFW operations. Prefer these helpers plus `ServerFacts` for idempotency checks instead of duplicating check/act shell snippets.

**Reporter hook** — `Provisioner.run()` may emit typed core events while preserving Rich rendering by default. CLI/UI renderers subscribe; steps still return `StepResult`.

**Renderer abstraction** — `StepRenderer.step_starting()` returns `AbstractContextManager[None]`. Rich stays in `progress.py`; `steps.py` has zero Rich imports. CLI passes `RichStepRenderer`; Engine/tests use `NoopStepRenderer`.

**Executor bridge** — deploy provisioning enters through `RemoteExecutorConnection`; steps can keep `conn.run()` while transports move behind core executor contracts.

**Relay pipeline is separate** — uses `RelayContext` and Realm TCP forwarding. Panel-agnostic.
## What's done well
- **Idempotent containers** — panel/node steps check `docker inspect` before deploying.
- **Health polling** — panel step waits for `/api/health`, node step waits for port binding.
- **Secret generation** — PostgreSQL password, JWT secrets generated per deploy via `secrets.token_hex`.

## Pitfalls

- **nginx `add_header` inheritance** — child `location` blocks with `add_header` suppress parent headers. Use `map` directives.
- **acme.sh shortlived IP certs** — 6-day certs need `--days 5` and explicit renew window.
- **nginx stream = dynamic module** — install `libnginx-mod-stream` package.
- **`return 444` is banned from HTTPS blocks** — use 403/404 instead (less fingerprintable).
- **Per-relay nginx files** — relay SNI routing uses per-file config, not monolithic rewrite.
- **Firewall must follow the effective sshd port** — never assume `22/tcp`.
- **Generated file content stays off shell commands** — use `conn.put_text()`/`put_bytes()` with mode/owner/sensitive flags, not heredocs or `printf`.
- **WARP belongs in both recipes** — a WARP-enabled exit may be node-only, so `build_node_steps()` must install it after Docker just like first setup.
