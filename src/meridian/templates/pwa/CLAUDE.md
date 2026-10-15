# PWA Connection Page

Server-hosted Progressive Web App and the only connection-page implementation.

## Design decisions

**Vanilla ES5, no build step** — one IIFE with zero dependencies; the self-hosting rule (root `CLAUDE.md`) applies to every asset.
**Runtime config** — `config.json` is fetched at load, not baked into HTML, so credentials rotate without redeploying pages; it points at Remnawave's canonical subscription URL, which Meridian never rebuilds.
**Shared/per-client split** — static assets (`app.js`, `selfcheck.js`, `styles.css`, `sw.js`, `icon.svg`, generated `apps.json` and `probe.bin`) deploy once to `/pwa/`; per-client shell and config live in `/{uuid}/`.
**Security model** — all user/config data goes through `escapeHtml()`; QR base64 is validated before `<img src>` injection; iOS deep links live in `data-` attributes, never inline JS strings.
**App catalog** — `_PWA_APPS` entries carry `platforms` (where it runs) and `recommended` (where the page features it), in recommendation order. The page reads the shared, icon-bearing `pwa/apps.json` and falls back to `config.apps`, so catalog edits reach old pages with the next asset upload.
**Self-check** — `selfcheck.js` (injected by `app.js`, so older shells get it) runs from the visitor's network: clock vs the server `Date` header, a stream of the 96 KB `probe.bin` (freeze window and timeouts mirror `diagnostics/blocking.py`), and the subscription's HTTP status. "Copy report" yields English text without links, hostnames, or UUIDs.
**i18n** — the website's asymmetric model (`website/src/i18n/CLAUDE.md`): English HTML, `data-t` swaps, and a full `renderPage()` to return to English.
**Two-step setup card** — step 1 lists the platform's recommended apps, step 2 their "Add to" deep links; the subscription QR sits beside them on desktop and folds away on phones. Everything else is quiet panels: all connections, "Not connecting?", router guide.

## What's done well

- **SW scope trick + graceful offline** — registered with `{scope: '../'}` to control sibling client dirs (needs nginx `Service-Worker-Allowed: "/"`); shell is stale-while-revalidate, config network-first with a 503 fallback.
- **Clock warnings share one threshold** — `CLOCK_WARN_MS` in `app.js` and `selfcheck.js` mirrors `core.defaults.CLOCK_DRIFT_WARN_SECONDS` (tested); the page check is skipped when the config is over an hour old, and the copy says connections *can* fail (Reality `maxTimeDiff` is unset).
- **Connection names are the subscription remarks** — rows show the Remnawave remark verbatim; only the protocol hint below it is translated.
- **Graceful extras** — wake lock for QR scanning fails quietly, the install banner auto-dismisses, and the skeleton admits a slow load after 10 s.

## Pitfalls

- **Nested `<details>` CSS** — always use the `> summary` child combinator; bare `summary` leaks parent styles into nested toggles.
- **`<details>` markers need three rules** — `list-style:none`, `::-webkit-details-marker{display:none}`, `::marker{content:none}`.
- **The SW caches stale files** — develop with `meridian dev preview --watch` (disables the SW, injects a reload script).
- **`_PWA_APPS` must match `website/src/data/apps.json`** field for field and in order; CI validates.
- **Deep links follow each app's own handler** — v2rayNG names a subscription from the `#fragment`, Throne's `addsub` takes standard Base64 only, and v2rayN has no import scheme (no button).
- **The CSP has no `style-src 'unsafe-inline'`** — `style=` attributes are silently dropped; use classes (CSSOM `el.style` still works).
- **Self-check requests skip every cache** — `probe.bin` and `/sub/` bypass the SW with `cache: "no-store"`; a warm connection may be reused, so a freeze is conclusive but a pass is weaker evidence.
- **Exit countries are data** — validate `config.exit_countries` ISO codes before building flags; names come from `Intl.DisplayNames` and refresh in `applyI18n`.
- **Never put `data-t` on data** — a translated key replaces the element's whole text, remark included.
- **Catalog `platform` is an English label** — the shared "All platforms" value renders through `data-t="apps.all"`; any other fixed label needs its own key, never raw text on a translated page.
