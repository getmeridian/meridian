---
title: Split Routing (Russia)
description: Route Russian traffic directly while proxying everything else — keep VPS IP protected and Russian sites working.
order: 6.5
section: guides
---

## The problem

Meridian enables **server-side geo-blocking** by default: the Xray server drops traffic destined for Russian domains and IPs (via `geosite:category-ru` + `geoip:ru` routing rules). This protects your VPS IP from appearing in Russian service logs.

But it also means Russian websites don't work through the VPN. Users have to disconnect to access Yandex, VK, Gosuslugi, and other domestic services.

## The solution: client-side split routing

Split routing sends Russian traffic **directly** from the client (bypassing the proxy) while routing everything else through the VPN:

```
Russian sites  → Client → Direct → Internet (via local ISP)
Everything else → Client → Proxy → VPS → Internet
```

This gives you both:
- **VPS protection** — server-side geo-block stays active as a safety net
- **Working Russian sites** — client routes them directly, never touching the proxy

## How to configure

Meridian uses Remnawave's subscription template system. The subscription template controls what routing rules clients receive when they import their connection.

### Step 1: Open the Remnawave panel

Navigate to your panel URL (shown after `meridian deploy`) and log in.

### Step 2: Edit the subscription template

1. Go to **Subscription Settings** → **Templates**
2. Find the **Xray JSON** template (or create a new one)
3. Replace the routing/DNS sections with the split routing config below

### Xray JSON template

Add these sections to your Xray JSON subscription template. The `dns` block ensures Russian domains resolve via the local DNS server, and the `routing` block sends Russian traffic direct:

```json
{
    "log": {
        "loglevel": "warning"
    },
    "dns": {
        "servers": [
            {
                "address": "https://dns.google/dns-query",
                "domains": ["geosite:geolocation-!cn"]
            },
            "8.8.8.8",
            {
                "address": "localhost",
                "domains": [
                    "geosite:category-ru",
                    "domain:.ru",
                    "domain:.su",
                    "domain:.рф"
                ],
                "expectIPs": ["geoip:ru"]
            }
        ]
    },
    "routing": {
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {
                "type": "field",
                "domain": ["geosite:private"],
                "outboundTag": "direct"
            },
            {
                "type": "field",
                "ip": ["geoip:private"],
                "outboundTag": "direct"
            },
            {
                "type": "field",
                "domain": [
                    "geosite:category-ru",
                    "domain:.ru",
                    "domain:.su",
                    "domain:.рф"
                ],
                "outboundTag": "direct"
            },
            {
                "type": "field",
                "ip": ["geoip:ru"],
                "outboundTag": "direct"
            }
        ]
    }
}
```

A reference copy of this template is included in the Meridian source at `src/meridian/data/subscription-templates/xray-split-ru.json`.

### Mihomo / Clash template

For clients using Mihomo (Clash Meta), add these rules to the subscription template:

```yaml
rules:
  - DOMAIN-SUFFIX,.ru,DIRECT
  - DOMAIN-SUFFIX,.su,DIRECT
  - DOMAIN-SUFFIX,.рф,DIRECT
  - GEOSITE,category-ru,DIRECT
  - GEOIP,RU,DIRECT
  - MATCH,PROXY
```

Reference copy: `src/meridian/data/subscription-templates/mihomo-split-ru.yaml`.

## How it works together

The two layers complement each other:

| Layer | What it does | Protects against |
|-------|-------------|-----------------|
| **Server-side geo-block** (Xray routing) | Drops RU-bound traffic at the server | VPS IP exposure in Russian service logs |
| **Client-side split routing** (subscription template) | Routes RU traffic direct, skips proxy | Broken Russian sites, unnecessary proxy load |

If split routing is configured, Russian traffic never reaches the server — the client sends it directly. The server-side geo-block acts as a safety net: if a client doesn't apply the template (older app, manual config), the server still blocks Russian destinations.

## Client compatibility

Split routing requires clients that support Xray routing rules:

| Client | Platform | Split routing support |
|--------|----------|----------------------|
| v2rayNG | Android | Yes (Xray JSON) |
| Hiddify | Android, iOS | Yes (Xray JSON) |
| Streisand | iOS | Yes (Xray JSON) |
| V2Box | iOS | Yes (Xray JSON) |
| Nekoray / Nekobox | Desktop | Yes (Xray JSON) |
| Clash Meta / Mihomo | All | Yes (Mihomo YAML, different format) |

Clients that don't parse routing rules will proxy all traffic. Russian sites won't work through the proxy (server-side geo-block drops them), but the VPS IP stays protected.

## DNS leak considerations

Some clients don't route DNS queries through the proxy by default. With split routing, this is actually desirable for Russian domains — they should resolve via local DNS. However, non-Russian DNS queries should go through the proxy's DNS to avoid leaks.

The template above handles this with the `dns` section: Russian domains resolve locally (`address: "localhost"`), everything else uses DNS-over-HTTPS (`dns.google`).

If your client ignores the `dns` section (some minimal clients do), consider enabling the client's built-in DNS leak protection for non-Russian traffic.
