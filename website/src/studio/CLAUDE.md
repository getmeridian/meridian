# studio — Contract-driven local UI surface

## Design decisions

**Generated contracts first** — `generated/` is built from `contracts/meridian/v1` with `pnpm run contracts:generate`.

**Static plus local Engine** — Static Studio remains the safe fallback; executable Studio talks to `meridian studio` for localhost-only SSH, files, secrets, operations, and dry-runs.

**Adapters over contracts** — Static, LocalEngine, Mock, Desktop, and Mobile adapters consume generated contracts rather than command modules.

**Guided cockpit UX** — Keep beginner-facing Studio screens focused on one task. Use the journey rail, contextual help, and command sheet instead of always-visible JSON/CLI panels.

## What's done well

- Generated TypeScript keeps Studio aligned with Pydantic schemas without hand-written wire types.
- Static adapter logic is pure browser-safe JavaScript with Node tests; it may persist non-secret wizard progress in versioned localStorage.
- LocalEngine adapter detects `meridian studio` and uses CSRF-protected typed endpoints without breaking static hosting.
- Help copy and journey structure live in `helpCatalog.js`; reusable Studio UI fragments live under `components/`.

## Pitfalls

- Do not hand-edit `generated/`; change Python contracts, export contracts, then regenerate.
- Do not let Astro concerns leak into `meridian.core`.
- Keep Engine assumptions behind adapters; static Studio still uses generated fixtures, exported requests, copied CLI, or pasted output.
- Persist only non-secret Studio draft progress in browser storage. Never persist pasted machine output by default.
- Never include SSH passwords in draft JSON, copied commands, generated contracts, storage, or URLs.
