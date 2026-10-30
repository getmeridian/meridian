---
title: Relay Nodes
description: Route traffic through a domestic server for IP-blocking resilience.
---

## What relays solve

When your exit server's IP gets blocked, users lose access. A relay gives them a domestic entry point that is harder to block:

```
Client → Relay (domestic IP) → Exit server (abroad) → Internet
```

Censors see traffic to a domestic IP. The relay forwards raw TCP to the exit; encryption is end to end between the client and the exit, so the relay never sees plaintext.

## How relays work

A relay runs [Realm](https://github.com/zhboner/realm), a small zero-copy TCP forwarder. It listens on port 443 (configurable) and forwards to the exit's Reality listener. A server with only the relay role runs no node agent or control service. Meridian manages it over SSH.

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
meridian client add guest   # relay included
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

To give a relay another camouflage SNI, for example when `meridian test --probe` reports its SNI blocked, change it in place. Meridian checks that the exit reaches the new site, shows the review, and applies it; the old name stops working at once, and apps switch at their next subscription refresh (every 6 hours) or a manual refresh. Pass the exit's own SNI to make the relay follow the exit again:

```bash
meridian relay sni 203.0.113.10 dl.google.com
```

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

### Sites on Russian CDNs

Sites behind some Russian CDNs inherit the CDN's whitelist status. October 2026 reports include **Beeline, EdgeCenter, Beget, Timeweb, and Yandex**. A site on one of these networks may provide an allowed SNI for a relay. Test the particular site while restrictions are active: the result can differ by operator, region, and subnet.

An allowed SNI helps where the operator checks the name. Where it also checks the destination IP, your relay's address must be allowed too. Borrowing a CDN site's name does not put Realm behind that CDN or make an arbitrary VPS address allowed.

### Public whitelist lists

The community maintains `WHITE-SNI-RU` and `WHITE-CIDR-RU` in [igareck/vpn-configs-for-russia](https://github.com/igareck/vpn-configs-for-russia). Use them as candidates to test, not as an operator's official list. This is a link you open yourself; Meridian never fetches these lists or contacts a whitelist service.

### A relay on an allowed address

Rent a small server on a domestic provider whose address is allowed on your users' mobile networks; see [Choosing hosting](/en/hosting/). Only a test from a mobile connection while restrictions are active proves it. Then add it as a relay and give it an allowed name:

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-mobile --sni allowed.example.com
```

With `--sni`, only this relay's entry in the subscription announces that name. Meridian gives the relay its own listener on the exit, reachable only from the relay's last hop, and a Reality listener (same keys) that imitates the name. It routes only that name to Reality and sends everything else to that site, so a prober that reaches the relay without a valid key sees the site's real certificate instead of a proxy answer. The exit's own camouflage target and its other entries don't change. The exit must reach the name with TLS 1.3 and HTTP/2; change it later with `meridian relay sni RELAY NEW_SNI`.

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

The relay must reach the exit's Reality port. A Russian cloud can lose its route to a foreign VPS while its domestic address stays reachable. Users then see “connected, nothing loads”: reaching the relay proves only the first leg works.

Check both servers from your managing machine, then test the route from the affected network:

```bash
meridian check 203.0.113.10  # relay services and the link toward the exit
meridian check 198.51.100.10  # exit services, camouflage target, and DNS
meridian test 203.0.113.10  # real traffic through the relay
meridian test 198.51.100.10  # real traffic to the exit directly
```

If the exit passes its checks and direct traffic works, but the relay's hop check fails, investigate the relay provider's foreign route or firewall. If the exit's own service or DNS checks fail, restore it first. Direct tests can themselves be blocked on a network that needs a relay; use the server-side hop results to locate the break. Add a second relay on another network, or a second exit reachable from your relays, so one broken foreign link does not remove every working route.

### Relay service not running

On the relay server, list the forwarder services and read their logs. Relay hosts use neutral names (`netfwd-*` units, `/etc/netfwd/` configs) so nothing on them names Meridian, Realm, or a relay:

```bash
systemctl list-units 'netfwd-*'
journalctl -u 'netfwd-*' --no-pager -n 20
```
