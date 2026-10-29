# control — Meridian's control service (`meridian serve`)

## Design decisions
- The service owns users, operator limits, privacy, traffic, and subscriptions; the deployer owns reviewed topology. `Store` shares one SQLite connection, lock, and conditions across ledgers; schema steps only move forward.
- `NodeAuthority` owns the fleet CA/signing key in `secrets`. Enrollment is single-use (30 minutes); hello verifies chain, revocation, SAN, time, nonce, and capabilities before opening a one-hour session. Renewal checks revocation in its transaction.
- Specs plus active users compose signed documents; unchanged resources, epoch, and signing certificate keep the revision. `NodeStatus.in_sync` compares the epoch the agent actually reported.
- `traffic.py` counts only cumulative growth per node/counter epoch, acknowledging retries uncounted. Daily history records that same growth on its UTC receipt day only when enabled; retention includes today and prunes on the 30-second timer.
- `privacy.py` owns the singleton settings and complete before/after audit. Both levels default off; applies never write them. Downgrades, shortened retention, and audit commit together; identical settings add no audit row. Reads return the newest 50 changes.
- Private devices share `DEVICE_HMAC_SECRET` through `private_device_key`; secrets never ride snapshots. Active device/history rows ride snapshots; off rows are stripped. Restore clears private devices because the old fingerprint key is gone.
- API scopes are read < propose < approve < admin; apply writes and privacy changes need admin. Root is unlisted. `TokenRegistry.actor` supplies token name, owner, or root for durable changes; reads below admin blank user credentials.
- Revisions hold secret-free reviewed intent and proposal/approval/result; the fence is one versioned CAS row. Console-declared ownership includes the newest applied intent and subsequent approved/failed revisions, never mere proposals.
- The owner uses scrypt plus optional TOTP, hashed sessions (30-minute idle/12-hour absolute), and one-use setup links. `OWNER_ROUTES` is an allowlist; `OWNER_STEP_UP` requires a recent confirmation held inside the write.
- Imports keep credentials and carried lifetime/period usage atomically. `user_policy.py` owns UTC resets and expiry. Operator-disabled users stay disabled; other statuses follow expiry and quota, with resets before stats growth.
- Stdlib HTTP runs behind nginx's secret path, with bounded bodies and socket reads. `formats/` fills compiler templates for the golden subscription corpus; the renderer receives plain user/endpoint values, never the deployer.
- `BlobStore` downloads pinned artifacts in one worker, verifying size/hash before atomic rename; misses retry at most once a minute and only the session's own node may fetch its pins.

## What's done well
- Only resource changes wake node polls; signing and proofs use the raw JSON values received.
- Transactions recheck owner liveness, recent confirmation, and declared-user restrictions after `BEGIN IMMEDIATE`.
- Snapshots use an online database copy, strip ephemeral state, VACUUM, then age-encrypt; restore regenerates trust and advances the epoch.

## Pitfalls
- Never log requests or source addresses: URLs hold secrets; token last-used timestamps update at most once a minute.
- Gateway 502/503/504 means unreachable; possibly committed writes raise `ControlServiceUncertain`, never silently retry as a known failure.
- `serve` prints no beta banner and never checks for updates.
- Decode paths before matching; node IDs follow topology constraints. HEAD is GET without a body, and malformed/missing subscription routes answer like nginx.
- Parse timestamps; lexical comparison breaks fractional seconds.
- `executescript` commits implicitly: use `Store.create` outside transactions.
- Strip snapshot tables only when present; old schemas lack newer tables. VACUUM requires autocommit, and private rows are cleared on restore, not while sealing an active snapshot.
- Record hello nonces only after proof verification and keep them through issued time + 300 seconds + margin.
- Traffic baselines and retired revisions survive node removal, so re-added agents never double-count or reject older documents.
- Finish polls before session expiry; recheck enrollment/session authority before returning a document.
- Preserve imported UUID case for Hysteria2; lowercase only VLESS documents. Imported reset dates must belong to the current UTC period.
- Keep a revision number from proposal through a lost approval reply; an approved revision must still be finished after retries.
- Owners cannot remove/reissue declared people or write intent-owned service settings; privacy is independently operator-owned.
- `control/` never imports CLI, SSH, provision, panel, or reconciler modules.
- nginx owns subscription compression and Referrer-Policy; never add either in the renderer.
- Fingerprint copies require their flag and separate groups per fingerprint; sing-box omits them. Stash uses `sni`/`auth` and no exclude-filter or GEOIP,private.
