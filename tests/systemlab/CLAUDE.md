# tests/systemlab — Multi-node Docker validation

```bash
make system-lab
```

## Design decisions

**Real services over mocks** — privileged systemd/SSH containers represent two exits, two relay hops, and a routing gateway. The controller deploys through the same CLI and SSH paths as a VPS.

**Nested Docker** — the exit node runs its own Docker daemon and pulls the pinned Remnawave backend, database, Valkey, subscription-page, and node images.

**Deterministic topology** — a fixed `172.30.0.0/24` bridge keeps SSH host keys, deploy targets, the local Pebble CA, and relay routing repeatable.

**Small fail-fast stages** — `scripts/controller-run.sh` runs bootstrap, deploy, canonical-subscription, and resilience scripts; Make always tears Compose down.

## What's done well

- Fresh deploy verifies all Remnawave containers, node `NET_ADMIN`, nginx, port 443, `cluster.yml`, and fleet connectivity.
- Client create/show/list/remove checks panel-backed isolation.
- PWA checks cover page/config delivery, shared assets, and security headers.
- Remnawave's canonical Xray document is executed directly, per endpoint and under automatic failover; no client config is rebuilt from local state.
- Redeploy must preserve Reality keys and live connectivity.
- Plan/apply covers no-op convergence, managed Host drift repair, unmanaged resource preservation, and unknown-checkpoint resume.
- Hardened redeploy verifies UFW, SSH password auth, fail2ban, teardown, and port release.

## Pitfalls

- Node API port 3010 becomes ready before Reality inbounds; keep the post-readiness grace period.
- Docker bridge addresses are private, so Xray routing can block lab-only echo targets.
- Intermediate Realm hops must never appear as canonical subscription endpoints.
- Pebble is installed for domain-mode work, but current coverage uses IP mode.
- Nested image pulls make cold runs slow; preserve BuildKit apt caches.
- `systemd` may report `degraded` inside privileged containers; this is accepted when required services are healthy.
- The controller image must copy the custom Hatch build hook before `pip install .`.
- JSON CLI assertions must read the standard envelope under `data`, not obsolete top-level command fields.
- Hysteria2 is enabled by default, so hardened exits must allow both TCP and UDP on port 443.
