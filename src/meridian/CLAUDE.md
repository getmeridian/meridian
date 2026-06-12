# src/meridian — Python CLI package

## Design decisions

**Protocol registry** — `protocols.py` defines `PROTOCOLS` as the sole source of truth. `ProtocolKey(StrEnum)` in `cluster.py` provides type-safe keys. All URL building, rendering, and provisioning loop over this registry.

**Cluster config** — Single `cluster.yml` at `~/.meridian/cluster.yml` replaces per-server `proxy.yml` files. Client/user state lives in Remnawave's PostgreSQL, not locally. Only deployment topology (panel URL, API token, nodes, relays) is stored locally.

**Remnawave integration** — `remnawave.py` wraps the REST API with `httpx`. Direct HTTPS calls from deployer's machine (no SSH tunneling for API). JWT auth, retry with backoff, Meridian-specific error types.

**SSH abstraction** — `ServerConnection` unifies local and remote execution. Local mode uses `bash -c`; remote uses SSH. Non-root triggers `sudo -n`. The `SSHUI` callback protocol in `ssh.py` decouples transport from presentation; CLI callers pass `RichSSHUI` from `ssh_ui.py`, Engine/headless callers get logger-only output by default.

**Remote execution primitives** — `conn.run()` returns `CommandResult` metadata and supports `cwd`, `env`, retries, ok codes, sensitive commands, and operation labels. File writes use `put_text`/`put_bytes`; never embed generated file content in shell heredocs.

**Server facts** — `facts.py` is the typed cache for OS, Docker, UFW, containers, sshd ports, disk, and sysctl data. Use it before adding one-off probe parsing in provisioners or diagnostics.

**Console output** — `fail()` with `hint_type` (user/system/bug) controls the footer. Every error must be actionable.

**Engine boundary** — `meridian.engine` owns command-free runtime use cases only when a local executable surface needs them. Static Studio does not need Engine; executable Studio will need it for SSH, files, secrets, events, and cancellation.

**Pinned version tuple** — `config.py` pins Remnawave images/SDK plus external binaries. Move the tuple together and update the CHANGELOG compatibility matrix; mismatched Remnawave backend/node/SDK versions silently lose data.

## What's done well

- **Forward-compatible YAML** — `_extra` dict in ClusterConfig preserves unknown YAML keys for forward-compat only. Reconciler state lives in the typed `applied_state: AppliedState` field; subscription page deployment status in `subscription_page.deployed`. Never store load-bearing runtime state in `_extra`.
- **Single source of state** — No split-brain. Remnawave DB is authoritative for users. cluster.yml is authoritative for deployment topology. No sync needed.
- **Relay = Host** — Relays map to Remnawave Host entries. Enable/disable host → subscriptions auto-adapt.
- **Extracted shared logic** — `panel_bootstrap.py` owns panel setup and node deploy (was setup.py). `relay_ops.py` owns relay infrastructure (was commands/relay.py). `resolve.py` owns `ResolvedServer`, `ensure_server_connection`, and pure resolution helpers; `commands/resolve.py` re-exports them and adds CLI-specific logic (prompts, Rich output). Library modules import from `meridian.resolve`, never from `commands/`. Applied-state snapshots and hybrid imperative-declarative sync live in `reconciler/snapshots.py`; `operations.py` re-exports for backward compat but new code should import from `reconciler.snapshots` directly.
- **Architecture tests** — `tests/test_architecture.py` enforces layer boundaries, file size budget, private import bans, commands/resolve import ban for library modules, and contract drift checks at CI time.

## Pitfalls

- **Shell injection**: ALL `conn.run()` interpolated values MUST use `shlex.quote()`.
- **ProtocolKey is StrEnum** — works as dict key but YAML serialization needs `_stringify_keys()` to avoid Python-tagged output.
- **Panel accessible via HTTPS** — Remnawave backend is reverse-proxied by nginx at a secret path on public 443; all REST goes from the deployer's machine directly, no SSH tunnel.
- **Local mode**: detection is file-based only — `/etc/meridian/node.yml` or dir existence.
- **Camouflage target**: never recommend apple.com (ASN mismatch with VPS providers).
