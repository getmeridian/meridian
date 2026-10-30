# tests

```bash
make test                              # pytest only (~2000 tests, ~45s)
make system-lab                        # multi-node Docker lab (~15 min)
uv run pytest tests/ -k "test_name"    # by pattern
```

## Design decisions

| Layer | What | Where |
|-------|------|-------|
| **Unit** | MockConnection steps, config parsing, cluster handling, pure system-lab contracts | `tests/` |
| **Deploy** | Mock-based topology setup (runtime, editor, persistence, node container) | `tests/test_setup_*.py` |
| **Command** | Mock-based command tests (node, fleet, client, recover) | `tests/test_*_commands.py` |
| **System lab** | Real multi-node deploy via Docker: panel+node, relay, connection tests | `tests/systemlab/` |

**Integration-level verification** — tests pin contracts (API returns X → we handle Y), not one test per function.
**PWA behavior** — pytest runs plain JavaScript scenarios in Node against a small DOM/fetch/storage boundary, with no npm packages; browser layout is verified separately.
**Xray routing runtime** — `MERIDIAN_TEST_XRAY=/path/to/xray uv run pytest tests/test_client_routes_runtime.py` runs real TCP/UDP packets against loopback outbounds. CI supplies the checksum-verified pinned binary on Python 3.12; ordinary local tests skip it unless opted in. The session `xray_binary` fixture (`conftest.py`) also runs `run -test` on composed agent configs (`test_meridian_drivers.py`). `MERIDIAN_TEST_MIHOMO` (+ `MERIDIAN_TEST_MIHOMO_GEODATA`) and `MERIDIAN_TEST_SINGBOX` likewise run `mihomo -t`/`sing-box check` on the control service's documents (`test_control_subscription_formats.py`).
**MockConnection** — `conn.when("pattern", stdout=..., rc=0)` stubs SSH commands by substring; first match wins, unmatched commands succeed, so register only what differs.
**`tmp_home`** isolates a pinned Meridian home (`MERIDIAN_HOME`) per test; **`servers_file`** is an isolated server-profile store; `test_contexts.py`'s `install` fixture unpins it to exercise contexts.
**Shared doubles and builders live in `tests/support/`** — `mock_connection.py`, `CaptureReporter`, `CancellationFlag`, `fence_host.py` (an SSH fence.json double), `control_service.py` (`serve_control`: a real loopback control service), `console_dom.py` (`run_console`, `needs_console_dom`: console scenarios on the website's jsdom), and `topology.py` (`single_exit_intent`, `gateway_fleet_intent`, `resource_plan`, `firewall_resource`, `panel_mock`). Search there before writing a local intent, plan, or panel builder.

## What's done well

- **Quirk-named tests** — e.g., `test_login_uses_form_urlencoded_not_json()`. The test name IS the documentation; docstrings only explain a non-obvious why.
- **Tables over near-duplicates** — cases that differ only in inputs and expected outcome are one `@pytest.mark.parametrize` table with `ids`; each distinct case keeps its own row.
- **Idempotency dual-path** — provisioner tests verify both "needs change" and "already done" paths.
- **RFC 5737 IPs** — always `198.51.100.x` for test data, never real IPs.

## Pitfalls
- **Traffic period clocks** — pin both service and ledger UTC clocks and the injected registry clock; a fixture that set resets with wall time while asserting a fixed midnight failed as soon as the date changed. Match aligned labels by content, not an exact number of spaces.
- **Local event facts** — exercise rollback on both sides of the event insert, concurrent request actors, no-op changes, the full node live window, missing-report recovery, cursor boundaries, count/age retention, and snapshot cursor preservation; details must reject credential and raw-error fields.
- Migration tests assert the stored version equals `SCHEMA_VERSION`, never the version current when the test was added.
- **`tmp_home` must patch both** — env var AND `meridian.config` attributes; patch `meridian.config.NAME`, never a command module's copy (there are none).
- **MockConnection substring matching** — `when("grep", rc=1)` matches ANY command containing "grep". Use specific patterns.
- **Don't restate registries** — a test that copies a constant, catalog, or schema list fails on every legitimate edit; assert behaviour or let the contract drift check do it.
- **`make dead-code` flags production code only tests use** — keep system-lab-only helpers in `tests/systemlab/subscription_client.py`.
- **System-lab collection** — pure `tests/systemlab/test_*.py` tests run under normal pytest; Docker runs only through `make system-lab`.
- **Wizard prompts read `/dev/tty`** — `CliRunner` input never reaches them; drive journeys with scripted IO or `pexpect` (`test_setup_wizard_tty.py`), matching prose across terminal line wraps.
- **Stall retries keep their count, not their pauses** — an autouse fixture zeroes `stall_retry.STALL_DELAYS`, so a stubbed rc 124/255 observation runs five times; inject `StallRetry(delays=, sleep=)` to assert pauses.
- **Privacy changes are transactional** — test downgrade/audit rollback, no-op writes, history-off gaps, UTC receipt days, owner step-up races, and enabled/off snapshot restoration.
- **V4 command JSON** — validate compiled-resource plan/apply variants through their advertised envelopes, including terminal errors.
- **Pin the service clock beside hard-coded dates** — user edits enforce at `meridian.control.service.utc_now()`; reset tests dated 2026-10-29/30 failed on the real 2026-10-30.
- **Older-schema snapshots** — drop later columns as well as tables before rewinding `schema_version`; retaining a new column makes its migration fail spuriously.
- **Console DOM tests take `needs_console_dom`** — CI's 3.11/3.13/3.14 legs have no jsdom; five modules importing only `run_console` failed there with a bare exit 1. The 3.12 leg installs it and sets `MERIDIAN_TEST_CONSOLE`, so a lost install fails instead of skipping.
