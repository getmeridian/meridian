# backup — Never lose a fleet with its control server (#95)

## Design decisions

- **Every managing device holds the service's state** — after a run that changed a control-service fleet, `commands/backup.pull_after_mutations()` (installed once by the root callback; every successful non-GET `ControlClient` request reports its URL through `control.client.add_mutation_listener()`) pulls the newest snapshot into the context's `snapshots/` (newest `KEEP`); a failed pull warns and never changes the command's outcome. Hidden `meridian backup pull|recovery-key|devices|revoke`.
- **One key per fleet context** — `keys.py` keeps an age X25519 identity in `device.key` (0600) beside `cluster.yml`; the first pull registers its public half (`/api/backup/devices`, named after the host). A revoked key is never re-registered silently, and a malformed file is an error, never replaced.
- **A pull is also a deposit** — `pull.py` sends this machine's `cluster.yml` (admin token blanked) and `servers.json` before fetching, so a snapshot plus the recovery key can rebuild the fleet on a machine with nothing else; it opens the snapshot with the device key before saving it.
- **Recovery key shown once** — the final `deploy` output creates one for a service fleet without one (never in JSON or quiet mode); `meridian backup recovery-key` makes or replaces it (confirmation unless `--yes`), printing the `AGE-SECRET-KEY-1...` line on the terminal only. The service and this machine keep only the public half.
- **Offsite copies are a later seam** — a snapshot is one self-describing file; an S3 store would upload what `save_snapshot()` writes, nothing more.

## What's done well

- The pull's own client never reports its writes, so a pull cannot trigger another one.

## Pitfalls

- **Never read the recovery key from argv** — shell history and `ps` keep it; `--recovery-key` asks with a hidden prompt or reads one line of stdin.
