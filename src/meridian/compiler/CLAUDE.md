# compiler — pure finite topology compilation
## Design decisions
**Finite resources** — only explicitly typed V4 payloads are emitted; this is not a generic provider or Terraform layer.

**Pure and deterministic** — compilation depends only on `SetupIntent`. It performs no I/O, secret generation, clock reads, or mutable observation.

**Reviewed payloads** — every resource has a stable logical ID, sorted dependencies, typed postconditions, ownership marker, and SHA-256 payload hash. The plan hash covers the ordered graph.

**Workload profiles are aggregate** — each exit Profile owns its complete typed Inbound set; derived Inbound resources expose the profile-scoped identities needed by bindings, Hosts, and squads.

**Fallback is capability-specific** — Xray JSON and Mihomo receive managed health-checked templates; Base64 receives ordered alternatives without an automatic-failover claim.

## What's done well
- Ports, tags, listener routes, and dependency order are deterministic.
- TCP paths terminate through typed nginx artifacts; Hysteria2 stays direct UDP.
- Realm hops are emitted downstream-first through graph dependencies.
- Custom relay Reality SNI is added to both the Profile’s accepted server names and the exit stream route before the relay Host.
- Routing gateway bridges use dedicated service Users and source-restricted firewall rules.
- Xray client failover uses one virtual template Host plus hidden tagged edge Hosts; Base64 and Mihomo keep normal visible alternatives.

## Pitfalls
- Never import SSH, HTTP, commands, console, config, Remnawave, or randomness here.
- Do not put generated credentials or private Reality keys in a resource plan.
- A Host is published only after its listener and node runtime dependencies.
- One exit workload per server: a Remnawave node runtime can activate only one Profile.
- Xray server pools use observatory-backed `leastPing`; do not claim unsupported strict-priority failover.
