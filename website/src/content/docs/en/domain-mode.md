---
title: Domain Mode
description: Add CDN fallback via Cloudflare for IP-blocking resilience.
order: 4
section: guides
---

## What domain mode adds

Domain mode extends the standalone setup with three components:

1. **nginx stream SNI routing** — routes domain traffic to nginx http alongside Reality traffic to Xray
2. **nginx TLS** — certificates managed by acme.sh (Let's Encrypt) for your domain
3. **VLESS+WSS inbound** — legacy CDN fallback through Cloudflare (prefer XHTTP as the secondary transport)

The WSS connection routes through Cloudflare's CDN, making it work even if your server's IP is blocked — Cloudflare's IP ranges are too widely used to block. WSS is maintained for backward compatibility; XHTTP is the preferred alternative transport for new deployments.

## Deploy with domain

```
meridian deploy 1.2.3.4 --domain proxy.example.com
```

## Cloudflare setup

**Follow this exact order** to avoid TLS certificate issues:

1. Add your domain in Cloudflare, create an **A record** pointing to your server IP
2. Keep the cloud icon **grey** ("DNS only") — don't enable proxying yet
3. Run `meridian deploy` — acme.sh obtains the TLS certificate automatically
4. Switch to **orange cloud** (Proxied)
5. Configure SSL/TLS → **Full (Strict)** and Network → **Enable WebSockets**

> **Important:** acme.sh obtains certificates via HTTP-01 challenge on port 80. If Cloudflare's "Always Use HTTPS" is active, it breaks the challenge. Disable it or add a page rule for `/.well-known/acme-challenge/*`.

> **Also important:** in domain mode, the hosted connection page and the hidden Remnawave admin UI + subscription-page paths are served on this same hostname. Once you switch the record to orange-cloud, those pages go through Cloudflare too. Disable Cloudflare features that inject scripts or modify HTML on this hostname (for example Website Analytics / RUM), because Meridian's connection page intentionally uses a strict self-hosted CSP. If the page starts failing while proxied, temporarily switch the record back to DNS only to confirm it is a Cloudflare-side issue.

## Connection links

With domain mode, users get three connection options:

| Protocol | Priority | Route |
|----------|----------|-------|
| Reality | Primary | Direct to server IP |
| XHTTP | Alternative | Through nginx on port 443 |
| WSS | Legacy backup | Through Cloudflare CDN |

Users should try Reality first (fastest), XHTTP second, and WSS only as a last resort if both fail (e.g. IP is blocked).
