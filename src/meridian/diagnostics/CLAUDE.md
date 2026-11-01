# diagnostics — Reusable server health checks

## Design decisions
**One result contract** — every reported check is a core `VerificationCheck`; SSH primitives return frozen `CheckResult`, lifted with `evidence.check_from_result()`.
**No CLI rendering** — checks return data and never import console, Rich, or Typer; commands own output.
**I/O follows perspective** — server health runs through a `ServerConnection`; external checks use bounded sockets and argv-only subprocesses.
**Flow checks** — `network.py` owns low-level TLS I/O, `tls_probe.py` the stage-timed flow (connect, handshake, bytes until EOF or stall), and `blocking.py` the SNI-vs-IP differential and data-stall checks shared by `test --probe` and `check`.
**Log tails** — `logs.py` maps roles to their units and nginx's error log and returns redacted tails; a missing source or lost SSH is an error, never an empty log.
**Control-service health** — `control_service.py` checks the units and judges each agent from its `NodeStatus`.
**Setup helpers** — `preflight.py` collects pre-deploy checks; `camouflage.py` runs the pinned RealiTLScanner and picks same-subnet Reality targets.

## Pitfalls
- **Execution failure is not negative evidence** — a timeout, SSH drop, or missing tool returns skipped, never failed; agent findings need a fresh report.
- **Protocol shape controls checks** — never require TCP/443 or HTTPS from a UDP-only Hysteria2 endpoint.
- **Probe Meridian's certificate as clients ask for it** — an IP goes with no SNI (an IP SNI reaches the camouflage site, so `check` long judged Google's certificate), and expiry warns relative to lifetime.
- **A certificate check verifies the name, and `check` reads every managed name on a port** — dates alone, on the first route only, passed while a panel domain's certificate served the server's IP (#110).
- **Validating TLS probes trust `SSL_CERT_FILE` before certifi**, as httpx does — the system lab's private CA reaches Python only through it.
- **Inspection is not validation** — unverified TLS may collect a certificate, but a pass needs trust, lifetime, and hostname checks; trust comes from `certifi.where()`, because uv's Pythons miss the macOS roots.
- **Neighbour targets must resolve into the scanned subnet, and scans cover at least a /24** — CDN names point elsewhere, and a VPS's `/32` alone found nothing.
- **Relay legs run curl on the hop, never Python**, and next-hop probes use bash `/dev/tcp` under `timeout`, never `nc` (absent on minimal images).
- **Probe cancellation is prompt** — workers stop on SIGINT so socket timeouts cannot delay exit 130.
- **Port 443 is Meridian's only with a `meridian-v4-*` stream artifact** — never trust 3.x markers.
- **A TLS alert is an answer, a timeout or reset is not** — classify by `server_answered_tls`/`silenced`; macOS reports a pre-handshake reset as EINVAL.
- **Blocking verdicts need a control** — an unreachable camouflage site or a self-steal name resolving to the server itself makes the differential inconclusive, never "IP blocked"; only the control server serves a size-known stall target.
- **Log lines are attacker-influenced** — strip control characters, search with `grep -F` on a quoted pattern, and print plain text.
- **Encrypted DNS is judged per operator** — a single-stack host fails the other family's addresses, so only an operator with no answer warns.
- **External checks need an outside vantage** — on the server itself, connections bypass the firewall over loopback; `local_vantage=True` skips instead of calling ports public.
