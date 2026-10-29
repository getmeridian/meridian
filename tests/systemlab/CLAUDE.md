# tests/systemlab — Multi-node Docker validation

```bash
make system-lab
uv run pytest tests/systemlab/ -q
```
## Design decisions

**Real services over mocks** — privileged systemd/SSH containers stand in for two exits, two relay hops, and a routing gateway; the controller deploys through the same CLI and SSH paths as a VPS.
**This checkout, not a release** — the controller is an editable install with uv, so deploys upload this checkout's wheel (`control_artifacts`) and the agent `make system-lab-agent` built for the Docker daemon's architecture; the control server installs `ctld` and exits run `netagent`/`netcore` as systemd units, with no nested Docker.
**Deterministic topology** — a fixed `172.30.0.0/24` bridge keeps SSH host keys, deploy targets, the local Pebble CA, and relay routing repeatable.
**Small fail-fast stages** — the controller runs bootstrap, single deploy, fleet deploy, subscription, golden, resilience, and removal scripts under `scripts/stages/`; Make always tears Compose down. Pure contract tests run in normal pytest.
**Golden subscription corpus** — `golden/` is the frozen record of what Remnawave served each `golden_agents.py` User-Agent on these fleets (`CAPTURED_BACKEND`); `scripts/diff-golden.py` replays every recorded request against the live fleet and diffs it through `tests/test_control_subscription_corpus.py`'s expectations and named deviations, which also replay it against the renderer in unit tests.

## What's done well

- Fresh deploy checks `ctld`, both exits' and the gateway's agents and cores, both Realm hops, `fleet status`, a no-op second apply, and that no unit, process, or `/opt` name reveals proxy software.
- The delivered Xray config is executed directly; Base64, Mihomo, and sing-box documents are parsed and endpoint-checked, and the last two also pass `mihomo -t` and `sing-box check` at versions pinned (with per-arch checksums) in the controller image.
- Resilience proves both one-exit-down directions and fail-closed all-down behavior through the `leastPing` pool, by stopping each exit's agent and core.
- Plan/apply covers no-op convergence, a console-added person surviving apply, and SIGKILL recovery after a real client-endpoint mutation but before observation.
- Removal checks both relays, removes the relay chain and exit B, and diffs active bindings (`assert-retired.py`) so every retired endpoint, node, agent, Realm unit, firewall rule, and nginx file is proven absent through the service and SSH.
- Exit DNS privacy is proven on the wire: `dns_capture.py` sniffs exit A, loopback included, during a proxied fetch and requires no plain question for the observer names; a host-resolver lookup of a control name proves the capture sees DNS.

## Pitfalls
- **Debug one stage, not the lab** — `SYSTEMLAB_KEEP=1 SYSTEMLAB_STAGES="10-deploy.sh 40-removal.sh"` keeps the fleet up; rerun a stage with `docker compose exec controller bash /workspace/tests/systemlab/scripts/stages/40-removal.sh` (copy edited scripts in with `docker compose cp`).
- **Agents verify the service against WebPKI roots** — images trust only the fixture CA (Pebble's own HTTPS), so 00-bootstrap adds Pebble's runtime issuance root to every node's and the controller's bundle and a `netagent.service.d` drop-in sets `NETAGENT_TLS_CA_FILE` to it before any deploy; without it no agent enrolls.
- **A regular install deploys the released package** — keep the controller's install editable (and `uv` in the image), or the control server gets `meridian-vpn==VERSION` from PyPI, not the code under test; copy the Hatch build hook before installing.
- Route the external connectivity probe explicitly to the pool; a fixed exit route makes failover assertions meaningless.
- Intermediate Realm hops must never appear as canonical subscription endpoints.
- Preflight must prove a real base-image pull (bridge DNS can pass while the daemon resolver fails), repair DNS only for the dedicated Meridian Colima profile, and keep local Docker state out of build contexts.
- `systemd` may report `degraded` inside privileged containers; accept it when required services are healthy.
- Hardened exits must allow TCP and UDP 443 (Hysteria2 is on by default).
- Tie interruption markers to an exact logical resource; never simulate recovery by editing `cluster.yml` checkpoints.
- Client startup failures must never satisfy negative proxy tests; version Xray runtime assets with the binary.
- Lab exits egress through Docker NAT, so an exit-IP mismatch counts only as traffic evidence, never a passed egress check.
- Qualify a Reality target only by authenticated Xray traffic, never raw TLS reachability.
- The DNS capture matches a name and its subdomains; keep the control name outside every observer's domain, or one lookup counts as both.
- Add any new secret to `diff-golden.py`'s known values: normalization replaces only those; the UUID and field patterns are a net.
- `sing-box check` resolves no outbound tags and `mihomo -t` downloads missing geodata: keep the graph check in `subscription_client.py`, pin new rule files in the image, and move the client-management docs with any engine pin.
