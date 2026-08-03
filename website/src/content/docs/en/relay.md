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

Legacy Realm relays advertise **Reality only**. Their dedicated SNI route terminates at a Reality inbound on the exit, while HTTP transports need their own host/path routing. Direct XHTTP and WSS entries remain available in the same subscription as fallbacks.

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
| `--sni HOST` | (auto) | Legacy scans a relay-local SNI; V4 omission inherits the exit Reality path |
| `--user/-u USER` | root | SSH user on the relay |
| `--ssh-port PORT` | 22 | SSH port on the relay server |
| `--yes/-y` | | Skip confirmation prompts |

### Example with all options

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow \
  --port 443 --sni www.microsoft.com --user ubuntu --ssh-port 2222 --yes
```

## How clients connect

After deployment, Meridian adds the Reality relay host to Remnawave. Existing clients receive it at their next subscription refresh; direct transports remain available as backup.

When you add new clients, relay URLs are included automatically:

```bash
meridian client add alice   # relay URLs included
```

## Manage relays

```bash
meridian relay list                    # all relays across all exit servers
meridian relay list --exit 198.51.100.10     # relays for a specific exit
meridian relay check RELAY_IP          # relay and panel health checks
meridian relay remove RELAY_IP         # stop service + remove from config
```

### Health check

For legacy relays, `meridian relay check` tests the relay path and its panel registration:

| Check | What it tests |
|-------|---------------|
| SSH to relay | Can you connect to the relay server? |
| Realm service | Is the systemd service active? |
| Relay → exit TCP | Can the relay reach the exit server on port 443? |
| Local → relay TCP | Can your machine reach the relay on its listen port? |
| Panel hosts | Are the relay's Remnawave host entries present and enabled? |

Exit `0` means healthy, `4` means completed findings, and `3` means required SSH or panel evidence was unavailable.

V4 chains are managed as one topology resource. Per-hop `relay check` and direct `relay remove` exit `2`; use `meridian test` for end-to-end traffic and `meridian setup` to change or retire the chain. `fleet status` reports only external TCP-listener evidence for public relays and keeps unobserved internal hops unknown.

### Remove a relay

```bash
meridian relay remove RELAY_IP [--exit EXIT_IP] [--yes]
```

On legacy deployments this stops Realm, removes its binary/config/unit and exact UFW listen-port rule, removes panel and exit-side routing, then updates `cluster.yml`. V4 changes must go through setup.

## Multiple relays

You can attach multiple relays to one exit server — for example, relays in different cities or ISPs:

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow
meridian relay deploy 203.0.113.11 --exit 198.51.100.10 --name ru-spb
```

Clients receive all relay options in their subscription.

## Troubleshooting

### Port conflict

Another service is using port 443 on the relay. Check with `ss -tlnp sport = :443` and stop the conflicting service, or use a different port with `--port 8443`.

### Firewall blocking

Ensure port 443 is open on the relay's cloud provider firewall / security group, not just UFW.

### Exit server unreachable

The relay must be able to reach the exit server on port 443. For legacy, run `meridian relay check`; for V4, run `meridian test` against the advertised route.

### Relay service not started

Check the Realm service: `systemctl status meridian-relay`. View logs: `journalctl -u meridian-relay --no-pager -n 20`.
