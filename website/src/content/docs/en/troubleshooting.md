---
title: Troubleshooting
description: Common issues, fixes, and diagnostic tools.
---

## Which tool to use

```
BEFORE INSTALL           → meridian check IP
  "Will this server work for Meridian?"
  Tests: SNI reachability, port 443, DNS, OS, disk space.

AFTER INSTALL, IS IT HEALTHY? → meridian check
  "Are the panel, nodes, relays, ports, and certificates OK?"
  Tests every server in the fleet by role, over SSH.

AFTER INSTALL, CAN'T CONNECT → meridian test IP
  "Is the proxy reachable from where I am right now?"
  Tests: configured listeners, TLS/SNI, the delivered subscription, and real proxy traffic.
  No SSH needed — runs from the client device.

AFTER INSTALL, CHECK EXPOSURE → meridian test IP --probe
  "Does this deployment reveal a proxy or management surface?"
  Tests: public ports, TLS/HTTP behavior, paths, SNI, DNS and topology policy.
  No SSH needed — runs from the client device.

AFTER INSTALL, SOMETHING BROKE → meridian doctor IP
  "Collect everything for debugging."
  Collects: server OS, Docker, Remnawave node and nginx logs, ports, firewall, SNI, DNS.
```

Add `--ai` to check or doctor for an AI-ready diagnostic prompt.

## Can't connect at all

### Port 443 not reachable

**Causes:**
1. Cloud provider firewall / security group blocks port 443 inbound
2. ISP or network blocks the server IP entirely
3. Server is down or proxy is not running
4. UFW on the server doesn't allow port 443

**Fixes:**
1. Check cloud provider console — ensure port 443/TCP is allowed inbound
2. Try from a different network (mobile data, another Wi-Fi)
3. SSH in and check: `docker ps` (are `ctlsvc-api` and `netagent` running?), `systemctl status nginx`, `ss -tlnp sport = :443`
4. Check UFW: `ufw status` — should show 443/tcp ALLOW

### TLS handshake fails

**Causes:**
1. Xray is not running inside the Docker container
2. Port 443 is occupied by another service
3. Reality SNI target is unreachable from the server

**Fixes:**
1. Check Xray: `meridian logs IP -c node -n 20`
2. Check port: `ss -tlnp sport = :443` — should be nginx
3. Test SNI: `meridian check IP --sni HOST`

### Domain not reachable

**Causes:**
1. DNS not pointing to server IP
2. nginx not running or failed to get TLS certificate
3. nginx not routing domain SNI correctly

**Fixes:**
1. Check DNS: `dig +short yourdomain.com @8.8.8.8`
2. Check nginx: `systemctl status nginx`
3. Check the nginx routing files: `ls /etc/nginx/stream.d/ /etc/nginx/sites-enabled/` (Meridian's files start with `meridian-v4-`) and `nginx -t`

## Connection drops after seconds

**Causes:**
1. MTU issues on the network path
2. ISP resetting long-lived TLS sessions

**Fixes:**
1. Try a different network
2. Use the XHTTP or WSS path (domain mode)

## Deploy fails

### "No traffic passes through it"

The server deployed, but real traffic didn't flow through the camouflage target you chose with `--sni`. Pick another target — `meridian scan IP` finds ones on the server's own network — and run the deploy command again with the new `--sni`. Without `--sni`, Meridian already tries `www.google.com`, `dl.google.com`, and `www.cloudflare.com` in turn.

### "The server cannot reach any Reality camouflage target"

The server has no working outbound HTTPS to any default target. Check the provider's outbound firewall, or pass a reachable target with `--sni`.

### Interrupted or failed deploy

Deploys and applies are checkpointed. Fix the reported cause and run the same command again; finished steps are skipped.

### Port 443 conflict

Another service (Apache, nginx) is using port 443. Stop it or use a clean server. `meridian check IP` tells you what's using the port.

### Docker installation fails

Conflicting Docker packages from distro repos. Meridian auto-removes them, but if Docker is already running with containers, it skips to avoid disruption.

### Panel rename stops during an upgrade

`meridian apply` moves a panel deployed by a 4.0 beta from `/opt/remnawave` to `/opt/ctlsvc`. Before stopping anything it refuses when both directories exist or `/opt/remnawave` is a mount point. Keep the directory that holds the panel's `data/` and `.env` as `/opt/ctlsvc` (or mount the volume there), move the other aside, then run `meridian apply` again. A rename interrupted midway resumes on the next apply.

### SSH connection errors

Test SSH manually: `ssh root@SERVER_IP`. Ensure you have key-based access. Use `--user` flag if not root. If the server accepts only a password, run `meridian deploy` in a terminal without `--yes` and let it install a key (see [Password-only servers](/en/deploy/#password-only-servers)), or run `ssh-copy-id root@SERVER_IP` once.

### Xray fails to start in the node container

Check the container: `meridian logs IP -c node -n 50`. Common causes are a port collision on the host (node runs in `network_mode: host`, so ports from `cluster.yml` must be free), an unreachable panel (node registration needs the panel's `node_secret_key` at boot), or a missing `NET_ADMIN` capability.

**Fix:** run `meridian check IP` and follow its remediation before redeploying. To verify Remnawave panel state, log into the admin UI at `https://<IP>/<secret_path>/` and check **Nodes** → the node should be `connected`; `meridian fleet status` surfaces the same information from the CLI.

### Disk space insufficient

Less than 2GB free. Free up space: `docker system prune -af`, `journalctl --vacuum-time=1d`, check `/var/log/`.

### DNS resolution fails (domain mode)

The certificate for your domain can only be issued once the domain's A record points at the server (grey cloud while deploying). Update the record, wait for it to propagate (usually 5–15 minutes), then run the same deploy command again. `meridian check IP --domain DOMAIN` checks this before you deploy.

## Was working, now stopped

**Most common cause:** Server IP got blocked. Run `meridian test IP`; its findings distinguish network reachability from TLS, subscription, and proxy execution failures. A TCP failure can also mean a cloud firewall or an offline server.

See the [IP Blocked Recovery guide](/en/recovery/) for recovery options (replacement exit, relay, CDN fallback).

Other causes:
- Server rebooted and services didn't start → `meridian plan` shows what is off and `meridian apply` repairs it. By hand: `docker compose up -d` in `/opt/ctlsvc` and `/opt/netagent`, then `systemctl restart nginx`
- Disk full → `df -h /`, `docker system prune -af`

## Slow speeds

1. Choose a server geographically closer (Finland, Netherlands, Sweden for Europe/Middle East)
2. Check server load: `htop` or `uptime`
3. Try the XHTTP or WSS path (domain mode) — routing through Cloudflare is sometimes better
4. Verify BBR is enabled: `sysctl net.ipv4.tcp_congestion_control`

**Do NOT** run other protocols (OpenVPN, WireGuard) on the same server — it flags the IP.

## AI-powered help

```
meridian doctor --ai
```

Copies a diagnostic prompt to your clipboard for use with any AI assistant.

Or collect diagnostics for a [GitHub issue](https://github.com/getmeridian/meridian/issues):

```
meridian doctor
```

## Relay not working

See the [Relay guide — Troubleshooting](/en/relay/#troubleshooting) section for relay-specific issues (port conflict, firewall, exit unreachable, service not started).

## Interpreting pre-flight checks

| Check | What it tests | If it fails |
|-------|--------------|-------------|
| SNI target reachability | Can the server reach the camouflage site? | Server's outbound is restricted. Try a different SNI with `--sni` |
| Port 443 availability | Is port 443 free or used by Meridian? | Another service is on 443. Stop it or use a clean server |
| Port 443 external reachability | Can the outside world reach port 443? | Cloud firewall blocks it. Open port 443/TCP inbound |
| Domain DNS | Does the domain resolve to server IP? | Update DNS A record |
| Server OS | Is it Ubuntu/Debian? | Fails without apt (CentOS, Fedora, Alpine); reinstall with Ubuntu or Debian |
| Disk space | At least 2GB free? | Free up space |
| Clock sync | Is client/server drift under a minute? | One side is not syncing time. Enable NTP and automatic time on both devices |

## Interpreting doctor output

| Section | What to look for |
|---------|-----------------|
| Local Machine | OS compatibility |
| Server | OS version, uptime (recent reboot?), disk/memory usage |
| Docker | Are the `ctlsvc-api` (panel) and `netagent` (node) containers running? Status should be "Up" |
| Remnawave Node Logs | Node startup errors, "failed to start" entries, certificate issues |
| Listening Ports | Port 443 should show nginx. If missing, proxy isn't running |
| Firewall (UFW) | Port 443/tcp should be ALLOW. If not listed, it's blocked |
| Camouflage Target | Should show CONNECTED with a certificate chain |
| Domain DNS | Should resolve to server IP |

## Interpreting test output

| Check | Pass | Fail or skipped |
|-------|------|-----------------|
| TCP reachability | Every configured public TCP listener is reachable | Firewall, ISP block, server down, or wrong topology |
| TLS/SNI camouflage | The target presents the expected camouflage identity | TLS routing, certificate, DNS, or SNI mismatch |
| Domain root | Hardened `403`/`404` response is reachable | DNS/nginx failure or unintended public content |
| Subscription | Remnawave delivered valid Xray JSON for an active client | Missing/inactive client, panel failure, or broken template |
| Automatic fallback | The delivered policy carried real traffic | Balancer, routing, credential, or endpoint failure |
| Protocol checks | Each target outbound carried real traffic | Protocol-specific listener, firewall, or delivered config failure |

The full test rotates independent public IP observers and checks the same observers directly. If neither the proxy nor control path can obtain an IP, the result is inconclusive rather than a false protocol failure. Set `MERIDIAN_CONNECT_TEST_URL` only when you operate a known observer endpoint.

The first full test may spend up to 120 seconds downloading and verifying the pinned Xray release from GitHub. After bootstrap, `--timeout` (default `5`, range `1`-`30`) applies to individual network operations. Exit `0` passed, `4` completed with negative findings, and `3` was inconclusive because required evidence was skipped. Basic mode cannot verify UDP-only Hysteria2 traffic.

If all checks pass but the VPN client still can't connect: re-scan the QR code, check that the device sets its date and time automatically, or try another recommended app (Happ, FlClashX, Clash Mi, or Throne on desktop).
