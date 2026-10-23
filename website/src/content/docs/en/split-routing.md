---
title: Split Routing (Russia)
description: Route Russian traffic directly while proxying everything else — keep VPS IP protected and Russian sites working.
---

## The problem

Meridian turns on **server-side geo-blocking** by default: the exit drops traffic to Russian domains (`geosite:category-ru`) and Russian IP ranges (`geoip:ru`). This keeps your VPS IP out of Russian services' logs, where it could be spotted and blocked.

The catch: Russian websites don't open while the VPN is on. Users have to disconnect to reach Yandex, VK, Gosuslugi, and other domestic services.

## The solution: split routing on the device

Split routing sends Russian traffic **directly** from the device and everything else through the VPN:

```
Russian sites   → Device → Direct → Internet (local ISP)
Everything else → Device → Proxy → VPS → Internet
```

You get both:
- **VPS protection** — the server-side block stays on as a safety net
- **Working Russian sites** — the device sends them directly, never through the proxy

## How it works in Meridian

Meridian ships split routing in its **Xray JSON** and **Mihomo** subscriptions, so most apps need no setup. Every subscription sends these destinations directly:

- local network addresses (`geosite:private`, `geoip:private`)
- `.ru`, `.su`, and `.рф` domains, plus `geosite:category-ru`
- Russian IP ranges (`geoip:ru`)

It also refuses QUIC (UDP 443) to everything else, so apps fall back to TCP, where the proxy routes them by name.

Apps that import the plain link list (Base64) don't receive routing rules. In those apps, add **direct** rules for `geosite:category-ru` and `geoip:ru` in the routing settings, or switch to an app that imports Xray JSON or Mihomo subscriptions. Hiddify users can instead set the region to **Russia**.

### Why not edit the template in the panel?

Meridian manages its own Xray JSON and Mihomo subscription templates in Remnawave. They carry the automatic fallback between your exits and relays, and the routing above. Meridian restores them on every apply (including redeploys and `client add`, `node add`, `relay add`), so rules added there in the panel won't last.

## How the layers work together

| Layer | What it does | Protects against |
|-------|-------------|-----------------|
| **Server-side geo-block** | Drops Russia-bound traffic at the exit | VPS IP showing up in Russian service logs |
| **Split routing on the device** | Sends Russian traffic directly | Broken Russian sites, needless proxy load |

Russian traffic never reaches the server. If a device doesn't have the rules (a Base64 import, an older app), the server still blocks Russian destinations, so the VPS IP stays protected.

If your users need Russian sites through the VPN instead, deploy with `--no-geo-block`.

## Through a routing gateway

A routing gateway (set up with `meridian setup`) is Xray on a server in front of your exits that picks, rule by rule, where traffic leaves. A rule sends matching traffic to an exit, to a pool of exits, **directly from the gateway**, or nowhere. On a gateway inside Russia, a direct rule opens Russian sites from the gateway's own Russian address, even in apps that get no routing rules:

```
Russian sites   → Device → Gateway (in Russia) → Internet
Everything else → Device → Gateway → Exit (abroad) → Internet
```

In the wizard's routing step, add a rule that matches the country `RU` (destinations in Russian IP ranges) or a few domains (subdomains included), and answer **directly from the gateway** when it asks where the traffic should go. In a topology file this is a rule with `"action": "direct"` and no `target_ref`. Rules apply in order and the first match wins; the review lists each rule with where it goes.

That traffic never reaches the exit, so the exit's geo-block stays as it is. Unlike a relay, the gateway is where this traffic leaves: Russian services see its address, and complaints about that traffic go to its host (see [Choosing hosting](/en/hosting/)).

## DNS considerations

In Xray JSON subscriptions, names that match no rule are resolved through the proxy, so foreign lookups don't leak to the local ISP. Mihomo subscriptions match Russian IP ranges only for connections made by IP and never resolve other names locally. If your app has a separate DNS setting, enable its DNS leak protection for proxied traffic.
