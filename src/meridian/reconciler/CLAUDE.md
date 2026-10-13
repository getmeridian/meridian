# reconciler — Declarative plan / apply engine

Checkpointed reconciliation of compiled topology resources.

## Design decisions

- **Compiled-resource apply is checkpointed** — `resource_executor.py` re-observes every immutable compiler action, persists each transition, and cuts over a generation only after all dependencies and postconditions succeed.
- **Remnawave drivers are ownership-safe** — UUID bindings win; deterministic wire-safe names recover unknown creates, mismatched collisions fail, and global settings updates preserve unmanaged Response Rules.
- **Server artifacts are per-resource and rollback-safe** — Realm services and nginx files use hashed logical IDs; failed reloads restore previous files before the action fails.
- **One progress stream** — `execute_resource_plan(reporter=, operation=)` emits `plan.action.started` then exactly one of completed/failed/skipped per action; `SetupRuntime` shares the same reporter and operation with baseline and control-plane provisioner steps, so Rich, JSONL, and Engine clients render one sequenced stream.
- **Cancellation is cooperative** — the executor polls its token only between actions, before an unknown-outcome retry, and before retirement; the generation stays pending so re-running the same plan resumes from checkpoints.
- **Server preparation is graph-owned** — one attested baseline prepares packages, hardening, SSH-safe UFW, and optional Docker; compiled rules alone open public ports.

## What's done well

- **Unknown outcomes are fail-closed** — a timed-out mutation remains `unknown` if observation fails; retries happen only after a successful non-converged observation using the same idempotency key.
- **Retirement after convergence** — `retirement.py` removes vanished bindings in dependents-first `RETIREMENT_ORDER` only after the whole new plan succeeds; a failed rank stops later ranks and leaves the generation uncommitted for retry. Only teardown-owned kinds (control plane, subscription template/settings) are refused in `validate_resource_plan_transition`.
- **Forget-only kinds are explicit** — `FORGET_ONLY` names kinds whose state lives in their Profile, is read-only, or must never be reverted (baseline packages/SSH/UFW); every other retirable kind needs a driver `retire()` or retirement fails closed.
- **Reviewed render contract** — setup hashes resolved SSH targets, runtime pins, and `render_contract.py`; bump its manifest whenever deployed bytes or commands change.

## Pitfalls

- **Imperative commands are intent edits** — `client add|remove`, `node add|remove`, and `relay add|remove` change `topology_intent` and apply it (removals through reviewed retirement); never mutate panel or server state beside the compiled plan.
- **Never catch an uncertain driver mutation as an ordinary failure** — raise `UnknownResourceOutcome` so the executor observes before any retry.
- **Unavailable observation evidence is not drift** — SSH/tool/panel failures stop reconciliation; only completed inspections prove absence, and read-only observation never allocates workload secrets.
- **Access users are partly operator-owned** — observe/apply only username and squads; status (client disable, panel limits/expiry), description, and quotas belong to the operator, so never re-enable or rewrite them (doing so undid `client disable` and failed every apply after a description edit).
- **Inbounds are Profile-derived resources** — create/update the aggregate Profile, then observe each exact profile-scoped tag and UUID; there is no standalone Inbound mutation.
- **Never share one TLS output path across Hosts** — hostname-derived directories prevent independent SNI endpoints from overwriting each other.
- **One stream owner per listener** — the V4 port-443 artifact includes the panel/no-SNI route and retires the legacy `stream.d/meridian.conf` with rollback.
- **Normalize asynchronous observations** — compare UFW rules as `shlex` tokens, read Realm's version before feature suffixes, and wait for panel node connectivity after every deploy path.
- **A cancelled checkpoint is a clean boundary** — only `OperationCancelledError` from a provisioning step boundary becomes `cancelled`; never map timeouts or killed processes to it, they stay `unknown`.
- **Attest server artifacts against their own inputs** — hashing the whole deployment contract makes every fleet edit re-run work on every server; include only the server's own target plus pins and renderer contract.
- **Do not mark old bindings inactive as a substitute for deletion** — add a driver `retire()` (idempotent, 404 = done) before adding a kind to `RETIREMENT_ORDER`; server-side retirement needs the binding's recorded `server_ref`.
- **Bindings are per generation** — a new generation finds resources by name; when a list endpoint omits content (Templates), GET the record by UUID before comparing or every intent edit rewrites it.
- **Remnawave deletes Nodes asynchronously** — DELETE only queues the job; `NodeRetirement` polls until the Node is gone so its Profile is not deleted under it.
- **Node runtime retirement shares a directory** — every exit uses `/opt/remnanode`; when the plan still runs a node on that server, only forget the old binding.
