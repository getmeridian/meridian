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
- **App-rule refusals are HTTP equivalence checks** — compare valid and unknown tokens through GET and HEAD on documents and personal files, with no device, event, or header-history write.
- **Quirk-named tests** — e.g. `test_fence_write_that_timed_out_after_landing_is_re_read_not_repeated()`; the name is the documentation, and docstrings only explain a non-obvious why.
- **Tables over near-duplicates** — cases that differ only in inputs and outcome are one `@pytest.mark.parametrize` table with `ids`.
- Idempotency dual-path: `tests/provision/CLAUDE.md`.

## Pitfalls
- **Console scenarios load shared builders** — load components before feature scripts, as the real page does, and set the locale's direction in partial DOM loaders.
- **Console drivers wait for enabled controls** — a visible button may still be loading its catalog; clicking it early is ignored and leaves a test waiting for a form that never opened.
- **Person doubles distinguish previews** — POST `/api/users/preview` is a read; counting it as a saved edit hides the changed-field assertion.
- **DOM facts count visible markers** — retained hidden badges keep row identity; count visible declarations rather than every mounted tag.
- **Demo route scans refuse unknown paths** — parse scoped constants initialized before the call and helper returns; pin opaque-ID exceptions to the complete source, and never drop a suffix or turn an unresolved expression into a wildcard.
- **Pinned geodata memory guards start cold** — trace file reads and complete explanations, with destinations that reach category-ru and geoip:ru (documentation ranges stop at the private lists); CI's Xray folder supplies both data files.
- **CLI locales are test-local** — reset current and requested language before each scenario, or a prior translated command makes later scripted English prompts fail.
- **CLI test state resets together** — current and requested language plus JSON and quiet modes must not survive into later command assertions.
- **Removal draft fixtures commit both ledgers** — an applied revision alone does not establish the service fence's committed topology identity.
- **Localized counts use native digits** — console assertions normalize Persian digits before comparing numeric evidence.
- **Restore command doubles supply profile evidence** — a confirmation-only scenario stubs the early compatibility check; profile scenarios use real snapshot and service state.
- **Import test doubles cover profile discovery** — previews that stub resources also supply evidence of an empty service profile map.
- **Older fixtures rewind membership before capacity** — shared helpers remove schema 24 before schema 23, preserving capacity when only membership is rewound.
- **Profile migration fixtures drop membership triggers first** — their row-version references otherwise prevent rewinding people tables; schema 24 is one self-contained membership step after capacity schema 23.
- **Policy migration fixtures drop override triggers first** — their people-version references otherwise prevent older-table rewinds; schema 25 is one self-contained step after membership schema 24.
- **The PWA DOM includes head** — locally loaded optional feature scripts need the same document parent as the real page, or a test double turns account loading into a page error.
- **Bulk and flow migrations stay ordered** — schema 20 owns people queries; schema 21 owns generations and receipts, and older fixtures rewind both.
- **Terminal test readers use poll or selectors** — full suites allocate descriptors above select's fixed limit, breaking pexpect and pipe-key reads.
- **People-query fixtures use real service pages** — DOM scenarios receive backend replies; rewound databases drop people indices and triggers before removing columns.
- **Report freshness uses service receipt time** — advancing a hello timestamp must never renew an older runtime report; pin both the receipt clock and revision in evidence tests.
- **Pinned Xray blocks private targets in freedom by default** — loopback session tests need an explicit final allow, or a target refusal can masquerade as successful revocation.
- **Capability IDs are data, not translation keys** — asset scans exclude catalog IDs from broad string matching but still require every literal `M.t()` call to resolve.
- **`tmp_home` must patch both** the env var and `meridian.config` attributes; patch `meridian.config.NAME`, never a module's copy.
- **The suite isolates the cache and drops `FORCE_COLOR`** — throttled banners would otherwise make output depend on test order, and Rich would turn plain-text assertions into ANSI escapes.
- **Output modes reset before every test** (autouse) — a leaked JSON mode made a later confirmation require `--yes`, and the quiet mode every `--json` run sets silenced a later test's stderr; read JSON from stdout apart from backup notices.
- **MockConnection matches substrings** — `when("grep", rc=1)` matches any command containing "grep"; use specific patterns.
- **Don't restate registries** — a test that copies a constant, catalog, or schema list fails on every legitimate edit; assert behaviour or let the contract drift check do it.
- **Every new admin handler needs a runnable `ROUTE_SAMPLES` entry** so the token-scope matrix exercises its permissions.
- **Pin every clock a scenario reads** — the service, the ledger, the registry, and settings each have one; tests dated near "today" failed when the real date arrived.
- **Migration replays pin receipt time before the replay** — a real receipt later than a fixed replay correctly keeps the newer activity evidence.
- **Migration tests assert the stored version equals `SCHEMA_VERSION`**, and older-schema fixtures drop later columns as well as tables before rewinding, or the migration fails spuriously.
- **Version-17 fixtures rewind both period and schedule steps** — version 18 keeps directional baselines and drops only account schedules.
- **Console scenarios travel on stdin** (`run_console` passes `-`) — Linux caps one argument at 128 KiB, so a large People scenario passed locally on macOS and failed in CI.
- **Console DOM tests take `needs_console_dom`** — legs without jsdom fail with a bare exit 1 otherwise; the 3.12 leg sets `MERIDIAN_TEST_CONSOLE`, so a lost install fails instead of skipping. Scenarios make the document visible, because hidden screens defer their requests.
- **Help text goes through `unstyle()`** — CI's 3.14 leg renders `--help` in colour, splitting options into styled runs.
- **Wizard prompts read `/dev/tty`** — `CliRunner` input never reaches them; drive journeys with scripted IO or `pexpect`.
- **Stall retries keep their count, not their pauses** — an autouse fixture zeroes the delays, so a stubbed rc 124/255 observation runs five times; inject `StallRetry(delays=, sleep=)` to assert pauses.
- **`make dead-code` flags production code only tests use** — keep system-lab-only helpers in `tests/systemlab/subscription_client.py`.
- **System-lab collection** — pure `tests/systemlab/test_*.py` tests run under normal pytest; Docker runs only through `make system-lab`.
- **Privacy-sensitive features need negative tests** — assert lower scopes read empty, events stay content-free, previews write nothing, and snapshots restore exactly what their level allows.
- **Connection-page tests** use browser HTML at `/sub/<token>`, JSON at `/info`, and token-free assets; corpus tests keep unchanged requests byte-identical.
