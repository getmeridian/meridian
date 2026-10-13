# tests/systemlab — Multi-node Docker validation

```bash
make system-lab
uv run pytest tests/systemlab/ -q
```
## Design decisions

**Real services over mocks** — privileged systemd/SSH containers stand in for two exits, two relay hops, and a routing gateway; the controller deploys through the same CLI and SSH paths as a VPS.
**Nested Docker** — exits run their own Docker daemon and pull the pinned Remnawave backend, database, Valkey, subscription-page, and node images.
**Deterministic topology** — a fixed `172.30.0.0/24` bridge keeps SSH host keys, deploy targets, the local Pebble CA, and relay routing repeatable.
**Small fail-fast stages** — the controller runs bootstrap, single deploy, fleet deploy, subscription, resilience, and removal scripts under `scripts/stages/`; Make always tears Compose down. Pure contract tests run in normal pytest.

## What's done well

- Fresh deploy verifies the Remnawave containers, both exits, the routing gateway, and both Realm hops.
- Remnawave's delivered Xray config is executed directly; Base64 and Mihomo documents are parsed and endpoint-checked, never treated as opaque strings.
- Resilience proves both one-exit-down directions and fail-closed all-down behavior through the `leastPing` pool.
- Plan/apply covers no-op convergence, unmanaged preservation, and SIGKILL recovery after a real Host mutation but before observation.
- Removal checks both relays, removes the relay chain and exit B, and diffs active bindings (`assert-retired.py`) so every retired resource is proven absent.

## Pitfalls
- **Debug one stage, not the lab** — `SYSTEMLAB_KEEP=1 SYSTEMLAB_STAGES="10-deploy.sh 40-removal.sh"` keeps the fleet up; rerun a stage with `docker compose exec controller bash /workspace/tests/systemlab/scripts/stages/40-removal.sh` (copy edited scripts in with `docker compose cp`).
- `scripts/panel-ui-check.py` is the only browser-level proof of the panel UI behind the secret path; run it locally after every backend bump, never in CI. The UI's own `ungh.cc` fetches are Remnawave's, not a Meridian leak.
- A running node API precedes panel `is_connected`; keep the stage grace period and runtime polling.
- Route the external connectivity probe explicitly to the pool; a fixed exit route makes failover assertions meaningless.
- Intermediate Realm hops must never appear as canonical subscription endpoints.
- Images trust the fixture CA for Pebble's HTTPS endpoint; the controller must also install Pebble's runtime issuance root before verified panel access.
- Preflight must prove a real base-image pull (bridge DNS can pass while the daemon resolver fails), repair DNS only for the dedicated Meridian Colima profile, and keep local Docker state out of build contexts.
- Nested Docker must keep bridge NAT, or the panel container loses its return path to sibling nodes.
- `systemd` may report `degraded` inside privileged containers; accept it when required services are healthy.
- The controller image must copy the Hatch build hook before `pip install .`.
- Hardened exits must allow TCP and UDP 443 (Hysteria2 is on by default).
- Tie interruption markers to an exact logical resource; never simulate recovery by editing `cluster.yml` checkpoints.
- Fast reruns remove nested containers, volumes, and networks before and after validation but keep pulled images.
- Client startup failures must never satisfy negative proxy tests; version Xray runtime assets with the binary.
- Lab exits egress through Docker NAT, so an exit-IP mismatch counts only as traffic evidence, never a passed egress check.
- Qualify a Reality target only by authenticated Xray traffic, never raw TLS reachability.
