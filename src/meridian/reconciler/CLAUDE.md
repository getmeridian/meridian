# reconciler — Declarative plan / apply engine

## Design decisions

- **Checkpointed apply** — `resource_executor.py` re-observes every immutable compiler action, persists each transition, and cuts over a generation only after all dependencies and postconditions succeed.
- **Ownership-safe Remnawave drivers** — UUID bindings win, deterministic names recover unknown creates, mismatched collisions fail, and settings updates keep unmanaged Response Rules.
- **Rollback-safe server artifacts** — Realm services and nginx files use hashed logical IDs; a failed reload restores the previous files before the action fails.
- **Neutral host names** — `server_render.py` names Realm units `netfwd-<token>`, UFW comments `fw-<token>`, and baseline attestations under `/var/lib/host-baseline/`.
- **One progress stream** — `execute_resource_plan(reporter=, operation=)` emits `plan.action.started` then exactly one completed/failed/skipped per action, and `SetupRuntime` shares it with provisioner steps.
- **Cooperative cancellation** — the executor polls between actions, before an unknown-outcome retry, and before retirement; the generation stays pending so re-running the plan resumes from checkpoints.
- **Fenced applies** — `fence.py` decides and `state_fence.py` stores the control server's `fence.json` (fleet ID, committed generation and plan hash, 30-minute lease) by compare-and-swap and holds it for the whole apply. A copy behind refuses, one ahead heals a lost commit, and only a fresh copy replaces a foreign fleet's fence; a swap that lost its connection re-reads before swapping again. A control-service fleet keeps it in the service (`service_fence.py`, `/api/fence`) once the service answers, and each apply there proposes, approves, and records a revision; see that module for bootstrap and migration.
- **Stalled connections retry read-only work** — `stall_retry.py` re-runs observations and fence reads/swaps on retryable system errors after 2/5/10/20s pauses, reported as `warning` events with `code: connection_stalled`; one outage that outlasts it ends retries for the run.
- **Graph-owned preparation** — one attested baseline prepares packages, hardening, SSH-safe UFW, and optional Docker; only compiled rules open public ports.
- **Meridian drivers (#95)** — `meridian_drivers.py` installs `netagent` over SSH with the contract's `AgentBootstrap` (0600: a single-use enrollment token and `ca_sha256` from the admin API, `tls_pin_sha256` null because 443 serves a Let's Encrypt IP certificate) and PUTs `agent_spec.py` node specs, endpoints, users, and settings; the agent observes in place only with its binary hash, an active unit, the bootstrap identity, and a recent hello, and a workload converges only once its agent reports the current revision applied. Reality keys come from `LocalRealityKeyFactory`, not a panel. WARP (an apt package, daemon, and registration, outside the agent's resource kinds) installs over SSH with the agent through `server_support.ensure_warp()`, the panel node's path.

## What's done well

- **Unknown outcomes are fail-closed** — a timed-out mutation stays `unknown` until observed; only a successful non-converged observation allows a retry, with the same idempotency key.
- **Retirement after convergence** — `retirement.py` removes vanished bindings dependents-first (`RETIREMENT_ORDER`) only after the whole plan succeeds; a failed rank stops later ranks and leaves the generation uncommitted. `validate_resource_plan_transition` refuses only teardown-owned kinds.
- **Forget-only kinds are explicit** — `FORGET_ONLY` names kinds whose state lives in their Profile, is read-only, or must never be reverted; every other retirable kind needs a driver `retire()` or retirement fails closed.
- **Reviewed render contract** — bump the `render_contract.py` manifest whenever deployed bytes or commands change.

## Pitfalls

- **An unseen but installed agent gets its backoff first** — after a control-service outage a healthy agent reads not-live until it retries (≤ 60 s); `AgentRuntimeDriver.apply` waits for it before re-enrolling, which once re-keyed every agent on the first apply after a ctld restart.
- **Raise `UnknownResourceOutcome` for an uncertain mutation** (a timeout or SSH exit 255) — never catch it as an ordinary failure or retry it through `StallRetry`; the executor observes before any retry. Only transport loss marks evidence retryable.
- **Unavailable evidence is not drift** — SSH/tool/panel failures stop reconciliation; only completed inspections prove absence, and read-only observation never allocates workload secrets.
- **Access users are partly operator-owned** — manage only username and squads (the control service's user driver only creates); status, description, limits, expiry, and quotas are the operator's (rewriting them undid `client disable`).
- **Server inbounds sniff route-only** — the panel's Torrent Blocker plugin sees BitTorrent only through sniffing; keep `routeOnly` so destinations are never rewritten.
- **Always render Reality `minClientVer`** — Xray 26.7.11+ otherwise rejects Mihomo/sing-box (they announce 1.8.x), and Meridian's own Xray-based `test` never notices.
- **Inbounds are Profile-derived** — update the aggregate Profile, then observe each profile-scoped tag and UUID; there is no standalone Inbound mutation.
- **One TLS output path per Host, one stream owner per listener** — hostname-derived certificate directories; the V4 port-443 artifact carries the panel/no-SNI route and retires `stream.d/meridian.conf` with rollback.
- **Normalize asynchronous observations** — compare UFW rules as `shlex` tokens, read Realm's version before feature suffixes, and wait for panel node connectivity after every deploy path; node and gateway probes poll on apply, because a recreated container still reads connected until the panel notices the restart.
- **Only a step-boundary `OperationCancelledError` is `cancelled`** — timeouts and killed processes stay `unknown`.
- **Attest server artifacts against their own inputs** (target, pins, renderer contract) — hashing the whole deployment re-runs every server on any fleet edit.
- **Delete, never mark inactive** — give a kind an idempotent driver `retire()` (404 = done) before adding it to `RETIREMENT_ORDER`; server-side retirement needs the binding's recorded `server_ref`. The one exception is `forget_server()`, on the operator's word (`--forget-unreachable`), for a removed server whose SSH port doesn't answer; panel-side kinds still retire.
- **Unbound dependencies are pending, not lost evidence** — a new generation binds identities as it applies, so raise `UnboundDependencyError` for a missing dependency identity; `plan` shows those dependents as `recheck` (exit 2), and every other observation failure stays unavailable (exit 3).
- **Commit forgets superseded bindings** — keep the newest binding per (logical ID, remote ID) pair (what `was_managed` matches) plus active and current-generation ones; keeping every generation's copy grew `cluster.yml` by tens of KB per apply.
- **Bindings are per generation** — new generations find resources by name; GET Templates by UUID before comparing (the list omits content).
- **A renamed Host keeps its UUID** — remarks change with fleet shape, so adopt the committed binding's Host before looking up by remark; name-only lookup created twins.
- **A moved exit keeps its Node** — `SERVER_PLACED` kinds retire their old placement, but the panel Node is PATCHed to the new address, never recreated.
- **Remnawave deletes Nodes asynchronously** — `NodeRetirement` polls until the Node is gone before its Profile is deleted.
- **Certificates retire through acme.sh's record** — match `Le_RealFullChainPath`, not the hostname; skip while the panel or a Host still uses the file; drop the ACME listener with the last one.
- **Node runtimes share `host_runtime.NODE_DIR`** — when the plan still runs a node there, only forget the old binding; retirement removes both the neutral and the `remnawave-node` names.
- **Renames migrate, never orphan** — a surviving pre-rename unit, `meridian-v4-*` UFW comment, or `remnawave*` container/directory is drift; stop the old unit before the new one binds, and re-add rules after deleting the old comment.
