# src/meridian — Python CLI package
## Design decisions
**Protocol registry** — `protocols.py` keeps ordered TCP transports in `PROTOCOLS` and the UDP fallback in `ADDITIONAL_PROTOCOLS`; `get_protocol()` spans both. `ProtocolKey(StrEnum)` provides stable cluster keys.

**Cluster config** — Single `cluster.yml` at `~/.meridian/cluster.yml` replaces per-server `proxy.yml` files. Client/user state lives in Remnawave's PostgreSQL, not locally. Only deployment topology (panel URL, API token, nodes, relays) is stored locally.

**Remnawave integration** — `remnawave.py` wraps the REST API with `httpx`. Direct HTTPS calls from deployer's machine (no SSH tunneling for API). JWT auth, retry with backoff, Meridian-specific error types.

**SSH abstraction** — `ServerConnection` unifies local and remote execution. Local mode uses `bash -c`; remote uses SSH. Non-root triggers `sudo -n`. The `SSHUI` callback protocol in `ssh_auth.py` decouples transport from presentation; CLI callers pass `RichSSHUI` from `ssh_ui.py`, Engine/headless callers get logger-only output by default. File transfer methods (`put_bytes`, `put_text`, `get_text`, `get_bytes`) live in `_FileTransferMixin` in `ssh_transfer.py`; `ServerConnection` inherits from it.

**Remote execution primitives** — `conn.run()` returns `CommandResult` metadata and supports `cwd`, `env`, retries, ok codes, sensitive commands, and operation labels. File writes use `put_text`/`put_bytes`; never embed generated file content in shell heredocs. `CommandResult` and `RemoteCommandResult` have explicit `to_remote()`/`from_remote()` conversion methods. Adapters delegate to these methods, not dict unpacking.

**Server facts** — `facts.py` is the typed cache for OS, Docker, UFW, containers, sshd ports, disk, and sysctl data. Use it before adding one-off probe parsing in provisioners or diagnostics.

**Console output** — `fail()` with `hint_type` (user/system/bug) controls the footer. Every error must be actionable. `ConsoleState` class wraps globals; module-level functions delegate to singleton instance.

**Engine boundary** — `meridian.engine` owns command-free runtime use cases only when a local executable surface needs them. Static Studio does not need Engine; executable Studio will need it for SSH, files, secrets, events, and cancellation.

**Pinned version tuple** — `config.py` pins Remnawave images/SDK plus external binaries. Move the tuple together and update the CHANGELOG compatibility matrix; mismatched Remnawave backend/node/SDK versions silently lose data.

**Errors over exits** — library modules raise typed exceptions (`MeridianError` hierarchy in `core/errors.py`) and expose their classification as `category`; `hint_type` belongs only to `console.fail()`. Only CLI command entry points call `console.fail()`.

## What's done well
- **Forward-compatible YAML** — `_extra` dict in ClusterConfig preserves unknown YAML keys for forward-compat only. Reconciler state lives in typed `applied_state`; v4.0 top-level `desired_*_applied` keys migrate into it on load. Never store load-bearing runtime state in `_extra`.
- **Single source of state** — No split-brain. Remnawave DB is authoritative for users. cluster.yml is authoritative for deployment topology. No sync needed.
- **Relay = Host** — Relays map to Remnawave Host entries. Enable/disable host → subscriptions auto-adapt.
- **Extracted shared logic** — `panel_bootstrap.py` owns panel setup orchestration (first deploy, redeploy, new node workflows). `node_deploy.py` owns node container deployment, host creation, panel API helpers, and inbound caching. `relay_ops.py` owns relay infrastructure. `resolve.py` owns `ResolvedServer`, `ensure_server_connection`, and pure resolution helpers; `commands/resolve.py` adds CLI prompts and rendering. Library modules import from `meridian.resolve`, never from `commands/`. Applied-state snapshots and hybrid imperative-declarative sync live in `reconciler/snapshots.py`. `cluster_persistence.py` owns YAML serialization/deserialization; `cluster.py` keeps the data model, validation, and query methods. `diagnostics/` owns reusable server health checks (disk, container, port, TLS, firewall) returning `CheckResult`; commands own rendering.
- **Architecture tests** — `tests/test_architecture.py` enforces layer boundaries, file size budget, private import bans, commands/resolve import ban for library modules, and contract drift checks at CI time.

## Pitfalls
- **Local state is fail-closed** — only missing `cluster.yml`/`servers.json` means fresh; malformed files require recovery, and generated `srv-*` IDs survive connection edits.
- **Rendered inputs are typed** — validate and canonicalize hostname, SNI, port, and transport path values before nginx, Xray, or Remnawave serialization.
- **Published Hosts are assertions** — reconcile complete protocol fields against public listeners; observation or mutation failure must stop apply.
- **Shell injection**: ALL `conn.run()` interpolated values MUST use `shlex.quote()`.
- **ProtocolKey is StrEnum** — works as dict key but YAML serialization needs `_stringify_keys()` to avoid Python-tagged output.
- **Panel accessible via HTTPS** — Remnawave backend is reverse-proxied by nginx at a secret path on public 443; all REST goes from the deployer's machine directly, no SSH tunnel.
- **Local mode**: detection is file-based only — `/etc/meridian/node.yml` or dir existence.
- **Camouflage target**: never recommend apple.com (ASN mismatch with VPS providers).
- **Do not call `console.fail()` from library modules** (operations, relay_ops, resolve, xray_config, provision/, panel_bootstrap). Raise a `MeridianError` subclass instead. Enforced by `test_library_modules_do_not_import_console_fail`.
- **PQ encryption removed in v4** — Xray VLESS PQ requires per-user encryption fields on inbound clients plus Remnawave API support. Do not re-add the flag without both.
- **Reality key material is atomic** — redeploy must refuse partial private/public/short-ID state rather than rotate keys and break clients.
- **Hysteria2 naming boundary** — external keys and URLs use `hysteria2`; Remnawave profiles require protocol/network `hysteria` plus version `2`.
