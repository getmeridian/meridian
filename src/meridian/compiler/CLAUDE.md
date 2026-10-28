# compiler — pure finite topology compilation
## Design decisions
**Finite resources** — only explicitly typed V4 payloads are emitted; this is not a generic provider or Terraform layer.
**Pure and deterministic** — output depends only on `SetupIntent` and a secret-free `DeploymentContract`: no I/O, secrets, clock reads, or randomness (tested). It never holds generated credentials or private Reality keys.
**Reviewed payloads** — every resource has a stable logical ID, sorted dependencies, typed postconditions, an ownership marker, and a SHA-256 payload hash; the plan hash covers the ordered graph, resolved SSH targets, runtime pins, and renderer contract.
**Aggregate workload profiles** — each exit Profile owns its complete Inbound set; derived Inbound resources expose the profile-scoped identities bindings, Hosts, and squads need.
**One client routing list** — `delivery.py::DEFAULT_CLIENT_ROUTES` renders into supported delivery formats; protocol matchers currently target Xray JSON only. Mihomo and inline sing-box rules stay unchanged (sing-box never downloads geo lists: an unfetchable rule set stops it starting). Add a rule there, never in one template.
**Exit egress overrides are server-side** — `ExitIntent.egress_overrides` compile into the exit Profile's `routing_rules` in operator order, ahead of the default egress (`routing.exit_override_routes`); an override-only `warp` adds WARP after `direct` and sets `NodeRuntimePayload.warp`.
**Gateway rule targets** — a rule reaches an exit's bridge edge, a pool's balancer, or the gateway's own `direct`/`block` Outbound (no edge); `SetupIntent.gateway_routes()` is the one rule order, shared with the wizard summary.
**Self-steal camouflage** — `self_steal.py`: a `self_steal` Reality path falls back to a loopback nginx TLS server holding the operator's own certificate (Reality `dest`, unknown-SNI stream fallback, relays inheriting the name, gateway bridges). XHTTP/WSS on the same name use that server, and the 443 route for the name belongs to Reality.
**Capability-specific fallback** — Xray JSON, Mihomo, and sing-box (urltest; Remnawave omits XHTTP) get managed health-checked templates; Base64 gets ordered alternatives without a failover claim.
## What's done well
- Ports, tags, listener routes, and dependency order are deterministic; Realm hops are emitted downstream-first.
- Every unique server has one baseline resource, and every server mutation depends on it.
- TCP paths terminate through typed nginx artifacts; Hysteria2 stays direct UDP.
- **One camouflage per Reality inbound** — a relay whose SNI differs from its exit path gets a loopback `purpose="relay"` inbound imitating that SNI; its fallback and Hosts bind to it, so probes never see the exit's certificate.
- Routing gateway bridges use dedicated service Users and source-restricted firewall rules.
- **Fingerprints never mix in a health-checked group** — copies carry `ALT_FINGERPRINT_TAG` and get their own Xray balancer and Mihomo `exclude-filter` (TSPU escalates when one changes); sing-box skips them.
## Pitfalls
- Xray sniffs BitTorrent with `enabled: true`; never add it to `destOverride` (invalid). Keep its DIRECT rule before the VPN catch-all but after UDP/443 blocking; UDP DHT/uTP and encrypted peers are not reliably bypassed.
- **The control bootstrap depends on its public 80/443 rules** — ACME HTTP-01 runs inside it.
- **Certificates depend on their TCP/80 ACME rule, never on the node runtime** — Hysteria2 makes Xray load the certificate at startup, so the runtime waits for the certificate.
- New optional payload or intent fields use `Field(exclude_if=...)` at their default, or every existing fleet's plan hash changes and re-applies.
- Raise `TopologyCompileError` for unsupported intent; deployment contracts are empty (pure use) or cover every topology server ref exactly.
- Build every Remnawave-facing name through `names.py`: short resources ≤ 30 characters, users ≤ 36, Host remarks ≤ 100 (`host_remark()`: flag plus `ExitIntent.region` from `exit_places()`, the exit ID when a country repeats or is unknown, then method and fingerprint or transport).
- A Host is published only after its listener and node runtime, with a `view_position` in `PROTOCOL_PRIORITY` order and the control server's exit first (the entry clients pick by default).
- Xray-JSON-only Hosts (hidden edges, the automatic-fallback virtual Host) exclude every other subscription type, or sing-box and Stash list duplicates.
- One exit workload per server: a Remnawave node runtime activates only one Profile.
- Bare-IP clients send no SNI: IP paths route on the no-SNI stream key and, on the control server, join the panel TLS server as `location` artifacts.
- Client geodata must exist in stock dat files (v2fly, MetaCubeX for Mihomo); an unknown `geosite:` category makes Xray refuse the whole config. Server rules are checked by `geodata.py` against `core/geodata_lists.py`, generated from the pinned node image.
- Every TCP Host and gateway edge carries its path's `fingerprint` (empty means Go's ClientHello, a DPI signature); Reality and XHTTP publish a copy per `FINGERPRINT_COPIES`. Hysteria2 carries none.
- **Client Xray JSON's `dns` block stays unused** — the final balancer rule matches every domain before `IPIfNonMatch` needs an IP; narrow or move it and plain lookups to 1.1.1.1/8.8.8.8 (redirected in Russia) leave through DIRECT (real-Xray test).
- Mihomo IP rules keep `no-resolve`: local resolution leaks names, and spoofed domestic answers would match `geoip:ru`.
- Xray server pools use `leastPing` balancers; never claim strict-priority failover.
- Access usernames keep their case in the panel, but `user:` logical IDs lowercase them (`AccessIntent` refuses case-only twins); a raw `user:Biba` failed every review.
