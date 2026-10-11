---
title: Relay Nodes
description: Route traffic through a domestic server for IP-blocking resilience.
order: 6
section: guides
---

## What relays solve

When your exit server's IP gets blocked, users lose access. A relay gives them a domestic entry point that is harder to block:

```
Client → Relay (domestic IP) → Exit server (abroad) → Internet
```

Censors see traffic to a domestic IP. The relay forwards raw TCP to the exit; encryption is end to end between the client and the exit, so the relay never sees plaintext.

## How relays work

A relay runs [Realm](https://github.com/zhboner/realm), a small zero-copy TCP forwarder. It listens on port 443 (configurable) and forwards to the exit's Reality listener. No Docker and no panel run on the relay.

Relays carry **Reality** traffic. XHTTP, WSS, and Hysteria2 stay on the exit's own address and domain, and remain in the same subscription as alternatives.

## Deploy a relay

Deploy the exit first, then point a relay at it:

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10
```

Meridian adds the relay to your topology and applies it:
1. Prepares the relay server (packages, BBR, SSH hardening, a firewall that opens only SSH and the relay port)
2. Installs Realm (version-pinned, SHA256-verified) as a systemd service
3. Points the relay at the exit's Reality listener
4. Publishes the relay in every client's subscription

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--exit/-e EXIT` | (required) | Exit server IP or name |
| `--name NAME` | (auto) | Friendly name for the relay (e.g. `ru-moscow`) |
| `--port/-p PORT` | 443 | Listen port on the relay server |
| `--sni HOST` | exit's target | Reality camouflage target for this relay; omit it to use the exit's |
| `--user/-u USER` | root | SSH user on the relay |
| `--ssh-port PORT` | 22 | SSH port on the relay server |
| `--yes/-y` | | Skip confirmation prompts |

### Example with all options

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow \
  --port 443 --sni dl.google.com --user ubuntu --ssh-port 2222 --yes
```

## How clients connect

Existing clients receive the relay on their next subscription refresh, and their connection pages list it. New clients get it automatically:

```bash
meridian client add alice   # relay included
```

## Manage relays

```bash
meridian relay list                       # all relays
meridian relay list --exit 198.51.100.10  # relays for one exit
```

To check a relay route, test the whole path from your machine with `meridian test`. Per-relay checks (`relay check`) and removal (`relay remove`) aren't available yet; both commands refuse and explain this.

## Multiple relays

Attach several relays to one exit, for example in different cities or networks:

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow
meridian relay deploy 203.0.113.11 --exit 198.51.100.10 --name ru-spb
```

Clients receive every relay in their subscription. For relay chains with more than one hop, use `meridian setup`.

## Troubleshooting

### Port conflict

Another service is using the listen port on the relay. Check with `ss -tlnp sport = :443` and stop it, or pick another port with `--port 8443`.

### Firewall blocking

Make sure the relay port is open in your cloud provider's firewall or security group, not only in UFW.

### Exit unreachable

The relay must reach the exit's Reality port. Run `meridian test` to check the route end to end, and `meridian node check EXIT` to check the exit itself.

### Relay service not running

On the relay server, list Meridian's Realm services and read their logs:

```bash
systemctl list-units 'meridian-realm-*'
journalctl -u 'meridian-realm-*' --no-pager -n 20
```
