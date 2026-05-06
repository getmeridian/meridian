# studio — Contract-driven local UI surface

## Design decisions

**Generated contracts first** — `generated/` is built from `contracts/meridian/v1` with `pnpm run contracts:generate`.

**Static plus local Engine** — Static Studio remains the safe fallback; executable Studio talks to `meridian studio` for localhost-only SSH, files, secrets, operations, and dry-runs.

**Adapters over contracts** — Static, LocalEngine, Mock, Desktop, and Mobile adapters consume generated contracts rather than command modules.

## What's done well

- Generated TypeScript keeps Studio aligned with Pydantic schemas without hand-written wire types.
- Static adapter logic is pure browser-safe JavaScript with Node tests; it does not store operator input.
- LocalEngine adapter detects `meridian studio` and uses CSRF-protected typed endpoints without breaking static hosting.

## Pitfalls

- Do not hand-edit `generated/`; change Python contracts, export contracts, then regenerate.
- Do not let Astro concerns leak into `meridian.core`.
- Keep Engine assumptions behind adapters; static Studio still uses generated fixtures, exported requests, copied CLI, or pasted output.
- Do not persist deploy form state or pasted machine output in browser storage or URLs.
- Never include SSH passwords in draft JSON, copied commands, generated contracts, storage, or URLs.
