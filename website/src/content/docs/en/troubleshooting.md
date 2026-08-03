---
title: Troubleshooting
description: Common issues, fixes, and diagnostic tools.
order: 8
section: guides
---

## Which tool to use

```
BEFORE INSTALL           → meridian preflight IP
  "Will this server work for Meridian?"
  Tests: SNI reachability, port 443, DNS, OS, disk space.

AFTER INSTALL, CAN'T CONNECT → meridian test IP
  "Is the proxy reachable from where I am right now?"
  Tests: configured listeners, TLS/SNI, canonical subscription and proxy traffic.
  No SSH needed — runs from the client device.

AFTER INSTALL, CHECK EXPOSURE → meridian probe IP
  "Does this deployment reveal a proxy or management surface?"
  Tests: public ports, TLS/HTTP behavior, paths, SNI, DNS and topology policy.
  No SSH needed — runs from the client device.

AFTER INSTALL, SOMETHING BROKE → meridian doctor IP
  "Collect everything for debugging."
  Collects: server OS, Docker, Remnawave node and nginx logs, ports, firewall, SNI, DNS.
```

Add `--ai` to preflight or doctor for an AI-ready diagnostic prompt.

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
3. SSH in and check: `docker ps` (are `remnawave` and `remnawave-node` running?), `systemctl status nginx`, `ss -tlnp sport = :443`
4. Check UFW: `ufw status` — should show 443/tcp ALLOW

### TLS handshake fails

**Causes:**
1. Xray is not running inside the Docker container
2. Port 443 is occupied by another service
3. Reality SNI target is unreachable from the server

**Fixes:**
1. Check Xray: `docker logs remnawave-node --tail 20`
2. Check port: `ss -tlnp sport = :443` — should be nginx
3. Test SNI: `meridian preflight IP`

### Domain not reachable

**Causes:**
1. DNS not pointing to server IP
2. nginx not running or failed to get TLS certificate
3. nginx not routing domain SNI correctly

**Fixes:**
1. Check DNS: `dig +short yourdomain.com @8.8.8.8`
2. Check nginx: `systemctl status nginx`
3. Check nginx config: `/etc/nginx/stream.d/meridian.conf`

## Connection drops after seconds

**Causes:**
1. System clock skew >30 seconds between client and server
2. MTU issues on the network path
3. ISP resetting long-lived TLS sessions

**Fixes:**
1. Server: `timedatectl set-ntp true`. Client: enable automatic date/time
2. Try a different network
3. Use WSS/CDN connection (domain mode)

## Setup fails

### Port 443 conflict

Another service (Apache, Nginx) is using port 443. Stop it or use a clean server. `meridian preflight` will tell you what's using the port.

### Docker installation fails

Conflicting Docker packages from distro repos. Meridian auto-removes them, but if Docker is already running with containers, it skips to avoid disruption.

### SSH connection errors

Test SSH manually: `ssh root@SERVER_IP`. Ensure you have key-based access. Use `--user` flag if not root.

### Xray fails to start in the node container

Check the container: `docker logs remnawave-node --tail 50`. Common causes are a port collision on the host (node runs in `network_mode: host`, so ports from `cluster.yml` must be free), an unreachable panel (node registration needs the panel's `node_secret_key` at boot), or a missing `NET_ADMIN` capability.

**Fix:** run `meridian node check IP` and follow its remediation before redeploying. To verify Remnawave panel state, log into the admin UI at `https://<IP>/<secret_path>/` and check **Nodes** → the node should be `connected`; `meridian fleet status` surfaces the same information from the CLI.

### XHTTP inbound creation fails (port conflict)

Older Meridian versions (pre-v3.6.0) tried to put both Reality and XHTTP on port 443. v4 allocates deterministic per-node XHTTP/Reality/WSS ports (see [Architecture → Port assignments](/docs/en/architecture/#port-assignments)) and reverse-proxies through nginx, so the conflict cannot recur.

### Disk space insufficient

Less than 2GB free. Free up space: `docker system prune -af`, `journalctl --vacuum-time=1d`, check `/var/log/`.

### DNS resolution fails (domain mode)

Domain doesn't resolve to server IP yet. Update the DNS A record. Propagation is usually 5-15 minutes (up to 48 hours). Meridian warns if DNS doesn't resolve but lets you proceed.

## Was working, now stopped

**Most common cause:** Server IP got blocked. Run `meridian test IP`; its findings distinguish network reachability from TLS, subscription, and proxy execution failures. A TCP failure can also mean a cloud firewall or an offline server.

See the [IP Blocked Recovery guide](/docs/en/recovery/) for step-by-step recovery options (new server, relay swap, CDN fallback).

Other causes:
- Server rebooted and services didn't auto-start → run `docker compose up -d` in `/opt/remnawave` and `/opt/remnanode`, then `systemctl restart nginx`
- Disk full → `df -h /`, `docker system prune -af`

## Slow speeds

1. Choose a server geographically closer (Finland, Netherlands, Sweden for Europe/Middle East)
2. Check server load: `htop` or `uptime`
3. Try WSS/CDN link — may have better routing through Cloudflare
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

See the [Relay guide — Troubleshooting](/docs/en/relay/#troubleshooting) section for relay-specific issues (port conflict, firewall, exit unreachable, service not started).

## Interpreting preflight output

| Check | What It Tests | If It Fails |
|-------|--------------|-------------|
| SNI target reachability | Can the server reach the camouflage site? | Server's outbound is restricted. Try a different SNI with `--sni` |
| Port 443 availability | Is port 443 free or used by Meridian? | Another service is on 443. Stop it or use a clean server |
| Port 443 external reachability | Can the outside world reach port 443? | Cloud firewall blocks it. Open port 443/TCP inbound |
| Domain DNS | Does the domain resolve to server IP? | Update DNS A record |
| Server OS | Is it Ubuntu/Debian? | Other distros may work but are untested |
| Disk space | At least 2GB free? | Free up space |
| Clock sync | Is client/server drift under 30 seconds? | Enable NTP and automatic time on both devices |

## Interpreting doctor output

| Section | What to Look For |
|---------|-----------------|
| Local Machine | OS compatibility |
| Server | OS version, uptime (recent reboot?), disk/memory usage |
| Docker | Are the `remnawave` and `remnawave-node` containers running? Status should be "Up" |
| Remnawave Node Logs | Node startup errors, "failed to start" entries, certificate issues |
| Listening Ports | Port 443 should show nginx. If missing, proxy isn't running |
| Firewall (UFW) | Port 443/tcp should be ALLOW. If not listed, it's blocked |
| SNI Target | Should show CONNECTED with a certificate chain |
| Domain DNS | Should resolve to server IP |

## Interpreting test output

| Check | Pass | Fail or skipped |
|-------|------|-----------------|
| TCP reachability | Every configured public TCP listener is reachable | Firewall, ISP block, server down, or wrong topology |
| TLS/SNI camouflage | The target presents the expected camouflage identity | TLS routing, certificate, DNS, or SNI mismatch |
| Domain root | Hardened `403`/`404` response is reachable | DNS/nginx failure or unintended public content |
| Canonical subscription | Remnawave delivered valid Xray JSON for an active client | Missing/inactive client, panel failure, or broken template |
| Automatic fallback | The delivered policy carried real traffic | Balancer, routing, credential, or endpoint failure |
| Protocol checks | Each target outbound carried real traffic | Protocol-specific listener, firewall, or delivered config failure |

The full test rotates independent public IP observers and checks the same observers directly. If neither the proxy nor control path can obtain an IP, the result is inconclusive rather than a false protocol failure. Set `MERIDIAN_CONNECT_TEST_URL` only when you operate a known observer endpoint.

The first full test may spend up to 120 seconds downloading and verifying the pinned Xray release from GitHub. After bootstrap, `--timeout` (default `5`, range `1`-`30`) applies to individual network operations. Exit `0` passed, `4` completed with negative findings, and `3` was inconclusive because required evidence was skipped. Basic mode cannot certify UDP-only Hysteria traffic.

If all checks pass but the VPN client still can't connect: re-scan the QR code, check device clock is accurate (within 30 seconds), or try a different app (v2rayNG, Hiddify).
