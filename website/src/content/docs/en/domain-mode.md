---
title: Domain Mode
description: Add CDN fallback via Cloudflare for IP-blocking resilience.
order: 4
section: guides
---

## What domain mode adds

```
meridian deploy 198.51.100.10 --domain vpn.example.com
```

With your own domain, the exit offers two more ways in next to Reality:

- **XHTTP** and **WSS** on your domain, with a Let's Encrypt certificate that Meridian issues and renews (acme.sh)
- Once the domain is proxied through Cloudflare, both paths travel through Cloudflare's CDN — so they keep working even if your server's IP gets blocked

The panel and the connection pages stay on the server's IP certificate; the domain carries only the proxy paths.

Add `--hysteria2` to also offer Hysteria2 on UDP/443 with the domain's certificate (see [Hysteria2 and Cloudflare](#hysteria2-and-cloudflare)).

## Cloudflare setup

**Follow this order** to avoid certificate problems:

1. Add your domain in Cloudflare and create an **A record** pointing to your server IP
2. Keep the cloud icon **grey** ("DNS only")
3. Run `meridian deploy 198.51.100.10 --domain vpn.example.com` — Meridian obtains the certificate
4. Switch the record to **orange cloud** (Proxied)
5. Set SSL/TLS to **Full (Strict)** and turn on **WebSockets** (Network settings)

Deploy prints these steps again when it finishes.

> **Important:** the certificate is issued with an HTTP challenge on port 80. Cloudflare's "Always Use HTTPS" breaks that challenge — turn it off, or exempt `/.well-known/acme-challenge/*`.

## Connection links

Each client's subscription and connection page list every path:

| Path | Route | When it helps |
|------|-------|---------------|
| Reality | Direct to the server IP | Primary — fastest |
| XHTTP | Your domain (through Cloudflare when proxied) | Reality is blocked or throttled |
| WSS | Your domain (through Cloudflare when proxied) | Server IP is blocked |
| Hysteria2 (opt-in) | Your domain over UDP/443 | Lossy or high-latency networks |

Apps that import the Xray JSON or Mihomo subscription switch between paths automatically when one stops working. Other apps show each path as a separate entry.

## Hysteria2 and Cloudflare

Hysteria2 uses QUIC over UDP, which Cloudflare's proxy does not carry. With an orange-cloud record, Hysteria2 clients reach Cloudflare instead of your server and won't connect; the TCP paths are unaffected. QUIC is also throttled in some regions. Only enable `--hysteria2` if your users need it and can reach the server over UDP.

## Adding a domain later

Run your deploy command again with `--domain` added. Existing Reality links keep working. Once a domain is deployed, keep passing the same `--domain` on later redeploys — removing paths from a running server isn't supported yet.
