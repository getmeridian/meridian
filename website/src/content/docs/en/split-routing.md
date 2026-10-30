---
title: Client Routing
description: Choose ru-split or full for client traffic, keep private networks direct, and review every change.
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

The default client policy is **`ru-split`**. Xray JSON, Mihomo, and Stash subscriptions send these destinations directly:

- local network addresses (`geosite:private`, `geoip:private`)
- `.ru`, `.su`, and `.рф` domains, plus `geosite:category-ru`
- Russian IP ranges (`geoip:ru`)

sing-box uses the same policy with inline rules for private IPs and the three Russian domain suffixes. It does not download geosite or geoip lists, so other Russian domains and IP ranges may still use the tunnel.

Both policies block QUIC (UDP 443) after the direct destination rules, so apps fall back to TCP. Both also retain the **BitTorrent direct rule in Xray JSON** to reduce abuse complaints against exits. It matches recognized peer handshakes only: encrypted peers and UDP DHT/uTP may still use the VPN. Mihomo, Stash, and sing-box do not receive a BitTorrent matcher.

### Choose a policy

| Policy | Routing on the device |
|--------|-----------------------|
| `ru-split` (default) | Private/LAN ranges and matched Russian destinations go directly; other traffic uses the tunnel |
| `full` | Internet traffic, including Russian destinations, uses the tunnel; private/LAN ranges stay direct |

```bash
meridian fleet routing
meridian fleet routing full --dry-run
meridian fleet routing full --yes
meridian fleet routing ru-split --yes
```

The bare command shows the current choice and explains both policies. A change edits the topology, shows the review, and applies exactly that review after confirmation. Without a terminal or with `--json`, pass `--yes`; `--dry-run --json` returns the review without applying it. Apps receive the policy at their next subscription refresh (six hours by default, set by `delivery.update_interval_hours`) or after a manual refresh. The setup wizard keeps `ru-split` without an extra question.

`fleet export` and `fleet import` preserve `delivery.client_routing: full`. The `ru-split` default is omitted; removing the field from an exported file restores it. The console shows the policy read-only in Settings with the CLI command that changes it.

Apps that import a plain Base64 link list receive no routing rules. Configure split or full routing in those apps yourself, or use a structured subscription. Hiddify receives the sing-box document and follows its inline rules.

### Where the rules come from

The control service builds every structured subscription from one client routing list and the topology's chosen policy. Client routing changes through [`meridian fleet routing`](/en/cli-reference/#meridian-fleet). [`meridian route`](/en/cli-reference/#meridian-route) controls egress on exits; a [routing gateway](#through-a-routing-gateway) has its own server rules.

## How the layers work together

| Layer | What it does | Protects against |
|-------|-------------|-----------------|
| **Server-side geo-block** | Drops Russia-bound traffic at the exit | VPS IP showing up in Russian service logs |
| **Split routing on the device** | Sends Russian traffic directly | Broken Russian sites, needless proxy load |

With `ru-split`, destinations matched by the client's Russian bypass rules do not reach the exit. If a device has no routing rules (a Base64 import or an older app), the exit still blocks Russian destinations while its geo-block is on.

`full` changes client routing only. To reach Russian sites through an exit, that exit must also allow them: deploy it with `--no-geo-block`. Changing the client policy does not change server-side geo-blocking.

## Through a routing gateway

A routing gateway (set up with `meridian setup`) is Xray on a server in front of your exits that picks, rule by rule, where traffic leaves. A rule sends matching traffic to an exit, to a pool of exits, **directly from the gateway**, or nowhere. On a gateway inside Russia, a direct rule opens Russian sites from the gateway's own Russian address, even in apps that get no routing rules:

Choose `full` if this traffic should reach the gateway's rules. With `ru-split`, matched Russian destinations already leave directly from the device.

```
Russian sites   → Device → Gateway (in Russia) → Internet
Everything else → Device → Gateway → Exit (abroad) → Internet
```

In the wizard's routing step, add rules that match the zones `ru, su, рф` (every site under them), the site list `category-ru` (Russian services under other zones), and the country `RU` (Russian IP ranges, for apps that connect by address), and answer **directly from the gateway** each time it asks where the traffic should go. Zone and list rules match the site name as is; a country rule makes the gateway look the name up first. In a topology file a direct rule has `"action": "direct"` and no `target_ref`; zones go in a `"match": "domain"` rule and lists in a `"match": "geosite"` rule. Rules apply in order and the first match wins; the review lists each rule with where it goes.

That traffic never reaches the exit, so the exit's geo-block stays as it is. Unlike a relay, the gateway is where this traffic leaves: Russian services see its address, and complaints about that traffic go to its host (see [Choosing hosting](/en/hosting/)).

## DNS considerations

In Xray JSON subscriptions, names that match no rule are resolved through the proxy, so foreign lookups don't leak to the local ISP. With `ru-split`, Mihomo matches Russian IP ranges only for connections made by IP and never resolves other names locally. If your app has a separate DNS setting, enable its DNS leak protection for proxied traffic.
