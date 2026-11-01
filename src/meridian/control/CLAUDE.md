# control — Meridian's own control service (`meridian serve`)

Owner sign-in and console screens: `ui/CLAUDE.md`. Subscription documents: `formats/CLAUDE.md`.

## Design decisions
- **Service owns users, deployer owns topology** — the reconciler PUTs node specs, endpoints, and users over the admin API; the service composes each node's whole document (`node_documents.py`), signs it, and serves it to that node's agent. Client-IP forwarding is not ported yet, so `--client-ips` is hidden.
- **Stdlib HTTP on loopback** — `ThreadingHTTPServer` behind nginx's secret path (stripped, so routes are `/api`, `/agent/v1`, `/sub`); a thread per long-poll is cheap at Meridian's fleet sizes, and `control/` never imports the deployer. `route_catalog.py` declares every `/api` operation (scopes, models, errors, credential marker) and drives dispatch, authorization, and `openapi.py`; `route_table.py` owns matching.
- **One SQLite file, migrated forward** — `store.Store` shares one connection, lock, and change condition among the ledgers, each owning its tables; `SCHEMA_VERSION` plus `_MIGRATIONS` move it forward, and a database from a newer Meridian is refused, never downgraded.
- **Schema steps stay self-contained** — `schema.py` owns table creation and forward migrations; the service delegates under its initialization transaction.
- **Scoped API tokens** — one revocable token per device or agent (`tokens.py`): `read` < `propose` < `approve` < `admin`, plus a separate `people` capability for user reads and edits. Whatever applying does is `admin`. Lower scopes see user credentials blank, because those credentials connect as the user. The config's admin token is the unlisted root.
- **Revisions record every reviewed apply** — `revisions.py` keeps author, secret-free intent, plan and review hashes, and status; only an `admin` token approves its own proposal (the one-person path). The apply fence is one compare-and-swap row: the service stores what `reconciler/fence.py` decides and never judges leases itself.
- **Fleet CA and agent sessions** — `NodeAuthority` keeps an Ed25519 CA and signing key; enrollment trades a single-use token and CSR for a node certificate and revokes the node's earlier ones; `hello` checks chain, revocation, SAN, clock, and nonce before opening a session. Tokens are stored only as SHA-256, and protocol skew follows `core/version_skew.py`.
- **Signed commands** — `commands.py` delivers only negotiated kinds with durable first results; cut-offs (`cutoff.py`) queue resets in the credential change's transaction, released only once that revision is reported applied (earlier, a reset could restore the revoked credentials).
- **Traffic counted exactly once** — `traffic.py` keeps each node's last sequence and totals per counter epoch and adds only growth; old epochs keep their own baselines, only a bounded number are kept, and counters for unknown user IDs are dropped, so a node cannot grow the database at will. Node totals are durable and never rebuilt from user totals. No client address is ever stored.
- **Period traffic has one projection** — directional baselines feed `core.traffic.period_usage` for people, delivery, and `/info`; resets preserve lifetime totals, and unknown historical splits stay disclosed as carried download until reset.
- **Operator-owned data** — status, expiry, limits, device caps, notes, tags, languages, privacy, notices, and allowances belong to the operator; applies never write them. Enforcement (a 30 s timer, every change, every stats report) moves users between `active`, `expired`, and `limited` on shared UTC periods (`control.periods`).
- **Privacy levels** — `privacy.py` keeps device limits `off|private|full` and usage history `off|daily`, both off by default, with a full audit; a lowering deletes or re-keys data in the same transaction as the audit row. `devices.py`: private stores only an HMAC of `x-hwid`, full stores device metadata but never an address.
- **Encrypted snapshots** — `snapshot.py` seals an age file to every device key and the recovery key, repeating the header inside because age has no associated data; `restore.py` stages it under the next epoch with a new CA and every document signed again. `blobs.py` mirrors pinned binaries for nodes that cannot reach GitHub.
- **Connection pages at subscriptions** — a browser at `/sub/<token>` gets the page, apps get their format, `/info` stays JSON (`subscription_serving.py`, `connection_pages.py`); shared assets live at token-free `/sub/~pwa/` (`~` is outside the token alphabet).
- **Derived reads write nothing** — attention and the previews read one view under the store lock and store no request data; `events.py` appends typed facts inside each mutation's transaction.
- **Stopped-use attention is aggregate evidence** — its own bounded day threshold compares last accepted traffic, including after a period reset; unknown activity stays unknown.

## What's done well
- A revision is signed only when the resources' hash or the signing certificate changes, so repeated applies never wake agents.
- A token's public ID finds its row, so its hash is compared once in constant time (against a dummy when none matches); a refused CSR rolls back and never burns the enrollment token.
- A proposal whose intent contains a secret-shaped key or value, or a malformed `access` block, is refused and never stored.

## Pitfalls
- **Classify every route in the catalog** — internal routes need a reason, and credential markers must cover conditionally visible people records, endpoint passwords, and snapshots.
- **Decode the path before routing** — `ControlClient` percent-encodes IDs, so an undecoded `%3A` made every endpoint PUT 404.
- **Answer like nginx everywhere** — `HEAD` as `GET`, nginx's error pages, the unknown-subscription answer for any miss under `/sub/`, and small bodies before authentication; anything else names the backend to a scanner.
- **Never log requests or store where they came from** — paths carry the secret path and subscription tokens.
- **nginx answers 502/503/504 for a stopped service** — treat them as retryable, and raise `ControlServiceUncertain` for a write that may have committed, so drivers observe before retrying.
- **`serve` is a daemon** — it skips the banner and the update check, or every start would print into journald and phone out.
- **`executescript()` commits on its own** — create tables with `Store.create()`, never inside `transaction()`.
- **Re-check session liveness and declared-person rules inside the write transaction** (`Store.conditions()`) — checked before the body was read, a revoked console tab could still write.
- **Hash and sign documents as received JSON values**, never re-serialized models (the agent verifies the bytes it got), and compare timestamps parsed, never as text.
- **Record a hello nonce only after the proof verifies**, and keep it past the clock window, or a captured hello replays.
- **Judge health only from a fresh report** — polls prove reachability, not convergence; a stale report is unavailable evidence.
- **Node baselines and revision numbers outlive node deletion; a person's totals do not** — a re-added node would otherwise count twice and refuse every document as older, while a late report must not recreate a removed person.
- **Validate expiry and quota bounds inside the write** — years from 2099 read as "never", and quotas stop at SQLite's int64 maximum.
- **An app without a valid `x-hwid` is served and counted nowhere**, and browsers never register a device; refusing it would lock out apps without HWID support.
- **Subscriber device capacity is counts only** — `/info` reads used/allowed slots under the subscription lock, only at a countable opt-in level; it never selects device details or registers a browser.
- **Secrets never ride a snapshot** — strip the secrets table, sessions, enrollment tokens, nonces, and owner tables through `strip_unsnapshotted()`, then VACUUM; device and history rows ride only while their level is on.
- **Background tasks log no exception text** — they keep fixed error codes in memory and warn at most once per five minutes.
- **Notes and tags stay private** — never in events or subscription output; a metadata-only edit makes no event or new node document.
- **Resolve a person's language under the settings lock**; User-Agent and Accept never choose it.
- **Person pages are no-store and noindex with the strict CSP**; only shared assets are cacheable, and support HTML fetches nothing.
