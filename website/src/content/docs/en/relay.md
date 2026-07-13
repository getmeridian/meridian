---
title: Relay Nodes
description: Route traffic through a domestic server for IP-blocking resilience.
order: 6
section: guides
---

## What relays solve

When your exit server's IP gets blocked, clients lose access. A relay node gives them a domestic entry point that's harder to block:

```
Client → Relay (domestic IP) → Exit server (abroad) → Internet
```

Censors see traffic to a domestic IP. The relay forwards raw TCP to the exit server — all encryption is end-to-end between client and exit. The relay never sees plaintext.

## How relays work

A relay runs [Realm](https://github.com/zhboner/realm), a lightweight zero-copy TCP forwarder (~5MB Rust binary). It listens on port 443 (configurable) and forwards all traffic to the exit server's port 443. No Docker, no VPN software, no management panel.

All protocols work through the relay:
- **Reality** — end-to-end handshake, relay fully transparent
- **XHTTP** — routed through relay with explicit `sni=` parameter
- **WSS** — domain mode, routed with `sni=domain&host=domain`

## Deploy a relay

First, deploy your exit server normally. Then deploy a relay pointing to it:

```bash
meridian relay deploy RELAY_IP --exit EXIT_IP
```

The provisioner:
1. Installs required packages and enables BBR
2. Configures UFW firewall (allow SSH + relay port)
3. Downloads Realm binary (version-pinned, SHA256-verified)
4. Writes Realm config and starts the systemd service
5. Verifies relay → exit connectivity

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--exit/-e EXIT` | (required) | Exit server IP or name |
| `--name NAME` | (auto) | Friendly name for the relay (e.g., `ru-moscow`) |
| `--port/-p PORT` | 443 | Listen port on the relay server |
| `--user/-u USER` | root | SSH user on the relay |
| `--yes/-y` | | Skip confirmation prompts |

### Example with all options

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow --port 443 --user ubuntu
```

## How clients connect

After deploying a relay, all existing client connection pages are **automatically regenerated**. Relay URLs are shown as the recommended connection, with direct URLs as backup.

When you add new clients, relay URLs are included automatically:

```bash
meridian client add alice   # relay URLs included
```

## Manage relays

```bash
meridian relay list                    # all relays across all exit servers
meridian relay list --exit 198.51.100.10     # relays for a specific exit
meridian relay check RELAY_IP          # 4-point health check
meridian relay remove RELAY_IP         # stop service + remove from config
```

### Health check

`meridian relay check` tests four things:

| Check | What it tests |
|-------|---------------|
| SSH to relay | Can you connect to the relay server? |
| Realm service | Is the systemd service active? |
| Relay → exit TCP | Can the relay reach the exit server on port 443? |
| Local → relay TCP | Can your machine reach the relay on its listen port? |

### Remove a relay

```bash
meridian relay remove RELAY_IP [--exit EXIT_IP] [--yes]
```

This stops the Realm service, removes the relay from exit server credentials, and regenerates all client connection pages (back to direct URLs only).

## Multiple relays

You can attach multiple relays to one exit server — for example, relays in different cities or ISPs:

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow
meridian relay deploy 203.0.113.11 --exit 198.51.100.10 --name ru-spb
```

Clients see all relay options on their connection page.

## Troubleshooting

### Port conflict

Another service is using port 443 on the relay. Check with `ss -tlnp sport = :443` and stop the conflicting service, or use a different port with `--port 8443`.

### Firewall blocking

Ensure port 443 is open on the relay's cloud provider firewall / security group, not just UFW.

### Exit server unreachable

The relay must be able to reach the exit server on port 443. Test with `curl -I https://EXIT_IP` from the relay, or run `meridian relay check`.

### Relay service not started

Check the Realm service: `systemctl status meridian-relay`. View logs: `journalctl -u meridian-relay --no-pager -n 20`.
