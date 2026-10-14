# src/meridian — Python CLI package
## Design decisions
**Cluster config** — one hidden Schema V3 `cluster.yml` holds topology intent, bindings, allocations, and checkpoints (`cluster.py` model, `cluster_persistence.py` YAML); users live in Remnawave.
**Panel access** — `remnawave/` speaks HTTPS from the deployer's machine to nginx's secret path on 443 with an API token; no SSH tunnel.
**SSH abstraction** — `ServerConnection` runs locally or over SSH (non-root adds `sudo -n`); `conn.run()` returns `CommandResult`, `ssh_transfer.py` moves files, and the `SSHUI` callbacks (`ssh_auth.py`) keep Rich out of transport.
**One key bootstrap** — `ssh_keys.py` owns Meridian's key pair, the `authorized_keys` install, and the key-login check for Engine and CLI alike.
**Server facts** — `facts.py` caches OS, kernel, Docker, UFW, containers, sshd ports, disk, and sysctl; use it before adding one-off probe parsing.
**Errors over exits** — library modules raise `MeridianError` subclasses (`core/errors.py`) carrying a `category`; only command entry points call `console.fail()`, whose `hint_type` picks the footer and exit code (tested). Every error must be actionable.
**Shared logic stays out of commands** — `resolve.py`, `panel_bootstrap.py`, `node_deploy.py`, and `reality_keys.py` serve commands and Engine; library code never imports `commands/`.
**Protocol keys** — `core/topology.py` owns the protocol vocabulary; `handoff.protocol_key()` classifies delivered links.
**Exit country** — `registry_country.py` reads the RIR country via RDAP (no geo-IP) when an exit is first named and stores it in `ExitIntent.region` (`--country` overrides), keeping the compiler pure.
**Local home** — `config.default_home()` prefers `$XDG_CONFIG_HOME/meridian` (`~/.config/meridian`), adopting an older 4.x home (`~/.meridian-v4`, `~/.meridian`) only if XDG is absent and it holds `servers.json`; `MERIDIAN_HOME` overrides.
**Workload secrets** — `reconciler/workloads.py` allocates or reuses complete Reality key triples and persists them before remote mutation; `xray_workload.py` renders only reviewed protocols and egress.
## What's done well
- **Forward-compatible YAML** — unknown keys land inertly in `_extra` (never store load-bearing state there); `load_topology_cluster()` refuses pre-topology files with rebuild guidance.
- **One owner per state** — Remnawave owns users, `cluster.yml` owns topology, and the control server's apply fence (`reconciler/fence.py`) stops a stale or foreign copy from applying.
- **Read-only fleet projection** — `adapters/cluster.py` is the only source of node/relay rows; it never projects routing gateways as exits or source-restricted relay hops as public endpoints.
## Pitfalls
- **Local state is fail-closed** — only missing `cluster.yml`/`servers.json` means fresh; malformed files need recovery, and `srv-*` IDs survive connection edits.
- **Never hard-code the home in messages** — print `config.HOME_DISPLAY`; a literal `~/.meridian` sent 4.x users into 3.x's directory.
- **Rendered inputs are typed** — validate and canonicalize hostname, SNI, port, and transport path before nginx, Xray, or Remnawave serialization.
- **Shared links never use the panel path** — build subscription URLs with `PanelConfig.subscription_url()` (connection-page path + `/sub/`); `SUB_PUBLIC_DOMAIN` matches it.
- **Local mode follows the saved profile** — `ServerEntry.runs_here()` matches the recorded `/etc/machine-id` or a bindable address, never the outbound IP (a laptop on the server's VPN shares it); open saved servers with `resolve.connection_for_entry()`.
- **Sudo must be passwordless and checked up front** — a failing `sudo -n true` is a user error with a `sudo "$(command -v meridian)"` or NOPASSWD hint, never an SSH key problem; so is an unwritable `known_hosts`.
- **SSH ControlPath must fit a Unix socket** — macOS allows 103 bytes and ssh adds 17; hash the target into the path and drop multiplexing when `MERIDIAN_HOME` is still too deep.
- **Camouflage targets** — never apple.com (ASN mismatch with VPS providers) or www.microsoft.com (certificate over REALITY's 8192-byte limit; no traffic passes).
- **Reality key material is atomic** — refuse partial private/public/short-ID state instead of rotating keys; `node replace` carries a workload's keys to the new server.
- **Hysteria2 always carries salamander** — the `finalmask` password derives from the workload's Reality private key (`salamander_password()`); never store or rotate it separately.
- **Unknown panel admin is a user error** — with no saved credentials and register and login both refused, stop with restore-or-`meridian teardown` guidance.
- **Local Xray is evidence, not a requirement** — validate delivered configs with `run -test` (malformed fails); a missing runtime or a bad SHA2-256 sidecar is inconclusive, and that archive never runs.
- **Exit-IP observers are single-stack** — match the server's address family, or a dual-stack exit answers over the other one and fails the egress match.
- **Failed traffic is classified by a direct TCP connect** — `connectivity.py` separates `no_traffic` from `unreachable`; UDP (Hysteria2) paths are never TCP-probed.
- **Whatever adds a firewall rule retires it** — node retirement deletes the Docker-internal node API rule (`fw-node-api`, plus its pre-rename comment).
- **Readiness checks name their transient errors** — pass `poll_until_ready(transient=...)`; anything else propagates instead of timing out silently.
- **A missing PWA template raises** — never upload a blank page.
