# agent — Rust node agent (`netagent`)
## Design decisions
**Contract first** — `src/meridian/core/agent_*.py` and `contracts/meridian/v1/agent/` are the spec; `model.rs` mirrors the bodies the agent speaks with `deny_unknown_fields` on what it reads, and always sends every field. Commands are not run yet (logged once at debug).
**Identity under the fleet CA** — `identity.rs` generates the Ed25519 node key on the node and sends only a CSR (x509-cert + ed25519-dalek, no OpenSSL); `identity.json` (0600) keeps key, node certificate, CA, and service certificate together, and enrolling nulls the bootstrap's token. The service certificate must chain to the CA pinned by `ca_sha256`, be a non-CA, and name `meridian-service://<fleet>`; its DER SHA-256 is the `key_id`.
**Sessions** — `session.rs` signs a `NodeProof` per hello (fresh nonce), keeping session times on the agent clock shifted by `server_time`. A session 401 re-hellos once; `renew_after` certifies a fresh key; `credential_revoked` stops the agent until a new token appears in the bootstrap (re-read without a restart); `unsupported_protocol` silences hellos for the service's `retry_after_seconds`, at least 300 s, keeping credentials. `tls_pin_sha256` is refused: only WebPKI TLS is verified.
**Verify the wire, then type it** — `verify.rs` checks floats, signature, `fleet_id`/`node_id`, and `sha256` over the parsed `Value`; only then do resources become typed `Spec`s. An unparsable supported kind is `validation_failed`, any other kind `unsupported`, blocking dependents. Nothing applies before credentials exist.
**Canonical JSON is Python's** — `jcs.rs` is `serde_json::to_vec` of a sorted map (no `preserve_order`, anywhere in the tree); `tests/fixtures/vectors.json` cross-checks bytes, signatures, certificates, and hello proofs (`uv run python agent/tests/fixtures/make_fixtures.py`).
**State is fail-closed** — `state.json` (0600) holds the epoch floor, the held document as received, the applied ref, and the applied-resource ledger; a malformed state or identity file stops the agent. A held document that no longer verifies (new service certificate) is dropped, never the floor or ledger.
**Apply engine** — `apply.rs` retires vanished ledger entries dependents-first (skipping paths/units the new document claims), then applies front to back and repairs drift; passes run on start, per new document, and every 300 s.
**Users without restarts** — `xray.rs` composes users into the config, checks it with `<xray> run -test`, and always writes it (0600). If only VLESS users changed, the file is the previous composition, and the unit is active, `xray api rmu`/`adu` apply them live; Hysteria2 users (adu cannot add them), config changes, or any API shortfall restart via `restart_on`.
**Traffic counted once** — `stats.rs` folds `api statsquery -reset` into `traffic.json` (0600: counter epoch, seq, totals); every send takes a new seq saved first, and users gone from the document are forgotten once the latest report is acked.
**Host behind traits** — `system::Exec` and `fetch::Fetch` keep the engine testable; `--root` joins every desired path onto a temp dir. Writes stay under `/etc/netagent/` and `/usr/local/lib/netagent/`; units must be `net[a-z0-9-]*.service`, never `netagent.service`.
**Static musl builds** — `make agent` builds in the digest-pinned `rust:alpine` image for the host's architecture and cross-compiles the other with zig cc + rust-lld; `agent/dist/` holds binaries and `.sha256` files.
## What's done well
- **Secrets stay out of logs and reports** — no token, key, service URL (`Client::scrub`), user UUID, or sensitive content is logged; messages are trimmed to 300 chars.
- **Both sides of the wire are tested** — `tests/roundtrip.rs` drives a fake service with a Rust test CA; `tests/service.rs` runs the real Python `ControlServer` (`fixtures/control_server.py`, skipped without `uv`).
- **Atomic writes everywhere** — private temp sibling, fsync, mode/owner, rename, directory fsync (`fsutil::write_atomic`).
## Pitfalls
- **ureq's body limit errors at exactly the limit** — downloads pass `size + 1` and `HashingWriter` enforces the exact size.
- **Never send the session token to a `sources` URL** — only `service` goes to `blobs/{sha256}` with the bearer.
- **Option fields deserialize when missing** — serde reads an absent `Option` as `null`; don't rely on the agent to catch omissions.
- **`xray api adu`/`rmu` exit 0 when a user fails** — check the `Added/Removed N user(s)` line; the adu file holds UUIDs (0600, deleted at once).
- **`statsquery` is protobuf JSON** — int64 values arrive as strings; zeros and empty lists are omitted.
- **Save credentials before nulling the token** — after a crash in between, `enrollment_token_invalid` falls back to the stored credentials.
- **`run -test` needs a `.json` name** — the temp config is `.<name>.netagent-test.json`.
- **Xray must run as root** — the composed config holds Reality keys and UUIDs at 0600; `User=`/`DynamicUser=` units cannot read it.
- **Health check names are sanitized resource IDs** — `:` becomes `-` and a leading digit gets `u`.
- **Bump the crate version with every agent change that ships** — `make agent` writes it beside each binary as `.version`, and `meridian check` warns about nodes reporting another.
- **Colima has no amd64 emulation** — `--platform linux/amd64` fails with `exec format error`; hence the cross-compile.
