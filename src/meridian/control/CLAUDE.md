# control — Meridian's own control service (`meridian serve`)

## Design decisions
- **Replaces Remnawave, walking skeleton first** — one exit on the control server, users, signed node documents, and Base64/Xray JSON subscriptions; hidden behind `--control=meridian` until it reaches parity (#95).
- **Service owns users, deployer owns topology (for now)** — the reconciler's Meridian drivers PUT node specs, endpoints, and users over the admin API; the service composes each node's whole document (spec + one users resource per managed inbound, right after the `xray_config`), signs it, and serves it to the agent.
- **Signed from day one** — every document carries an Ed25519 signature (`core/agent_signing.py`); SSH bootstrap pins the raw public key from `signing.pub`, so TLS interception can't inject state. `key_id` is the key's SHA-256 until the M2 internal CA.
- **Skeleton auth** — admin and node tokens are bearer tokens stored only as SHA-256; minting a node token revokes the previous one. Enroll/hello with CSRs replaces node tokens in M2.
- **Stdlib HTTP on loopback** — `ThreadingHTTPServer` behind nginx's secret path; a thread per waiting long-poll is cheap at Meridian's fleet sizes. nginx strips the secret path, so routes are `/api`, `/agent/v1`, `/sub`.
- **One SQLite file** — fleet identity, signing key (`secrets` table), nodes, users, endpoints; WAL mode, one process-wide lock, a condition variable wakes polls on a new revision.
- **Subscriptions follow the golden corpus** — URI order, remarks, and Xray outbound shapes match `tests/systemlab/golden/`; Hysteria2 auth is the VLESS UUID, as Remnawave delivered it.

## What's done well
- A revision is signed only when the resources' hash changes, so repeated applies never wake agents.
- Agent errors use the protocol's `AgentErrorResponse`; admin errors a plain `{"error": {...}}`.

## Pitfalls
- Decode the path before routing — `ControlClient` percent-encodes IDs, so `:` in endpoint IDs arrives as `%3A` and every endpoint PUT 404'd.
- Never log requests: paths carry the secret path and subscription tokens (`log_message` is silenced).
- `executescript()` commits on its own — run the schema outside `_transaction()`.
- Hash and sign the document as JSON values, never re-serialized typed models (the agent verifies the bytes it received).
- `control/` never imports the deployer (CLI, SSH, panel, provision, reconciler); architecture-tested.
