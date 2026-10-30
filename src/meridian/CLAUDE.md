# src/meridian — Python CLI package
## Design decisions
**Cluster config** — one hidden `cluster.yml` holds topology intent, bindings, allocations, and checkpoints (`cluster.py` model, `cluster_persistence.py` YAML); users and operator-owned data live in the control service.
**Service access** — `control/client.py` speaks HTTPS from the deployer to nginx's secret path on 443 with the admin token, no SSH tunnel; client paths use `control_access.access_panel()`, fleet views `fleet_panel()`. `remnawave/` keeps only what `meridian migrate` reads.
**SSH abstraction** — `ServerConnection` runs locally or over SSH (non-root adds `sudo -n`); `conn.run()` returns `CommandResult`, `ssh_transfer.py` moves files, and `SSHUI` callbacks keep Rich out of transport. A timeout or exit 255 drops the ControlMaster so the next command redials.
**One key bootstrap** — `ssh_keys.py` owns Meridian's key pair, `authorized_keys` install, and the key-login check; `resolve.install_meridian_key()` is the one-time-password path.
**Server facts** — `facts.py` caches OS, kernel, firewall, sshd ports, disk, and sysctl; use it before adding one-off probe parsing.
**Errors over exits** — library modules raise `MeridianError` subclasses (`core/errors.py`) with a `category`; only command entry points call `console.fail()`. Every error must be actionable.
**Shared logic stays out of commands** — `resolve.py` and `setup/` serve all commands; library code never imports `commands/`.
**Exit country** — `registry_country.py` reads the RIR country via RDAP (no geo-IP) when an exit is first named and stores it in intent, keeping the compiler pure.
**Local home and contexts** — `config.default_home()` prefers `$XDG_CONFIG_HOME/meridian`; the home keeps per-person files and is the `default` context, and other fleets live in `contexts/NAME/` (`contexts.py`). `fleet_contexts.refuse_servers_claimed_elsewhere()` keeps a host in one fleet; a context a restore took over (`superseded.json`) only runs reads. `config.select_context()` binds per-fleet globals once.
**Neutral host names** — `host_runtime.py` is the one place for unit, `/opt`, and config names (`ctld`, `netagent`, `netcore`).
**Binary pins** — `binary_pins.py` holds the server binaries `fleet upgrade` may raise and their trust rules; `github_releases.py` reads same-major releases with GitHub's per-asset sha256.
**Remnawave fleets are only read to move them** — a legacy `cluster.yml` loads as `remnawave`, and every command except the move points at `meridian migrate` (`migrate/`).
**Revisions on the service** — `service_fence.py` moves the apply fence into the service and records each reviewed apply as a revision; once handed over, fence.json makes older Meridians refuse, and a service newer than the CLI refuses too (`core/version_skew.py`).
**Workload secrets** — `reconciler/workloads.py` allocates complete Reality key triples and persists them before remote mutation; `xray_workload.py` renders only reviewed protocols and egress.
## What's done well
- **Forward-compatible YAML** — unknown keys land inertly in `_extra` (never store load-bearing state there); pre-topology files are refused with rebuild guidance.
- **Applied kinds are explicit** — `core/resources.APPLIED_KINDS` lists what plan/apply drives; a leftover panel binding fails closed outside migrate's cut-over.
- **One owner per state** — the service owns users, `cluster.yml` owns topology, and the fence stops a stale or foreign copy from applying.
- **Read-only fleet projection** — `adapters/cluster.py` is the only source of node and relay rows; it never shows gateways as exits or restricted relay hops as public endpoints.
## Pitfalls
- **A handed-over fence.json is never a fallback** — an unreachable service is retried and refused, or a stale copy could apply around it.
- **Server ownership compares canonical addresses and fails closed** — two spellings of one IPv6 address once passed as two machines; an unreadable context refuses, and bindings claim servers until retirement.
- **A live agent that could not apply is a failed node**, and a refused service token (401/403) stops `fleet status` with exit 2 rather than reading as degraded.
- **Persist a requested cut-off in the typed binding before applying the removal** — runtime flags alone lose it after an interrupted apply.
- **Local state is fail-closed** — only missing `cluster.yml`/`servers.json` means fresh; malformed files need recovery.
- **Print `config.HOME_DISPLAY`, never a literal home path**, and read paths as `config.NAME` at call time — an import-time copy ignores `--context` (architecture-tested).
- **Rendered inputs are typed** — validate and canonicalize hostname, SNI, port, and path before nginx, Xray, or service serialization.
- **Build subscription URLs with `PanelConfig.subscription_url()`** — shared links never use the panel path.
- **Local mode follows the saved profile** — `ServerEntry.runs_here()` matches the recorded machine ID or a bindable address, never the outbound IP (a laptop on the server's VPN shares it).
- **A one-time SSH password only reaches a trusted host key** — it rides one command's askpass environment and is never saved, logged, or put in an error.
- **Sudo must be passwordless and checked up front** — a failing `sudo -n true` is a user error with a NOPASSWD hint, never an SSH key problem.
- **SSH ControlPath must fit a Unix socket** (103 bytes on macOS) — hash the target into the path and drop multiplexing when the home is too deep.
- **Camouflage targets** — never apple.com (ASN mismatch with VPS providers) or www.microsoft.com (certificate over REALITY's size limit, so no traffic passes); `camouflage_refusal()` refuses both. A relay's SNI must be reachable from its exit, and a `--self-steal` name's DNS is checked from the exit.
- **Reality key material is atomic** — refuse partial state instead of rotating keys; `node replace` carries keys to the new server. Hysteria2's salamander password derives from the Reality private key and is never stored separately.
- **Exits resolve only over IP-addressed DoH** (`xray_workload.EXIT_DOH_RESOLVERS`) — a hostname resolver or `AsIs` strategy hands names to the host's plain resolver; gateways keep the host resolver, since foreign DoH may be blocked in-country.
- **Local Xray is evidence, not a requirement** — a missing runtime is inconclusive, and an archive that misses `XRAY_SHA256` never runs.
- **Deployer-side HTTPS uses httpx** — urllib failed certificate checks on uv-managed Pythons.
- **Exit-IP observers match the server's address family**, or a dual-stack exit answers over the other one.
- **Failed traffic is classified by a direct TCP connect** (`connectivity.py`); UDP paths are never TCP-probed.
- **Whatever adds a firewall rule retires it.**
- **Readiness checks name their transient errors** (`poll_until_ready(transient=...)`); anything else propagates.
- **A missing PWA template raises** — never render a blank page.
