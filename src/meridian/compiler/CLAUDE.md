# compiler — pure finite topology compilation
## Design decisions
**Finite resources** — only explicitly typed V4 payloads are emitted; this is not a generic provider or Terraform layer.

**Pure and deterministic** — compilation depends only on `SetupIntent`. It performs no I/O, secret generation, clock reads, or mutable observation.

**Reviewed payloads** — every resource has a stable logical ID, sorted dependencies, typed postconditions, ownership marker, and SHA-256 payload hash. The plan hash covers the ordered graph.

## What's done well
- Ports, tags, listener routes, and dependency order are deterministic.
- TCP paths terminate through typed nginx artifacts; Hysteria2 stays direct UDP.
- Realm hops are emitted downstream-first through graph dependencies.

## Pitfalls
- Never import SSH, HTTP, commands, console, config, Remnawave, or randomness here.
- Do not put generated credentials or private Reality keys in a resource plan.
- A Host is published only after its listener and node runtime dependencies.
