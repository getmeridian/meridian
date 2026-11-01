# tests

```bash
make test                              # pytest only
make system-lab                        # multi-node Docker lab (~15 min)
uv run pytest tests/ -k "test_name"    # by pattern
```

## Design decisions

| Layer | What | Where |
|-------|------|-------|
| **Unit** | MockConnection steps, config parsing, cluster handling, pure system-lab contracts | `tests/` |
| **Deploy** | Mock-based topology setup (runtime, editor, persistence) | `tests/test_setup_*.py` |
| **Command** | Mock-based command tests | `tests/test_*_commands.py` |
| **System lab** | Real multi-node deploy via Docker: control service, agents, relays, connection tests | `tests/systemlab/` |

**Integration-level verification** — tests pin contracts (API returns X → we handle Y), not one test per function.
**Real runtimes when available** — `MERIDIAN_TEST_XRAY`, `MERIDIAN_TEST_MIHOMO` (+ `_GEODATA`), and `MERIDIAN_TEST_SINGBOX` run real binaries against routing and delivered documents; CI's 3.12 leg supplies the pinned Xray and jsdom, and local runs skip them unless opted in.
**PWA and console behaviour** — pytest runs plain JavaScript scenarios in Node against a small DOM/fetch/storage boundary or the website's jsdom, with no npm packages of its own.
**MockConnection** — `conn.when("pattern", stdout=..., rc=0)` stubs SSH commands by substring; first match wins and unmatched commands succeed, so register only what differs.
**Shared doubles live in `tests/support/`** — `mock_connection.py`, `CaptureReporter`, `fence_host.py`, `control_service.py` (`serve_control`, a real loopback service), `console_dom.py`, and `topology.py` builders; search there before writing a local intent, plan, or panel builder. `tmp_home` isolates a pinned home per test.

## What's done well
- **Quirk-named tests** — e.g. `test_fence_write_that_timed_out_after_landing_is_re_read_not_repeated()`; the name is the documentation, and docstrings only explain a non-obvious why.
- **Tables over near-duplicates** — cases that differ only in inputs and outcome are one `@pytest.mark.parametrize` table with `ids`.
- Idempotency dual-path: `tests/provision/CLAUDE.md`.

## Pitfalls
- **`tmp_home` must patch both** the env var and `meridian.config` attributes; patch `meridian.config.NAME`, never a module's copy.
- **The suite isolates the cache and drops `FORCE_COLOR`** — throttled banners would otherwise make output depend on test order, and Rich would turn plain-text assertions into ANSI escapes.
- **Reset JSON mode after each CLI invocation** — a leaked JSON mode made a later confirmation require `--yes`; read JSON from stdout apart from backup notices.
- **MockConnection matches substrings** — `when("grep", rc=1)` matches any command containing "grep"; use specific patterns.
- **Don't restate registries** — a test that copies a constant, catalog, or schema list fails on every legitimate edit; assert behaviour or let the contract drift check do it.
- **Every new admin handler needs a runnable `ROUTE_SAMPLES` entry** so the token-scope matrix exercises its permissions.
- **Pin every clock a scenario reads** — the service, the ledger, the registry, and settings each have one; tests dated near "today" failed when the real date arrived.
- **Migration replays pin receipt time before the replay** — a real receipt later than a fixed replay correctly keeps the newer activity evidence.
- **Migration tests assert the stored version equals `SCHEMA_VERSION`**, and older-schema fixtures drop later columns as well as tables before rewinding, or the migration fails spuriously.
- **Version-17 fixtures rewind both period and schedule steps** — version 18 keeps directional baselines and drops only account schedules.
- **Console DOM tests take `needs_console_dom`** — legs without jsdom fail with a bare exit 1 otherwise; the 3.12 leg sets `MERIDIAN_TEST_CONSOLE`, so a lost install fails instead of skipping. Scenarios make the document visible, because hidden screens defer their requests.
- **Help text goes through `unstyle()`** — CI's 3.14 leg renders `--help` in colour, splitting options into styled runs.
- **Wizard prompts read `/dev/tty`** — `CliRunner` input never reaches them; drive journeys with scripted IO or `pexpect`.
- **Stall retries keep their count, not their pauses** — an autouse fixture zeroes the delays, so a stubbed rc 124/255 observation runs five times; inject `StallRetry(delays=, sleep=)` to assert pauses.
- **`make dead-code` flags production code only tests use** — keep system-lab-only helpers in `tests/systemlab/subscription_client.py`.
- **System-lab collection** — pure `tests/systemlab/test_*.py` tests run under normal pytest; Docker runs only through `make system-lab`.
- **Privacy-sensitive features need negative tests** — assert lower scopes read empty, events stay content-free, previews write nothing, and snapshots restore exactly what their level allows.
- **Connection-page tests** use browser HTML at `/sub/<token>`, JSON at `/info`, and token-free assets; corpus tests keep unchanged requests byte-identical.
