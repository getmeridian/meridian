# commands — One module per subcommand

## Design decisions

**One file per command** — keeps concerns isolated. Each is a Typer sub-app registered in `cli.py`.

**Topology-first pattern** — fleet commands load `cluster.yml` through `load_topology_cluster()`, which refuses pre-topology files with rebuild guidance. Mutations (`node add`, `relay deploy`, `client add`) edit `topology_intent` and run the reviewed `SetupRuntime` apply; reads use the panel API plus `adapters/cluster.py`.

**Server resolution cascade** in `resolve.py` — adds CLI-specific Rich output around the shared helpers in `meridian.resolve`. Strict priority order for server-touching commands (deploy, node add):
1. Explicit IP / `local` → 2. `--server` name → 3. Local-mode detection → 4. Single-server auto-select → 5. Fail with candidates and hint

**Machine deploy mode** — `deploy --json`, `deploy --events=jsonl`, `deploy --request FILE`, and `deploy --dry-run` are process API surfaces for UI clients. Keep prompts and Rich output out; stdout is the final `meridian.output/v1` envelope and JSONL progress goes to stderr. Hidden `--cancel-file PATH` makes deploy stop at its next safe point and emit a `cancelled` envelope (exit 5) once the file exists.

**Health process API** — completed negative evidence exits 4, unavailable required evidence exits 3, and only fully passed checks exit 0; panel/system failures are inconclusive, while `--basic` never accepts or selects `--client`.
**Canonical connection test** — full `test` fetches one deterministic active client's delivered Xray JSON and executes its fallback plus each target outbound without rebuilding credentials or transports locally.

**V4 setup is presentation-only** — `setup_v4.py` and `setup_wizard.py` collect resumable choices; compilation, checkpointed apply, and canonical verification stay in `meridian.setup`.

**Validate at entry** — command functions build core request models first, then render wrapped validation errors with `fail()` before opening SSH or panel connections.

**Command groups**: `client` (add/show/list/remove/enable/disable), `node` (add/list/check/remove), `relay` (deploy/list/remove/check), `fleet` (status/inventory). Top-level: `setup`, `deploy`, `plan`, `apply`, `test`, `probe`, `doctor`, `teardown`. `client remove`, `node remove`, and `relay remove` edit the intent and let reviewed retirement delete what vanished; `topology_removal.py` owns their shared review, confirmation, and apply. `relay check` refuses until per-hop checks exist.

## What's done well

- **`client add` follows topology ownership** — it expands declared access intent and applies the reviewed topology; it never creates panel users directly.
- **`deploy --dry-run` and Engine share one preview** — `setup/single_server.py::preview_single_server_deploy()` compiles the intent; neither path imports the other.
- **`fleet inventory`** — prints local topology with live panel status without exposing tokens.
- **Read and health commands** — node, relay, fleet, and doctor consume registry-backed topology plus compiled listener allocations.

## Pitfalls
- **`console.fail()` always exits** — raises `typer.Exit` with semantic codes (user=2, system=3, bug=1). Only call from command entry points.
- **`confirm()` returns bool** — returns True on accept, False on reject. Callers must check `if not confirm(...): raise typer.Exit(1)`.
- **Topology deletion is guarded** — teardown and server removal check intent roles, active workloads, and allocations; only a whole single-server fleet tears down in one step.
- Removal commands refuse the control server and dependents (relays into an exit, gateways bridged through it) before review; `--force` on `node remove` only extends the edit to those relay chains.
- Post-mutation local save failures exit 3, say remote state changed, and preserve typed partial data where the command contract supports it.
- V4 client reads and connectivity tests expose only users declared in `topology_intent.access.users`; never fall back to panel-wide service users.
- Never render saved panel administrator credentials. Handoff links (`HANDOFF_LINK_KEYS`) appear verbatim only in the final output of the command that produced them (`emit_json(..., reveal=...)`); logs, events, and errors stay redacted.
- Doctor reports pass every free-form line through `redact_string()` and render with Rich markup disabled before sharing.
- Do not use `model_copy(update=...)` to apply untrusted request data; rebuild the Pydantic request model.
- V4 plan/apply must bind the reviewed hash and emit typed compiled results or exactly one structured terminal error.
