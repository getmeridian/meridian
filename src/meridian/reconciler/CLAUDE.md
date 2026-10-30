# reconciler — Declarative plan / apply engine

## Design decisions
- **Checkpointed apply** — `resource_executor.py` re-observes every immutable compiler action, persists each transition, and cuts over a generation only after all dependencies and postconditions succeed.
- **Fenced applies** — `fence.py` decides and `state_fence.py` stores the control server's fence (fleet, committed generation and plan hash, lease) by compare-and-swap for the whole apply: a copy behind refuses, one ahead heals a lost commit. Once the control service answers, the fence and a revision per apply live there (`service_fence.py`).
- **Service bootstrap is a driver** — `control_service_driver.py` installs `ctld`; its attestation hashes payload, target, package identity, and render contract, so only a CLI upgrade or contract bump reinstalls.
- **Meridian drivers** — `meridian_drivers.py` installs `netagent` with a single-use enrollment bootstrap and PUTs node specs, endpoints, users, and settings over the admin API. A healthy agent of this build is observed in place, one of another build is upgraded in place (`agent_upgrade.py`, see `agent/CLAUDE.md`), and only a missing, foreign, or dead one is enrolled again. A workload converges only once its agent reports the current revision applied. WARP installs over SSH beside the agent (`server_support.ensure_warp()`).
- **Settings travel through the service-settings driver** — branding, exit countries, public URL, refresh interval, allowances, and payment metadata, independent of signed node resources, so metadata-only edits keep node document hashes.
- **Rollback-safe server artifacts** — Realm services and nginx files use hashed logical IDs, and a failed reload restores the previous files before the action fails. Host names stay neutral (`netfwd-<token>` units, `fw-<token>` UFW comments).
- **One progress stream** — `execute_resource_plan(reporter=, operation=)` emits one start and exactly one outcome per action, shared with provisioner steps.
- **Cooperative cancellation** — the executor polls between actions, before an unknown-outcome retry, and before retirement; the generation stays pending so re-running resumes from checkpoints.
- **Stalled connections retry read-only work** — `stall_retry.py` re-runs observations and fence reads/swaps on retryable errors with growing pauses (`connection_stalled` warnings); one long outage ends retries for the run.

## What's done well
- **Unknown outcomes are fail-closed** — a timed-out mutation stays `unknown` until observed; only a successful non-converged observation allows a retry, with the same idempotency key.
- **Retirement after convergence** — `retirement.py` removes vanished bindings dependents-first (`RETIREMENT_ORDER`) only after the whole plan succeeds; a failed rank stops later ranks and leaves the generation uncommitted. `FORGET_ONLY` names kinds that need no driver `retire()`; every other kind fails closed without one.
- **Renderer stamps track what each piece deploys** — bump `render_contract.py` whenever deployed bytes or commands change, and every stamp the change reaches (control service, API, subscription, PWA, console); a missed stamp leaves servers running old bytes after an apply.

## Pitfalls
- **Reviewed settings carry only what the topology owns** — never the service-owned fleet language, person overrides, or notices.
- **Access users are partly operator-owned** — the user driver only creates and observes existence; first limits ride atomic creation, and later applies never rewrite status, notes, tags, limits, expiry, or quotas (rewriting them undid `client disable`).
- **A user retirement with a saved cut-off deletes credentials and queues resets atomically**; a 404 after a lost reply means the deletion already committed.
- **An unseen but installed agent gets its backoff first** — after a service outage a healthy agent reads not-live for up to a minute; re-enrolling it then re-keyed every node.
- **A new agent build is an upgrade, never an enrollment** — hash the running image (`/proc/<MainPID>/exe`), not the file, so a lost restart still reads as the old build; any failure after the swap rolls back.
- **Raise `UnknownResourceOutcome` for an uncertain mutation** (timeout, SSH exit 255) — never retry it through `StallRetry`; the executor observes first.
- **Unavailable evidence is not drift** — only completed inspections prove absence; pass each SSH read through `require_available_evidence()`, since a lost `systemctl is-active` once read as a dead agent and re-keyed a healthy node.
- **Unbound dependencies are pending, not lost evidence** — raise `UnboundDependencyError`; `plan` shows dependents as `recheck` (exit 2), other observation failures stay unavailable (exit 3).
- **Endpoint observation compares the typed enabled flag**, so an apply restores an API-disabled entry.
- **Always render Reality `minClientVer`** — newer Xray otherwise rejects Mihomo and sing-box clients, and Meridian's own Xray-based `test` never notices. Server inbounds sniff route-only, so destinations are never rewritten.
- **One TLS output path per Host, one stream owner per listener.**
- **Normalize asynchronous observations** — compare UFW rules as `shlex` tokens and read Realm's version before feature suffixes.
- **A failed postcondition names its predicate** — drivers put plain `mismatches` on the observation (never file contents, which carry secret paths). After an nginx reload, wait for the listener: new ports open shortly after `reload` returns.
- **The node's architecture picks both builds** — read `uname -m` once per apply for the agent build and the Xray pin, or an ARM exit gets a core that never starts.
- **Only a step-boundary `OperationCancelledError` is `cancelled`** — timeouts and killed processes stay `unknown`.
- **Attest server artifacts against their own inputs** (target, pins, own stamp) — hashing the whole deployment re-runs every server on any edit.
- **Delete, never mark inactive** — give a kind an idempotent `retire()` (404 = done) before adding it to `RETIREMENT_ORDER`; the one exception is `forget_server()` for an unreachable removed server, on the operator's word.
- **Commit forgets superseded bindings** — keep the newest per (logical ID, remote ID) plus active and current-generation ones, or `cluster.yml` grows on every apply. New generations find resources by name.
- **Certificates retire through acme.sh's record** (`Le_RealFullChainPath`), and only while nothing (control service or nginx route) still uses the file.
- **An agent retires with its core** — stop and delete both units with their config, keys, and state; removing only the agent left Xray serving.
- **Renames migrate, never orphan** — a surviving pre-rename unit or UFW comment is drift; stop the old unit before the new one binds.
