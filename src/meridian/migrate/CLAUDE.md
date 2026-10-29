# migrate — In-place move of a Remnawave fleet onto the control service (#95)

## Design decisions

- **One hidden command, one resumable flow** — `commands/migrate.py` reviews then confirms (no terminal and no `--yes` stops after the review); `flow.ControlMigrator` checkpoints each step in `ClusterConfig.control_migration` (`started → backed_up → imported → cut_over → applied → done`), so a re-run continues and a finished one is a no-op.
- **Refuse before touching anything** — a fleet already on the service, or one `validate_meridian_backend()` refuses, stops at review with no SSH; the fence and panel users are read before any change.
- **Backup first, no rollback** — `backup.py` pg_dumps the panel (neutral or legacy container) to `SERVER_BACKUP_ROOT` on the control server and copies it home (sha256-checked, 0600) with `.env`, `cluster.yml`, `servers.json`, `users.json`, and `links.json`.
- **Users keep their credentials** — `users.py` carries only `topology_intent.access.users`: VLESS UUID, short UUID as `sub_token`, status, expiry, limit, strategy, period and lifetime usage; whatever the service cannot hold is reported per user, and `DROPPED_PANEL_STATE` lists what nobody keeps.
- **Install beside, import over SSH** — `service_import.py` starts `ctld` on `MIGRATION_SERVICE_PORT` (the panel keeps 3000) with the token the bootstrap writes again, and POSTs the batch with curl on the control server; the import is admin-scoped, transactional, and idempotent.
- **Cut over, then the normal apply** — `cutover.py` stops the panel (restart=no, data kept), removes node containers through node retirement (agents bind their ports), forgets panel-side bindings (their state is the backed-up database), swaps in the service token, and flips the backend; the command applies through `apply_reviewed_intent()`.
- **Links are proven, then the panel goes** — after the apply every user's subscription URL must equal `links.json`, else a `bug` error stops before `remove_panel()` (teardown's `panel_stack_removal()`) and `drop_panel_state()`.

## What's done well

- **Steps are monkeypatchable module names in `flow.py`** — `tests/test_migrate_flow.py` runs the whole flow, a resume, and the no-op with fakes; `tests/test_migrate_links.py` proves the service publishes what the panel's Hosts did.

## Pitfalls

- **The service token is persisted before install** — a re-run after a crash must reuse it, or the installed service refuses the import; it is cleared once it becomes `panel.api_token`.
- **Panel-side kinds cannot retire after the cut-over** — no API answers; forget them at cut-over or the commit refuses kinds outside `RETIREMENT_ORDER`.
- **Node containers go before the apply** — the agent's core binds the same inbound ports; the panel stops first, because the bootstrap restarts `ctld` on 3000.
- **Compare against links saved before the cut-over** — the post-apply panel URL loses its trailing slash; `subscription_url()` reads only host and path.
- **A short UUID outside `SUB_TOKEN_PATTERN` is refused at review** with a `client reissue` hint; never widen the pattern silently.
