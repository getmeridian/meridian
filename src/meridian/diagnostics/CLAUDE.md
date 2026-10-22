# diagnostics — Reusable server health checks

## Design decisions

**One result contract** — every reported check is a core `VerificationCheck`; SSH primitives return frozen `CheckResult`, lifted with `evidence.check_from_result()`.
**No CLI rendering** — checks return data and never import console, Rich, or Typer; commands own output, so Engine and tests reuse the checks.
**I/O follows perspective** — server health runs through a `core.execution.ServerConnection`; external checks use bounded sockets and argv-only subprocesses.
**Flow checks** — `network.py` owns low-level TLS I/O and the child-interpreter `run_json_helper()`; `tls_probe.py` is the stage-timed flow primitive (connect, handshake, bytes until EOF or stall); `blocking.py` builds the SNI-vs-IP differential and data-stall checks from deployment facts (`blocking_jobs`, shared by `test --probe` and `check`); `probe.py` selects the rest.
**Log tails** — `logs.py` maps fleet roles to sources (container names from `images.py`, Realm units from `server_render`) and returns redacted `LogTail`s; a missing source or lost SSH is an error, never an empty log.
**Setup helpers** — `preflight.py` collects pre-deploy checks; `images.py` classifies each Remnawave container's image with the setup version policy; `camouflage.py` runs the pinned RealiTLScanner and picks same-subnet Reality targets (`commands/scan.py` adds only the spinner and prompt).

## What's done well

- Disk, container, port, TLS, and firewall primitives are shared by every check scope and testable without CLI scaffolding.

## Pitfalls

- **Execution failure is not negative evidence** — a command timeout, SSH drop, or missing inspection tool returns skipped, never failed.
- **Protocol shape controls checks** — never require TCP/443 or HTTPS from a UDP-only Hysteria2 endpoint; full canonical traffic is the UDP authority.
- **Inspection is not validation** — unverified TLS may collect a certificate, but a pass needs separate trust, lifetime, and hostname validation.
- **A neighbor target must resolve into the scanned subnet** — CDN-fronted names point elsewhere; check `getent ahostsv4` before the TLS 1.3 + HTTP/2 probe.
- **Neighbor scans cover at least a /24** — cloud VPSes get a `/32` point-to-point address; scanning only that found nothing, so every deploy fell back to a well-known site.
- **Relay legs run curl on the hop, never Python** — `relay.check_relay_leg` sends the chain's client SNI through `--connect-to` with the blocking stall thresholds; unreachable next hops defer to the TCP check, and a small camouflage page stays inconclusive.
- **Relay next-hop probes use bash `/dev/tcp` under `timeout`** — never `nc` (absent on minimal images); the probe echoes its own status so SSH or tool failures stay skipped.
- **Probe cancellation is prompt** — daemon workers stop taking jobs on SIGINT so socket timeouts cannot delay exit 130.
- **Running is not healthy** — a crash-looping container reports `Running` between restarts; read Docker's health status and warn on `unhealthy`.
- **Port 443 is Meridian's only with a `meridian-v4-*` stream artifact** — never trust 3.x markers.
- **A TLS alert is an answer, a timeout or reset is not** — classify flows by `TlsFlowResult.server_answered_tls`/`silenced`; macOS reports a pre-handshake reset as EINVAL from `getpeername()`.
- **Blocking verdicts need a control** — an unreachable camouflage site, or a self-steal name resolving to the server itself, makes the differential inconclusive, never "IP blocked"; only the control server's ~50 KB `pwa/app.js` is a size-known stall target, so exit-only servers get no data-flow check.
- **Unreadable images are unavailable, not drift** — missing Docker, SSH failure, or partial `docker inspect` output yields `ServerImages.error`, never `upgrade`.
- **Node-plugin readiness checks the kernel only** — `node_plugins.py` warns on a pre-5.7 kernel with a fix prefixed "Only if you use node plugins"; the node container bundles its own nftables library, so never require the host `nft` binary. An unreadable `uname` is skipped.
- **Node logs are not plain text, and the node image has no `pgrep`** — `grep -a` every log pipe (grep prints nothing for a "binary" stream) and find Xray (`rw-core`) with host-side `docker top`, not `docker exec`.
- **Log lines are attacker-influenced** — a prober's request line lands in nginx's error log; strip control characters, search with `grep -F` on a quoted pattern, and print plain text.
- **External checks need an outside vantage** — on the server itself, own-address connections bypass UFW over loopback; `check_port_surface(local_vantage=True)` skips instead of calling firewalled ports public.
