# diagnostics — Reusable server health checks

## Design decisions

**CheckResult is a frozen dataclass** — status is a Literal, not a bool. Four states (passed/failed/warning/skipped) cover partial-info and degraded scenarios that a bool cannot express. `remediation` field gives callers an actionable fix string without hardcoding UI copy in the check.

**No CLI rendering** — checks return data; commands own Rich output. This keeps the module reusable from Engine, tests, and future Studio surfaces.

**conn.run() is the only I/O** — checks accept a connection-like object (satisfies `core.execution.ServerConnection` protocol). Works with MockConnection in tests.

## What's done well

- **shlex.quote on all interpolated values** in conn.run() calls.
- **Extracted from commands/node.py run_check** — disk, container, port, TLS, firewall checks are now testable without CLI scaffolding.

## Pitfalls

- **Do not import console, Rich, or Typer** — enforced by architecture tests as a library module.
- **UFW port matching is line-based** — a rule for port 80 will not match port 8080 only if the line contains the exact port string; callers should be aware of broad ALLOW rules.
