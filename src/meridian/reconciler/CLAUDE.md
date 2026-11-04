# reconciler — Declarative plan / apply engine
## Design decisions
- **App rules share settings observation** — the service-settings driver applies and compares the full typed ordered list, preserving request-free intent and unchanged default payloads.
- **Checkpointed apply** — `resource_executor.py` re-observes every immutable compiler action, persists each transition, and cuts over a generation only after all dependencies and postconditions succeed.
- **Fenced applies** — compare-and-swap fleet, generation, plan and lease for the whole apply. Behind copies refuse; ahead copies heal lost commits. Once available, the service owns the fence and records each revision.
- **Service bootstrap is a driver** — `control_service_driver.py` installs `ctld`; its attestation hashes payload, target, package identity, and render contract, so only a CLI upgrade or contract bump reinstalls.
- **Meridian drivers** — the drivers install and upgrade agents, PUT node specs/endpoints/settings, and wait for a fresh applied revision. Only missing, foreign or dead agents re-enroll. WARP installs beside the agent and leaves only after its removal revision applies.
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
- **Forget only adopted accounts** — `control_user` stays outside `FORGET_ONLY`; vanished pre-upgrade bindings still retire with their saved atomic cut-off.
- **People query changes reach three stamps** — membership projections and filter controls change service, API, and console bytes together.
- **Settings re-check profile declarations** — unreviewed writes compare the fingerprint; reviewed replacements use the membership fence, including empty declarations. Save profile-use history before the request, or observations misclassify its empty fingerprint as drift.
- **Membership is creation-only in setup seeds** — missing people receive their initial profiles; existing people keep their live memberships.
- **Creation seeds preserve live schedules** — duration, reset anchor, renewals, caps and progress stay operator-owned.
- **Reviewed settings carry only what the topology owns** — never the service-owned fleet language, person overrides, or notices.
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
- **Commit forgets superseded bindings** — retain the newest remote identity plus active/current-generation records, or cluster.yml grows on every apply; later generations locate resources by name.
- **Certificates retire through acme.sh records** — delete only unused files; the service holds its domain certificate, or its IP certificate without a domain. Stop and delete agents together with their core, config, keys and state. Stop renamed units and remove old UFW ownership before their replacements bind.
