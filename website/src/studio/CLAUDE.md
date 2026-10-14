# studio — Contract-driven local UI surface

## Design decisions

**Generated contracts first** — `generated/` is built from `contracts/meridian/v1` with `pnpm run contracts:generate`.

**Schema barrel namespaces** — export each generated schema under its filename-derived namespace; flat wildcard exports collide on shared `$defs` types.

**Static plus local Engine** — Static Studio remains the safe fallback; executable Studio talks to `meridian studio` for localhost-only SSH, files, secrets, operations, and dry-runs.

**Adapters over contracts** — `staticAdapter.js`, `engineSession.js`, and `storageAdapter.js` consume generated contracts, never command modules.

**Guided cockpit UX** — Keep beginner-facing Studio screens focused on one task. Use the journey rail, contextual help, and command sheet instead of always-visible JSON/CLI panels.

## What's done well

- Generated TypeScript keeps Studio aligned with Pydantic schemas without hand-written wire types.
- Static adapter logic is pure browser-safe JavaScript with Node tests; it may persist non-secret wizard progress in versioned localStorage.
- `engineSession.js` probes health (static vs Engine), takes the Engine key from the `#k=` fragment into sessionStorage (stripped with `history.replaceState`), sends it on every call, and locks on a 401 instead of guessing.
- Operation state copy and the Cancel control's visibility live in pure `operationState.js`; the page only renders what it returns.
- Help copy and journey structure live in `helpCatalog.js`; reusable Studio UI fragments live under `components/`.

## Pitfalls

- Do not hand-edit `generated/`; change Python contracts, export contracts, then regenerate.
- Do not let Astro concerns leak into `meridian.core`.
- Build every DeployRequest (Engine calls and `deploy.json`) with `buildDeployRequestFromJourney()`; it keeps only generated-schema keys, because `DeployRequest` forbids extras and a removed option (`pq`) once made every Studio request a 422.
- Keep Engine assumptions behind adapters; static Studio still uses generated fixtures, exported requests, copied CLI, or pasted output.
- Persist only non-secret Studio draft progress in browser storage. Never persist pasted machine output by default.
- Never include SSH passwords in draft JSON, copied commands, generated contracts, storage, or URLs.
- The Engine key lives only in this tab's sessionStorage, never localStorage, drafts, logs, or a URL left in history.
- Cancel means "stop at the next safe point": keep the control disabled (not hidden) while `cancel_requested`, and render `cancelled` from `snapshot.cancellation`, including whether the server changed.
