# agent — Rust node agent (`netagent`), walking skeleton
## Design decisions
**Contract first** — `src/meridian/core/agent_*.py` and `contracts/meridian/v1/agent/` are the spec; `model.rs` mirrors only the bodies the skeleton speaks (`poll`, `report`, `blobs/{sha256}`) with `deny_unknown_fields` on what it reads, and always sends every field.
**Skeleton deviations** — no enroll/hello/renew, stats, or commands (ignored, logged once at debug): every request carries the bootstrap `node_token` as a bearer, and `key_id` is the SHA-256 of the raw service public key pinned in `/etc/netagent/bootstrap.json` (`NodeBootstrap`).
**Verify the wire, then type it** — `verify.rs` checks floats, signature, `fleet_id`/`node_id`, and `sha256` over the parsed `Value`; only then do resources become typed `Spec`s. A supported kind that fails to parse is `failed`/`validation_failed`; any other kind is `failed`/`unsupported` and blocks its dependents.
**Canonical JSON is Python's** — `jcs.rs` is `serde_json::to_vec` of a sorted map (no `preserve_order`, anywhere in the tree); cross-checked byte for byte by `tests/fixtures/vectors.json` (regenerate with `uv run python agent/tests/fixtures/make_fixtures.py`).
**State is fail-closed** — `state.json` (0600) holds the epoch floor, the held document as received, the applied ref, and a ledger of applied resources in document order; a malformed file stops the agent. A held document that no longer verifies (new service key) is dropped, never the floor or ledger.
**Apply engine** — `apply.rs` retires vanished ledger entries dependents-first (skipping paths/units the new document claims), then applies front to back; every pass re-checks applied resources and repairs drift. Passes run on start, on each new document, and every 300 s; reports go after each apply and every 60 s. `target` is always the held ref, so `resources` is never empty.
**Users live in the config file** — no Xray gRPC yet: `compose.rs` fills `settings.clients` of the inbounds `xray_users`/`hysteria2_users` name, the agent validates with `<xray> run -test` (`XRAY_LOCATION_ASSET` = geosite's directory) and renames into place (0600, root). Changed composed bytes mark `xray_config` and each users resource whose hash changed, so `restart_on` restarts the unit.
**Host behind traits** — `system::Exec` (systemctl, journalctl, xray) and `fetch::Fetch` keep the engine testable; `--root` joins every desired path onto a temp dir. Writes stay under `/etc/netagent/` and `/usr/local/lib/netagent/`; units must be `net[a-z0-9-]*.service`, never `netagent.service`.
**Static musl builds** — `make agent` runs `scripts/build-musl.sh` in the digest-pinned `rust:alpine` image on the host's own architecture and cross-compiles the other target with zig cc + rust-lld (no emulation); `agent/dist/` holds binaries and bare-hex `.sha256` files.
## What's done well
- **Secrets stay out of logs and reports** — no token, service URL (`Client::scrub`), user UUID, or sensitive content is logged; failure messages are trimmed and journal tails capped at 300 chars.
- **One download per artifact per pass** — pins sharing an archive reuse the verified cache file in `<state>/cache`, removed after the pass.
- **Atomic writes everywhere** — private temp sibling, fsync, mode/owner, rename, directory fsync (`fsutil::write_atomic`).
## Pitfalls
- **ureq's body limit errors at exactly the limit** — reading a body whose length equals `limit` fails; downloads pass `size + 1` and `HashingWriter` enforces the exact size.
- **Never send the node token to a `sources` URL** — only `service` goes to `blobs/{sha256}` with the bearer; https upstreams get none (ureq drops auth on redirects).
- **Option fields deserialize when missing** — serde treats an absent `Option` as `null`; the service always sends every field, so don't rely on the agent to catch omissions.
- **`run -test` needs a `.json` name** — the temp config is `.<name>.netagent-test.json` so Xray picks the JSON loader.
- **Xray must run as root** — the composed config holds Reality keys and user UUIDs at 0600; a unit with `User=`/`DynamicUser=` cannot read it until `xray_config` grows an owner.
- **Health check names are sanitized resource IDs** — `:` becomes `-` and a leading digit gets `u`, to fit `^[a-z][a-z0-9_.-]*$`.
- **Colima has no amd64 emulation** — `docker run --platform linux/amd64` fails with `exec format error`; that is why the build cross-compiles.
