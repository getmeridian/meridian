# compiler — pure finite topology compilation
## Design decisions
**Pure and deterministic** — output depends only on `SetupIntent` and a secret-free `DeploymentContract`: no I/O, secrets, clock reads, or randomness (tested). It never holds generated credentials or private Reality keys.
**Finite, reviewed resources** — only explicitly typed payloads, each with a stable logical ID, sorted dependencies, typed postconditions, an ownership marker, and a payload hash; the plan hash covers the ordered graph, resolved SSH targets, runtime pins, and renderer contract. This is not a generic provider layer.
**Control-service backend** — `meridian_backend.py` runs after the base compile and swaps panel kinds for `control_service`, `agent_runtime`, `node_workload`, `client_endpoint`, `control_user`, and `service_settings`; a routing gateway is one more agent node whose pools and routes fold into its workload. Only client-IP forwarding is refused (it changes the PROXY chain).
**One client routing list, two policies** — `delivery.DEFAULT_CLIENT_ROUTES` renders into every structured format (add a rule there, never in one template); `ru-split` (default) and `full` differ only in Russian bypasses, while LAN bypasses, QUIC blocking, and the BitTorrent direct rule stay in both. sing-box never downloads geo lists, because an unfetchable rule set stops it starting; Base64 links carry no rules.
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
- **New optional payload or intent fields use `Field(exclude_if=...)` at their default** — otherwise every existing fleet's plan hash changes and re-applies.
- **A relay's endpoint takes keys from the exit behind it** — resolve `workload_ref` through the Host's `node_ref` runtime, never `owner_ref` (the relay).
- **Certificates depend on their TCP/80 ACME rule, never on the node runtime** — Hysteria2 makes Xray load the certificate at startup, so the runtime waits for the certificate; the control bootstrap likewise depends on its public 80/443 rules.
- **Xray sniffs BitTorrent with `enabled: true`**, never in `destOverride` (invalid); keep its direct rule after UDP/443 blocking and before the catch-all.
- **Raise `TopologyCompileError` for unsupported intent**; deployment contracts are empty or cover every topology server ref exactly.
- **Build every published name through `names.py`** — length limits per format, and `host_remark()` composes flag, country, method, and fingerprint.
- **Endpoints publish only after their listener and runtime**, in `PROTOCOL_PRIORITY` order with the control server's exit first (the entry clients pick by default); Xray-JSON-only entries exclude other formats, or sing-box and Stash list duplicates.
- **Bare-IP clients send no SNI** — IP paths route on the no-SNI stream key. On the control server's 443, a TLS path joins the panel's server as locations only when the panel holds its name (its domain, or the IP of a panel without one); with a panel domain, IP paths take the no-SNI route for their own IP-certified server, since the panel's serves only the domain's certificate.
- **Geodata categories must exist in stock dat files** — an unknown `geosite:` makes Xray refuse the whole config; `geodata.py` checks server rules against `core/geodata_lists.py`, generated from the pinned Xray archive (compare both when the pin moves).
- **Every TCP path carries its fingerprint** — empty means Go's ClientHello, a DPI signature; Hysteria2 carries none.
- **Client Xray JSON's `dns` block stays unused** — the final balancer rule matches every domain first; narrowing it sends plain lookups to resolvers Russia redirects, through DIRECT.
- **Mihomo IP rules keep `no-resolve`** — local resolution leaks names, and spoofed domestic answers would match `geoip:ru`.
- **Xray server pools use `leastPing`**; never claim strict-priority failover.
- **`user:` logical IDs lowercase usernames** while the service keeps their case (`AccessIntent` refuses case-only twins).
