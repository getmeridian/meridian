# control/ui — The owner's web console (M5, #95)

## Design decisions
- **Served by the service, under the secret path** — `ui_routes.py` serves `index.html` at `/` and these files at `/ui/<name>`; the API lives in the same process, so there is no CORS, no build step, and no outside request (system fonts, a local icon, QR codes are SVG data URIs from `segno`).
- **Vanilla JS on one namespace** — `boot.js` sets theme and direction before paint; `core.js` owns `M.h` (the only DOM builder), `M.api`, dialogs, and formatting; each screen file adds `M.screens.*`; `app.js` runs the state machine (setup link → no owner → sign in → shell).
- **The console changes only what the service owns** — People (add, link and QR, enable/disable, expiry, limits and reset strategy, reset traffic, reissue, remove), API tokens (secret shown once, revoke), and the fleet title. Servers and Revisions are read-only, and topology screens print the `meridian` command instead of offering a button.
- **People the deployer declared stay the deployer's** — users in the last applied revision's intent (`ui_views.is_declared`, `declared` on each share view, `declared_users` on the fleet view); only they have a connection page. Removing one or issuing a new link shows `meridian client remove|reissue NAME`, and the service refuses both to an owner session (`declared_user`); API tokens stay allowed.
- **Same locale approach as the PWA** — `locales/{en,ru,fa,zh}.json`, English fallback, `fa` sets `dir="rtl"`; numbers and sizes are bidi-isolated.
- **Passkeys are a seam** — `PasskeyState` reports `no_domain` on a bare IP and `not_built` otherwise; py_webauthn would add pyOpenSSL, pyasn1, and cbor2 to every CLI install, and attestation is never parsed by hand.

## What's done well
- **CSP with no exceptions for script** — `default-src 'none'`, `script-src 'self'`, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer` on every console response; `test_control_ui_assets.py` refuses inline script, style, handlers, HTML sinks, outside URLs, and locale drift.
- **Every mutating request is same-origin twice** — `SameSite=Strict` plus the `X-Meridian-Request: 1` header `M.api` adds (and a matching `Origin`/`Sec-Fetch-Site` when sent).
- **Assets revalidate by ETag** (content hash, `no-cache`); anything holding a secret is `no-store`.

## Pitfalls
- The session cookie is scoped to `ServiceConfig.public_path` (the bootstrap writes it); without one sign-in is refused (`ui_unconfigured`), since a `/` cookie would ride every subscription request.
- Guess limits are per bucket, never per source — every browser arrives as nginx's 127.0.0.1, and one shared source bucket let setup-link guesses lock sign-in and failed sign-ins lock a signed-in owner's re-check. Never trust a forwarded-address header.
- Judge every guess inside `LoginLimiter.turn()` (check, hash, count) — checking the lock before the slow hash let every concurrent request pass it before the first failure counted.
- Write what a password check grants (a session, `confirmed_at`, a new password) in the same transaction that re-reads the hash it was compared with and the session it acts for — the scrypt runs unlocked, so a reset or sign-out in between once still let the old password or an ended session through.
- A setup link travels in the URL fragment (never sent to nginx) and `auth.js` strips it with `history.replaceState` before anything else renders.
- An owner session is admin for what the console does; re-check the password (and code) before anything that hands out a credential (`OWNER_STEP_UP`), and admit it only on routes the screens call (`server.OWNER_ROUTES`, an allowlist a test derives from every `M.api` call; a borrowed browser once could seal a snapshot to its own key, and could still PUT node specs and approve revisions under the old denylist), and keep the `ui_*` tables out of snapshots.
- The fleet title is intent-owned today: `ServiceSettingsDriver` puts the deployer's back on the next apply, which the Settings screen says.
- An unconfigured service shows its notice before a setup link's form, and `check_setup_link` refuses too; the form once took a password only for setup to answer `ui_unconfigured`.
- `M.busy` ignores a `cancelled` step-up — closing the password check once showed a generic error.
- Treat page input as hostile: decode the hash through `M.route()`'s guard (a bare `%` threw at boot, and retry reloaded the same hash) and read name maps with `M.has()` (a person named `toString` broke People).
- Saving a person PATCHes only the fields edited, and the limit field shows bytes unrounded (`M.gbText`) — a CLI quota of 4 MB rounded to "0" GB, so saving the expiry sent 0: unlimited.
- Adding a person is one `POST /api/users` carrying expiry and limits (`ControlService.create_user`, one transaction, 409 `user_exists`); create-then-PATCH left an active, unlimited person whenever the PATCH never landed, and a retry only met `user_exists`.
- Judge "declared" from the person's own share view, never only the fleet list fetched at boot — a console reissue once moved a declared person's UUID while their page stayed under the old one (the service cannot write `/var/www/private/`), and a stale list offered Remove.
- Keep each script under 800 lines and every `M.t("…")` key in `en.json`; the asset tests read literal keys only.
