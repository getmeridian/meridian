# Browser journeys

These tests exercise the real owner console, anonymous connection pages and VPN
app responses together. Normal `pytest` and `make test` deliberately exclude them.
No server, credentials, cloud resources, or internet access are needed during tests.

```sh
uv sync --extra dev --group e2e
uv run --group e2e playwright install chromium
make e2e
# Linux: install browser system libraries as well.
uv run --group e2e playwright install --with-deps chromium
# Diagnose one journey or record durations and seed timings.
uv run --group e2e pytest tests/e2e -k toast --durations=0 --junitxml=/tmp/e2e.xml
```

The dependency group pins a compatible Playwright minor release. After upgrading
it, repeat the browser install. CI caches the browser by the resolved version and
runs this suite only for control/PWA, harness, dependency or workflow changes.

## Reusing the harness

- `service`: a new SQLite-backed `ControlService` seeded with the invented showcase
  fleet for every test. Assert hidden outcomes through its public methods.
- `clock`: starts at 2027-01-15 12:00 UTC; `clock.advance(seconds=...)` advances the
  service and every open browser page's Date. Browser timers continue running.
- `owner`: signed-in Detailed console page, initially in English; saved changes to
  mode, theme, and language survive reload.
- `owner_session`: the same owner grant, for signed-in contexts created with a
  different browser locale or viewport through `context_factory`.
- `visitor`: separate, cookie-free browser context; navigate to the shared URL.
- `app`: independent HTTPX client with `Happ/1.0`, without owner credentials.
- `base_url`: loopback HTTPS service, using a throwaway local TLS certificate.
- `context_factory(**options)`: create additional guarded, traced contexts with
  alternate locale, viewport, or preferences; teardown belongs to the harness.
- `browser_faults.expect(regex)`: expect exactly one deliberate console/page error;
  include the URL and status for HTTP refusals. All other errors fail the test.

All browser requests must be loopback. Service workers are blocked to keep every
request observable. TLS verification is bypassed only for the local test origin.

Failure traces and screenshots go to `test-results/e2e/` (gitignored), or
`E2E_ARTIFACTS`. They contain throwaway links and sessions. Open a trace with:

```sh
uv run --group e2e playwright show-trace test-results/e2e/TEST/context-0-trace.zip
```

Some macOS agent sandboxes deny Chromium child-process Mach ports. In that
environment only, use `E2E_SINGLE_PROCESS=1 make e2e` to run full Chromium with its
renderer and GPU in the browser process, with a fresh browser for each isolated
context. CI shares one browser and uses the normal process model.

## Layout, accessibility, and public demo

`test_modes_locales_a11y.py` follows saved modes and Detailed bookmarks, guards a
language change with a live draft, measures Persian mirroring, and adds/shares a
person through the keyboard. Accessible names come from Playwright ARIA snapshots;
this is a focused browser check, not an axe audit. The phone table uses one fresh
service and context per locale, visiting five screens in both themes (40 layouts)
to avoid reseeding and relaunching a browser for every screen/theme combination.

`demo_url` exports the public demo through `scripts/build_showcase.py` once per
session into pytest's temporary directory and serves it over loopback. Demo pages
reuse `visitor`/`context_factory` for the same tracing and network/error guard.
Each journey starts a new page/context, so adapter mutations stay isolated; the
shared static recording is never edited. Screen loading follows real fragment
links, since a demo document reload intentionally resets its in-memory mutations
and display mode. Language choices travel through `?lang=` across both surfaces.
