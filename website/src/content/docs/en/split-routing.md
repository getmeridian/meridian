---
title: Split Routing (Russia)
description: Route Russian traffic directly while proxying everything else — keep VPS IP protected and Russian sites working.
order: 6.5
section: guides
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

## How to set it up

Split routing is configured in the client app. Meridian does not yet ship split routing in its subscriptions.

- **Hiddify** — in settings, set the region to **Russia**. Hiddify then sends Russian sites directly.
- **v2rayNG, v2RayTun, v2rayN, and other Xray apps** — open the app's routing settings and add **direct** rules for `geosite:category-ru` and `geoip:ru`.
- **Mihomo / Clash Meta apps** — subscription refreshes replace the config, so add these rules through the app's override or merge feature, above the final `MATCH` rule:
  ```yaml
  - GEOSITE,category-ru,DIRECT
  - GEOIP,RU,DIRECT
  ```

Menu names differ between apps and versions; look for "Routing", "Region", or "Bypass".

### Why not edit the template in the panel?

Meridian manages its own Xray JSON and Mihomo subscription templates in Remnawave — they carry the automatic fallback between your exits and relays. Meridian restores them on every apply (including redeploys and `client add`, `node add`, `relay deploy`), so rules added there in the panel won't last. Replacing their routing sections can also break the automatic fallback.

## How the layers work together

| Layer | What it does | Protects against |
|-------|-------------|-----------------|
| **Server-side geo-block** | Drops Russia-bound traffic at the exit | VPS IP showing up in Russian service logs |
| **Split routing on the device** | Sends Russian traffic directly | Broken Russian sites, needless proxy load |

With split routing on, Russian traffic never reaches the server. If a device doesn't have the rules (an older app, a new phone), the server still blocks Russian destinations, so the VPS IP stays protected.

If your users need Russian sites through the VPN instead, deploy with `--no-geo-block`.

## DNS considerations

With split routing, Russian domains should resolve through local DNS, and everything else through the proxy, to avoid leaks. Apps with a region or bypass preset usually handle this. If your app has a separate DNS setting, enable its DNS leak protection for proxied traffic.
