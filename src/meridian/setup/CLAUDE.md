# setup — resumable setup application service
## Design decisions
**Secret-free draft** — `setup.json` stores only validated server refs, topology choices, stage progress, and reviewed/applied hashes. Passwords, private keys, and API tokens use one-time runtime channels.

**Atomic stage boundary** — every completed or rewound stage is validated and atomically replaced with mode `0600`; only a missing file means fresh setup.

**Server shelf IDs** — human names and IPs resolve to immutable `srv-*` profile IDs before entering the draft.

**Reviewed runtime** — setup hashes resolved targets and runtime pins, persists topology intent before mutation, shares runtime-only node secrets in memory, and verifies canonical subscriptions before handoff.

**Retirement-bound review** — `SetupReview.review_hash` binds the plan hash to the exact retirement set and the SSH targets of every server it touches, including servers no longer in the intent (resolved from the shelf). The plan hash alone stays the persisted generation identity.

**Default paths are invisible-first** — `default_exit_paths()` is the one builder for deploy, `node add`, and the wizard: Reality always, XHTTP and Hysteria2 on the domain or else the IPv4 address (IP certificate), WebSocket only with a domain. Redeploys keep saved SNI, fingerprint, and transport paths.

**Client IPs are a fleet setting** — `SetupIntent.forward_client_ips` (off: Remnawave sees only 127.0.0.1) turns on `compiler/client_ips.py`'s post-pass: streams send PROXY, a local hop strips it before the camouflage fallback, the panel takes it on `CONTROL_PROXY_TLS_PORT`, and nginx-fronted inbounds trust it. Bridges and Hysteria2 never change.

**Attested lazy control plane** — panel construction waits for checkpointed bootstrap; readiness binds the reviewed host, URL, listener, and only the bootstrap's own inputs (control target, runtime pins, renderer contract, profile ports/SNI, hardening) to a remote marker.

## What's done well
- Downstream answers are invalidated whenever an earlier stage changes.
- Apply accepts only the exact hash shown at review.
- Core contracts stay independent of filesystem, SSH, console, and Remnawave adapters.
- Existing `plan` and `apply` commands select the same V4 compiler/runtime whenever cluster state contains topology intent.

## Pitfalls
- **Control bootstrap values come from the exit on the control server** — taking the first exit in plan order re-ran the bootstrap whenever an exit that sorts earlier was added, and fed another server's ports to the control nginx.
- Never add credential-shaped fields to `SetupDraft`.
- Preserve relay `listen_port` and exit `egress_overrides` in both `SetupDraft.from_intent()` and `to_intent()` (and when the wizard re-asks roles); otherwise review silently drops them.
- Do not skip stages; presentation adapters must call `SetupDraftService`.
- Keep remote action checkpoints in typed cluster state, not `setup.json`.
- Inspect a cloned cluster through read-only drivers; cached plan hashes cannot detect managed remote drift.
- Control readiness reports confirmed identity/artifact drift separately from unavailable SSH, socket-table, or panel evidence.
- Approve and apply `review_hash`, never `plan.plan_hash`: a plan hash cannot see what the change removes; a missing shelf profile for a retiring server fails review.
- Never attest the control bootstrap against the whole deployment contract: adding or removing any other server would re-run the full panel provisioner on the live control server.
- Apply checks every target server for Meridian 3.x/2.x artifacts and for apt/dpkg (`legacy_guard.py`) before persisting intent; V4 takes over 443 and stops old proxies, so a live old deployment must be torn down explicitly, never replaced silently, and a non-apt OS is refused before any step half-runs.
