# PWA Connection Page

Server-hosted PWA; Meridian's only connection-page implementation.

## Design decisions
**Vanilla ES5** — no dependencies or build step; `app.js` owns setup and injects `account.js` and `selfcheck.js`, so older shells receive new features.
**Served at the subscription URL** — ctld renders the shell at `/sub/<token>` with no-store config and manifest beneath it; shared assets live at content-versioned `/sub/~pwa/<version>/` (the token alphabet excludes `~`).
**Support page** — `support.html#<token>` opens the person's page; with owner contact configured it stays visible with an explicit action instead. It is no-store, outside the worker, and its server-rendered attributes carry no token.
**Owner text** — `notice.js` and `contact.js` render the notice and contact with text nodes, make no requests, and open no link before a click.
**Security** — guard versions, shapes, bounds, import schemes, and image sources; escape text and attribute quotes; validate QR base64; deep links never enter inline JS.
**No indexing** — the shell and nginx both say `noindex, nofollow, noarchive`.
**Catalog** — embedded apps render immediately; a valid shared `apps.json` may replace setup within seconds, preserving platform, app, focus, selection, and disclosures.
**Account** — same-origin `/info` on open, return, and refresh; save only status, traffic, expiry, and check time, and treat a saved answer as unverified until checked again.
**Device slots** — show fresh used/allowed counts and the full-slot rule only when `/info` includes them; keep counts in memory, never in the saved account snapshot.
**Three-step setup** — platform and app pickers drive install, import, and Connect; manual import shows a selectable URL and the app's menu path. Choices stay in memory.
**i18n** — English markup with `data-t` swaps; `language.js` runs in the head before CSS and picks saved page choice > person/fleet default > browser for `auto` > English, setting lang and dir (Persian is RTL); dates and numbers use Intl.
**Self-check** — client clock vs server Date, a probe stream, and subscription status; copied reports contain no links, hostnames, UUIDs, or device data.
**App help** — the collapsed **Not connecting?** panel holds generic app tips (a red ping is not a failure, refresh, update on launch, clock) and fetches nothing.

## What's done well
- **SW scope and privacy** — the worker caches only content-versioned shared assets; person pages, config, account checks, and probes always use the network. Old `/pwa/sw.js` URLs serve a retirement worker that clears Meridian caches and unregisters.
- **One clock threshold** — `CLOCK_WARN_MS` mirrors `core.defaults.CLOCK_DRIFT_WARN_SECONDS`, and the page says connections *can* fail.
- **Connection remarks stay verbatim** — translate protocol hints only, and show entries in subscription order.

## Pitfalls
- **Routing comes from the subscription** — setup never promises a fixed split policy; raw links carry no routing rules.
- **Nested details CSS** — use `> summary`, and suppress markers with `list-style:none`, `::-webkit-details-marker`, and `::marker`.
- **SW staleness** — develop with `meridian dev preview --watch`, which disables the worker; every `no-store` request bypasses it.
- **Catalog parity** — `_PWA_APPS` must match `website/src/data/apps.json` field for field and in order; CI validates.
- **Optional data never gates setup** — reject a bad catalog whole and ignore late replies.
- **Handler-specific encodings** — v2rayNG uses the fragment, Throne standard Base64, Stash percent encoding; an invalid action keeps the manual route.
- **No inline styles** — CSP drops `style=`; use classes.
- **Self-check requests skip caches** — a reused warm connection makes a pass weaker evidence than a freeze.
- **Country names and remarks are data** — validate ISO codes, use Intl.DisplayNames, and never put `data-t` on user data.
- **Locale state is in memory** — re-detecting after a failed localStorage write reverts the choice.
- **Clipboard recovery** — handle missing APIs, throws, and rejections, and keep selectable text.
- **Suspended imports stay disabled** — apply account state after every catalog, platform, or list update, before an iOS handler can navigate.
