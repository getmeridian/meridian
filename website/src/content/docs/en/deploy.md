---
title: Deploy Guide
description: Full deployment walkthrough with all configuration options.
order: 3
section: guides
---

This guide covers every option of `meridian deploy`. If this is your first deployment, start with [Getting Started](/docs/en/getting-started/).

## Basic deployment

```
meridian deploy 198.51.100.10
```

One server becomes both the control plane (the Remnawave panel) and the exit that carries traffic. Meridian builds the same reviewed topology that [`meridian setup`](/docs/en/cli-reference/#meridian-setup) produces and applies it with checkpoints. Before it prints "Done", it pushes real traffic through the delivered subscription.

Run `meridian deploy` without an IP and it asks for one. Add `--yes` to skip confirmations:

```
meridian deploy 198.51.100.10 --client-name alice --yes
```

## All flags

| Flag | Default | Description |
|------|---------|-------------|
| `--sni HOST` | (auto) | Camouflage target. Without it, deploy prefers a site in the server's own subnet, then `www.google.com`, `dl.google.com`, `www.cloudflare.com` |
| `--domain DOMAIN` | (none) | Your domain for XHTTP and WSS, with Cloudflare CDN fallback |
| `--hysteria2/--no-hysteria2` | on | Offer Hysteria2 on UDP/443 as the last fallback |
| `--client-name NAME` | default | Name of the first client |
| `--display-name NAME` | (none) | Label for connection pages (e.g. "Alice's VPN") |
| `--icon EMOJI_OR_URL` | (none) | Page icon — emoji or image URL |
| `--color PALETTE` | ocean | Page color theme (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH user (non-root users need passwordless sudo) |
| `--ssh-port PORT` | 22 | SSH port, if not 22 |
| `--server NAME` | | Saved server to deploy to (name or IP) |
| `--harden / --no-harden` | on | SSH key-only login, fail2ban, and UFW firewall. Use `--no-harden` on a server shared with other services |
| `--warp / --no-warp` | off | Send outgoing traffic through Cloudflare WARP |
| `--geo-block / --no-geo-block` | on | Block Russian domains and IPs on the server |
| `--client-ips / --no-client-ips` | off | Let the panel see each client's real IP (see [Client IPs](#client-ips)) |
| `--country CC` | looked up | Country shown in connection names, such as `DE`. Meridian looks it up from the address registry; set it if that is wrong |
| `--yes` | | Skip confirmation prompts |

Flags for scripts and UIs (`--json`, `--events`, `--request`, `--dry-run`) are described in the [CLI reference](/docs/en/cli-reference/#meridian-deploy).

## Choosing a camouflage target

The camouflage target (SNI) is the website your server impersonates. It is **not** a domain you own — it is a real site with TLS 1.3. Anyone who probes your server sees that site's real certificate.

The best target lives next to your server. A censor sees the SNI together with your server's IP; when the site's DNS really points into the same network, the pair looks like ordinary traffic, while a famous foreign site on a VPS address stands out.

Without `--sni`, a first deploy scans the server's own subnet for such sites (about a minute) and keeps those whose DNS points into the subnet and that pass Reality's TLS 1.3 and HTTP/2 check. Then it falls back to `www.google.com`, `dl.google.com`, and `www.cloudflare.com`. It uses the first target that real traffic passes through. `meridian node add` picks a neighbor the same way. A target you pass with `--sni` is never swapped; if traffic fails, deploy stops and tells you. A redeploy keeps the saved target.

To see the candidates yourself, scan the server's IPv4 neighborhood:

```
meridian scan 198.51.100.10
```

The scan runs a pinned, checksum-verified build of RealiTLScanner on the server and lists certificate domains it found nearby. Then deploy with your pick:

```
meridian deploy 198.51.100.10 --sni dl.google.com
```

To check one target by hand, run `meridian check 198.51.100.10 --sni HOST`. Avoid `apple.com` and `icloud.com` — their networks don't match VPS providers.

### Camouflage target vs. domain

- **Camouflage target** (`--sni`) — a popular website you **don't** own. Reality impersonates it.
- **Domain** (`--domain`) — a domain you **do** own, pointed at your server. XHTTP and Hysteria2 move to it, and it adds a WSS path through Cloudflare's CDN. See [Domain mode](/docs/en/domain-mode/).

## Hysteria2

```
meridian deploy 198.51.100.10 --no-hysteria2
```

Every deploy offers Hysteria2 on UDP/443 with your domain's certificate, or the server's IP certificate without a domain. Its traffic is wrapped in salamander obfuscation, so censors see random UDP rather than QUIC. It helps on lossy or high-latency networks; where UDP is blocked, apps fall back to the TCP methods listed before it. `--no-hysteria2` turns it off. Hysteria2 doesn't pass through relays.

## Branding

Personalize connection pages so recipients know who set up their VPN:

```
meridian deploy 198.51.100.10 --display-name "Alice's VPN" --icon 🚀 --color sunset
```

- **`--display-name`** — shown in the page header and title
- **`--icon`** — an emoji or image URL shown at the top of the page
- **`--color`** — accent palette: `ocean` (default), `sunset`, `forest`, `lavender`, `rose`, `slate`

Branding is saved in `cluster.yml` and applies to every client's connection page.

## Redeploying

To redeploy, run the same command again. Meridian keeps the saved paths, keys, and users, so existing links keep working. A redeploy without `--sni` keeps the saved camouflage target.

At the end of every deploy, Meridian prints the exact redeploy command with your current settings. Running it again with different flags changes the server to match: adding or dropping `--domain` or `--no-hysteria2` adds or removes those paths.

`meridian deploy` only redeploys a server that is already an exit in your fleet. To add another server, use `meridian node add NEW_IP`.

## Pre-flight check

Not sure a server is suitable?

```
meridian check 198.51.100.10
```

Checks the camouflage target, whether port 443 is free and reachable from outside, optional domain DNS, OS, disk space, and clock skew, without installing anything.

## Non-root deployment

```
meridian deploy 198.51.100.10 --user ubuntu
```

Commands run through `sudo`, so the user needs passwordless sudo.

## Local deployment

If you are logged into the server as root:

```
meridian deploy local
```

This skips SSH. `check local`, `scan local`, and `doctor local` work the same way, and once deployed, later commands on the server run locally too. Running Meridian from your own machine is still recommended, because `meridian test` needs an outside vantage point.

## Cloudflare WARP

WARP sends your server's outgoing traffic through Cloudflare, so websites see a Cloudflare address instead of your VPS IP.

```
meridian deploy 198.51.100.10 --warp
```

**When WARP helps:** sites that block datacenter IP ranges, or when you want to hide the VPS address from destination sites.

**When you don't need it:** most sites work fine without it, and it adds one hop (VPS → Cloudflare → destination).

Meridian installs the `cloudflare-warp` client in proxy mode (SOCKS5 on `127.0.0.1:40000`) and makes it Xray's outbound. Incoming connections are not affected. WARP is free and needs no Cloudflare account.

### WARP for some sites only

To send only chosen sites through WARP and keep everything else direct, add a route on the exit instead of turning WARP on for all traffic. Meridian sets up WARP on the exit the first time a route needs it:

```
meridian route add exit-1 --via warp chatgpt.com netflix.com
meridian route add exit-1 --via block geosite:category-ads-all
meridian route list
meridian route remove exit-1 warp
```

Values are domains (subdomains included), `geosite:NAME` lists, or `geoip:NAME` lists from the stock v2fly data files; one route holds one kind. `--via direct` does the opposite on a WARP exit: those destinations skip WARP. Routes apply in the order you added them, before the exit's default egress and before geo-blocking. These routes live on the server; they are separate from the client-side rules that send Russian sites direct on the user's device.

## Geo-blocking

By default, the server blocks Russian domains (`geosite:category-ru`) and Russian IP ranges (`geoip:ru`) through the proxy. This keeps your VPS IP out of Russian services' logs, which lowers the chance of it being found and blocked.

Turn it off if your users need those sites through Meridian:

```
meridian deploy 198.51.100.10 --no-geo-block
```

For a way to open Russian sites directly while keeping the block, see [Split routing](/docs/en/split-routing/).

## Client IPs

By default the panel never learns where your users connect from: every TCP connection reaches Xray through nginx on the server, so Remnawave records the server's own address. That is the private choice, and nothing about users' locations is stored.

Remnawave's per-user IP limits and connection logs need the real addresses. Turn them on with:

```
meridian deploy 198.51.100.10 --client-ips
```

nginx then passes each client's address to Xray and the panel. Hysteria2 is direct UDP and always shows the real address. Connections through a relay show the relay's address, and WebSocket or XHTTP through the Cloudflare CDN show Cloudflare's. The setting applies to the whole fleet; redeploy without the flag to turn it off.

## Adding a relay

After the exit is running, add a relay on a domestic server so users keep a way in if the exit IP gets blocked. See the [Relay guide](/docs/en/relay/).

```
meridian relay add 203.0.113.10 --exit 198.51.100.10
```

## Management panel

Meridian installs [Remnawave](https://remna.st/) as its panel. The panel is reverse-proxied by nginx on a random secret path, separate from the connection-page path, so links you share never reveal it. `meridian panel` prints the URL and admin login (`--show-password` for the password).

Treat the panel path like a password. The CLI uses the same panel API, so CLI changes show up in the panel. Meridian owns the hosts, profiles, and templates it creates: if you edit those in the panel, `meridian plan` reports the difference and the next apply restores Meridian's version. Client status, limits, expiry, and users you add yourself are yours; see [Web panel](/docs/en/client-management/#web-panel).

Meridian pins the Remnawave versions it has tested. `meridian fleet upgrade` moves your servers to the newest Remnawave release in the same major, without waiting for a Meridian update. If you upgrade Remnawave yourself, Meridian does not downgrade it.
