# reconciler — Declarative plan / apply engine

Checkpointed reconciliation of compiled topology resources.

## Design decisions

- **Compiled-resource apply is checkpointed** — `resource_executor.py` re-observes every immutable compiler action, persists each transition, and cuts over a generation only after all dependencies and postconditions succeed.
- **Remnawave drivers are ownership-safe** — UUID bindings win; deterministic wire-safe names recover unknown creates, mismatched collisions fail, and global settings updates preserve unmanaged Response Rules.
- **Server artifacts are per-resource and rollback-safe** — Realm services and nginx files use hashed logical IDs; failed reloads restore previous files before the action fails.
- **Server preparation is graph-owned** — one attested baseline prepares packages, hardening, SSH-safe UFW, and optional Docker; compiled rules alone open public ports.

## What's done well

- **Unknown outcomes are fail-closed** — a timed-out mutation remains `unknown` if observation fails; retries happen only after a successful non-converged observation using the same idempotency key.
- **Retirement after convergence** — `retirement.py` deletes vanished bindings in `RETIREMENT_ORDER` only after the whole new plan succeeds; a failure leaves the generation uncommitted for retry. Kinds outside that order (exits, relays, gateways) are refused in `validate_resource_plan_transition` before any state is written.
- **Reviewed render contract** — setup hashes resolved SSH targets, runtime pins, and `render_contract.py`; bump its manifest whenever deployed bytes or commands change.

## Pitfalls

- **Imperative commands are intent edits** — `client add`, `node add`, and `relay deploy` change `topology_intent` and apply it; never mutate panel or server state beside the compiled plan.
- **Never catch an uncertain driver mutation as an ordinary failure** — raise `UnknownResourceOutcome` so the executor observes before any retry.
- **Unavailable observation evidence is not drift** — SSH/tool/panel failures stop reconciliation; only completed inspections prove absence, and read-only observation never allocates workload secrets.
- **Inbounds are Profile-derived resources** — create/update the aggregate Profile, then observe each exact profile-scoped tag and UUID; there is no standalone Inbound mutation.
- **Never share one TLS output path across Hosts** — hostname-derived directories prevent independent SNI endpoints from overwriting each other.
- **One stream owner per listener** — the V4 port-443 artifact includes the panel/no-SNI route and retires the legacy `stream.d/meridian.conf` with rollback.
- **Normalize asynchronous observations** — compare UFW rules as `shlex` tokens, read Realm's version before feature suffixes, and wait for panel node connectivity after every deploy path.
- **Do not mark old bindings inactive as a substitute for deletion** — add a driver `retire()` (idempotent, 404 = done) before adding a kind to `RETIREMENT_ORDER`; server-side retirement needs the binding's recorded `server_ref`.
