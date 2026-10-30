# agent — Rust node agent (`netagent`)

Runtime rules (identity, sessions, apply, Xray, traffic, signed commands, host I/O) live in `src/CLAUDE.md`; this file covers the contract, builds, upgrades, and tests.

## Design decisions
**Contract first** — `src/meridian/core/agent_*.py` and `contracts/meridian/v1/agent/` are the spec; `model.rs` mirrors the bodies the agent speaks with `deny_unknown_fields` on what it reads, and always sends every field.
**Canonical JSON is Python's** — `jcs.rs` is `serde_json::to_vec` of a sorted map (no `preserve_order`, anywhere in the tree); `tests/fixtures/vectors.json` cross-checks bytes, signatures, certificates, and hello proofs (`uv run python agent/tests/fixtures/make_fixtures.py`).
**Static musl builds** — `make agent` builds in the digest-pinned `rust:alpine` image for the host's architecture and cross-compiles the other with zig cc + rust-lld; `agent/dist/` holds binaries with `.sha256` and `.version` files, which every wheel carries as `meridian/agent_dist/` (`control_artifacts` checks the digest).
**Upgrades keep the identity** — the deployer stages a build at `netagent.new` and runs its `netagent check` (loads bootstrap, credentials, state, and traffic store and verifies the held document; writes and dials nothing; prints `version`, `node_id`, `enrolled`), keeps the old binary as `netagent.prev`, renames, restarts, and restores it unless the new build's version reaches the service; `build.rs` makes that version the crate version plus `make agent`'s source hash, so each build names itself.
**Self-upgrade is designed, not built** — the contract's `agent_upgrade` resource (sources with `service` blobs, sha256, `health_timeout_seconds`) would let the service roll agents out; rollback must live outside the new binary: before swapping, the old build starts a transient `systemd-run --on-active=<timeout>` unit that restores `netagent.prev`, which the new build cancels after its first hello. Until then `agent_upgrade` stays unoffered and upgrades go over SSH (`reconciler/agent_upgrade.py`).

## What's done well
- **Both sides of the wire are tested** — `tests/roundtrip.rs` drives a fake service with a Rust test CA; `tests/service.rs` runs the real Python `ControlServer` (`fixtures/control_server.py`, using `MERIDIAN_TEST_PYTHON` or `uv`), installing its pin from the service's blob store.

## Pitfalls
- **Python round-trip fixtures load their own checkout's `src` before imports** — a shared interpreter can otherwise test another worktree's editable install.
- **Bump the crate version with every agent change that ships** — `make agent` writes it beside each binary as `.version`, and `meridian check` warns about nodes reporting another.
- **Colima has no amd64 emulation** — `--platform linux/amd64` fails with `exec format error`; hence the cross-compile.
