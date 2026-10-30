# studio — Contract-driven local UI surface

## Design decisions

**Generated contracts first** — `generated/` is built from `contracts/meridian/v1` with `pnpm run contracts:generate`; field labels, help, and defaults come from the workflow contracts through `contractDefaults.js`.

**Schema barrel namespaces** — export each generated schema under its filename-derived namespace; flat wildcard exports collide on shared `$defs` types.

**Static plus local Engine** — Static Studio is the safe fallback (copy human CLI commands, download `deploy.json`, paste output); executable Studio talks to `meridian studio` for SSH, secrets, operations, and dry-runs.

**One job per module** — `journey.js` (pure state and step gates), `engineSession.js` (handshake, key, polling), `staticAdapter.js` (requests, validation, commands), `payloadPresenters.js` (one presenter per response schema), `views/` (one per screen, DOM only), `app.js` (the controller), `studio.css`. `studio.astro` is markup plus one module script.

**Bare layout** — `StudioLayout.astro` has no Nav, i18n, or inline scripts (the Engine's CSP is `script-src 'self'`); the early theme is the external `public/studio/theme.js`, and website links are absolute because the wheel ships only Studio's files.

**Guided cockpit UX** — one task per screen; the journey rail, contextual help, and native `<dialog>` command sheet replace always-visible JSON/CLI panels.

## What's done well
- Generated preview types compose the shared selector and endpoint contracts, preserving header bounds and credential-free response fields.
- Generated client and user types retain nullable language overrides and the shared four-code enum from Python.
- Generated notice types preserve Unicode text bounds and the explicit preview/save confirmation field from Python.
- Generated delivery and contact models retain the optional public contact and canonical-link constraints from Python; Studio does not define another presentation contract.
- Generated person contracts preserve private metadata bounds and scoped-read descriptions without embedding operator data in Studio assets.

- Every module is `// @ts-check` with JSDoc types from `generated/`, so `astro check` catches undefined names and wire-type drift.
- `engineSession.js` takes the Engine key from the `#k=` fragment into sessionStorage (stripped with `history.replaceState`), re-handshakes after a restart, and locks on a 401 instead of guessing.
- Pure modules have Node tests (`pnpm run studio:test`, run by `pnpm run build`; fetch mocked, views under jsdom) — no Playwright needed.

## Pitfalls
- Preserve `ServerUsage.measurement` alongside nullable usage/period fields when consuming generated fleet contracts; null usage must not become a measured zero.

- Do not hand-edit `generated/`; change Python contracts, export contracts, then regenerate.
- Build every DeployRequest (Engine calls and `deploy.json`) with `buildDeployRequestFromJourney()`; it keeps only generated-schema keys, because `DeployRequest` forbids extras and a removed option (`pq`) once made every Studio request a 422.
- Apply defaults when building the request, never into the draft: a field the user cleared stays cleared.
- Typing updates state only; validation shows on blur or submit, and the timeline only appends (re-rendering closed every open Details).
- Presenters branch on the error `category`, never on message wording.
- A preview lives in memory with the signature of the request it planned; never persist it, and never enable Start deploy unless it matches the current request.
- Text reaches the DOM only through `textContent` (`dom.js`); a test rejects HTML sinks.
- Persist only non-secret draft progress; never persist pasted output, and never put SSH passwords in drafts, commands, storage, or URLs.
- The Engine key lives only in this tab's sessionStorage, never localStorage, drafts, logs, or a URL left in history.
- Cancel means "stop at the next safe point": keep the control disabled (not hidden) while `cancel_requested`, and render `cancelled` from `snapshot.cancellation`, including whether the server changed.
