# control — Meridian's own control service (`meridian serve`)

## Design decisions
- **Replaces Remnawave, walking skeleton first** — one exit on the control server, users, signed node documents, and every subscription format; hidden behind `--control=meridian` until it reaches parity (#95).
- **Service owns users, deployer owns topology (for now)** — the reconciler's Meridian drivers PUT node specs, endpoints, and users over the admin API; the service composes each node's whole document (spec + one users resource per managed inbound, right after the `xray_config`), signs it, and serves it to the agent.
- **Fleet CA from the first start** — `NodeAuthority` (`enrollment.py`) creates an Ed25519 CA (`pki.py`; keys in the `secrets` table) and certifies the signing key with URI SAN `meridian-service://<fleet>`; `key_id` is that certificate's DER SHA-256. Documents signed under another certificate are signed again at start.
- **Enrollment and sessions follow the contract** — the admin API mints one single-use, 30-minute enrollment token per node; `enroll` trades it and a CSR for a 90-day certificate (SAN `meridian-agent://<fleet>/<node>`) and revokes the node's earlier certificates and sessions; `hello` checks chain, revocation, SAN, ±300 s, and nonce, then opens a one-hour session. Every token is stored only as SHA-256; the 443 handshake never asks for a client certificate.
- **Traffic counted exactly once** — `traffic.py` keeps each node's last (counter_epoch, seq, totals) and adds only growth; a resent seq is acked uncounted, an epoch the node left never counts again. `GET /api/traffic` joins totals to usernames; no client address is ever stored.
- **Scoped API tokens** — one revocable `/api` token per device or agent (`tokens.py`), scoped `read` < `propose` < `approve` < `admin`; each `/api` route in `server.ROUTES` names its scope (`WRITE` is `admin` until topology revisions bring `propose`/`approve`). The config's admin token is the root `admin`: unlisted, rotated only by rewriting the config.
- **Stdlib HTTP on loopback** — `ThreadingHTTPServer` behind nginx's secret path; a thread per waiting long-poll is cheap at Meridian's fleet sizes. nginx strips the secret path, so routes are `/api`, `/agent/v1`, `/sub`.
- **Operator-owned users** — the operator sets `active`/`disabled`, expiry, and limits over `PATCH /api/users`; enforcement (a 30 s timer plus every change) turns an active user `expired` or `limited` and back once a change, `reset-traffic`, or a new day/week/month (UTC, weeks from Monday) lifts it. Node documents carry only active users. Use is the traffic ledger's lifetime total minus the offset the last reset recorded.
- **Schema migrations** — `SCHEMA_VERSION` plus `_MIGRATIONS` (statements per version step); a database from a newer Meridian is refused, never downgraded.
- **One SQLite file** — `store.Store` shares one connection, lock, and change condition between `ControlService`, `NodeAuthority`, `TrafficLedger`, and `TokenRegistry`, each owning its tables; WAL mode, and the condition wakes polls on a new revision.
- **Subscriptions follow the golden corpus** — `formats/` renders Base64, Xray JSON (a list: "Automatic", then fingerprint copies' own config), Mihomo, Stash, sing-box, and a browser's status JSON by filling `compiler/delivery.py`'s templates; `formats/selector.py` picks by suffix, Accept, then User-Agent. `tests/test_control_subscription_corpus.py` diffs every corpus request, each deliberate fix a named row (plain Clash/Stash get Meridian's Mihomo, Hiddify sing-box, an empty User-Agent Base64; no panel security headers). Hysteria2 auth is the VLESS UUID, as Remnawave delivered it.
- **Renderer takes plain values** — `SubscriptionUser` (UUID, short ID, status `active|disabled|limited|expired`, expiry, traffic) and `ClientEndpoint`s; a suspended user gets Remnawave's status placeholder entries in every format. `ServiceSettings.public_url` feeds `support-url`/`profile-web-page-url`; `ClientEndpoint.fingerprint_copy` marks copies.

## What's done well
- A revision is signed only when the resources' hash (or the signing certificate) changes, so repeated applies never wake agents.
- Agent errors use the protocol's `AgentErrorResponse` with the status `AGENT_HTTP_SURFACE` maps (`errors.agent_error`); admin errors a plain `{"error": {...}}`.
- An API token's public ID (`mrd_<id>_<secret>`) finds its row, so the hash is compared once with `hmac.compare_digest`, against a dummy when no row matches.
- A refused CSR rolls back with its transaction, so a bad request never burns the enrollment token.

## Pitfalls
- `serve` is a daemon: the root callback skips the beta banner and the update check for it (it once printed the banner into journald and phoned out on every start).
- Decode the path before routing — `ControlClient` percent-encodes IDs, so `:` in endpoint IDs arrives as `%3A` and every endpoint PUT 404'd.
- Compare timestamps parsed, never as strings — the contract allows fractional seconds, and `…:00.5Z` sorts before `…:00Z`.
- Never log requests or store where they came from: paths carry the secret path and subscription tokens (`log_message` is silenced); a token keeps only a once-a-minute `last_used_at`.
- `executescript()` commits on its own — create tables with `Store.create()`, never inside `transaction()`.
- Hash and sign the document as JSON values, never re-serialized typed models (the agent verifies the bytes it received); verify a `NodeProof` over the hello dict as received, too.
- Record a hello nonce only after the proof verifies, and keep it until `issued_at` + 300 s + margin, or a captured hello replays.
- Traffic baselines outlive node deletion — a re-added node whose agent kept its counter store would otherwise count its totals twice.
- A poll answers before its session expires, so the agent's next request is a fresh `hello` rather than a 401.
- `control/` never imports the deployer (CLI, SSH, panel, provision, reconciler); architecture-tested.
- Never compress or send Referrer-Policy on `/sub/` — nginx gzips JSON/Base64 (YAML stays plain, as `gzip_types` has none) and adds its own Referrer-Policy; the panel's duplicate was a corpus defect.
- A fingerprint copy without `fingerprint_copy` lands in the primary balancer and failover groups, mixing fingerprints toward one server (TSPU escalates); sing-box omits copies entirely.
- Stash spells fields its own way (`sni`, Hysteria2 `auth`) and has no `exclude-filter` or `GEOIP,private`; Remnawave's Stash and Clash answers had no failover at all.
