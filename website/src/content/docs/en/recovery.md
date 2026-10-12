---
title: IP Blocked Recovery
description: What to do when your server's IP gets blocked — diagnosis and recovery options.
order: 7
section: guides
---

## Diagnose

Run from your own machine (no SSH needed):

```
meridian test 198.51.100.10
```

If the TCP check on port 443 fails from your network but works from elsewhere (another network, a VPN, the [ping tool](https://getmeridian.org/ping)), the IP is most likely blocked. A TCP failure everywhere usually means a cloud firewall or a server that is down.

## Immediate relief

If you deployed with a **domain** (`--domain`) and the record is proxied through Cloudflare, the XHTTP and WSS paths still work — they reach Cloudflare, not your blocked IP. Apps using the Xray JSON or Mihomo subscription switch over on their own; others can pick the XHTTP or WSS entry by hand.

If you have a **relay**, clients connecting through it are unaffected as long as the relay itself can still reach the exit.

## Recovery options

### Add a replacement exit

The fastest path, as long as your control-plane server (the one running the panel) is still reachable from your machine:

```bash
# 1. Get a new VPS from your provider (new IP)
# 2. Add it to the fleet
meridian node add 198.51.100.20 --name replacement
```

Clients keep their accounts. They receive the new exit on their next subscription refresh — ask them to refresh in their app if it doesn't happen on its own.

### Add a domain for CDN fallback

If you weren't using a domain, add one to the new exit:

```bash
meridian node add 198.51.100.20 --domain vpn.example.com
```

See [Domain mode](/docs/en/domain-mode/) for the Cloudflare steps.

### Add a relay

A relay on a domestic server gives users an entry point that stays reachable when foreign IPs are blocked:

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.20
```

Moving an existing relay to a different exit isn't available yet; deploy a new relay against the new exit instead.

### If the control-plane server is lost

`node add` and `relay add` need the panel. If the server that hosted it is gone and cannot be restored, there is no automatic migration yet: deploy a fresh server with `meridian deploy`, then recreate each client with `meridian client add`. Each person gets a new link.

## Prepare before a block

1. **Add a relay** on a domestic server: `meridian relay add RELAY_IP --exit EXIT_IP`
2. **Use a domain** for CDN fallback: deploy with `--domain vpn.example.com`
3. **Both** — clients then have three ways in: relay (domestic), CDN (Cloudflare), and direct
4. **Back up `~/.meridian/`** — it is what lets you manage the fleet from another machine

## Keep the old server

Don't tear down the blocked server right away — blocks are sometimes lifted after days or weeks. Check now and then:

```bash
meridian test 198.51.100.10
```

Retiring a single exit from a multi-server fleet isn't available yet, so a blocked exit stays in the topology for now.
