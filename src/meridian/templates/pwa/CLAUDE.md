# PWA Connection Page

Server-hosted Progressive Web App and the only connection-page implementation.

## Design decisions
**Vanilla ES5, no build step** — one IIFE with zero dependencies; the self-hosting rule (root `CLAUDE.md`) applies to every asset.
**Runtime config** — `config.json` is fetched at load, not baked into HTML, so credentials rotate without redeploying pages; it points at Remnawave's canonical subscription URL, which Meridian never rebuilds.
**Shared/per-client split** — static assets (`app.js`, `selfcheck.js`, `styles.css`, `sw.js`, `icon.svg`, `support.html`/`support.js`, generated `apps.json` and `probe.bin`) deploy once to `/pwa/`; per-client shell and config live in `/{uuid}/`.
**Support page** — `support.html#<short id>` is the panel's `support-url`: `support.js` reads the person's own subscription for their UUID (VLESS or Hysteria2 link, or Xray JSON) and redirects to their page, or says the page is unavailable. The id stays in the fragment, out of server and CDN logs.
**Security model** — guard config/catalog versions, shapes, bounds, schemes, and local images before rendering; escape text and attribute quotes alike. QR base64 is validated, and deep links never enter inline JS.
**App catalog** — render `config.apps` immediately; apply the shared icon-bearing `pwa/apps.json` within 5 seconds, replacing only setup and preserving selection, focus, and disclosures. Catalog edits reach old pages with the next asset upload.
**Self-check** — `selfcheck.js` (injected by `app.js`, so older shells get it) runs from the visitor's network: clock vs the server `Date` header, a stream of the 96 KB `probe.bin` (freeze window and timeouts mirror `diagnostics/blocking.py`), and the subscription's HTTP status. "Copy report" yields English text without links, hostnames, or UUIDs.
**Account information** — `account.js` reads same-origin subscription `/info` on open, return, and refresh; its saved projection holds status, traffic, expiry, and check time only, and is labelled unverified until checked again.
**i18n** — English HTML with `data-t` swaps; choose the saved locale before loading, always set lang/dir, and rebuild to return to English. Support follows the same preference.
**Three-step setup** — a native platform/app picker drives install, import (with a nearby manual URL and menu path), and plain-text Connect records in all four locales. Clash desktop guides include TUN; choices stay in memory.
**Installer alternatives** — platform-scoped records label stores, official downloads, packages, and architecture without detecting the chip; new destinations come only from Meridian's catalog or Remnawave's pinned defaults.

## What's done well
- **SW scope trick + graceful offline** — registered with `{scope: '../'}` to control sibling client dirs (needs nginx `Service-Worker-Allowed: "/"`); shell is stale-while-revalidate, config network-first with a 503 fallback.
- **Clock warnings share one threshold** — `CLOCK_WARN_MS` in `app.js` and `selfcheck.js` mirrors `core.defaults.CLOCK_DRIFT_WARN_SECONDS` (tested); the page check is skipped when the config is over an hour old, and the copy says connections *can* fail (Reality `maxTimeDiff` is unset).
- **Connection names are the subscription remarks** — rows show the Remnawave remark verbatim; only the protocol hint below it is translated.
- **Graceful extras** — wake lock for QR scanning fails quietly, the install banner auto-dismisses, and the skeleton admits a slow load after 10 s.

## Pitfalls
- **Nested `<details>` CSS** — always use the `> summary` child combinator; bare `summary` leaks parent styles into nested toggles.
- **`<details>` markers need three rules** — `list-style:none`, `::-webkit-details-marker{display:none}`, `::marker{content:none}`.
- **The SW caches stale files** — develop with `meridian dev preview --watch` (disables the SW, injects a reload script).
- **Catalog timeouts must work without AbortController** — ignore late answers as well as aborting supported requests; optional data never gates config rendering.
- **`_PWA_APPS` must match `website/src/data/apps.json`** field for field and in order, including localized guides; CI validates.
- **Deep links follow each handler lazily** — v2rayNG uses the `#fragment`, Throne takes standard Base64 of an ASCII URL, and v2rayN has no scheme; unused encodings must never break an action.
- **Stash stays an additional iOS app** — its URL is percent-encoded for `stash://install-config`; its packaged SVG monogram travels in the shared catalog, with no external icon request.
- **The CSP has no `style-src 'unsafe-inline'`** — `style=` attributes are silently dropped; use classes (CSSOM `el.style` still works).
- **Self-check requests skip every cache** — `probe.bin` and `/sub/` bypass the SW with `cache: "no-store"`; a warm connection may be reused, so a freeze is conclusive but a pass is weaker evidence.
- **Exit countries are data** — validate `config.exit_countries` ISO codes before building flags; names come from `Intl.DisplayNames` and refresh in `applyI18n`.
- **Never put `data-t` on data** — a translated key replaces the element's whole text, remark included.
- **Bad shared catalogs keep embedded apps** — reject the whole optional catalog before applying it; a text-node HTML serializer alone does not escape attribute quotes.
- **Account freshness is separate from reachability** — failed `/info` reads keep the last saved answer unverified; suspended accounts explain and dim import actions, and every `no-store` request bypasses the SW.
- **Catalog display labels need translations** — translate fixed platform labels and keep app names as data; changing platform resets the app, while catalog updates keep it if still available.
- **A locale switch must work without storage** — rendering uses the chosen language in memory; re-detecting it after a failed `localStorage` write reverts the choice.
- **Clipboard failures need a selectable fallback** — catch missing APIs, throws, and rejected promises for URLs and reports; reports never include a browser fingerprint.
