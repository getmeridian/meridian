# setup — resumable setup application service
## Design decisions
**Secret-free draft** — `setup.json` stores only validated server refs, topology choices, stage progress, and reviewed/applied hashes. Passwords, private keys, and API tokens use one-time runtime channels.
**Atomic stage boundary** — every completed or rewound stage is validated and atomically replaced with mode `0600`; only a missing file means fresh setup.
**Server shelf IDs** — human names and IPs resolve to immutable `srv-*` profile IDs before entering the draft.
**Reviewed runtime** — setup hashes resolved targets and runtime pins, persists topology intent before mutation, shares runtime-only node secrets in memory, and verifies canonical subscriptions before handoff.
**Retirement-bound review** — `SetupReview.review_hash` binds the plan hash to the exact retirement set and the SSH targets of every server it touches, including servers no longer in the intent (resolved from the shelf). The plan hash alone stays the persisted generation identity.
**Default paths are invisible-first** — `default_exit_paths()` is the one builder for deploy, `node add`, and the wizard: Reality always, XHTTP and Hysteria2 on the domain or else the IPv4 address (IP certificate), WebSocket only with a domain. Redeploys keep saved SNI, fingerprint, and transport paths.
**Client IPs are a fleet setting** — `SetupIntent.forward_client_ips` (off: Remnawave sees only 127.0.0.1) turns on `compiler/client_ips.py`'s post-pass: streams send PROXY, a local hop strips it before the camouflage fallback, the panel takes it on `CONTROL_PROXY_TLS_PORT`, and nginx-fronted inbounds trust it. Bridges and Hysteria2 never change.
**Attested control-service bootstrap** — `control_service.py` installs the service behind the control server's nginx and saves its URL and admin token where the panel's lived (`cluster.panel`), so subscription links keep their shape; the URL takes the control domain when there is one (readiness refuses a URL on another name). Readiness also needs the saved host and a remote attestation of only the bootstrap's own inputs (`service_attestation()`; what it hashes is in `reconciler/CLAUDE.md`), so editing another server never re-runs the bootstrap. Verification reads the service and each user's delivered Xray JSON; handoff, `client` commands, and `meridian test` reach users through `control_access.access_panel()`, which serves the panel subset from the service.

## What's done well
- Downstream answers are invalidated whenever an earlier stage changes.
- Apply accepts only the exact hash shown at review.
- `plan` and `apply` run the same compiler and runtime as setup.

## Pitfalls
- Server metadata survives drafts and fleet import/export name mapping; removal drops it only after the physical server loses its final role, and moving an exit never copies its provider or payment settings to a new host.
- Pass the command's cut-off result collector through the reviewed runtime into control-user retirement; copy neither its command list nor the saved cut-off request away during driver construction.
- **Control bootstrap values come from the exit on the control server** — taking the first exit in plan order re-ran the bootstrap whenever an exit that sorts earlier was added, and fed another server's ports to the control nginx.
- **A control-domain bootstrap renders no WSS location** — the service has no WSS listener, and an empty path rendered a second `location /` to port 0, failing `nginx -t`; compiled artifacts own WSS.
- **A self-steal exit bootstraps with the default SNI** — the interim legacy 443 fallback dials the bootstrap SNI, and the operator's own name would point the server at itself.
- **Review keeps a control-service fleet on its backend** — the wizard re-asks roles without `backend` and intent files omit the default, so `review` takes it from the committed intent; otherwise re-running `setup` compiled a Remnawave plan for a service fleet.
- **The control-service bootstrap needs an agent build first** — `require_agent_build()` refuses before provisioning when this install has no agent build (a source install or a checkout without `make agent`); found at the first agent install, it left a running service with no nodes.
- Never add credential-shaped fields to `SetupDraft`.
- **The fleet document never sets what it hides** — `fleet_document.py` writes server names for `srv-*` IDs and drops transport paths (shared tokens masked as secrets); import refuses a `path:` key and carries the saved path by exit and protocol. Anything read from the file before validation guards its own shape (`exits: 42` once crashed import with a TypeError and no JSON envelope).
- Preserve relay `listen_port` and exit `egress_overrides` in both `SetupDraft.from_intent()` and `to_intent()` (and when the wizard re-asks roles); otherwise review silently drops them.
- Do not skip stages; presentation adapters must call `SetupDraftService`.
- Keep remote action checkpoints in typed cluster state, not `setup.json`.
- Inspect a cloned cluster through read-only drivers; cached plan hashes cannot detect managed remote drift.
- Control readiness reports confirmed identity/artifact drift separately from unavailable SSH, socket-table, or panel evidence.
- Approve and apply `review_hash`, never `plan.plan_hash`: a plan hash cannot see what the change removes; a missing shelf profile for a retiring server fails review.
- Apply checks every target server for Meridian 3.x/2.x artifacts and for apt/dpkg (`legacy_guard.py`) before persisting intent; V4 takes over 443 and stops old proxies, so a live old deployment must be torn down explicitly, never replaced silently, and a non-apt OS is refused before any step half-runs.
