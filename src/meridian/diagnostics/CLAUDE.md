# diagnostics — Reusable server health checks

## Design decisions

**One result contract** — every reported check is a core `VerificationCheck`; SSH primitives still return frozen `CheckResult` and are lifted with `evidence.check_from_result()`. `preflight.py` collects pre-deploy checks with an optional progress callback.

**No CLI rendering** — checks return data; commands own Rich output. This keeps the module reusable from Engine, tests, and future Studio surfaces.

**I/O follows perspective** — server health accepts a `core.execution.ServerConnection`; external checks use bounded sockets and argv-only subprocesses. `network.py` owns low-level TLS I/O and the child-interpreter `run_json_helper()`; `tls_probe.py` is the stage-timed flow primitive (connect → handshake → bytes until EOF or stall) every blocking/throughput check builds on; `blocking.py` turns flows into the SNI-vs-IP differential and data-stall checks and selects them from deployment facts (`blocking_jobs`, shared by `test --probe` and `check`); `probe.py` selects the rest. `images.py` reads each Remnawave container's configured image and classifies it with the setup version policy.

## What's done well

- **shlex.quote on all interpolated values** in conn.run() calls.
- **Shared by every check scope** — disk, container, port, TLS, and firewall primitives are testable without CLI scaffolding.

## Pitfalls

- **Do not import console, Rich, or Typer** — enforced by architecture tests as a library module.
- **Skipped evidence is not success** — aggregate verification must remain inconclusive until every required check completes.
- **Execution failure is not negative evidence** — command timeout, SSH drop, or a missing inspection tool must return skipped rather than failed.
- **Protocol shape controls checks** — do not require TCP/443 or HTTPS from a UDP-only Hysteria endpoint; full canonical traffic is the UDP authority.
- **Inspection is not validation** — unverified TLS may collect a certificate, but pass status requires separate trust, lifetime, and hostname validation.
- **Probe cancellation is prompt** — daemon workers stop accepting jobs on SIGINT so socket timeouts cannot delay process exit 130.
- **Relay next-hop probes use bash `/dev/tcp` under `timeout`** — never `nc` (absent on minimal images); the probe echoes its own status so SSH or tool failures stay skipped.
- **Running is not healthy** — a crash-looping container reports `Running` between restarts; read Docker's health status and treat `unhealthy` as a warning.
- **Ownership markers must be ones v4 writes** — the port-443 owner is Meridian's only when a `meridian-v4-*` stream artifact exists; the 3.x `/etc/meridian/node.yml` is never written.
- **A TLS alert is an answer, a timeout or reset is not** — classify flows by `TlsFlowResult.server_answered_tls`/`silenced`; macOS reports a pre-handshake reset as EINVAL from `getpeername()`.
- **Blocking verdicts need a control** — an unreachable real camouflage site makes the differential inconclusive, never "IP blocked"; only Meridian's own ~50 KB `pwa/app.js` on the control server is a size-known stall target, so exit-only servers get no data-flow check.
- **UFW port matching is line-based** — a rule for port 80 will not match port 8080 only if the line contains the exact port string; callers should be aware of broad ALLOW rules.
- **Unreadable images are unavailable, not drift** — a missing Docker, SSH failure, or partial `docker inspect` output yields `ServerImages.error`, never `upgrade`.
- **External checks need an outside vantage** — from the server itself, own-address connections arrive over loopback past UFW; `check_port_surface(local_vantage=True)` skips instead of reporting firewalled ports as public.
