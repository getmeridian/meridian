# control — Meridian's own control service (`meridian serve`)

## Design decisions
- **Replaces Remnawave, walking skeleton first** — one exit on the control server, users, signed node documents, and Base64/Xray JSON subscriptions; hidden behind `--control=meridian` until it reaches parity (#95).
- **Service owns users, deployer owns topology (for now)** — the reconciler's Meridian drivers PUT node specs, endpoints, and users over the admin API; the service composes each node's whole document (spec + one users resource per managed inbound, right after the `xray_config`), signs it, and serves it to the agent.
- **Fleet CA from the first start** — `NodeAuthority` (`enrollment.py`) creates an Ed25519 CA (`pki.py`; keys in the `secrets` table) and certifies the signing key with URI SAN `meridian-service://<fleet>`; `key_id` is that certificate's DER SHA-256. Documents signed under another certificate are signed again at start.
- **Enrollment and sessions follow the contract** — the admin API mints one single-use, 30-minute enrollment token per node; `enroll` trades it and a CSR for a 90-day certificate (SAN `meridian-agent://<fleet>/<node>`) and revokes the node's earlier certificates and sessions; `hello` checks chain, revocation, SAN, ±300 s, and nonce, then opens a one-hour session. Every token is stored only as SHA-256; the 443 handshake never asks for a client certificate.
- **Traffic counted exactly once** — `traffic.py` keeps each node's last (counter_epoch, seq, totals) and adds only growth; a resent seq is acked uncounted, an epoch the node left never counts again. `GET /api/traffic` joins totals to usernames; no client address is ever stored.
- **Stdlib HTTP on loopback** — `ThreadingHTTPServer` behind nginx's secret path; a thread per waiting long-poll is cheap at Meridian's fleet sizes. nginx strips the secret path, so routes are `/api`, `/agent/v1`, `/sub`.
- **One SQLite file** — `store.Store` shares one connection, lock, and change condition between `ControlService`, `NodeAuthority`, and `TrafficLedger`, each owning its tables; WAL mode, and the condition wakes polls on a new revision.
- **Subscriptions follow the golden corpus** — URI order, remarks, and Xray outbound shapes match `tests/systemlab/golden/`; Hysteria2 auth is the VLESS UUID, as Remnawave delivered it.

## What's done well
- A revision is signed only when the resources' hash (or the signing certificate) changes, so repeated applies never wake agents.
- Agent errors use the protocol's `AgentErrorResponse` with the status `AGENT_HTTP_SURFACE` maps (`errors.agent_error`); admin errors a plain `{"error": {...}}`.
- A refused CSR rolls back with its transaction, so a bad request never burns the enrollment token.

## Pitfalls
- Decode the path before routing — `ControlClient` percent-encodes IDs, so `:` in endpoint IDs arrives as `%3A` and every endpoint PUT 404'd.
- Never log requests: paths carry the secret path and subscription tokens (`log_message` is silenced).
- `executescript()` commits on its own — create tables with `Store.create()`, never inside `transaction()`.
- Hash and sign the document as JSON values, never re-serialized typed models (the agent verifies the bytes it received); verify a `NodeProof` over the hello dict as received, too.
- Record a hello nonce only after the proof verifies, and keep it until `issued_at` + 300 s + margin, or a captured hello replays.
- Traffic baselines outlive node deletion — a re-added node whose agent kept its counter store would otherwise count its totals twice.
- A poll answers before its session expires, so the agent's next request is a fresh `hello` rather than a 401.
- `control/` never imports the deployer (CLI, SSH, panel, provision, reconciler); architecture-tested.
