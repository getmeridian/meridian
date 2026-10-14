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
meridian relay add 203.0.113.10 --exit 198.51.100.10
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
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-moscow \
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

Check a relay's hops — each Realm service is running, each hop reaches the next, and the public entry accepts connections — then test real traffic through the whole route:

```bash
meridian check 203.0.113.10
meridian test
```

The check also measures each leg: from every hop it downloads the exit's camouflage site through the next hop, sending the same SNI clients send, and reports the connect time, the time to first byte, and how many bytes arrived. A transfer that freezes between roughly 8 and 32 KB is the signature of throttling on that leg and is reported as a warning; the usual fix is a relay host at a different provider (another network, or ASN). If the camouflage site's home page is smaller than 32 KB, the leg check is inconclusive rather than passing.

To retire a relay, remove the chains through it. Meridian shows what it will delete, asks for confirmation, and cleans up the Realm service, firewall rules, and published entry points; existing clients lose the relay on their next subscription refresh:

```bash
meridian relay remove 203.0.113.10
```

## Multiple relays

Attach several relays to one exit, for example in different cities or networks:

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-moscow
meridian relay add 203.0.113.11 --exit 198.51.100.10 --name ru-spb
```

Clients receive every relay in their subscription. For relay chains with more than one hop, use `meridian setup`.

## Mobile whitelists

During some mobile internet restrictions in Russia, operators let through only an approved list of sites and addresses. Reports vary by operator, region, and week, and no operator publishes its list. Meridian does not detect or track whitelists; this section covers what you can set up today and what stays manual.

### What deployers report

- **Networks differ.** Some seem to look only at the server name (SNI) a connection announces. Others also check the destination IP. Where only the name is checked, a direct Reality path with an allowed name can pass. Where the IP is checked too, the first hop has to be an address on the list.
- **Domestic cloud addresses are the usual first hop.** Not every address from a provider is listed: people describe one subnet of a provider's range being allowed while another from the same provider is not. Providers may also limit private customers, cap how often you can recreate a server, or shape bandwidth.
- **Foreign CDN addresses are generally not allowed on mobile networks.** Treat [domain mode](/en/domain-mode/) through Cloudflare as a fallback for home connections, not as a whitelist answer.
- **The relay-to-exit link can be disrupted too.** A relay that reaches users can still lose its connection to a foreign exit.

### A relay on an allowed address

Rent a small server on a domestic provider whose address is allowed on your users' mobile networks. Community-maintained lists of allowed ranges exist, but only a test from a mobile connection while restrictions are active proves it. Then add it as a relay and give it an allowed name:

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-mobile --sni allowed.example.com
```

With `--sni`, only this relay's entry in the subscription announces that name. Meridian adds the name to the exit's Reality listener (same keys) and gives the relay its own listener on the exit. It routes only that name to Reality, sends everything else to the camouflage site, and is reachable only from the relay's last hop. The exit's own camouflage target and its other entries don't change. The name doesn't have to be reachable from the exit, because the exit still presents its own camouflage target. A prober that reaches the relay without a valid key is therefore forwarded to that target instead of getting a proxy answer.

Then check the relay and test from the network that matters:

```bash
meridian check 203.0.113.10
meridian test
```

`meridian test` from your own machine can't tell you what a whitelisted mobile network allows. Try it on a real device on that network.

### Pick an exit SNI

`--sni` on `meridian deploy` and `meridian node add` sets the camouflage target for the exit's direct Reality path. The target must be a site the exit can reach with TLS 1.3, and deploy checks real traffic through it and stops if none passes. Before deploying, check a candidate with `meridian check IP --sni HOST`. On a network that checks names only, an allowed name may let the direct path through, but whether a name works depends on the operator, so test each one.

### What Meridian doesn't do

- It doesn't tell you whether a relay's IP is on any operator's list, or whether restrictions are active.
- It doesn't set up fronting through a domestic CDN. Domain mode is built and tested for Cloudflare.
- A relay carries Reality only. XHTTP, WSS, and Hysteria2 stay on the exit.
- It doesn't tunnel through third-party calling or messaging services.

## Troubleshooting

### Port conflict

Another service is using the listen port on the relay. Check with `ss -tlnp sport = :443` and stop it, or pick another port with `--port 8443`.

### Firewall blocking

Make sure the relay port is open in your cloud provider's firewall or security group, not only in UFW.

### Exit unreachable

The relay must reach the exit's Reality port. Run `meridian test` to check the route end to end, and `meridian check EXIT` to check the exit itself.

### Relay service not running

On the relay server, list the forwarder services and read their logs. Relay hosts use neutral names (`netfwd-*` units, `/etc/netfwd/` configs) so nothing on them names Meridian, Realm, or a relay:

```bash
systemctl list-units 'netfwd-*'
journalctl -u 'netfwd-*' --no-pager -n 20
```
