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
- Remnawave's canonical Xray document is executed directly; Base64 and Mihomo documents are parsed and endpoint-checked instead of treated as opaque strings.
- The `ifconfig.me` probe explicitly targets the `leastPing` pool; resilience proves both one-exit-down directions and fail-closed all-down behavior.
- Plan/apply covers no-op convergence, unmanaged preservation, and SIGKILL recovery after a real managed Host mutation but before observation.

## Pitfalls

- Node API port 3010 becomes ready before Reality inbounds; keep the post-readiness grace period.
- Keep the external connectivity probe explicitly routed to the pool; a fixed exit route makes failover assertions meaningless, while private bridge targets are blocked.
- Intermediate Realm hops must never appear as canonical subscription endpoints.
- Pebble is installed for domain-mode work, but current coverage uses IP mode.
- Run the Docker/container-DNS preflight before cold builds; Colima profiles with broken DNS need explicit resolvers.
- `systemd` may report `degraded` inside privileged containers; this is accepted when required services are healthy.
- The controller image must copy the custom Hatch build hook before `pip install .`.
- JSON CLI assertions must read the standard envelope under `data`, not obsolete top-level command fields.
- Hysteria2 is enabled by default, so hardened exits must allow both TCP and UDP on port 443.
- Keep interruption markers bounded and tied to an exact logical resource; never simulate recovery by editing `cluster.yml` checkpoints.
