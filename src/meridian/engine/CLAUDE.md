# engine - Local executable runtime use cases

## Design decisions

- **Command-free runtime boundary** - Engine never imports `meridian.commands` (including `commands.resolve`), `meridian.console`, provision, remnawave, ssh, Typer, Rich, or process-exit helpers (tested).
- **Core is the contract source** - Engine calls `meridian.core` validation, planning, models, reporters, and events instead of redefining request/result shapes.
- **Adapters stay explicit** - filesystem state, server registries, SSH, and panel clients are passed in or isolated behind small runtime helpers.
- **Only for execution** - static Studio uses generated contracts without Engine; Engine serves the executable UI: SSH, files, secrets, operation state, events, and cancellation.
- **Localhost API** - `meridian studio` serves a FastAPI app on `127.0.0.1` (stable port 7391, else the next free one up to 7400, because browsers keep the Studio draft per origin) with contract/workflow discovery, server onboarding, deploy dry-run, and process-local deploy operations.
- **Cancellation is a request, not a kill** - `POST /operations/{id}/cancel` returns a typed `outcome`; a running deploy gets a marker file (`adapters/cancellation.py`) and its child stops at the next safe point. Nothing signals or kills the child.
- **Routes are module-level** - `routes.py` holds standalone handlers on an APIRouter; `api.py` wires `app.state` and includes the router. No closures capture injected services.

## Pitfalls

- The deploy child runs in its own session and Engine shutdown cancels it cooperatively; never let terminal SIGINT or a closed pipe cut a step off mid-change.
- Only a `cancelled` deploy envelope (exit 5) means a clean stop; exit 130 without an envelope is an interruption with possibly unknown outcomes and must surface as a failure.
- Deploy dry-run compiles through `setup/single_server.py::preview_single_server_deploy()`, the same preview `deploy --dry-run` uses; never add a parallel deploy planner.
- Never expose raw shell, arbitrary file access, or unredacted secrets from Engine APIs.
- Serve only `assets.STUDIO_ASSET_ROOTS` (`/` redirects to `/studio/`), even when `--assets-dir` points at a full website build; the Studio page must stay free of inline scripts, or CSP blocks them.
- Keep localhost security hostile by default: Host/Origin/Sec-Fetch-Site checks, the per-launch session key on every API call but health, CSP `script-src 'self'`, no permissive CORS.
- Never return the session key from an endpoint — any local user can call 127.0.0.1; `meridian studio` hands it over only in the `#k=` fragment of the address it prints (health once served it, so anyone on a shared machine could drive deploys).
- Password bootstrap is one-time only: never in generated contracts, logs, events, browser storage, or profiles.
- Keep human wording and terminal rendering in CLI adapters.
