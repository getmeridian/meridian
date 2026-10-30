# PWA Connection Page

Server-hosted PWA; Meridian's only connection-page implementation.

## Design decisions
- `language.js` blocks in the head before CSS/config: saved page choice > rendered person/fleet default > browser for `auto` > English; support's initial labels follow lang through CSS before its main script loads, and Persian sets RTL.
- `notice.js` renders owner text with text nodes and four local labels, hides it at expiry, and makes no requests; the existing no-store account check refreshes it in memory without adding notice text to saved account snapshots.
- `contact.js` renders bounded plain instructions and HTTPS/TG/mailto anchors with text nodes and four locale labels; it performs no fetch and opens no link before a click.
- Configured owner contact keeps the support page visible with an explicit Open connection page action; without contact the existing direct redirect remains. Its escaped server-rendered attributes carry no person token, and its HTML is no-store and excluded from the worker.
**Vanilla ES5** — no dependencies or build step; `app.js` owns setup, injecting `account.js` and `selfcheck.js` so older shells receive new features.
**Runtime config** — ctld renders the shell at `/sub/<token>` and live config/manifest beneath that token; config requests are no-store. Explicit script data paths handle the URL without a trailing slash. The canonical subscription URL is also the page URL.
**Package assets** — `/sub/~pwa/<content version>/` holds shared assets; the token alphabet excludes `~`. The stable `/sub/~pwa/sw.js` revalidates, and the package-wide content hash versions its cache and self-check report.
**Support page** — `support.html#<token>` opens the token's subscription page directly, including suspended people; the token enters the request path only when opening that page, and requests are never logged.
**Security** — guard versions, shapes, bounds, import schemes, and image sources; escape text and attribute quotes. QR base64 is validated, and deep links never enter inline JS.
**No indexing** — shell robots metadata and the connection-page nginx header both say `noindex, nofollow, noarchive`.
**Catalog** — render embedded apps immediately, applying valid shared `apps.json` within 5 seconds. Replace only setup, preserving platform, app, focus, text selection, and disclosures.
**Account** — same-origin `/info` on open, return, and refresh; save only status, traffic, expiry, days left, and check time. The saved answer is unverified until checked again, independently of reachability.
**Three-step setup** — native platform/app pickers drive install, import, and localized plain-text Connect records. Manual import includes a selectable URL and the app's menu path; Clash desktop guides include TUN. Choices stay in memory.
**Install alternatives** — platform-scoped records label stores, packages, and architecture without detecting the chip; new destinations come only from the existing catalog or Remnawave's pinned defaults.
**Preferred store labels** — name App Store or Google Play from the validated destination hostname; keep their labels localized when the platform or language changes.
**i18n** — English markup with `data-t` swaps; choose the saved language before loading, always set lang/dir, and rebuild to return to English. Support follows the same preference; account dates/numbers use Intl, including the Persian calendar.
**Self-check** — visitor-side clock vs server Date, a 96 KB probe stream, and subscription HTTP status; copied English reports contain no links, hostnames, UUIDs, or device data.

## What's done well
- **SW scope and privacy** — the worker caches only its content-versioned shared assets under `/sub/`; person pages, config, manifests, account checks, and probes always use the network. Old `/pwa/sw.js` URLs serve the retirement worker, which clears Meridian caches, unregisters, and reloads controlled pages to follow UUID redirects.
- **One clock threshold** — `CLOCK_WARN_MS` in both scripts mirrors `core.defaults.CLOCK_DRIFT_WARN_SECONDS`; skip config-based checks after an hour and say connections *can* fail.
- **Connection remarks stay verbatim** — translate protocol hints only; show three entries first and reveal the rest locally in subscription order, with an accessible total.
- **Graceful extras** — wake lock fails quietly, install banner dismisses, and the skeleton admits a slow load after 10 seconds.

## Pitfalls
- **Routing comes from the subscription** — setup never promises a fixed split policy; raw protocol links carry no routing rules.
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
