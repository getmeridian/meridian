# commands — One module per subcommand

## Design decisions

**One file per command** — keeps concerns isolated. Each is a Typer sub-app registered in `cli.py`.

**Cluster-first pattern** — commands load `ClusterConfig` from `cluster.yml`, create `MeridianPanel` client, call REST API. No SSH needed for client/fleet operations.

**Server resolution cascade** in `resolve.py` — adds CLI-specific prompts and Rich output around the shared helpers in `meridian.resolve`. Strict priority order for server-touching commands (deploy, node add):
1. Explicit IP → 2. `--server` name → 3. `local` keyword → 4. Single-server auto-select → 5. Multi-server prompt → 6. Fail with hint

**Machine deploy mode** — `deploy --json`, `deploy --events=jsonl`, `deploy --request FILE`, and `deploy --dry-run` are process API surfaces for UI clients. Keep prompts and Rich output out; stdout is the final `meridian.output/v1` envelope and JSONL progress goes to stderr.

**Validate at entry** — command functions build core request models first, then render wrapped validation errors with `fail()` before opening SSH or panel connections.

**Command groups**: `client` (add/show/list/remove/enable/disable), `node` (add/list/remove), `relay` (deploy/list/remove/check), `fleet` (status/inventory/recover). Top-level: `deploy`, `test`, `probe`, `doctor`, `teardown`.

## What's done well

- **`client add` is one API call** — `panel.create_user(name)`. No multi-step credential sync.
- **`fleet recover`** — reconstruct cluster.yml from panel API when local state is lost.
- **`fleet inventory`** — prints local topology with live panel status without exposing tokens.

## Pitfalls

- **`console.fail()` always exits** — raises `typer.Exit` with semantic codes (user=2, system=3, bug=1). Only call from command entry points.
- **`confirm()` returns bool** — returns True on accept, False on reject. Callers must check `if not confirm(...): raise typer.Exit(1)`.
- **Panel node cannot be removed** — `node remove` blocks removal of the panel host server.
- Do not use `model_copy(update=...)` to apply untrusted request data; rebuild the Pydantic request model.
