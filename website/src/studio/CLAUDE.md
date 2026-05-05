# studio — Contract-driven local UI surface

## Design decisions

**Generated contracts first** — `generated/` is built from `contracts/meridian/v1` with `pnpm run contracts:generate`.

**Static first** — Studio should prove the deploy workflow as a static request builder/demo before adding localhost execution.

**Adapters over contracts** — Static, LocalEngine, Mock, Desktop, and Mobile adapters consume generated contracts rather than command modules.

## What's done well

- Generated TypeScript keeps Studio aligned with Pydantic schemas without hand-written wire types.

## Pitfalls

- Do not hand-edit `generated/`; change Python contracts, export contracts, then regenerate.
- Do not let Astro concerns leak into `meridian.core`.
- Do not add Engine assumptions to static Studio; use generated fixtures, exported requests, copied CLI, or pasted output.
