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
Everything else → Device → VPN → VPS → Internet
```

You get both:
- **VPS protection** — the server-side block stays on as a safety net
- **Working Russian sites** — the device sends them directly, never through the VPN

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

`fleet export` and `fleet import` preserve `delivery.client_routing: full`. The `ru-split` default is omitted; removing the field from an exported file restores it. The web panel shows the policy read-only in Settings with the CLI command that changes it.

Apps that import a plain Base64 link list receive no routing rules. Configure split or full routing in those apps yourself, or use a structured subscription. Hiddify receives the sing-box document and follows its inline rules.

### Where the rules come from

The control service builds every structured subscription from one client routing list and the topology's chosen policy. Client routing changes through [`meridian fleet routing`](/en/cli-reference/#meridian-fleet). [`meridian route`](/en/cli-reference/#meridian-route) controls egress on exits; a [routing gateway](#through-a-routing-gateway) has its own server rules.

## How the layers work together

| Layer | What it does | Protects against |
|-------|-------------|-----------------|
| **Server-side geo-block** | Drops Russia-bound traffic at the exit | VPS IP showing up in Russian service logs |
| **Split routing on the device** | Sends Russian traffic directly | Broken Russian sites, needless VPN load |

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

In Xray JSON subscriptions, names that match no rule are resolved through the VPN tunnel, so foreign lookups don't leak to the local ISP. With `ru-split`, Mihomo matches Russian IP ranges only for connections made by IP and never resolves other names locally. If your app has a separate DNS setting, enable its DNS leak protection for tunneled traffic.


## Named policies and DNS

Edit `delivery.delivery_policies` in an exported fleet file and review it with `meridian fleet import`. Each policy has an `id`, a built-in `preset` (`full` or `ru-split`), ordered `rules`, and optional `dns`. Set `delivery.default_policy` to its name, or select it with `meridian fleet routing work --dry-run` and `meridian fleet routing work --yes`. The built-in names cannot be replaced. Existing fleets keep exactly the same preset subscriptions.

```json
{
  "delivery_policies": [{
    "id": "work",
    "preset": "full",
    "rules": [{
      "match": {"kind": "domain_suffix", "values": ["example.test"]},
      "action": {"kind": "block"}
    }],
    "dns": {
      "resolvers": [{"transport": "doh", "address": "198.51.100.53", "path": "/dns-query"}],
      "fake_ip": false
    }
  }],
  "default_policy": "work"
}
```

This is the `delivery` section, with documentation addresses: replace the resolver with your own. `profile_policies` maps access-profile names, including `default`, to policy names. Profiles without a mapping follow the fleet; multiple mapped profiles must select the same policy. A preset selection through `fleet routing` clears a named fleet default. Removing the optional fields restores the existing preset behavior.

Single and bulk profile previews, `--dry-run`, and undo dry runs resolve each person's delivery choice before returning a review. Conflicting mapped profiles are refused unless a saved person override selects the policy. Initial memberships in `access.creation_settings` are also checked for conflicting mapped defaults during intent validation and compilation, before a plan or apply.

Rules match domain suffixes, full domains, ASCII keywords, pinned `geosite`/`geoip` lists, CIDRs, destination ports, or TCP/UDP. Use `match.kind` values `domain_suffix`, `domain_full`, `domain_keyword`, `geosite`, `geoip`, `cidr`, `port`, and `network`. Port values are objects such as `{"start": 8443, "end": 8445}`; omitting `end` selects one port. Values within a match are alternatives. Optional rule `network` and `ports` conditions narrow the whole match. LAN bypasses come first, custom rules follow in their written order, and the preset's remaining rules follow them. The first match wins.

Stash receives private-range matches as `LAN`, including inside rules with network and port conditions. These IP matches retain `no-resolve`.

Geosite attributes select literal keys in the pinned data. For example, `@!cn` matches entries carrying `!cn`; entries without that attribute do not match.

Actions are `{"kind":"proxy"}`, `{"kind":"direct"}`, `{"kind":"block"}`, or `{"kind":"exit","exit":"west"}` for a declared exit. Exit groups use eligible connections. A delivered profile without that exit blocks the matching destination; it never chooses another exit. Xray's individual and fingerprint profiles retain their isolation. BitTorrent detection remains best-effort and Xray-only.

DNS resolvers use `transport: doh | dot | plain`, a unicast IP `address`, and an optional `port` (443, 853, or 53 by default). DoH accepts a bounded `path`; plain DNS accepts `protocol: udp | tcp`. Encrypted resolvers must have a certificate valid for their IP. Hostname bootstrap, credentials, raw templates, and regex rules are refused. Xray supports DoH/plain but omits DoT and fake-IP; Mihomo and sing-box support all three transports and fake-IP; Stash keeps its native DNS mode and omits fake-IP. Base64 carries neither routing nor DNS settings. An omitted DNS choice is never converted to a weaker transport.

Each policy has at most 128 rules, 64 values per domain/IP match, and four resolvers; the catalog has at most 64 policies. A policy must fit within 64 KiB and the catalog within 256 KiB. Geodata names must exist in the pinned files clients already have. No policy adds remote rule-set fetching; sing-box omits geosite and non-private geoip matches. Refresh the subscription after applying a change.


### Private person routing

`meridian client routing person` reads the effective policy. Use `meridian client routing person work --dry-run --json`, then `meridian client routing person work --yes --json` to review and save a private override; `meridian client routing person --inherit --yes` clears it. The console's person card uses the same service review and keeps drafts in memory. Person overrides win over profile defaults, which win over the fleet default. Applies and credential reissue preserve overrides; encrypted snapshots carry them, and deletion removes them. This choice changes subscription delivery at app refresh, with no account or node-access edit.

Policy reads need `people.read` and `people.private`; assignment additionally needs `people.delivery`. `delivery.read` reads the reviewed catalog. Existing issued tokens keep their frozen grants; issue a token with the new grant to assign policies. Overrides are blank without private permission and never appear in subscriptions, events, or reviewed intent. A preview writes nothing. Confirmation requires its `review_hash`; a changed override, membership, or catalog needs another preview.

| Operation | Meaning |
| --- | --- |
| `GET /api/delivery-policies` | Reviewed catalog and profile defaults; `delivery.read` |
| `GET /api/users/{username}/delivery-policy` | Private override and effective policy; `people.read` and `people.private` |
| `PUT /api/users/{username}/delivery-policy` | `{ "policy": "work", "confirm": false }` previews; confirm with its `review_hash`; `people.delivery`, `people.read`, `people.private`. Explicit `policy: null` inherits. |


## Preview and explain delivery

`meridian client preview person --suffix singbox --json` and the console's subscription preview show the effective policy, its source, every format's ordered rules and DNS, and omitted features with reasons. The optional Destination field explains the selected format. Xray omissions distinguish individual and fingerprint profiles that cannot reach a requested exit. Policy rule numbers count LAN first, then custom rules, then the preset tail; they are shared across formats even when a client needs several serialized rules.

```bash
meridian fleet routing set work --dry-run --json
meridian fleet routing explain chat.example.test:443 --person person --format singbox --json
meridian fleet routing explain 198.51.100.10 --format xray-json --port 443 --network tcp --protocol other --json
```

`fleet routing explain` reads the currently applied service settings and optional private person assignment. It returns the first matched rule and action, for example “this destination goes via exit west because rule 2 matched.” Omit `--person` to explain the fleet default. `--format` selects Xray JSON, Mihomo/Clash, Stash, sing-box, or Base64. TCP and ordinary traffic are assumed unless supplied; `--protocol bittorrent` supplies synthetic detected-protocol evidence. No DNS, reachability, or network lookup occurs. A missing port or cached pinned geodata is reported as unknown when an earlier rule needs it. Xray checks names with `IPIfNonMatch`; its final TCP/UDP rule sends unmatched names through the tunnel without a local DNS routing pass. IP rules match connections addressed by IP. Actual app selection and reachability can change which connection an automatic group uses.

| Format / apps | Destination rules | DNS | fake-IP | Via exit | BitTorrent |
| --- | --- | --- | --- | --- | --- |
| Xray JSON apps | All eight typed matchers | DoH, plain | Omitted | Yes, within the delivered profile | Best-effort detection |
| Mihomo / Clash Meta | All eight | DoH, DoT, plain | Yes | Primary connections only | Omitted |
| Stash | All eight | DoH, DoT, plain | Native mode; custom fake-IP omitted | Primary connections only | Omitted |
| sing-box / Hiddify | Inline domains, CIDR, ports, TCP/UDP, private IPs | DoH, DoT, plain | Yes | Supported connections only | Omitted |
| Base64 link apps | No rules | No settings | No | No rule-based choice | No |

The service reads geodata only from already-cached Xray archives whose hash matches Meridian's pin. Explanations never start a download. Clients never receive remote rule sets. Xray sends configured public DNS through its automatic group and retains direct LAN resolvers; other clients use their represented DNS settings. Choosing an unsupported transport never converts it to plain DNS. Existing preset bytes and default fleet corpus answers stay unchanged.

`POST /api/delivery-policies/explain` accepts `destination`, optional `person`, `format`, `port`, `network`, and `protocol`; it needs `delivery.explain`, plus `people.read` and `people.private` with a person. Subscription previews require private permission for policy details or a destination explanation; lower grants receive the existing metadata only. Connection-page `/info` may include `user.deliveryCapabilities`: format names, support flags, and omission codes, without policy names, assignments, memberships, destinations, or endpoint details. The page keeps this fresh data in memory.
