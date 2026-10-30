# control/ui — The owner's web console

## Design decisions
- **Served by the service, under the secret path** — `ui_routes.py` serves `index.html` at `/` and assets at `/ui/<name>`; the API lives in the same process, so there is no CORS, no build step, and no outside request (system fonts, local icons, QR codes as SVG data URIs from `segno`).
- **Vanilla JS on one namespace** — `boot.js` sets theme and direction before paint; `core.js` owns `M.h` (the only DOM builder), `M.api`, dialogs, and formatting; each screen file adds `M.screens.*`; `app.js` runs the state machine (setup link → no owner → sign in → shell).
- **Owner sign-in** — one owner: a scrypt password (`passwords.py`) and an optional TOTP code. `owner.py` stores sessions only as SHA-256 with idle and absolute limits, and mints single-use setup links that only the root token can request (`meridian panel`). Passive read polls (`X-Meridian-Passive`) don't extend idle time; writes always re-check session liveness in their transaction.
- **An owner session reaches `server.OWNER_ROUTES` only** — an allowlist derived by a test from every `M.api` call; node, endpoint, and fence writes, revisions, enrollment, import, and backups refuse it. Tokens it mints stay below `admin` and need a recent sign-in.
- **The console changes only what the service owns** — people, tokens, privacy, notices, fleet language, and signed unit restarts. Topology (servers, routing, contact, title, allowances) is read-only and shows the `meridian` command that changes it, because the next apply would undo a console edit.
- **People the deployer declared stay the deployer's** — users in the last applied revision's intent; removal and reissue show the CLI command, and the service refuses both to owner sessions and people tokens (`declared_user`).
- **Person drafts live only in memory** — changed forms guard navigation and dismissal with Leave/Stay, and visible reads refresh passively without replacing edits.
- **Same locale approach as the PWA** — `locales/{en,ru,fa,zh}.json`, English fallback, `fa` sets `dir="rtl"`, numbers and sizes bidi-isolated.
- **Passkeys are a seam** — `PasskeyState` reports `no_domain` on a bare IP and `not_built` otherwise; py_webauthn would add pyOpenSSL, pyasn1, and cbor2 to every CLI install, and attestation is never parsed by hand.

## What's done well
- **CSP with no script exceptions** — `default-src 'none'`, `script-src 'self'`, `frame-ancestors 'none'`, `no-referrer` on every response; `test_control_ui_assets.py` refuses inline script, style, handlers, HTML sinks, outside URLs, and locale drift.
- **Every mutating request is same-origin twice** — `SameSite=Strict` plus the `X-Meridian-Request: 1` header `M.api` adds (and matching `Origin`/`Sec-Fetch-Site` when sent).
- **Assets revalidate by content-hash ETag**; anything holding a secret is `no-store`.
- Restart and cut-off actions disclose reconnects and affected nodes before confirming, and repeated clicks cannot queue duplicates.

## Pitfalls
- **Scope the session cookie to `ServiceConfig.public_path`** — a `/` cookie would ride every subscription request; without a path, sign-in is refused (`ui_unconfigured`), and so is a setup link.
- **Guess limits are per bucket, never per source** — every browser arrives as nginx's 127.0.0.1, so `login_limits.py` keeps separate in-memory buckets for sign-in, setup links, and each session's re-checks; one shared bucket let guesses lock out a signed-in owner. Never trust a forwarded-address header.
- **Judge every guess inside `LoginLimiter.turn()`** (check, hash, count) — checking before the slow hash let concurrent guesses all pass.
- **Write what a password check grants in the transaction that re-reads the hash and session** — the scrypt runs unlocked, so a reset or sign-out in between could still let the old password through.
- **Re-check the password before anything that hands out a credential** (`OWNER_STEP_UP`) — otherwise a borrowed browser could seal a snapshot to its own key.
- **A setup link travels in the URL fragment** (never sent to nginx), and `auth.js` strips it with `history.replaceState` before rendering.
- **Treat page input as hostile** — decode the hash through `M.route()`'s guard (a bare `%` threw at boot) and read name maps with `M.has()` (a person named `toString` broke People).
- **Adding a person is one `POST /api/users` carrying expiry and limits** — create-then-PATCH left an active, unlimited person when the PATCH never landed.
- **Saving a person PATCHes only edited fields** — a rounded display value once saved a 4 MB quota back as 0 (unlimited); show bytes unrounded and fill textareas with text nodes, not a `value` attribute.
- **Refreshes never clobber an edit** — preserve edited inputs, focus, and selection; apply a mutation's result only to the screen generation that sent it.
- **Validate edited expiry as a whole-second future UTC instant before 2099** — later dates read as "never", and DST-gap local times don't exist.
- **Judge "declared" from the person's own share view**, never a list fetched at boot that may be stale.
- **Poll only while visible**, keep stale data with retry and backoff, and accept replies through `M.latestOnly()` — the revision diff once showed one request's reply under another's label.
- **Command polling keeps finished sibling results** and turns terminal read errors into an unavailable result, never a false timeout.
- **A relay's null usage is "not measured", never zero**; render billing dates as UTC calendar dates.
- **Pass literal strings to `M.confirm`** — it creates its own text node; a DOM node displays as an object label instead of the reviewed text.
- **Token issuance may be uncertain** — lock the dialog throughout; a success without a secret refreshes the list and says the secret cannot be recovered.
- **Quotas use GiB/TiB with an explicit Unlimited choice**, refusing values beyond JavaScript's exact integer range; a browser's `badInput` must never clear a cap to its default.
- **Invalid unfinished edits must never block lowering a privacy level or clearing a notice.**
- **Dialogs fit phones** — constrain the scrolling body to dvh and safe areas, keep actions sticky, scroll focused fields clear of the footer, and give coarse-pointer controls 44 px targets.
- **Locale catalogs load independently** — a failed selected locale keeps English, and a failed switch keeps the previous catalog and direction.
- **Keep each script under 800 lines and every `M.t("…")` key in `en.json`**; asset tests read literal keys only.
