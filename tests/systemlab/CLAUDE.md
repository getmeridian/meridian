# tests/systemlab — Multi-node system validation lab

```bash
make system-lab               # ~3 min (cached images), ~5 min (cold)
# or manually:
bash tests/systemlab/scripts/setup-fixtures.sh
docker compose -f tests/systemlab/compose.yml up --build \
  --abort-on-container-exit --exit-code-from controller
docker compose -f tests/systemlab/compose.yml down -v
```

## What is tested

The system lab deploys Meridian across two separate containers via SSH — exactly like a real VPS deployment — and verifies the full relay data path.

### Commands exercised

| # | Command | What it validates |
|---|---------|-------------------|
| 1 | `meridian deploy EXIT_IP --user root --yes --no-harden` | Full provisioner pipeline: Docker install, 3x-ui panel, Xray Reality+XHTTP inbounds, nginx SNI routing, TLS bootstrap cert |
| 2 | `meridian client add alice` | Panel API client creation, credential generation, connection page rendering |
| 3 | `meridian relay deploy RELAY_IP --exit EXIT_IP --sni www.google.com --yes` | Realm install, relay firewall (ufw), per-relay Xray inbound on exit, nginx relay-map, credential sync |
| 4 | **Reality direct** (xray client → exit:443) | VLESS+Reality tunnel through nginx SNI routing — proves port, UUID, keys, SNI all match |
| 5 | **Reality via relay** (xray client → relay:443 → exit) | Realm TCP forwarding + per-relay Xray inbound — proves relay port, relay SNI, and full tunnel chain |
| 6 | `meridian teardown EXIT_IP --yes` | 3x-ui removal, nginx cleanup, credential removal |

### Checks asserted

- `proxy.yml` credentials file exists after deploy
- 3x-ui container is running (`docker ps`)
- nginx config is valid (`nginx -t`)
- `meridian-relay` systemd service is active after relay deploy
- Reality tunnel returns HTTP response through SOCKS5 (direct + via relay)

### What is NOT tested (phase 2)

- **XHTTP/TLS** — needs domain mode + Pebble cert (Pebble runs but can't issue IP certs)
- **Domain mode / WSS** — needs internal DNS via pebble-challtestsrv
- **IP verification** — xray `geoip:private` blocks lab IPs; x-ui regenerates routing rules
- **Idempotent redeploy** — deploy-over-existing not exercised
- **WARP** — Cloudflare WARP client not installed in lab

## Design decisions

**Real systemd, real SSH** — containers run `/sbin/init`, services managed by real unit files. No mocked systemctl.

**Separate Docker daemon** — exit node runs its own `dockerd` (vfs driver, no iptables). 3x-ui pulls from GHCR inside this nested daemon.

**Static IPs** — deterministic `172.30.0.0/24` bridge so SSH known_hosts, credential paths, and deploy commands are stable.

**Fixtures generated at runtime** — SSH keypair and Pebble CA created by `setup-fixtures.sh`, never committed (`.gitignore`).

## Pitfalls

- **xray `geoip:private` blocks lab IPs** — can't use internal echo service; x-ui regenerates config on restart. Test uses an external exit-IP endpoint.
- **Pebble can't issue IP certs** — ACME protocol limitation. Infrastructure is ready; needs domain mode (phase 2).
- **Relay remove fails** — credential sync bug: `_refresh_exit_credentials_or_fail` overwrites local relay data. Product bug, not test bug.
- **systemd reports `degraded`** — some kernel features missing in containers. Accepted.
- **Base image build is slow** — docker-ce from official repo. Uses BuildKit apt cache mounts. Cached after first build.
