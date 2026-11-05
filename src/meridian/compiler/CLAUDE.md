# compiler — pure finite topology compilation
## Design decisions
**People never compile** — access retains its legacy squad label; account creation belongs to setup seeds and the service, while the old kind remains available for retirement.
**App rules travel with delivery** — compile the reviewed response list into service settings only; empty lists stay omitted and app choices never change node resources.
**Topology probes stay separate** — `probes.py` builds verification resources from the reviewed graph; the compile spine only orders them.
**Named delivery uses one representation** — LAN precedes custom rules and the preset tail; every client renderer preserves conjunctions and reports unsupported geodata without widening a match.
**Access profiles resolve authentication identities** — exit, gateway, endpoint, and inbound grants compile to stable managed inbounds; unknown references and gateway bridges fail review.
**Pure and deterministic** — output depends only on `SetupIntent` and a secret-free `DeploymentContract`: no I/O, secrets, clock reads, or randomness (tested). It never holds generated credentials or private Reality keys.
**Finite reviewed resources** — typed payloads have stable IDs, sorted dependencies, postconditions, ownership markers and hashes; the plan binds the graph, SSH targets, runtime pins and renderer contract.
**Control-service backend** — `meridian_backend.py` runs after the base compile and swaps panel kinds for `control_service`, `agent_runtime`, `node_workload`, `client_endpoint`, and `service_settings`; a routing gateway is one more agent node whose pools and routes fold into its workload. Only client-IP forwarding is refused (it changes the PROXY chain).
**One routing list** — `delivery.DEFAULT_CLIENT_ROUTES` serves every structured format; presets differ only in Russian bypasses and retain LAN, QUIC and BitTorrent rules. Never require sing-box to fetch remote lists, since failures prevent startup. Base64 carries no routing rules.
**Aggregate workload profiles** — each exit Profile owns its complete Inbound set; derived Inbound resources expose the identities bindings and endpoints need. Exit egress overrides compile into the Profile's routing rules ahead of the default egress.
**Gateway rule targets** — an exit's bridge edge, a pool's balancer, or the gateway's own `direct`/`block`; `SetupIntent.gateway_routes()` is the one rule order, shared with the wizard summary.
**Self-steal camouflage** — `self_steal.py` falls a Reality path back to a loopback nginx TLS server holding the operator's own certificate; XHTTP/WSS on that name use it, and the 443 route for the name belongs to Reality.
**Capability-specific fallback** — Xray JSON, Mihomo, and sing-box get health-checked groups; Base64 gets ordered alternatives without a failover claim.
**Server metadata stays out of workloads** — declared allowances compile into the service-settings payload only.
## What's done well
- Ports, tags, listener routes, and dependency order are deterministic; Realm hops are emitted downstream-first.
- Every unique server has one baseline resource, and every server mutation depends on it.
- **One camouflage per Reality inbound** — a relay whose SNI differs from its exit gets its own loopback inbound imitating that SNI, so probes never see the exit's certificate.
- **Fingerprints never mix in a health-checked group** — copies carry `ALT_FINGERPRINT_TAG` and get their own groups (TSPU escalates when one server's fingerprint changes); sing-box skips them.
## Pitfalls
- **Legacy backends cannot enforce response rules** — refuse nonempty app rules before compilation, including built-in policy choices, since only Meridian's service-settings resources carry them.
- **Hash grants before resolving them** — service settings carry the core declaration fingerprint; a compiled map changes when protocols or camouflage inbounds change, even when the declared grants do not.
- **Legacy backends cannot enforce profiles** — refuse named grants or memberships on Remnawave before compiling; only migration uses that backend.
- **Policies follow stored references** — settings wait for workloads and endpoints; accounts always wait for settings, or their new membership can invalidate the same apply's access fence when the last profile disappears.
**Public aliases share enforcement** — an endpoint grant includes every public connection backed by that inbound; profile previews must disclose this boundary.
- **New optional payload or intent fields use `Field(exclude_if=...)` at their default** — otherwise every existing fleet's plan hash changes and re-applies.
- **A relay's endpoint takes keys from the exit behind it** — resolve `workload_ref` through the Host's `node_ref` runtime, never `owner_ref` (the relay).
- **Certificates depend on their TCP/80 ACME rule, never on the node runtime** — Hysteria2 makes Xray load the certificate at startup, so the runtime waits for the certificate; the control bootstrap likewise depends on its public 80/443 rules.
- **Xray sniffs BitTorrent with `enabled: true`**, never in `destOverride` (invalid); keep its direct rule after UDP/443 blocking and before the catch-all.
- **Raise `TopologyCompileError` for unsupported intent**; deployment contracts are empty or cover every topology server ref exactly.
- **Build every published name through `names.py`** — format length limits and `host_remark()` keep labels consistent.
- **Endpoints publish only after their listener and runtime**, in `PROTOCOL_PRIORITY` order with the control server's exit first (the entry clients pick by default); Xray-JSON-only entries exclude other formats, or sing-box and Stash list duplicates.
- **Bare-IP clients send no SNI** — route IP paths on the no-SNI key. Join a control TLS server only when its certificate holds that name; with a panel domain, IP paths require their own IP-certified server.
- **Geodata categories must exist in stock dat files** — an unknown `geosite:` makes Xray refuse the whole config; `geodata.py` checks server rules against `core/geodata_lists.py`, generated from the pinned Xray archive (compare both when the pin moves).
- **Every TCP path carries its fingerprint** — empty means Go's ClientHello, a DPI signature; Hysteria2 carries none.
- **Client Xray JSON's `dns` block stays unused** — the final balancer rule matches every domain first; narrowing it sends plain lookups to resolvers Russia redirects, through DIRECT.
- **Mihomo IP rules keep `no-resolve`** — local resolution leaks names, and spoofed domestic answers would match `geoip:ru`.
- **Xray server pools use `leastPing`**; never claim strict-priority failover.
