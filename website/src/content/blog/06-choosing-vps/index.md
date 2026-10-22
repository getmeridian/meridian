---
date: 2026-03-25
author: Meridian Team
category: Guide
tags:
  - vps
  - hosting
  - ip-blocking
  - relay
  - guide
title: "Choosing a VPS for a proxy: the address matters more than the specs"
description: "What to check in a VPS for a Meridian exit or relay, how to test an address before you pay for it, and what to do when it gets blocked."
readingTime: 5
---

![Choosing the right VPS for a proxy server](./hero.webp)

Meridian configures the server correctly, but it can't change where the server lives. A perfect Reality setup on an address your users' network blocks still doesn't connect.

![VPS checklist: the control server needs 2 GB RAM and 2 cores, an extra exit 1 GB and 1 core, a relay the smallest plan. Before deploying: public IPv4, TCP and UDP 443 open, fresh Debian or Ubuntu LTS, 2 GB free disk, clock within 30 seconds, hourly billing, no exit inside the censored country. Then test from inside users' networks.](./checklist.webp)

## Two kinds of servers

- **Exit**: carries all traffic to the open internet. It goes outside the censored country.
- **Relay**: a small TCP forwarder in front of an exit, usually inside the country. It needs a reachable address and little else.

## Size: modest, but not the smallest plan for the panel

Your first server is the control server. It runs the Remnawave panel, PostgreSQL, and Valkey in Docker, plus Xray. Remnawave's own requirements page asks for at least 2 GB of RAM and 2 cores for the panel, and 1 GB and 1 core for a node. An extra exit added with `meridian node add` runs no panel. A relay runs only Realm, a zero-copy forwarder, with no Docker at all.

Meridian refuses to deploy with less than 2 GB of free disk.

## The address: reputation is the real risk

Censors block a single IP, a subnet, or a whole hosting network. Large, popular hosting networks are the most likely to be blocked wholesale. Deployers in Russia report blocks that took down neighboring customers who never ran a proxy. A smaller host in a less obvious place often lasts longer, but a host everyone recommends stops being obscure.

You can't read reputation off a pricing page; you test it (below). Two details change the result:

- **Mobile networks are stricter than home lines.** The same IP can work at home and fail on every mobile operator.
- **Blocks can arrive late.** Some addresses work for days, then get throttled.

## Location: routing beats distance

Pick a country with good routing to your users and low latency from their networks. Measure from those networks, not from your laptop abroad: a nearby country with poor routes to their ISPs can be slower than a farther one.

The host's network also shapes your camouflage. Reality impersonates a real TLS 1.3 site, and a censor sees that site's name next to your server's IP. A site whose DNS points into the same network looks ordinary; a famous foreign site on a VPS address stands out. A first deploy scans the server's subnet for such neighbors, so a network full of real sites gives better candidates. Don't pick `apple.com` or `icloud.com` by hand (their networks don't match hosting providers), and never `www.microsoft.com`: its certificate chain is too large for Reality, so the deploy converges and no traffic passes.

## IPv4, ports, and the clean OS

- **Get a public IPv4 address.** Some hosts charge extra for it. Many client networks still have no IPv6, and `meridian scan` works on IPv4 only.
- **Port 443 must be free and reachable.** Meridian serves Reality, XHTTP, and the panel on TCP 443, and Hysteria2 on UDP 443. Open both in the provider's cloud firewall or security group, not only on the server.
- **Start from a fresh Debian or Ubuntu LTS image** with SSH key login, as root or a user with passwordless sudo. Avoid hosts that preinstall control panels, web servers, or firewalls you don't control.
- **The clock must be within a minute** of the device you deploy from. Certificate checks depend on it.

## Bandwidth: read the fair-use clause

"Unlimited" usually means a fair-use limit, often several terabytes a month. Some hosts shape cheap or free plans to a low speed. Check the real port speed. A relay carries everything its users send through it, so size its traffic the same way as the exit's.

## Payment, account, and billing

Pick payment methods and signup checks you accept, and keep the account apart from your personal identity if privacy matters. Prefer **hourly billing** over a small discount: a blocked address then costs an hour, not a month.

## Where not to put an exit

Keep exits outside the censored country. A domestic host works under local rules and can see and act on your server; Russian hosts are reported to demand identity checks and take proxy servers down.

## A domestic relay as the second server

When foreign IPs are blocked, a relay gives users a domestic entry point. It forwards raw TCP to the exit's Reality listener. Encryption runs end to end between the client and the exit, so the relay host sees connections to your exit and their volume, not the content.

Know the limits before you rent one:

- Relays carry Reality only. XHTTP, WSS, and Hysteria2 stay on the exit's own address and remain in the subscription as alternatives.
- The person who rents the relay carries the risk. It is in their name, under local law, and the host can demand identity checks or end the service.
- The relay-to-exit link can be throttled too. `meridian check RELAY_IP` measures each leg; a transfer that freezes between roughly 8 and 32 KB is the throttling signature, and the usual fix is a relay at a different provider and network.

## Test before you commit

1. Rent the smallest suitable plan with hourly billing or a refund window.
2. Run `meridian check 198.51.100.10`. It checks the camouflage target, port 443, outside reachability, OS, disk, and clock without installing anything.
3. Deploy, then run `meridian test` and `meridian test --probe` from inside your users' network, on a home line and on mobile. `--probe` reports whether the server IP or the camouflage name looks blocked. Results from outside the censored network prove little.
4. Test again after a few days. If it fails, rent a new address and repeat.

## When the IP gets blocked

Every subscription lists all of a fleet's paths, and apps that use the Xray JSON or Mihomo subscription switch between them on their own. So:

- Keep a second exit at a **different provider and network**, so one blocked range doesn't take everything down.
- Add [domain mode](/en/domain-mode/), so XHTTP and WSS reach Cloudflare instead of your IP.
- Add a relay for networks that block the exit's address.

When an exit is blocked and the control server is still reachable, rent a new server and either add it with `meridian node add 198.51.100.20` or move the blocked exit with `meridian node replace exit-1 198.51.100.20`. Clients keep their accounts and pick up the change on their next subscription refresh. Keep the old server for a while; blocks are sometimes lifted.

The control server itself can't be moved that way. Choose its host with the most care.

Full steps: [Choosing hosting](/en/hosting/) and [IP blocked recovery](/en/recovery/).
