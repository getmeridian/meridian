# backup — Never lose a fleet with its control server (#95)

## Design decisions

- **Every managing device holds the service's state** — after a run that changed a control-service fleet, `commands/backup.pull_after_mutations()` (installed once by the root callback; every non-GET `ControlClient` request that succeeded, or may have (a lost reply or a 5xx), reports its URL through `control.client.add_mutation_listener()`) pulls the newest snapshot into the context's `snapshots/` (newest `KEEP`); a failed pull warns and never changes the command's outcome. Hidden `meridian backup pull|recovery-key|devices|revoke`.
- **One key per fleet context** — `keys.py` keeps an age X25519 identity in `device.key` (0600) beside `cluster.yml`; the first pull registers its public half (`/api/backup/devices`, named after the host). A revoked key is never re-registered silently, a malformed file is an error, never replaced, and a new key is linked in exclusively, so two first pulls at once share the first one (replacing it left a snapshot sealed to a lost key).
- **A pull is also a deposit** — `pull.py` sends this machine's `cluster.yml` (admin token blanked) and `servers.json` before fetching, so a snapshot plus the recovery key can rebuild the fleet on a machine with nothing else; it opens the snapshot with the device key before saving it.
- **Recovery key shown once** — the final `deploy` output creates one for a service fleet without one (never in JSON or quiet mode); `meridian backup recovery-key` makes or replaces it (confirmation unless `--yes`), printing the `AGE-SECRET-KEY-1...` line on the terminal only. The service and this machine keep only the public half.
- **Restore rebuilds, never copies** — hidden `meridian restore --to NEW-SERVER` (`restore.py`, steps in `ClusterConfig.control_restore`, the same `step_reached()` shape as migrate) stages the snapshot through `control.restore.stage_restored_database()` (one epoch above the snapshot and every epoch this machine has seen, every node certificate revoked, a new CA and signing key, documents re-signed), rotates the admin token, moves the control plane and the old server's exits in the intent, uploads the database, installs through the setup path, then runs the normal reviewed apply: a new CA and service URL make every agent observe as drift and re-enroll.
- **A snapshot carries no secrets** — `control.backups.NOT_SNAPSHOTTED` drops secrets, sessions, enrollment tokens, nonces, and the web owner (password, TOTP secret, sessions), so a seized server's keys sign nothing a restored fleet trusts and the owner takes a new `meridian panel` setup link.
- **Bare-IP links move; domain links don't** — when `control.public_hostname` is unset the review says every subscription URL changes and the end prints each client's new link; a domain fleet keeps its URLs once DNS moves, and the review names the records.
- **Offsite copies are a later seam** — a snapshot is one self-describing file; an S3 store would upload what `save_snapshot()` writes, nothing more.

## What's done well

- The pull's own client never reports its writes, so a pull cannot trigger another one.

## Pitfalls

- **A copy behind the snapshot's deposited generation must not restore** — its intent would retire what newer applies added; it is sent to a fresh context, which takes the snapshot's copy.
- **Refuse while the old control server answers** — two services at once split the fleet; only `--force` overrides it.
- **Open the control service's firewall rules before its bootstrap** — restore installs before the plan runs, so a fresh server's SSH-only UFW kept port 80 closed and the ACME challenge timed out; `open_control_ports()` installs ufw (stock Debian has none) and applies the rules `control:service` depends on.
- **Never read the recovery key from argv** — shell history and `ps` keep it; `--recovery-key` asks with a hidden prompt or reads one line of stdin.
- **Checkpoint before deleting what a rerun needs** — the staged database goes only after `loaded` is saved; deleted first, a failed save left a rerun uploading a file that no longer existed.
- **Restore above every epoch the agents may hold** — they keep the highest epoch they accepted and refuse less; an epoch-1 snapshot restored as 2 after an earlier restore reached 3, so no agent connected. `_highest_known_epoch()` reads the last restore and saved snapshots.
- **An older snapshot revives what was revoked since** — API tokens, backup devices, and people live in its database; restore takes the newest saved one by default and warns when `--snapshot` names an older one (`newer_saved_snapshot()`).
- **An unfinished restore never travels** — deposits and adopted snapshot copies keep only a `done` record (`finished_restore()`); a snapshot pulled mid-restore once held a fresh context to the dead target.
- **Refuse at review what the apply would refuse** — restore uploads and installs before its apply, so `review()` runs `refuse_servers_claimed_elsewhere()` itself; left to the apply, a server another context held was already changed.
- **An unread fleet identity is not an empty server** — `upload_database()` refuses when reading `/etc/ctld/config.json` fails or it doesn't parse; an SSH 255 read as "no fleet" and the upload went ahead.
