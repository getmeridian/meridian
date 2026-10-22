---
title: Domain Mode
description: Add CDN fallback via Cloudflare for IP-blocking resilience.
---

## What domain mode adds

```
meridian deploy 198.51.100.10 --domain vpn.example.com
```

Without a domain, the exit already offers Reality, plus XHTTP and Hysteria2 on the server's IP address. Your own domain changes two things:

- **XHTTP** moves to your domain, and **WSS** is added there, with a Let's Encrypt certificate that Meridian issues and renews (acme.sh)
- Once the domain is proxied through Cloudflare, both paths travel through Cloudflare's CDN — so they keep working even if your server's IP gets blocked

The panel and the connection pages stay on the server's IP certificate; the domain carries only the proxy paths.

Hysteria2 on UDP/443 switches to the domain's certificate too (see [Hysteria2 and Cloudflare](#hysteria2-and-cloudflare)); `--no-hysteria2` turns it off.

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
| Hysteria2 | Your server's IP over UDP/443, with the domain's certificate | Lossy or high-latency networks, or when every TCP path is blocked |

Apps that import the Xray JSON, Mihomo, or sing-box subscription switch between paths automatically when one stops working. Other apps show each path as a separate entry.

## Hysteria2 and Cloudflare

Hysteria2 dials your server's IP over UDP and uses the domain only for its TLS certificate, so it keeps working when the record is proxied through Cloudflare (which can't carry it). Salamander obfuscation makes its packets look random rather than like QUIC. Where UDP is blocked, apps fall back to the TCP paths listed before it.

## Adding a domain later

Run your deploy command again with `--domain` added. Existing Reality links keep working. To drop the domain later, redeploy without `--domain`: WSS is removed, XHTTP and Hysteria2 move back to the server's IP certificate, and subscriptions update.
