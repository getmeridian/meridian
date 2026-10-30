# control/ui — The owner's web console (M5, #95)

## Design decisions
- **New console tokens last 90 days by default** — Tokens offers 7, 30, 90 days or never, always sends the choice through the existing owner step-up, and shows expiry or an expired state alongside revocation metadata.
- **People tokens** — after the usual owner step-up, Tokens can mint the separate people capability; it manages accounts and credentials, with the same declared-person remove/reissue guard as an owner session.
- **Client routing is read-only** — Settings reads the service's `client_routing` (an omitted value is `ru-split`), explains the current policy, and prints `meridian fleet routing` to change it; owner sessions never write topology settings.
- **Served by the service, under the secret path** — `ui_routes.py` serves `index.html` at `/` and these files at `/ui/<name>`; the API lives in the same process, so there is no CORS, no build step, and no outside request (system fonts, a local icon, QR codes are SVG data URIs from `segno`).
- **Vanilla JS on one namespace** — `boot.js` sets theme and direction before paint; `core.js` owns `M.h` (the only DOM builder), `M.api`, dialogs, and formatting; each screen file adds `M.screens.*`; `app.js` runs the state machine (setup link → no owner → sign in → shell).
- **The console changes only what the service owns** — People (add, link and QR, enable/disable, expiry, limits and reset strategy, reset traffic, reissue, remove), API tokens (secret shown once, revoke), operator-owned privacy settings, and signed managed-unit restarts. Server topology, Revisions, and the fleet title stay read-only; topology screens print the `meridian` command. Server restart results sit alongside reachability, fresh runtime health, and pending convergence.
- **People the deployer declared stay the deployer's** — users in the last applied revision's intent (`ui_views.is_declared`, `declared` on each share view, `declared_users` on the fleet view); only they have a connection page. Removing one or issuing a new link shows `meridian client remove|reissue NAME`, and the service refuses both to an owner session (`declared_user`); API tokens stay allowed.
- **Same locale approach as the PWA** — `locales/{en,ru,fa,zh}.json`, English fallback, `fa` sets `dir="rtl"`; numbers and sizes are bidi-isolated.
- **Privacy and history** — Settings discloses every level before confirmation and PUT; M.api handles owner step-up. Usage charts and icons use SVG through M.h, with UTC day labels and an accessible totals table.
- **Passkeys are a seam** — `PasskeyState` reports `no_domain` on a bare IP and `not_built` otherwise; py_webauthn would add pyOpenSSL, pyasn1, and cbor2 to every CLI install, and attestation is never parsed by hand.
- **Device controls** — `devices.js` owns the person card: counts/clear in private, metadata/remove in full, no device data in off. Cap edits PATCH only `device_limit`; blank uses the fleet default. Confirmation explains that removed registrations can return; global privacy choices stay in Settings.

## What's done well
- Server cards offer a signed Xray restart with a reconnect disclosure and per-node results; the owner command route accepts only `restart_unit`, and repeated clicks before confirmation cannot queue duplicate restarts.
- Person removal offers an opt-in open-session cut-off with the affected nodes and the reconnect/core-restart disclosure; command results poll only same-origin reads, and declared people keep the CLI ownership guard.
- **CSP with no exceptions for script** — `default-src 'none'`, `script-src 'self'`, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer` on every console response; `test_control_ui_assets.py` refuses inline script, style, handlers, HTML sinks, outside URLs, and locale drift.
- **Every mutating request is same-origin twice** — `SameSite=Strict` plus the `X-Meridian-Request: 1` header `M.api` adds (and a matching `Origin`/`Sec-Fetch-Site` when sent).
- **Assets revalidate by ETag** (content hash, `no-cache`); anything holding a secret is `no-store`.

## Pitfalls
- Command polling keeps successful sibling replies, times out only a node's pending records, and stops terminal read errors with an unavailable result while preserving finished outcomes.
- People keeps rendering and polling users when fleet metadata fails, using cached names and declared markers; user-list failures still keep stale data with retry.
- A failed language switch keeps the previous catalog, language, and direction when both fetches fail; switch to English only after its catalog loads successfully.
- Both person expiry forms validate the converted UTC end of day before submitting; a local date before 2099 can cross into the legacy no-expiry range.
- A successful token response without a secret is still uncertain; refresh the metadata list and explain recovery instead of closing an empty result.
- Scroll focused fields clear of a sticky dialog footer after layout; native focus scrolling treated a covered input as visible on a phone.
- Dialogs constrain their scrolling body to dvh and safe areas, keep form actions sticky, and use unique title IDs for nested identity checks; icon-only actions need an aria-label and coarse-pointer controls need 44 px targets.
- Load locale catalogs independently so a selected-locale failure retains English; if both fail, keep startup waiting on a local retry notice whose bootstrap labels match the catalogs.
- Lock token dialog dismissal throughout issuance and reject duplicate submits; the secret lives only in its result step, and an uncertain response refreshes metadata and explains that the secret cannot be recovered.
- People and Servers poll only while visible (30 s / 10 s), stop on navigation, keep stale data with retry and bounded backoff, and accept replies through M.latestOnly; passive GETs carry X-Meridian-Passive and every request has a body-inclusive deadline.
- Quotas use GiB/TiB with an explicit Unlimited choice; validate at the field before any form submits, round decimal bytes like the CLI, and refuse new values above JavaScript's exact integer range.
- Server cards keep reachability, runtime health, and convergence separate; resource metrics and health checks come only from fresh report evidence.
- The session cookie is scoped to `ServiceConfig.public_path` (the bootstrap writes it); without one sign-in is refused (`ui_unconfigured`), since a `/` cookie would ride every subscription request.
- Guess limits are per bucket, never per source — every browser arrives as nginx's 127.0.0.1, and one shared source bucket let setup-link guesses lock sign-in and failed sign-ins lock a signed-in owner's re-check. Never trust a forwarded-address header.
- Judge every guess inside `LoginLimiter.turn()` (check, hash, count) — checking the lock before the slow hash let every concurrent request pass it before the first failure counted.
- Write what a password check grants (a session, `confirmed_at`, a new password) in the same transaction that re-reads the hash it was compared with and the session it acts for — the scrypt runs unlocked, so a reset or sign-out in between once still let the old password or an ended session through.
- A setup link travels in the URL fragment (never sent to nginx) and `auth.js` strips it with `history.replaceState` before anything else renders.
- An owner session is admin for what the console does; re-check the password (and code) before anything that hands out a credential (`OWNER_STEP_UP`), and admit it only on routes the screens call (`server.OWNER_ROUTES`, an allowlist a test derives from every `M.api` call; a borrowed browser once could seal a snapshot to its own key, and could still PUT node specs and approve revisions under the old denylist), and keep the `ui_*` tables out of snapshots.
- The fleet title is the intent's (`delivery.profile_title`, decided 2026-10-29): Settings shows it with the CLI commands that change it, and owner sessions cannot `PUT /api/settings`; an editable title was put back by the next apply.
- An unconfigured service shows its notice before a setup link's form, and `check_setup_link` refuses too; the form once took a password only for setup to answer `ui_unconfigured`.
- `M.busy` ignores a `cancelled` step-up — closing the password check once showed a generic error.
- Treat page input as hostile: decode the hash through `M.route()`'s guard (a bare `%` threw at boot, and retry reloaded the same hash) and read name maps with `M.has()` (a person named `toString` broke People).
- Saving a person PATCHes only the fields edited, and the limit field shows bytes unrounded (`M.gbText`) — a CLI quota of 4 MB rounded to "0" GB, so saving the expiry sent 0: unlimited.
- Adding a person is one `POST /api/users` carrying expiry and limits (`ControlService.create_user`, one transaction, 409 `user_exists`); create-then-PATCH left an active, unlimited person whenever the PATCH never landed, and a retry only met `user_exists`.
- Judge "declared" from the person's own share view, never only the fleet list fetched at boot — a console reissue once moved a declared person's UUID while their page stayed under the old one (the service cannot write `/var/www/private/`), and a stale list offered Remove.
- Turning a privacy level off preserves its disabled numeric defaults; invalid unfinished edits must never block lowering a level.
- Keep each script under 800 lines and every `M.t("…")` key in `en.json`; the asset tests read literal keys only.
- A screen that re-requests on every pick wraps its request in `M.latestOnly()` and labels from the value it asked for — the revision diff once showed #2's reply under "1 to 3".
- Reject invalid numeric cap input before treating blank as default; a browser's `badInput` must never clear an override.
