# reconciler — Declarative plan / apply engine

Terraform-style reconciliation of `cluster.yml` desired state against actual
Remnawave panel state. Pure `compute_plan()` + `execute_plan()` executor.

## Design decisions

- **`compute_plan` is a pure function** — no I/O, no network, no side effects. Takes `(desired, actual, applied_*)` dataclasses, returns `Plan[PlanAction]`. Fully unit-testable; covers every diff case.
- **Typed `PlanAction.kind`** — `ADD_NODE / UPDATE_NODE / REMOVE_NODE / ADD_RELAY / UPDATE_RELAY / REMOVE_RELAY / ADD_CLIENT / REMOVE_CLIENT / ADD_SUBSCRIPTION_PAGE / REMOVE_SUBSCRIPTION_PAGE`. Executor dispatches by kind.
- **Applied-state snapshot** — `cluster.applied_state` (typed `AppliedState` dataclass) recorded after every successful apply. Distinguishes intentional removal (in applied -> from_extras=False -> executes under `--yes`) from drift (not in applied -> from_extras=True -> requires `--prune-extras=yes`).
- **Parallel executor** — `ADD_NODE` actions run via `ThreadPoolExecutor`. Per-worker `MeridianPanel` clone (`_make_worker_panel`); `threading.local()` event loop keeps async SDK calls isolated. Destructive kinds stay serial.
- **Display order is not execution order** — `plan --json` exposes both `plan_index` and `execution_order`. Relay/client removals run before some adds for host-remark safety; node removals still run last. `UPDATE_RELAY` is a destructive replacement.

## What's done well

- **Drift-aware apply** — panel-side edits (admin adds a user in the UI) surface as plan actions on next `meridian plan`. Users see diffs; `--prune-extras` controls whether drift is pruned.
- **Failure-safety gate** — after any failure in a phase, later destructive phases are skipped. This does not make early destructive phases atomic; future work needs preflight/journal/switch phases.
- **Rich terraform-style display** — `+` adds, `-` removes, `~` updates; `[drift]` marker on `from_extras=True`.
- **Typed handlers** — `ActionHandler = Callable[[PlanAction, MeridianPanel, ClusterConfig], None]` enforces handler signatures at compile time.
- **Shared plan computation** — `prepare.py::compute_reconciliation_plan()` is the single entry point for both `plan` and `apply` commands.

## Pitfalls

- **`from_extras=True` is drift, False is intentional.** Classification relies on the applied-state snapshot; don't skip it (`apply.py` must record after every success).
- **Hybrid imperative commands (`client add`, `node add`) must mirror into the applied snapshot** — otherwise the next plan re-classifies the fresh-imperative resource as drift. See `operations.py::_applied_snapshot_mirror_add`.
- **`compute_plan` takes `applied_*` as `set[str] | None`** — `None` means "no history, treat every actual-not-desired as drift". Preserve the None vs empty-set distinction.
- **Duplicate node names silently misroute** relay `exit_node` — the validator in `cluster.py` rejects duplicates at load time.

## Links

- Pure diff: `diff.py::compute_plan`
- Shared entry point: `prepare.py::compute_reconciliation_plan`
- Executor: `executor.py`
- Dataclasses: `state.py` / `diff.py`
- Display: `display.py::print_plan`
- Tests: `tests/test_reconciler.py`, `tests/test_apply_command.py`
