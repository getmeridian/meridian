# setup — resumable setup application service
## Design decisions
**Secret-free draft** — `setup.json` stores only validated server refs, topology choices, stage progress, and reviewed/applied hashes, replaced atomically (0600) at every stage boundary; passwords, keys, and tokens use one-time runtime channels.
**Server shelf IDs** — names and IPs resolve to immutable `srv-*` profile IDs before entering the draft.
**Reviewed runtime** — setup hashes resolved targets and runtime pins, persists intent before mutation, and verifies delivered subscriptions before handoff.
**Retirement-bound review** — `SetupReview.review_hash` binds the plan hash to the exact retirement set and the SSH targets of every server it touches, including removed ones; the plan hash alone stays the generation identity.
**Default paths are invisible-first** — `default_exit_paths()` is the one builder for deploy, `node add`, and the wizard: Reality always, XHTTP and Hysteria2 on the domain or the IPv4 address, WebSocket only with a domain. Redeploys keep saved SNI, fingerprint, and paths.
**Attested control-service bootstrap** — `control_service.py` installs the service behind the control server's nginx and saves its URL and admin token in `cluster.panel`, so links keep their shape; readiness attests only the bootstrap's own inputs, so editing another server never re-runs it.

## What's done well
- Downstream answers are invalidated whenever an earlier stage changes, and apply accepts only the exact hash shown at review.
- `plan` and `apply` run the same compiler and runtime as setup.

## Pitfalls
- **Approve and apply `review_hash`, never `plan.plan_hash`** — a plan hash cannot see what the change removes.
- **Control bootstrap values come from the exit on the control server** — the first exit in plan order changed whenever an earlier-sorting exit was added, re-running the bootstrap with another server's ports.
- **A self-steal exit bootstraps with the default SNI** — the operator's own name would point the server at itself.
- **Review keeps a control-service fleet on its backend** — intent files omit the default, so `review` reads it from the committed intent.
- **The bootstrap needs an agent build first** — `require_agent_build()` refuses before provisioning, or a source install leaves a running service with no nodes.
- **Never add credential-shaped fields to `SetupDraft`.**
- **The fleet document never sets what it hides** — it writes names for `srv-*` IDs and drops transport paths; import refuses a `path:` key, and every value read before validation guards its own shape.
- **Preserve every intent field through `SetupDraft.from_intent()`/`to_intent()`** and when the wizard re-asks roles (relay ports, egress overrides, server metadata), or review drops them silently.
- **Do not skip stages** — presentation adapters call `SetupDraftService`; remote checkpoints live in typed cluster state, not `setup.json`.
- **Readiness separates confirmed drift from unavailable evidence**, and control-service readiness requires a current certificate naming the service (its domain, else the IP): an exit's IP certificate once replaced a panel domain's while readiness only read the attestation.
- **Apply refuses Meridian 2.x/3.x leftovers and non-apt systems before persisting intent** (`legacy_guard.py`) — V4 takes over 443, so an old deployment must be torn down explicitly, never replaced silently.
