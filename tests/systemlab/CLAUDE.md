# tests/systemlab — Multi-node Docker validation

```bash
make system-lab
uv run pytest tests/systemlab/ -q
```
## Design decisions

**Real services over mocks** — privileged systemd/SSH containers represent two exits, two relay hops, and a routing gateway. The controller deploys through the same CLI and SSH paths as a VPS.

**Nested Docker** — the exit node runs its own Docker daemon and pulls the pinned Remnawave backend, database, Valkey, subscription-page, and node images.

**Deterministic topology** — a fixed `172.30.0.0/24` bridge keeps SSH host keys, deploy targets, the local Pebble CA, and relay routing repeatable.

**Small fail-fast stages** — the controller runs bootstrap, deploy, canonical-subscription, and resilience scripts; Make always tears Compose down. Pure contract tests run in normal pytest.

## What's done well

- Fresh deploy verifies the Remnawave containers, both exits, the routing gateway, and both Realm hops.
- Remnawave's single delivered Xray config is executed directly; Base64 and Mihomo documents are parsed and endpoint-checked instead of treated as opaque strings.
- The `ifconfig.me` probe explicitly targets the `leastPing` pool; resilience proves both one-exit-down directions and fail-closed all-down behavior.
- Plan/apply covers no-op convergence, unmanaged preservation, and SIGKILL recovery after a real managed Host mutation but before observation.

## Pitfalls

- A running node API precedes panel `is_connected`; preserve the stage grace period and runtime polling through the asynchronous handshake.
- Keep the external connectivity probe explicitly routed to the pool; a fixed exit route makes failover assertions meaningless, while private bridge targets are blocked.
- Intermediate Realm hops must never appear as canonical subscription endpoints.
- V4 Realm units use hashed `meridian-realm-*` names; never assert the retired `meridian-relay` unit.
- IP mode uses Pebble short-lived certificates; base images must trust its fixture CA and use the certificate-covered `pebble` hostname.
- Bridge-container DNS can pass while BuildKit fails; keep the scratch probe, repair dangling guest DNS, and exclude local Docker state from build contexts.
- Nested Docker must retain bridge NAT; disabling iptables removes the panel container's return path to sibling lab nodes.
- `systemd` may report `degraded` inside privileged containers; this is accepted when required services are healthy.
- The controller image must copy the custom Hatch build hook before `pip install .`.
- JSON CLI assertions must read the standard envelope under `data`, not obsolete top-level command fields.
- Hysteria2 is enabled by default, so hardened exits must allow both TCP and UDP on port 443.
- Keep interruption markers bounded and tied to an exact logical resource; never simulate recovery by editing `cluster.yml` checkpoints.
- Fast reruns must remove nested containers, volumes, and networks before and after validation while retaining pulled images.
- Client startup failures must never satisfy negative proxy tests; keep Xray runtime assets versioned with its binary.
- Raw TLS reachability does not qualify a Reality target; use only targets proven by authenticated Xray traffic.
