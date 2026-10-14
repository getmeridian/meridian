# tests/provision — Provisioner step tests

## Design decisions

**Mocked**: `ServerConnection.run()` via `MockConnection` (substring-pattern matching).
**Not mocked**: step logic, `StepResult`, credential generation.
We test the step's *decision logic* — not the shell commands it runs.

**Renderer tests are tables** — `test_services.py` renders each config once into a module-level `CONFIGS` dict and checks it against `PRESENT`/`ABSENT` substring tables; add a row, not a new test, for a new directive.

## What's done well

**Idempotency dual-path** — every step test has two paths:
- **Already done** — mock returns "installed" → step returns `status="ok"`, no install called
- **Needs change** — mock returns partial state → step returns `status="changed"`, install called

This catches regressions where a step stops checking state before acting.

## Pitfalls

- **Mock rule order** — first matching pattern wins. Register more specific patterns (and failures) before shared stubs such as `test_common.py`'s `_ufw()`.
- **Unmatched commands succeed** — don't stub `rc=0` for commands that only need to succeed; a stub that matches too broadly (`"systemctl reload nginx"` inside an install-cert command) hides the path under test.
- **Provision context stays typed** — add explicit `ProvisionContext` fields for inter-step data; pipeline contract tests catch missing or misspelled fields.
