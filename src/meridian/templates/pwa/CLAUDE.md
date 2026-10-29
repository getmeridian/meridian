# PWA Connection Page

Server-hosted PWA; Meridian's only connection-page implementation.

## Design decisions
**Vanilla ES5** — no dependencies or build step; `app.js` owns setup, injecting `account.js` and `selfcheck.js` so older shells receive new features.
**Runtime config** — fetch `config.json`; keep the canonical subscription URL, never rebuild it. Shared assets deploy under `/pwa/`, client shells/config under `/{uuid}/`.
**Support page** — `support.html#<short id>` reads the person's own subscription for their VLESS/Hysteria2 UUID (or Xray JSON) and redirects to their page; the id stays out of request paths and logs.
**Security** — guard versions, shapes, bounds, import schemes, and image sources; escape text and attribute quotes. QR base64 is validated, and deep links never enter inline JS.
**No indexing** — shell robots metadata and the connection-page nginx header both say `noindex, nofollow, noarchive`.
**Catalog** — render embedded apps immediately, applying valid shared `apps.json` within 5 seconds. Replace only setup, preserving platform, app, focus, text selection, and disclosures.
**Account** — same-origin `/info` on open, return, and refresh; save only status, traffic, expiry, days left, and check time. The saved answer is unverified until checked again, independently of reachability.
**Three-step setup** — native platform/app pickers drive install, import, and localized plain-text Connect records. Manual import includes a selectable URL and the app's menu path; Clash desktop guides include TUN. Choices stay in memory.
**Install alternatives** — platform-scoped records label stores, packages, and architecture without detecting the chip; new destinations come only from the existing catalog or Remnawave's pinned defaults.
**i18n** — English markup with `data-t` swaps; choose the saved language before loading, always set lang/dir, and rebuild to return to English. Support follows the same preference; account dates/numbers use Intl, including the Persian calendar.
**Self-check** — visitor-side clock vs server Date, a 96 KB probe stream, and subscription HTTP status; copied English reports contain no links, hostnames, UUIDs, or device data.

## What's done well
- **SW scope and offline** — `{scope: '../'}` controls sibling client dirs (nginx needs `Service-Worker-Allowed: "/"`); config is network-first, shell stale-while-revalidate.
- **One clock threshold** — `CLOCK_WARN_MS` in both scripts mirrors `core.defaults.CLOCK_DRIFT_WARN_SECONDS`; skip config-based checks after an hour and say connections *can* fail.
- **Connection remarks stay verbatim** — translate protocol hints only; show three entries first and reveal the rest locally in subscription order, with an accessible total.
- **Graceful extras** — wake lock fails quietly, install banner dismisses, and the skeleton admits a slow load after 10 seconds.

## Pitfalls
- **Nested details CSS** — use `> summary`; suppress markers with `list-style:none`, `::-webkit-details-marker`, and `::marker`.
- **SW staleness** — develop with `meridian dev preview --watch`, which disables the SW and reloads changed assets; every `no-store` request bypasses the SW.
- **Catalog parity** — `_PWA_APPS` must match `website/src/data/apps.json` field for field and in order, including localized guides; CI validates.
- **Optional data never gates setup** — reject a bad catalog as a whole; ignore late replies even without AbortController.
- **Handler-specific encodings** — v2rayNG uses the fragment, Throne standard Base64 of an ASCII URL, and Stash percent encoding. Compute only used placeholders; an invalid action keeps the manual route.
- **No inline styles** — CSP drops `style=`; use classes (CSSOM `el.style` works).
- **Self-check evidence** — requests skip caches; a reused warm connection makes a pass weaker evidence than a freeze.
- **Country names are data** — validate ISO codes, use Intl.DisplayNames, and refresh after language changes; never put `data-t` on a remark or other user data.
- **Locale state is in memory** — re-detecting after a failed localStorage write reverts the choice; translate every fixed platform label, accessibility label, and error title.
- **Clipboard recovery** — catch missing APIs, throws, and rejected promises; provide selectable text for both URLs and reports.
- **Suspended imports stay disabled** — apply account state after catalog, platform, or connection-list updates, before any iOS handler can navigate.
