# compiler — pure finite topology compilation
## Design decisions
**Finite resources** — only explicitly typed V4 payloads are emitted; this is not a generic provider or Terraform layer.

**Pure and deterministic** — compilation depends only on `SetupIntent` plus an explicit secret-free `DeploymentContract`. It performs no I/O, secret generation, clock reads, or mutable observation.

**Reviewed payloads** — every resource has a stable logical ID, sorted dependencies, typed postconditions, ownership marker, and SHA-256 payload hash. The plan hash covers the ordered graph, resolved SSH targets, runtime pins, and renderer contract.

**Workload profiles are aggregate** — each exit Profile owns its complete typed Inbound set; derived Inbound resources expose the profile-scoped identities needed by bindings, Hosts, and squads.

**Client routing is one typed list** — `delivery.py::DEFAULT_CLIENT_ROUTES` (private + Russian destinations direct, foreign QUIC blocked) renders into Xray JSON rules, Mihomo rule lines, and inline sing-box rules (sing-box skips downloaded geo lists: an unfetchable remote rule set stops it from starting); add a rule there, never in one template.

**Exit egress overrides are server-side** — `ExitIntent.egress_overrides` compile into the exit Profile's `routing_rules` (in operator order, ahead of the default egress and geo block) via `routing.exit_override_routes`; an override-only `warp` adds WARP after `direct` (`exit_outbound_tags`) and sets `NodeRuntimePayload.warp`. Client routes stay in `delivery.py`.

**Fallback is capability-specific** — Xray JSON, Mihomo, and sing-box (urltest; Remnawave omits XHTTP) receive managed health-checked templates; Base64 receives ordered alternatives without an automatic-failover claim.

## What's done well
- Ports, tags, listener routes, and dependency order are deterministic.
- Every unique server has one baseline resource; all server mutations depend on it.
- Deployment review binds every resolved host/user/port and pinned renderer/runtime input without storing credentials.
- TCP paths terminate through typed nginx artifacts; Hysteria2 stays direct UDP.
- Realm hops are emitted downstream-first through graph dependencies.
- Custom relay Reality SNI is added to both the Profile’s accepted server names and the exit stream route before the relay Host.
- Routing gateway bridges use dedicated service Users and source-restricted firewall rules.
- Xray client failover uses a virtual template Host plus hidden tagged edge Hosts; Base64 and Mihomo keep visible alternatives. Health-checked groups never mix fingerprints (TSPU escalates a freeze when one changes): copies carry `ALT_FINGERPRINT_TAG`, get their own Xray balancer and Mihomo `exclude-filter`, and skip sing-box.

## Pitfalls
- **The control bootstrap depends on its public 80/443 rules** — ACME HTTP-01 runs inside it; without the edges a fresh server requests its certificate with port 80 still closed.
- Never import SSH, HTTP, commands, console, config, Remnawave, or randomness here, and never put generated credentials or private Reality keys in a resource plan.
- Raise `TopologyCompileError` for unsupported intent so CLI and Engine adapters preserve a typed user failure; deployment contracts are either empty (pure compiler use) or cover every topology server ref exactly.
- Build every Remnawave-facing name through `names.py`; short resources cap at 30 characters, users at 36, Host remarks at 100 (`host_remark()`: exit place from `exit_places()` — flag plus `ExitIntent.region`, the exit ID when a country repeats or is unknown in a multi-exit fleet — then method and fingerprint or transport).
- A Host is published only after its listener and node runtime dependencies, and carries a `view_position` in `PROTOCOL_PRIORITY` order, then the control server's exit before others (path and exit IDs once put UDP Hysteria2, then "exit-2", first — the entry clients pick by default).
- Xray-JSON-only Hosts (hidden edges, the automatic-fallback virtual Host) exclude every other subscription type, or sing-box and Stash list them as duplicate servers.
- Certificate resources depend on their reviewed TCP/80 ACME firewall rule and never on the node runtime: a Hysteria2 inbound makes Xray load the certificate at startup, so that runtime waits for the certificate (a fresh `--hysteria2` deploy never converged).
- One exit workload per server: a Remnawave node runtime can activate only one Profile.
- Clients dialing a bare IP send no SNI: IP paths route on the no-SNI stream key, and on the control server they join the panel TLS server as `location` artifacts instead of stealing its route.
- Client geodata must exist in stock dat files (v2fly `geosite`/`geoip`, MetaCubeX for Mihomo); an unknown `geosite:` category makes Xray refuse the whole delivered config.
- Every TCP Host (Reality, XHTTP, WSS) and gateway edge carries its path's `fingerprint`; an empty one makes clients send the Go TLS ClientHello, a known DPI signature. Hysteria2 (QUIC) carries none. Reality and XHTTP also publish a copy per `FINGERPRINT_COPIES`, so one blocked ClientHello leaves another entry.
- Mihomo IP rules keep `no-resolve`: resolving names locally leaks them and spoofed domestic answers would match `geoip:ru`.
- Xray server pools use observatory-backed `leastPing`; do not claim unsupported strict-priority failover.
