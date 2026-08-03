# diagnostics — Reusable server health checks

## Design decisions

**Four-state results** — SSH health checks use frozen `CheckResult`; client-side verification uses core `VerificationCheck`. Both model passed/failed/warning/skipped and carry remediation without CLI rendering.

**No CLI rendering** — checks return data; commands own Rich output. This keeps the module reusable from Engine, tests, and future Studio surfaces.

**I/O follows perspective** — server health accepts a `core.execution.ServerConnection`; external checks use bounded sockets and argv-only subprocesses. `network.py` owns low-level TLS I/O; `probe.py` selects checks from deployment facts.

## What's done well

- **shlex.quote on all interpolated values** in conn.run() calls.
- **Extracted from commands/node.py run_check** — disk, container, port, TLS, firewall checks are now testable without CLI scaffolding.

## Pitfalls

- **Do not import console, Rich, or Typer** — enforced by architecture tests as a library module.
- **Skipped evidence is not success** — aggregate verification must remain inconclusive until every required check completes.
- **Execution failure is not negative evidence** — command timeout, SSH drop, or a missing inspection tool must return skipped rather than failed.
- **Protocol shape controls checks** — do not require TCP/443 or HTTPS from a UDP-only Hysteria endpoint; full canonical traffic is the UDP authority.
- **Inspection is not validation** — unverified TLS may collect a certificate, but pass status requires separate trust, lifetime, and hostname validation.
- **Probe cancellation is prompt** — daemon workers stop accepting jobs on SIGINT so socket timeouts cannot delay process exit 130.
- **UFW port matching is line-based** — a rule for port 80 will not match port 8080 only if the line contains the exact port string; callers should be aware of broad ALLOW rules.
