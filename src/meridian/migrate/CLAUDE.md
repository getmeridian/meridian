# migrate — In-place move of a Remnawave fleet onto the control service

## Design decisions
- **One command, one resumable flow** — `commands/migrate.py` reviews then confirms; `flow.ControlMigrator` checkpoints each step in `ClusterConfig.control_migration` (`started → backed_up → imported → cut_over → applied → done`), so a re-run continues and a finished one is a no-op. Client-IP forwarding is dropped, since the service cannot run it yet.
- **Refuse before touching anything** — an already-moved or unsupported fleet stops at review with no SSH; `run()` holds fence.json's lease from its first step to the cut-over and hands it to the apply, so no other apply lands between.
- **Backup first, no rollback** — `backup.py` dumps the panel database to the control server and copies it home (sha256-checked, 0600) with the fleet files and every user's link.
- **Users keep their credentials** — `users.py` carries keys, status, expiry, limits, and usage for declared users; panel device data is never imported, descriptions become private notes, and anything dropped is reported without its values.
- **Install beside, import over SSH** — `service_import.py` starts `ctld` on a side port while the panel keeps its own, then POSTs one admin-scoped, transactional, idempotent import.
- **Cut over, then the normal apply** — `cutover.py` stops the panel (data kept), removes node containers, forgets panel-side bindings, swaps in the service token, and flips the backend; then `apply_reviewed_intent()` runs.
- **Links are proven, then the panel goes** — every user's subscription URL must equal the saved one, or a `bug` error stops before the panel and its leftover state are removed.

## What's done well
- **Steps are monkeypatchable module names in `flow.py`** — tests run the whole flow, a resume, and the no-op with fakes, and prove the service publishes what the panel did.

## Pitfalls
- **Persist the service token before install** — a re-run after a crash must reuse it, or the installed service refuses the import.
- **Forget panel-side kinds at cut-over** — nothing answers for them afterwards, so the commit would refuse them.
- **Node containers go before the apply, uncommitted ones included** — the agent's core binds the same ports.
- **A resume before the cut-over imports again with `replace`** — the panel kept serving, so a reissue or disable there would otherwise be lost and a revoked link revived.
- **Compare against links saved before the cut-over** — the panel's URL form differs in a trailing slash.
- **Refuse at review what the apply would refuse** — an unresolved earlier apply or a server with no agent build once surfaced only after the panel was gone, leaving the fleet down.
- **Done only after a completed check finds no panel** — every removal command's exit counts; a failed `rm -rf` once reached `done`, after which reruns were no-ops.
- **A short UUID outside `SUB_TOKEN_PATTERN` is refused at review** with a `client reissue` hint; never widen the pattern silently.
- **Never delete `/var/backups/meridian-migration`** — it holds the only copy of the migrated panel's database.
