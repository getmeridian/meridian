# PWA Connection Page

Server-hosted Progressive Web App and the only connection-page implementation.

## Design decisions

**Vanilla ES5, no build step** — zero external dependencies, no CDN requests. Target users live in censored regions where external resources are blocked. The whole app is ~1000 lines in an IIFE.

**Runtime config** — `config.json` fetched at load, not baked into HTML. Enables server-side credential rotation without redeploying pages.

**Shared/per-client split** — static assets (`app.js`, `styles.css`, `sw.js`, `icon.svg`) deploy once to `/pwa/`; per-client shell/config files live in `/{uuid}/`. `config.json` points at Remnawave's canonical subscription URL; Meridian never rebuilds it.

**Security model** — all user/config data goes through `escapeHtml()` (uses `textContent` via dummy div). QR base64 validated with `/^[A-Za-z0-9+/=]+$/` before `<img src>` injection. iOS deep links stored in `data-` attributes, never inline JS strings.

**i18n** — English is the HTML default, NOT a translation dict. Non-EN languages swap via `data-t` attributes. Switching back to EN requires full `renderPage()` re-render.

**Subscription QR hero layout** — the subscription QR (encodes Remnawave's URL) is the first thing after the header. Rationale: scanning one QR imports ALL protocols at once — most users don't need to understand individual protocol cards. Deep link "Add to App" buttons sit directly below the QR for one-tap import. Everything else is secondary:
- Apps list collapsed — we assume the user already has an app installed
- Quick setup collapsed — returning users skip it
- Individual protocol cards collapsed under "Individual connections" — for advanced users or troubleshooting
- Stats at the bottom — informational, not actionable

This layout serves both sides of the UX: end-users scan the QR and go, power users expand the advanced section.

## What's done well

- **`config.json` decoupling** — credentials change without HTML redeploy.
- **SW scope trick** — registered with `{scope: '../'}` to control sibling client dirs. Requires nginx `Service-Worker-Allowed: "/"` header.
- **Graceful offline** — SW caches shell (cache-first), fetches config fresh (network-first with 503 fallback).
- **Color palettes** — six curated presets (ocean, sunset, forest, lavender, rose, slate) with auto dark/light variants. Selected via `config.color`, switches on system theme change.
- **Clock skew detection** — warns if device clock is >2min behind server. Critical for Reality/XHTTP which need tight time sync. Skipped if page was cached >1h to avoid false positives.
- **Relay routing** — per-relay protocol cards marked with a star, direct connections labeled as backup. Relay names are HTML-escaped.

## Pitfalls

- **Nested `<details>` CSS** — ALWAYS use `> summary` child combinator, never bare `summary`. Parent styles leak into nested summaries otherwise. This was a real bug.
- **`<details>` markers need THREE rules** — `list-style:none`, `::-webkit-details-marker{display:none}`, `::marker{content:none}`. Missing any one leaves native triangles in some browsers.
- **SW caches stale files** — use `--watch` mode during dev (disables SW, injects reload script).
- **`_PWA_APPS` must match `apps.json`** — platform strings must be identical. CI validates.
- **Stats path** — `/stats/{uuid}.json` (sibling, not child of client dir).
- **Wake lock** — keeps screen on while page is visible (mobile QR scanning). Fails gracefully on unsupported browsers.
- **PWA install banner** — captures `beforeinstallprompt` event. Auto-dismisses after user action.
- **Skeleton loading** — placeholder shimmer while `config.json` loads. "Taking longer than expected" message after 10s timeout.
