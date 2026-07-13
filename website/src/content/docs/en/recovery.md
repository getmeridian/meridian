---
title: IP Blocked Recovery
description: What to do when your server's IP gets blocked — diagnosis and recovery options.
order: 7
section: guides
---

## Diagnose

Run from your local machine (no SSH needed):

```
meridian test IP
```

If the TCP port 443 check fails, the IP is likely blocked by your ISP or government. This is the most common issue in censored regions.

## Immediate relief

If you deployed with **domain mode** (`--domain`), your WSS/CDN connection still works — it routes through Cloudflare's CDN, bypassing the IP block entirely. Tell your users to switch to the WSS connection link on their connection page.

If you have a **relay** deployed, clients connecting through the relay are unaffected — they're connecting to the relay's domestic IP, not the blocked exit IP.

## Recovery options

### Option A: Add a replacement exit

The fastest path while the existing panel is still reachable:

```bash
# 1. Get a new VPS from your provider (new IP)
# 2. Add it to the existing fleet
meridian node add NEW_IP --name replacement
```

Existing clients receive the replacement exit on their next subscription refresh; their accounts do not need to be recreated.

### Option B: New exit server + existing relay

Best if you have a relay deployed — your clients keep their relay connection while you swap the exit server behind it:

```bash
# 1. Deploy new exit server
meridian node add NEW_EXIT_IP --name replacement

# 2. Switch relay to new exit
meridian relay remove RELAY_IP --exit OLD_EXIT_IP
meridian relay deploy RELAY_IP --exit NEW_EXIT_IP

# Clients reconnect automatically — relay IP unchanged
```

### Option C: Add domain mode for CDN fallback

If you weren't using domain mode before, add it now to prevent future disruption:

```bash
meridian node add NEW_IP --domain proxy.example.com
```

With domain mode, the WSS/CDN connection works even when the server IP is blocked — traffic routes through Cloudflare. See the [Domain mode guide](/docs/en/domain-mode/) for Cloudflare setup.

`meridian node add` requires the current Remnawave panel to remain reachable. If the lost server also hosted the panel, restore that host first; automatic panel migration is not available yet.

## Proactive defense

Set up resilience **before** your IP gets blocked:

1. **Deploy a relay** — gives clients a domestic entry point. When the exit IP is blocked, swap the exit behind the relay without touching clients:
   ```bash
   meridian relay deploy RELAY_IP --exit EXIT_IP
   ```

2. **Enable domain mode** — adds WSS/CDN fallback that works even if the IP is blocked:
   ```bash
   meridian deploy EXIT_IP --domain proxy.example.com
   ```

3. **Both** — maximum resilience. Clients have three paths: relay (domestic), CDN (Cloudflare), and direct (if unblocked).

## Clients and subscriptions

Clients belong to the Remnawave panel, not to an individual exit node. `meridian node add` and `meridian relay deploy` add hosts to existing subscriptions, so clients do not need to be recreated.

Ask users to refresh their subscription. Run `meridian client show NAME` if you need to resend the PWA page, subscription URL, or QR code.

If the panel database itself is lost and cannot be restored from backup, you must create a new cluster and recreate each client; automatic cross-panel migration is not available.

## Keep your old server

Don't tear down the old server immediately — it may become unblocked after days or weeks. You can check periodically:

```bash
meridian test OLD_IP
```

If it comes back, you have a spare exit server ready to go.
