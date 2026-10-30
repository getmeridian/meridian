# tests/systemlab — Multi-node Docker validation

```bash
make system-lab
uv run pytest tests/systemlab/ -q
```
## Design decisions
**Real services over mocks** — privileged systemd/SSH containers stand in for two exits, two relay hops, and a routing gateway; the controller deploys through the same CLI and SSH paths as a VPS.
**This checkout, not a release** — the controller is an editable install, so deploys upload this checkout's wheel and the agent `make system-lab-agent` built; services run as systemd units, with no nested Docker.
**Deterministic topology** — a fixed bridge subnet keeps host keys, targets, the local Pebble CA, and relay routing repeatable.
**Small fail-fast stages** — `scripts/stages/` run bootstrap, deploys, subscription, golden, resilience, and removal; Make always tears Compose down.
**Golden subscription corpus** — `golden/` is the frozen record of what Remnawave served each User-Agent; `scripts/diff-golden.py` replays it against the live fleet through the unit corpus test's expectations and named deviations.

## What's done well
- Fresh deploy checks every unit, `fleet status`, a no-op second apply, and that no unit, process, or `/opt` name reveals proxy software.
- Delivered documents are executed or checked by real clients (Xray, `mihomo -t`, `sing-box check`) at pinned versions.
- Resilience proves one-exit-down failover both ways and fail-closed all-down behaviour; plan/apply covers SIGKILL recovery mid-mutation.
- Removal diffs active bindings (`assert-retired.py`), proving every retired resource absent through the service and SSH.
- Exit DNS privacy is proven on the wire: no plain DNS question leaves exit A during a proxied fetch.

## Pitfalls
- **Stage scripts import Meridian outside pytest** — a moved helper breaks only the Docker run; grep `tests/systemlab/scripts` whenever a public helper moves.
- **Debug one stage, not the lab** — `SYSTEMLAB_KEEP=1 SYSTEMLAB_STAGES="10-deploy.sh 40-removal.sh"` keeps the fleet up for reruns.
- **Agents verify the service against WebPKI roots** — bootstrap adds Pebble's issuance root and `NETAGENT_TLS_CA_FILE` before any deploy, or no agent enrolls.
- **Keep the controller's install editable** — a regular install deploys the released package from PyPI, not the code under test.
- **Browser captures replay at `/info`** — a bare browser URL now gets the page, which is checked separately.
- **Route the connectivity probe to the pool** — a fixed exit makes failover assertions meaningless.
- **Intermediate Realm hops never appear as subscription endpoints.**
- **Preflight proves a real image pull** — bridge DNS can pass while the daemon's resolver fails.
- **Accept `degraded` systemd** inside privileged containers when required services are healthy.
- **Client startup failures never satisfy negative tests**; qualify a Reality target only by authenticated traffic, never raw TLS reachability.
- **Lab exits egress through Docker NAT** — an exit-IP mismatch is traffic evidence only, never a passed egress check.
- **Keep the control name outside every DNS observer's domain**, or one lookup counts as both.
- **Add any new secret to `diff-golden.py`'s known values** — normalization replaces only those.
- **`mihomo -t` downloads missing geodata** — pin new rule files in the image.
