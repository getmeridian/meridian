# engine - Local executable runtime use cases

## Design decisions

- **Command-free runtime boundary** - Engine code does not import `meridian.commands`, `meridian.console`, Typer, Rich, renderers, or process-exit helpers.
- **Core is the contract source** - Engine calls `meridian.core` validation, planning, models, reporters, and events instead of redefining request/result shapes.
- **Adapters stay explicit** - Filesystem state, server registries, SSH, panel clients, and future HTTP handlers are passed in or isolated behind small runtime helpers.
- **Only for execution** - Static Studio can use generated contracts without Engine. Engine is for local executable UI mode: SSH, files, secrets, operation state, events, and cancellation.
- **No HTTP yet** - The initial Engine package owns deploy planning and dry-run orchestration only. Localhost API, operation registry, SSE, and cancellation come later.

## Pitfalls

- Do not import `commands.resolve`; it creates CLI output, process exits, and SSH connections at import/use time.
- Do not expose raw shell, arbitrary file access, or unredacted secrets from Engine APIs.
- Keep human wording and terminal rendering in CLI adapters.
- Keep localhost security hostile-by-default when HTTP arrives: Host/Origin checks, CSRF, no permissive CORS.
