# control/ui — The owner's web console

## Design decisions
- **Served by the service, under the secret path** — `ui_routes.py` serves `index.html` at `/` and assets at `/ui/<name>`; the API shares the process, so there is no CORS, no build step, and no outside request (system fonts, local icons, QR codes as SVG data URIs from `segno`).
- **Vanilla JS on one namespace** — `boot.js` sets theme and direction before paint; `core.js` owns `M.h` (the only DOM builder), `M.api`, dialogs, and formatting; `app.js` lists screens in `M.consoleScreens` and screen scripts add `M.render*`; it runs setup link → no owner → sign in → shell.
- **One owner signs in** — a scrypt password plus an optional TOTP code or passkeys; `owner.py` stores sessions only as SHA-256 with idle and absolute limits and mints single-use setup links that only the root token requests (`meridian panel`). Passive polls (`X-Meridian-Passive`) never extend idle time.
- **An owner session reaches `server.OWNER_ROUTES` only** — a test derives the allowlist from every `M.api` call; node, endpoint, fence, revision, enrollment, import, and backup writes refuse it, and tokens it mints stay below `admin`.
- **The console changes only what the service owns** — people, tokens, privacy, notices, fleet language, and signed restarts. Topology (servers, routing, contact, title, allowances, profile grants, response rules) is read-only and points to the CLI, because the next apply would undo an edit. Key rotation, reissue, and removal of people the deployer declared stay on the CLI (`declared_user`), judged from the person's fresh share view, never the list fetched at boot.
- **Display mode is browser-local** — `M.mode()` reads Simple or Detailed and `M.chooseMode()` emits `meridian:mode`; drafts stay mounted, Detailed-only routes and tabs show the shared mode note, and leaving one goes to Overview through the navigation guard.
- **Fragments hold place** — Settings sections, person tabs, and People filters and sorting live in the URL fragment and survive reloads; a person with one visible panel gets a labelled region instead of a tab strip.
- **Drafts live only in memory** — changed forms guard navigation and dismissal with Leave/Stay, passive reads refresh without replacing edits, and person rows PATCH only their edited fields.
- **Reviews come from the service** — schedule, token, and format previews are service reads (token issuance sends the reviewed expiry); routing, profiles, and bulk actions commit one frozen hash or ID. Lifecycle forms show browser time beside UTC.
- **People are bounded service reads** — pages, facets, and selection come from the people query; a selection sends usernames or the same query, then a receipt.
- **Sharing has one view** — Overview's person picker and the person page use `share.js`; links and QR data stay in memory.
- **Help uses toggletips** — `M.tip` sits beside a term, never inside its label, and opens a click-toggled bubble through a status region; Escape returns focus, and `M.keyValue`, `M.field`, and `M.titled` take an optional tip.
- **Same locale approach as the PWA** — `locales/{en,ru,fa,zh}.json` with English fallback; `fa` sets `dir="rtl"`; numbers, percentages, sizes, and ranges use native digits and bidi isolation.
- **Passkeys verify locally** — reviewed `public_url` fixes RP and origin; required user verification, none attestation, bounded CBOR, and existing cryptography verify ES256/Ed25519. Passkeys satisfy step-up, and removing one ends other sessions.

## What's done well
- **CSP with no script exceptions** — `default-src 'none'`, `script-src 'self'`, `frame-ancestors 'none'`, `no-referrer`; `test_control_ui_assets.py` refuses inline script, style, handlers, HTML sinks, outside URLs, and locale drift.
- **Every mutating request is same-origin twice** — `SameSite=Strict` plus the `X-Meridian-Request: 1` header `M.api` adds, and matching `Origin`/`Sec-Fetch-Site` when sent.
- **Assets revalidate by content-hash ETag**; anything holding a secret is `no-store`.
- **Disruptive actions name their boundary** — restart, cut-off, reissue, and revocation receipts show affected nodes, reconnect scope, and key generation, and repeated clicks cannot queue duplicates.

## Pitfalls
- **Stay restores the edited field and selection** — navigation clicks focus their link before the guard opens; remember the draft field instead of relying on native dialog focus restoration.
- **Scope the session cookie to `ServiceConfig.public_path`** — a `/` cookie would ride every subscription request; without a path, sign-in and setup links are refused (`ui_unconfigured`).
- **Guess limits are per bucket, never per source** — every browser arrives as nginx's 127.0.0.1 and forwarded headers are untrusted; sign-in, setup links, and each session's re-checks keep separate buckets so anonymous guesses cannot lock out the owner. Judge each guess inside `LoginLimiter.turn()` (check, hash, count), since checking before the slow hash let concurrent guesses pass.
- **Write what a password check grants in the transaction that re-reads the hash and session** — the scrypt runs unlocked, so a reset in between could let the old password through.
- **Step-up guards credentials and privacy** (`OWNER_STEP_UP`: tokens, privacy, sign-in changes) — a recent password (+ code) or passkey, or a borrowed browser could seal a snapshot to its own key.
- **WebAuthn challenges bind purpose, owner handle, session, and origin** — check recoverable refusals (recent sign-in, key capacity, liveness) before consuming, or the proof dies with its challenge and step-up cannot retry; consume once before verifying; cap them per caller so anonymous traffic cannot evict a session's; zero-length handles are truthy ArrayBuffers and must read as null. Root recovery clears passkeys and changes the owner handle.
- **A setup link travels in the URL fragment**, which never reaches nginx or its logs; `auth.js` strips it with `history.replaceState` before rendering.
- **Treat page input as hostile** — decode the hash through `M.route()` (a bare `%` threw at boot) and read name maps with `M.has()` (a person named `toString` broke People).
- **Adding a person is one `POST /api/users` with expiry and limits** — create-then-PATCH left an active, unlimited person when the PATCH was lost.
- **Show bytes unrounded and PATCH only edited fields** — a rounded display once saved a 4 MB quota back as 0 (unlimited); parse quotas as BigInt and refuse values past `Number.MAX_SAFE_INTEGER`, and a browser's `badInput` must never clear a cap.
- **Textareas get text nodes** — `M.h` sets attributes, and a textarea ignores a `value` attribute, so a note editor once opened blank and saved empty.
- **Device caps have three states** — null inherits, 0 is an unlimited exemption, 1–100 caps; treating 0 as falsy turns an exemption into the fleet cap.
- **Schedule previews carry only schedule fields** — the request forbids extras, so forwarding the cap failed every quota save; an expiry row leaves the reset strategy to the service, saving cancels pending previews, and passive reads never re-POST one (it renews idle time).
- **Custom date cancels presets quietly** — send a silent change through the expiry guard so an earlier +1/+3 month reply cannot fill the custom editor, and refresh the preview when a date is already set.
- **Extending pending first-use access uses the bulk extend preview** — a calendar expiry preview sets a fixed date and would start the clock before first use.
- **Bulk expiry validates before reading** — its submit guard attaches to a form that does not exist yet, so an unchecked empty date cleared everyone's expiry to Never.
- **Validate edited expiry as a whole-second future UTC instant before 2099** — later dates read as "never", and DST-gap local times don't exist.
- **Settle a form's disabled state before `M.trackForm()`** — a field disabled after the snapshot reads as edited.
- **Invalid unfinished edits never block lowering a privacy level or clearing a notice** — review the saved values instead.
- **Uncertain replies read the record** — undo and token issuance confirm the receipt or list before reporting success, and never offer a blind retry.
- **Refreshes never clobber an edit** — keep inputs, focus, and selection; apply a reply only to the screen generation that sent it (`M.latestOnly()`), and also compare search and cursor, since a reply can land during the debounce. An expired cursor restarts at page one, and a failed page restores the last cursor.
- **Poll only while visible** — keep stale data with retry and backoff; partial failures keep usable evidence, and a terminal poll error is unavailable, never a timeout.
- **Unmeasured is not zero** — null relay usage and capacity gaps stay gaps, and peak labels use the observed peak, not the axis floor; period counters come from the person record, never from lifetime totals; `history_off` clears Overview's cached traffic window so deleted history never lingers.
- **Pass literal strings to `M.confirm`** — it sets text content, so a node shows as `[object HTMLElement]`; bind downloads to a locally created URL, since `M.h` refuses Blob hrefs.
- **Menu actions close and focus their summary before opening a dialog**, or dismissal returns focus to a hidden button.
- **Verb-labelled buttons are actions** — Enable and Disable rename themselves and never announce a pressed state; Undo sends the opposite request once, locks the toggle meanwhile, and the page button stays the keyboard path.
- **Phone targets follow width and pointer** — 44 px at the phone breakpoint and for coarse pointers; a tip keeps its 24 px face with a `::before` hit area, and its bubble resets the body font.
- **Open tips close when focus moves on** — Tab, a closing disclosure (not its summary), a mode change, or the next scroll after removal; the bubble takes focus on click (`tabindex=-1`), or a click inside it would move focus to an ancestor and Tab and Escape would stop closing it. Measure from the viewport corner, since the static position squeezes it near an edge.
- **Badge contrast includes its translucent fill** — 4.5:1 for labels and 3:1 for controls and chart marks in both themes.
- **Dialogs fit phones** — the body scrolls within dvh and safe areas, actions stay sticky, and focused fields scroll clear of the footer.
- **Scrolling rows reveal their selection** — navigation and chip rows scroll the active item into view with logical alignment for RTL.
- **Paired values follow the page direction** — ratios, before/after, and match/action pairs sit in isolated inline rows, so Persian reads the first value on the right.
- **Checkbox rows use the shared `.check-row` and `.choice-group`** — a screen-scoped rule left access profiles as raw, run-together checkboxes.
- **Locale catalogs load independently** — a failed locale keeps English, and a failed switch keeps the previous catalog and direction.
- **Keep each script under 800 lines and every `M.t("…")` key in `en.json`** — asset tests read literal keys only.
