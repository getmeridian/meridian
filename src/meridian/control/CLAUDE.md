# control — Meridian's control service (`meridian serve`)
## Design decisions
- **State ownership** — the deployer PUTs topology; the service owns users, status, quotas, expiry, device caps, and privacy. Applies preserve operator settings. Client-IP forwarding stays unsupported and migrate drops it.
- **One database** — all ledgers share `Store`, its connection, lock, and condition. WAL and numbered migrations advance only; newer databases refuse. `users.py` owns the user projection and UTC normalization.
- **Fleet CA** — `NodeAuthority` creates Ed25519 credentials in `secrets`; SSH pins the CA. Documents carry the signing certificate's DER hash and are signed again when its identity changes.
- **Agent sessions** — single-use 30-minute enrollment, 90-day certificates, nonce-checked hello (±300 seconds), one-hour sessions, transactional renewal. Negotiation follows `core/version_skew.py`.
- **Traffic** — count growth once per sequence and epoch; bound old baselines and retired epochs, discard unknown user IDs. Lifetime includes imported traffic; the reset offset leaves current-period use. Periods advance before growth counts.
- **Tokens and revisions** — read < propose < approve < admin; applies need admin. Lesser reads hide credentials. Revisions hold secret-free intent/review hashes; only admin approves its own proposal. The fence is a versioned CAS row.
- **Subscriptions** — suffix, Accept, and app choose Base64, Xray JSON, Mihomo, Stash, sing-box, or browser JSON. Formats share templates/status placeholders; the golden corpus pins compatibility.
- **Devices** — off reads nothing; private stores HMAC-SHA256 fingerprints with all metadata/timestamps NULL; full stores HWIDs and bounded metadata. Registration checks caps transactionally. Known devices stay allowed above lowered caps; inactive users consume no new slots.
- **Privacy integration** — shared defaults/DDL live in `privacy.py`; the settings framework owns writes/auditing. Its transaction calls `DeviceLedger.lower_level` for re-keying/deletion and the deleted count. Private to full learns IDs on subsequent fetches without extra slots.
- **Snapshots** — consistent copies are age-encrypted to device/recovery keys; ZIP repeats the header for integrity. Privacy/changes always ride, devices while on. Restore raises epoch, revokes certificates, regenerates secrets, and re-signs documents.
- **HTTP and console** — threaded loopback HTTP sits behind nginx's secret path. Owner sessions reach `OWNER_ROUTES` only; credential writes need recent confirmation. Console: `ui/CLAUDE.md`.
- **Blobs** — a worker mirrors pinned artifacts, verifies size/hash, and publishes atomically; agents fetch only their own spec's hashes.
## What's done well
- Unchanged resource hashes keep revisions quiet; refused enrollment/device writes roll back completely.
- Formats share cap placeholders; full records require nullable metadata fields, so parsing private IDs never adds metadata.
## Pitfalls
- Never log requests or their source: paths contain credentials; token last use updates once a minute. Never read forwarded-address headers.
- Browsers/apps without supported HWID are served and counted nowhere; limits govern fetches, and removed registrations can return.
- Private keys never ride snapshots: restore forgets private registrations, including fingerprints awaiting full promotion; devices re-register.
- Strip before migrating through `strip_unsnapshotted`, skipping absent tables; VACUUM copies in autocommit to erase secret pages.
- `executescript` commits implicitly: create tables through `Store.create` outside transactions.
- Re-check owner liveness/declarations in write transactions (`Store.conditions`); pre-body checks lose races. Declared includes approved/failed revisions after newest applied, excluding proposals.
- Imported UUID case stays for Hysteria2, lowercase for VLESS; imported resets must fall in the current UTC period.
- Parse timestamps, never compare strings. Synchronization uses the agent's reported epoch, never the service's current epoch.
- Sign received JSON values, not re-serialized models; verify proofs before recording nonces and retain them through replay windows.
- Enrollment revokes earlier sessions/certificates; renewal re-checks revocation transactionally, polls re-check sessions and answer before expiry.
- Keep traffic baselines/retired revision numbers after node deletion; re-added agents otherwise double-count or refuse older documents.
- Reserve revision numbers when proposed; losing approval replies must not strand approved revisions. Refuse malformed `intent.access` before storage.
- Gateway 502/503/504 means retryable unreachable; potentially committed writes raise `ControlServiceUncertain` and trigger backup recovery.
- Decode paths before routing; node IDs use topology bounds. HEAD drops GET bodies, errors resemble nginx, unknown subscriptions share one answer, pre-auth bodies cap at 64 KiB.
- Daemons skip banners/update checks; socket reads have deadlines and Content-Length must be digits.
- nginx owns subscription compression/Referrer-Policy. Copies stay outside primary balancers and group by fingerprint; sing-box omits them.
- Stash keeps its field/rule dialect; pin WSS generators and deliberate corpus fixes in tests.
- `control/` never imports CLI, SSH, panel, provision, or reconciler modules.
