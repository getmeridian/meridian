# PWA Connection Page

Server-hosted Progressive Web App and the only connection-page implementation.

## Design decisions

**Vanilla ES5, no build step** — zero external dependencies, no CDN requests. Target users live in censored regions where external resources are blocked. The whole app is ~1200 lines in an IIFE.

**Runtime config** — `config.json` fetched at load, not baked into HTML. Enables server-side credential rotation without redeploying pages.

**Shared/per-client split** — static assets (`app.js`, `styles.css`, `sw.js`, `icon.svg`, generated `apps.json`) deploy once to `/pwa/`; per-client shell/config files live in `/{uuid}/`. `config.json` points at Remnawave's canonical subscription URL; Meridian never rebuilds it.

**Security model** — all user/config data goes through `escapeHtml()` (uses `textContent` via dummy div). QR base64 validated with `/^[A-Za-z0-9+/=]+$/` before `<img src>` injection. iOS deep links stored in `data-` attributes, never inline JS strings.

**App catalog** — `_PWA_APPS` entries carry `platforms` (where the app runs) and `recommended` (where the page features it); file order is the recommendation order (Happ, FlClashX, Clash Mi, Throne). The page reads the shared, icon-bearing `pwa/apps.json` and falls back to the icon-free `config.apps`, so catalog edits reach old pages with the next asset upload.

**Self-check** — `selfcheck.js` (injected by `app.js`, so shells rendered before it existed still get it) runs from the visitor's own network: clock vs the server `Date` header (warn above 60 s), a stream of the generated 96 KB `probe.bin` (freeze window and timeouts mirror `diagnostics/blocking.py`), and the subscription's HTTP status. "Copy report" yields English text with no links, hostnames, or UUIDs.

**i18n** — English is the HTML default, NOT a translation dict. Non-EN languages swap via `data-t` attributes. Switching back to EN requires full `renderPage()` re-render.

**Subscription QR hero layout** — the subscription QR (Remnawave's URL, imports ALL protocols at once) comes right after the header, with the recommended apps' "Add to" buttons below it. Everything else is secondary: apps list (collapsed for returning visitors), quick setup, individual protocol cards, self-check, stats. End users scan and go; power users expand.

## What's done well

- **SW scope trick + graceful offline** — registered with `{scope: '../'}` to control sibling client dirs (needs nginx `Service-Worker-Allowed: "/"`); shell is stale-while-revalidate, config network-first with a 503 fallback.
- **Color palettes** — six curated presets (ocean, sunset, forest, lavender, rose, slate) with auto dark/light variants. Selected via `config.color`, switches on system theme change.
- **Clock skew detection** — warns if device clock is >2min behind server. Critical for Reality/XHTTP which need tight time sync. Skipped if page was cached >1h to avoid false positives.
- **Relay routing** — per-relay protocol cards marked with a star, direct connections labeled as backup. Relay names are HTML-escaped.

## Pitfalls

- **Nested `<details>` CSS** — ALWAYS use `> summary` child combinator, never bare `summary`. Parent styles leak into nested summaries otherwise. This was a real bug.
- **`<details>` markers need THREE rules** — `list-style:none`, `::-webkit-details-marker{display:none}`, `::marker{content:none}`. Missing any one leaves native triangles in some browsers.
- **SW caches stale files** — use `--watch` mode during dev (disables SW, injects reload script).
- **`_PWA_APPS` must match `apps.json`** — every field and the order must be identical; regenerate `apps.json` from `_PWA_APPS`. CI validates.
- **Deep links follow each app's own handler** — v2rayNG names a subscription from the `#fragment` (its `name=` query is ignored), Throne's `addsub` takes standard Base64 only, and v2rayN documents no import scheme (no button).
- **The CSP has no `style-src 'unsafe-inline'`** — `style=` attributes in rendered markup are silently dropped; use classes (CSSOM `el.style` still works).
- **Self-check requests skip every cache** — `probe.bin` and `/sub/` bypass the SW and use `cache: "no-store"`; a browser may reuse the page's warm connection, so a freeze is conclusive but a pass is weaker evidence.
- **Stats path** — `/stats/{uuid}.json` (sibling, not child of client dir).
- **Wake lock, install banner, skeleton** — wake lock keeps the screen on for QR scanning and fails gracefully; the `beforeinstallprompt` banner auto-dismisses; the skeleton says "Taking longer than expected" after 10 s.
