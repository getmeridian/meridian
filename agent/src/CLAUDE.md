# agent/src — netagent runtime: identity, apply, Xray, traffic, commands
## Design decisions
**Identity under the fleet CA** — `identity.rs` generates the Ed25519 node key on the node and sends only a CSR (no OpenSSL); `identity.json` (0600) keeps key and certificates together. The service certificate must chain to the CA pinned by `ca_sha256`, be a non-CA, and name the fleet.
**Sessions** — `session.rs` signs a fresh `NodeProof` per hello and keeps times on the service's clock; a 401 re-hellos once, `credential_revoked` stops until a new bootstrap token appears, and `unsupported_protocol` backs off for the service's retry time. Only WebPKI TLS is verified: a pin would break at every renewal of 443's short-lived IP certificate, and the CA-pinned service certificate already authenticates every document. `NETAGENT_TLS_CA_FILE` may name one private CA (the system lab's Pebble).
**Verify the wire, then type it** — `verify.rs` checks floats, signature, fleet and node IDs, and the hash over the parsed `Value`; only then do resources become typed `Spec`s. Nothing applies before credentials exist.
**State is fail-closed** — `run` holds a flock on `run.lock` for its lifetime (never unlink it); `state.json` (0600) holds the epoch floor, held document, and resource ledger, and malformed state or identity stops the agent.
**Apply engine** — `apply.rs` retires vanished ledger entries dependents-first, then applies in order and repairs drift, on start, per new document, and every 300 s.
**Users without restarts** — `xray.rs` composes users into the config and checks it with `run -test`; when only users changed and the unit is active, VLESS users apply live through `xray api`, and generation-mode Hysteria2 additions use its typed account API; legacy configurations rebuild that inbound. Anything else restarts.
**Traffic counted once** — `stats.rs` folds `statsquery -reset` into `traffic.json` (0600) and saves each new sequence number before sending; users who left keep their totals in `retired`.
**Host behind traits** — `Exec`/`Fetch` fake host I/O and `--root` redirects paths for tests; writes stay under `/etc/netagent/` and `/usr/local/lib/netagent/`, and units match `net[a-z0-9-]*.service`, never `netagent.service`.
**Reports stay apart** — `agent/reporting.rs` builds and sends observations; the polling loop owns their cadence.
**Revoked identities gate new streams** — block only old credential-derived emails before reviewed routing; core.json contains no revocations, while bounded retry intent follows one systemd InvocationID and clears on restart.
## What's done well
- **Signed commands run only through negotiated kinds** — `commands.rs` verifies signature, epoch, and expiry first; `command_host.rs` restarts only held, installed units and resets only managed inbounds.
- **Durable command results** — `commands.json` (0600, bounded) saves an interrupted outcome before execution and the result before sending, so a redelivery replays the result, never the action.
- **Secrets stay out of logs and reports** — no token, key, service URL (`Client::scrub`), user UUID, or sensitive content; messages are trimmed.
- **Atomic writes everywhere** — private temp sibling, fsync, mode and owner, rename, directory fsync (`fsutil::write_atomic`).
## Pitfalls
- **Every core change uses held credentials** — commands check the applied document and exact core.json and use exported refusal codes; live updates publish the new file before accounts, compare invocations, and reconcile an uncertain instance.
- **Reports inspect the running guard** — cached apply errors cannot prove current blocking; compare live rules and the core invocation without mutating them.
- **IPIfNonMatch needs an unmatched first pass** — an active-user catch-all suppresses DNS and bypasses private/country blocks and IP egress; install only revoked-identity blocks.
- **Guard retries preserve earlier blocks** — save intent before removing authentication, clear blocks for active identities before adding accounts, and retry the complete graph after a failed replacement.
- **Removing a password leaves authenticated QUIC and mux sessions usable** — block new dispatched streams before `rmu`; existing TCP/UDP associations survive. VLESS's accepted flows also outlive an inbound reset, so its explicit cut-off must restart the core; Hysteria2's reset closes QUIC.
- **Expiry gates only the start of a command** — keep results and executed IDs beyond the service's redelivery window, or clock rounding repeats an action.
- **Serde unit variants ignore extra fields even with `deny_unknown_fields`** — use empty struct variants so signed extras are rejected.
- **Re-check in Rust whatever reaches a path, an argument, or a socket** — tags, user IDs, the API address, and reserved file names, though Python checked them; serde reads an absent `Option` as `null`.
- **Never send the session token to a `sources` URL** — only the service's own blob route gets the bearer. ureq errors at exactly the body limit, so downloads pass `size + 1` and `HashingWriter` enforces the size.
- **`xray api adu`/`rmu` exit 0 when a user fails** — check the `Added/Removed N user(s)` line; their input files hold credentials (0600, deleted at once).
- **`adu` cannot build Hysteria2 users** — Xray's CLI has no hysteria case and a VLESS-shaped account panics its validator, so Hysteria2 rebuilds the inbound; a failed `adi` leaves it gone until the fallback restart.
- **A returning user resumes their retired totals** — the service keeps every baseline, so restarting at zero read as a counter regression and refused the whole report.
- **Fold traffic before dialing the service** — an outage must not leave counters in Xray to die on restart; track collection apart from delivery, and collect before an apply's report.
- **`statsquery` is protobuf JSON** — int64 values arrive as strings, and zeros and empty lists are omitted.
- **A restart is owed until it succeeds** — content on disk the ledger does not record counts as changed, or a crash before the save leaves Xray on its old config.
- **`run -test` and `is-active` are not readiness** — after a core restart, probe `statsquery` with a bound and restore the `.netagent-prev` artifacts on failure.
- **A retirement counts only once nothing runs** — `stop`'s exit code says little; check `is-active` before deleting.
- **Save a document before holding or applying it**, and save an advanced epoch floor before a command's host actions — otherwise a restart can bring an older document and revoked users back.
- **Save credentials before nulling the enrollment token**, so a crash in between falls back to them.
- **`run -test` needs a `.json` file name**, and Xray must run as root, because the composed 0600 config holds Reality keys and UUIDs.
- **Check service text is ASCII before slicing by byte** — release builds abort on panic.
- **One owner waits, kills, and reaps a child** (`system.rs`) — a separate waiter could reap first, and the timeout's kill would hit a reused PID as root.
