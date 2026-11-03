# e2e — Real-browser journeys

## Design decisions
- **Opt-in suite** — `norecursedirs` excludes this directory from normal pytest; `make e2e` installs the `e2e` group and runs it explicitly, with Chromium installed separately.
- **Journeys cross surfaces** — console gestures drive a real seeded service, anonymous connection page, and VPN app HTTP client. Component contracts stay in the normal suite.
- **Reusable fixtures** — `service` is fresh per test; `clock` controls its injected clock and browser Date without stopping timers; `owner`, `visitor`, `app`, `base_url`, and `context_factory` share one service.
- **Local TLS** — use the shared throwaway certificate helper; only its certificate verification is bypassed. Settings and share URLs follow the real HTTPS contract.
- **Isolation** — contexts pin UTC, en-US, light theme, and viewport; service workers are blocked so every request reaches the network guard and live service. Worker-cache behavior belongs in a dedicated journey.
- **Failure evidence** — each context traces from creation; failures keep traces and full-page screenshots under `test-results/e2e` or `E2E_ARTIFACTS`.
- **Static demo** — `demo_url` builds once per session through the real showcase exporter; demo journeys reuse guarded visitor contexts and start with fresh adapter memory.
- **Phone matrix** — one fresh service/context per locale visits all five screens in both themes, amortizing seed and browser startup across 40 layouts.

## What's done well
- Browser failures include console errors and page errors; a deliberate HTTP refusal expects one URL/status error through `browser_faults.expect`.
- Browser contexts refuse every non-loopback request; app requests use a VPN User-Agent without owner cookies.
- Keyboard journeys inspect each tab stop and the focus destination after Add, Share, and tab navigation.
- Playwright actions and `expect` wait for observable states; no timed sleeps, DOM doubles, or route mocks.

## Pitfalls
- Keep `base_url` in the context factory dependencies so browser and trace teardown finish before the service stops; late icon requests otherwise fail only during teardown.
- The loopback server receives nginx's stripped console path; the fixture session cookie covers that test origin, while production cookies keep the secret path.
- Use fixed browser Date with live timers: pausing timers would freeze polling, reviews, and toasts.
- Wait for form dismissal or a receipt before asserting service state; the click only starts the write.
- A used bulk Undo is hidden; replay its real HTTP request to verify the second refusal without bypassing the service.
- Handshake TLS in handler threads with a deep backlog — wrapping the listening socket handshakes inside the single accept loop, and Chromium's burst of asset requests was reset (`ERR_CONNECTION_RESET`); a client dropping the handshake is gone, not an error.
- ARIA snapshots must inspect the active dialog while it is open: visually mounted controls behind a native modal are inert and have no accessibility tree.
- Demo screen journeys follow fragment links; reloading resets adapter mutations and display mode, while language survives in the URL.
- Native summary snapshots flatten into text; read their computed names through Playwright's Chromium AX session, and parse snapshot YAML before checking names containing colons.
