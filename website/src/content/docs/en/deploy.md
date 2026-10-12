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
| `--sni HOST` | (auto) | Camouflage target. Without it, the server picks the first working target from `www.google.com`, `dl.google.com`, `www.cloudflare.com` |
| `--domain DOMAIN` | (none) | Your domain for XHTTP and WSS, with Cloudflare CDN fallback |
| `--hysteria2` | off | Also offer Hysteria2 on UDP/443 (needs `--domain`) |
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
| `--yes` | | Skip confirmation prompts |

Flags for scripts and UIs (`--json`, `--events`, `--request`, `--dry-run`) are described in the [CLI reference](/docs/en/cli-reference/#meridian-deploy).

## Choosing a camouflage target

The camouflage target (SNI) is the website your server impersonates. It is **not** a domain you own — it is a popular site with TLS 1.3. Anyone who probes your server sees that site's real certificate.

Without `--sni`, the server itself tries `www.google.com`, `dl.google.com`, and `www.cloudflare.com` in order and uses the first one it can reach. If real traffic then fails through that target, Meridian moves on to the next one before finishing. A target you pass with `--sni` is never swapped; if traffic fails, deploy stops and tells you.

To find a target on the same network as your server, scan its IPv4 neighborhood:

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
- **Domain** (`--domain`) — a domain you **do** own, pointed at your server. It adds XHTTP and a WSS path through Cloudflare's CDN. See [Domain mode](/docs/en/domain-mode/).

## Hysteria2

```
meridian deploy 198.51.100.10 --domain vpn.example.com --hysteria2
```

Adds Hysteria2 on UDP/443 using your domain's certificate. It can help on lossy or high-latency networks, but QUIC is throttled or blocked in some regions, so it is opt-in. Hysteria2 doesn't pass through relays.

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

At the end of every deploy, Meridian prints the exact redeploy command with your current settings. Running it again with different flags changes the server to match: adding or dropping `--domain` or `--hysteria2` adds or removes those paths.

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

## Geo-blocking

By default, the server blocks Russian domains (`geosite:category-ru`) and Russian IP ranges (`geoip:ru`) through the proxy. This keeps your VPS IP out of Russian services' logs, which lowers the chance of it being found and blocked.

Turn it off if your users need those sites through Meridian:

```
meridian deploy 198.51.100.10 --no-geo-block
```

For a way to open Russian sites directly while keeping the block, see [Split routing](/docs/en/split-routing/).

## Adding a relay

After the exit is running, add a relay on a domestic server so users keep a way in if the exit IP gets blocked. See the [Relay guide](/docs/en/relay/).

```
meridian relay add 203.0.113.10 --exit 198.51.100.10
```

## Management panel

Meridian installs [Remnawave](https://remna.st/) as its panel. The panel is reverse-proxied by nginx on a random secret path, separate from the connection-page path, so links you share never reveal it. Deploy prints the panel URL as "Remnawave panel (advanced)"; the URL and admin login are in the `panel` section of `~/.meridian/cluster.yml`.

Treat the panel path like a password. The CLI uses the same panel API, so CLI changes show up in the panel. Meridian owns the hosts, profiles, and templates it creates: if you edit those in the panel, `meridian plan` reports the difference and the next apply restores Meridian's version.

Meridian pins the Remnawave versions it has tested. If you upgrade Remnawave yourself, Meridian does not downgrade it.
