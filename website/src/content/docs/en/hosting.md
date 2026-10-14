---
title: Choosing hosting
description: How to pick and test a VPS for an exit or a relay before you commit to it.
order: 2.8
section: guides
---

Meridian configures the server correctly, but it can't change where the server lives. A perfect Reality setup on an address that your users' network blocks still doesn't connect. This page helps you pick and test hosting before you spend much on it.

You rent two kinds of servers, and they have different needs:

- **Exit**: carries all traffic to the open internet. It should be outside the censored country.
- **Relay**: a small TCP forwarder in front of an exit, see [Relay nodes](/en/relay/). It needs a clean address that users can reach, and little else.

## What to look at

**Address and network reputation.** Censors can block or throttle a single IP, a subnet, or everything from one hosting network. Large, well-known hosting networks are the most likely to be blocked wholesale, and deployers in Russia report that blocks sometimes take down neighboring customers who never ran a proxy. A smaller host in a less obvious location often lasts longer, but a host that everyone recommends stops being obscure. You can't know ahead of time, so test (below). Mobile networks are stricter than home connections: the same IP can work on a home line and fail on every mobile operator.

**Location and latency.** Pick a country with good routing to your users, close enough for low latency. Measure from the users' own network, not from your laptop abroad. Use `meridian check` before you deploy to see how the server reaches its camouflage target.

**Bandwidth and fair use.** "Unlimited" usually comes with a fair-use clause, often at several terabytes a month, and some hosts shape cheap or free plans to a low speed. Read the terms, and check the port speed you'll really get. A relay carries as much traffic as the users send through it, so size it the same way.

**IPv4.** Plan on a public IPv4 address, and expect to pay extra for it on some hosts. Some tools, such as `meridian scan`, work on IPv4 only.

**Payment and account.** Choose a provider whose payment methods and signup requirements you are comfortable with, and keep the account details separate from your personal accounts if privacy matters. Hourly billing is worth more than a small discount, because it lets you test cheaply.

**Clean OS and root access.** Meridian expects a fresh Debian or Ubuntu server with SSH key login. Avoid hosts that preinstall control panels or firewalls you don't control.

## Test the IP before you pay a lot

1. Rent the smallest plan with **hourly billing**, or one with a refund window.
2. Run `meridian check IP` for the pre-flight checks: camouflage target, port 443, outside reachability, OS, disk, and clock.
3. Deploy, then run `meridian test` and `meridian test --probe` **from inside the network your users are on**, on both a home connection and a mobile one. `--probe` also reports whether the server IP or the camouflage SNI looks blocked. Results from outside the censored network prove very little.
4. Leave it running for a few days and test again. Some addresses work at first and get throttled later.
5. If it fails, delete the server and rent another address, then test again. Hourly billing makes this cheap.

When a server stops working after it was fine, see [IP blocked recovery](/en/recovery/).

## Where not to put an exit

An exit inside Russia is fragile. The host operates under local rules, can see and act on your server, and reports from Russian hosts describe identity verification and takedowns of proxy servers. Keep exits abroad. Use a domestic server only as a relay, which forwards encrypted bytes and runs no proxy software.

## Domestic relays carry risk

A relay on a domestic host helps when foreign IPs are blocked, but the person who rents it carries the risk. It is rented in their name, under local law, and the host can ask for identity checks or end the service. The relay only forwards TCP that is encrypted end to end between the client and the exit, so the host sees connections to your exit's address and their volume, not the content. Abuse complaints about what users do online go to the exit's provider. This isn't legal advice: understand your local rules and your host's terms before you rent a relay for other people.

## Don't depend on one path

Blocks change quickly, so plan for them:

- Deploy with the defaults (Reality, XHTTP, and Hysteria2), and add [domain mode](/en/domain-mode/) so XHTTP and WSS travel through Cloudflare.
- Keep a second exit at a different provider and network (`meridian node add`), so one blocked address range doesn't take everything down.
- Add a relay for networks that block your exit's address.

Every client's subscription lists all of them, and apps that use the Xray JSON or Mihomo subscription switch paths automatically.

## Camouflage target

The host's network also shapes the best camouflage target. Pick a popular site with TLS 1.3 that sits on a network that looks natural next to your server: `meridian scan` finds candidates in the server's own neighborhood. Don't choose `apple.com` or `icloud.com` (their networks don't match hosting providers), and don't choose `www.microsoft.com`: its certificate chain is too large for REALITY, so the deploy converges but no traffic passes. See [Choosing a camouflage target](/en/deploy/#choosing-a-camouflage-target).
