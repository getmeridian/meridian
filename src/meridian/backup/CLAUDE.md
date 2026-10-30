# backup — Never lose a fleet with its control server

## Design decisions
- **Every managing device holds the service's state** — after a run that changed (or may have changed) a control-service fleet, `commands/backup.pull_after_mutations()` pulls the newest snapshot into the context's `snapshots/`; `ControlClient` reports each mutating request through `add_mutation_listener()`. A failed pull warns and never changes the command's outcome.
- **One key per fleet context** — `keys.py` keeps an age X25519 identity in `device.key` (0600) beside `cluster.yml`, registered on first pull. A revoked key is never re-registered silently, a malformed file is an error, and a new key is linked in exclusively, so two first pulls at once share one key instead of sealing a snapshot to a lost one.
- **A pull is also a deposit** — `pull.py` sends this machine's `cluster.yml` (admin token blanked) and `servers.json` first, so a snapshot plus the recovery key can rebuild the fleet on an empty machine.
- **Recovery key shown once** — the first `deploy` prints it on the terminal only (never in JSON); `backup recovery-key` replaces it after confirmation. Only the public half is stored anywhere.
- **Restore rebuilds, never copies** — `meridian restore --to NEW-SERVER` (`restore.py`, checkpointed in `ClusterConfig.control_restore`) stages the snapshot, rotates the admin token, moves the control plane in the intent, installs, then runs the normal reviewed apply; the new CA makes every agent re-enroll.
- **A snapshot carries no secrets** — owner password, TOTP, and sessions included, so after a restore the owner takes a new `meridian panel` setup link.
- **Bare-IP links move; domain links don't** — without a control domain every subscription URL changes and the end prints each new link; a domain fleet keeps its URLs once DNS moves.

## What's done well
- The pull's own client never reports its writes, so a pull cannot trigger another.

## Pitfalls
- **A copy behind the snapshot's deposited generation must not restore** — its intent would retire what newer applies added; it restores into a fresh context, and the stale one gets `superseded.json` and refuses changes.
- **Refuse while the old control server answers** — two services at once split the fleet; only `--force` overrides.
- **Open the control service's firewall rules before its bootstrap** — restore installs before the plan runs, and a fresh server's SSH-only firewall kept the ACME challenge out.
- **Mark the new server's fence.json handed over** — otherwise an apply during an outage takes the lease over SSH around the service.
- **Never read the recovery key from argv** — shell history and `ps` keep it; ask with a hidden prompt or read stdin.
- **Checkpoint before deleting what a rerun needs** — the staged database goes only after `loaded` is saved.
- **Restore above every epoch the agents may hold** — they refuse anything lower, so count earlier restores and saved snapshots.
- **An older snapshot revives what was revoked since** (tokens, devices, people) — take the newest by default and warn when `--snapshot` names an older one.
- **An unfinished restore never travels** — deposits keep only a finished record, or a fresh context chases a dead target.
- **Refuse at review what the apply would refuse** — restore mutates before its apply, so `review()` runs the server-ownership check itself.
- **An unread fleet identity is not an empty server** — `upload_database()` refuses when reading the remote config fails; an SSH 255 once read as "no fleet".
