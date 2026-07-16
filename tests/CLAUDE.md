# tests

```bash
make test                              # pytest only (~1000 tests, ~22s)
make system-lab                        # multi-node Docker lab (~15 min)
uv run pytest tests/ -k "test_name"    # by pattern
```

## Test layers

| Layer | What | Where | Speed |
|-------|------|-------|-------|
| **Unit** | MockConnection steps, config parsing, cluster handling, pure system-lab contracts | `tests/` (pytest) | ~22s |
| **Deploy** | Mock-based deploy pipeline tests (setup.py orchestration, hosts, container, redeploy) | `tests/test_setup_*.py` (pytest) | <1s |
| **Command** | Mock-based command tests (node, fleet, client, recover) | `tests/test_*_commands.py` (pytest) | <1s |
| **System lab** | Real multi-node deploy via Docker: Remnawave panel+node, relay, connection tests | `tests/systemlab/` | ~15 min |

## Testing philosophy

**Integration-level verification** — tests ensure contracts hold (API returns X → we handle Y), not that every function has a unit test.
**MockConnection pattern** — `conn.when("pattern", stdout=..., rc=0)` stubs SSH commands by substring match. First match wins.

## Fixture organization

Top-level `conftest.py` provides:
- **`tmp_home`** — isolates `~/.meridian/` per test via env var + monkeypatch. Both env AND config module constants must be patched.
- **`servers_file`** — isolated v4 server-profile store.
**`tests/support/mock_connection.py`** — shared `MockConnection` class. Provision tests import via conftest fixtures; other layers import directly.

## Conventions

- **Quirk-named tests** — e.g., `test_login_uses_form_urlencoded_not_json()`. The test name IS the documentation.
- **RFC 5737 IPs** — always `198.51.100.x` for test data, never real IPs.
- **Idempotency dual-path** — provisioner tests verify both "needs change" and "already done" paths.

## Pitfalls

- **`tmp_home` must patch both** — env var AND `meridian.config` attributes.
- **MockConnection substring matching** — `when("grep", rc=1)` matches ANY command containing "grep". Use specific patterns.
- **System-lab collection** — pure `tests/systemlab/test_*.py` tests run under normal pytest; Docker runs only through `make system-lab`.
