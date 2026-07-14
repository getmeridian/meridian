# setup — resumable setup application service
## Design decisions
**Secret-free draft** — `setup.json` stores only validated server refs, topology choices, stage progress, and reviewed/applied hashes. Passwords, private keys, and API tokens use one-time runtime channels.

**Atomic stage boundary** — every completed or rewound stage is validated and atomically replaced with mode `0600`; only a missing file means fresh setup.

**Server shelf IDs** — human names and IPs resolve to immutable `srv-*` profile IDs before entering the draft.

**Reviewed runtime** — setup recompiles before apply, persists topology intent before mutation, shares runtime-only node secrets in memory, and verifies canonical subscriptions before handoff.

## What's done well
- Downstream answers are invalidated whenever an earlier stage changes.
- Apply accepts only the exact hash shown at review.
- Core contracts stay independent of filesystem, SSH, console, and Remnawave adapters.
- Existing `plan` and `apply` commands select the same V4 compiler/runtime whenever cluster state contains topology intent.

## Pitfalls
- Never add credential-shaped fields to `SetupDraft`.
- Do not skip stages; presentation adapters must call `SetupDraftService`.
- Keep remote action checkpoints in typed cluster state, not `setup.json`.
- Inspect a cloned cluster through read-only drivers; cached plan hashes cannot detect managed remote drift.
