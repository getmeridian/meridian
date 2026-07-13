# tests/systemlab — Multi-node Docker validation

```bash
make system-lab
```

## Design decisions

**Real services over mocks** — two privileged Debian containers run systemd and SSH. The controller deploys through the same CLI and SSH paths as a VPS.

**Nested Docker** — the exit node runs its own Docker daemon and pulls the pinned Remnawave backend, database, Valkey, subscription-page, and node images.

**Deterministic topology** — a fixed `172.30.0.0/24` bridge keeps SSH host keys, deploy targets, the local Pebble CA, and relay routing repeatable.

**One end-to-end controller** — `scripts/controller-run.sh` owns the staged lifecycle and accumulates failures so one run exposes more than the first broken assertion.

## What's done well

- Fresh deploy verifies all Remnawave containers, node `NET_ADMIN`, nginx, port 443, `cluster.yml`, and fleet connectivity.
- Client create/show/list/remove checks panel-backed isolation.
- PWA checks cover page/config delivery, shared assets, and security headers.
- Reality is tested directly and through Realm; a bogus UUID must be rejected.
- Redeploy must preserve Reality keys and live connectivity.
- Plan/apply covers convergence, intentional removal, panel drift, hybrid imperative sync, and subscription-page disable/enable.
- Hardened redeploy verifies UFW, SSH password auth, fail2ban, teardown, and port release.

## Pitfalls

- Node API port 3010 becomes ready before Reality inbounds; keep the post-readiness grace period.
- Docker bridge addresses are private, so Xray routing can block lab-only echo targets.
- Pebble is installed for domain-mode work, but current coverage uses IP mode.
- Nested image pulls make cold runs slow; preserve BuildKit apt caches.
- `systemd` may report `degraded` inside privileged containers; this is accepted when required services are healthy.
- The controller image must copy the custom Hatch build hook before `pip install .`.
