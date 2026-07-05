---
title: Deploy Guide
description: Full deployment walkthrough with all configuration options.
order: 3
section: guides
---

This guide covers every deployment option. If you're deploying for the first time, start with [Getting Started](/docs/en/getting-started/) — it walks through the basics in five minutes.

## Basic deployment

```
meridian deploy 1.2.3.4
```

The wizard guides you through configuration. Or specify everything upfront:

```
meridian deploy 1.2.3.4 --sni www.cloudflare.com --client-name alice --yes
```

## All flags

| Flag | Default | Description |
|------|---------|-------------|
| `--sni HOST` | www.cloudflare.com | TLS camouflage target |
| `--domain DOMAIN` | (none) | Cloudflare CDN fallback domain |
| `--client-name NAME` | default | Name for the first client |
| `--display-name NAME` | (none) | Label for connection pages (e.g. "Alice's VPN") |
| `--icon EMOJI_OR_URL` | (none) | Page icon — emoji or image URL |
| `--color PALETTE` | ocean | Page color theme (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH user (non-root gets sudo automatically) |
| `--harden / --no-harden` | enabled | Harden SSH + firewall (disable with `--no-harden` if other services share the server) |
| `--server NAME` | | Target server (name or IP) |
| `--yes` | | Skip confirmation prompts |
| `--warp / --no-warp` | disabled | Route outgoing traffic through Cloudflare WARP |
| `--geo-block / --no-geo-block` | enabled | Block `.ru` domains and Russian IPs through the proxy |
| `--pq / --no-pq` | disabled | Post-quantum encryption — ML-KEM-768 hybrid (experimental) |

## Branding

Personalize connection pages so recipients know who set up their VPN:

```
meridian deploy 1.2.3.4 --display-name "Alice's VPN" --icon 🚀 --color sunset
```

- **`--display-name`** — appears in the trust bar and page title. Use your name or a friendly label.
- **`--icon`** — an emoji or image URL shown at the top of the connection page.
- **`--color`** — sets the accent color palette. Options: `ocean` (default), `sunset`, `forest`, `lavender`, `rose`, `slate`.

These settings are stored in server credentials and apply to all client connection pages.

## Choosing an SNI target

The SNI (Server Name Indication) target is the domain your server impersonates. This is **not** a domain you own — it's any popular website with TLS. When a censor probes your server, they see that real site's certificate, making your server indistinguishable from normal traffic.

The default (`www.cloudflare.com`) works well for most cases. For optimal stealth, scan your server's network for same-ASN targets — these are harder to detect because the IP range matches:

```
meridian scan 1.2.3.4
```

**Good targets** (global CDN):
- `www.cloudflare.com` — Cloudflare CDN, global
- `www.twitch.tv` — Fastly CDN, global
- `dl.google.com` — Google CDN, global
- `github.com` — Fastly CDN, global

**Avoid** `apple.com` and `icloud.com` — Apple controls its own ASN ranges, making the IP/ASN mismatch instantly detectable.

### Camouflage target vs. domain

These are two different things:

- **Camouflage target** (`--sni`) — any popular website you **don't** own. Reality impersonates it so your traffic looks like normal HTTPS to that site. Censors probing see the real site's certificate.
- **Domain** (`--domain`) — a domain you **do** own, pointed to your server via Cloudflare. Adds a CDN fallback connection path. See [Domain mode](/docs/en/domain-mode/).

You can't use your own domain as a camouflage target — the certificate would match your server, not an unrelated site, which defeats the impersonation.

## Pre-flight check

Not sure if your server is compatible?

```
meridian preflight 1.2.3.4
```

Tests SNI target reachability, ASN match, port availability, DNS, OS compatibility, and disk space — without installing anything.

## Re-running deploy

It's safe to re-run `meridian deploy` at any time. The provisioner is fully idempotent:
- Credentials are loaded from cache, not regenerated
- Steps check existing state before acting
- No duplicate work

## Non-root deployment

```
meridian deploy 1.2.3.4 --user ubuntu
```

Non-root users get `sudo` automatically. The user must have passwordless sudo access.

## Local deployment

If you're running Meridian directly on the server (e.g. logged in via SSH as root):

```
meridian deploy local
```

This skips SSH entirely and runs all commands locally. The `local` keyword works with all commands:

```
meridian client add alice --server local
meridian preflight local
meridian scan local
```

Useful when SSH to self doesn't work (missing keys, firewall rules), for re-deploying on the same server, or in cloud-init startup scripts.

## Cloudflare WARP

WARP routes your server's outgoing traffic through Cloudflare's network. Destination websites see a Cloudflare IP address instead of your VPS IP.

```
meridian deploy 1.2.3.4 --warp
```

The interactive wizard also offers this option.

**When WARP helps:**
- Websites block your VPS provider's IP range (common with datacenter IPs on ChatGPT, Netflix, etc.)
- You want to hide the VPS IP from destination sites

**When you don't need WARP:**
- Normal browsing works fine through the proxy — most sites don't block VPS IPs
- You want maximum speed — WARP adds one extra hop (VPS → Cloudflare → destination)

**How it works technically:** Meridian installs the `cloudflare-warp` client on the server in SOCKS5 proxy mode (listening on `127.0.0.1:40000`). Xray uses this as its default outbound — all proxy egress goes through Cloudflare. Incoming connections (SSH, nginx, Xray inbound) are completely unaffected. WARP is free and doesn't require a Cloudflare account.

## Geo-blocking

By default, Meridian blocks `.ru` domains and Russian IP ranges through the proxy:

```bash
meridian deploy 1.2.3.4 --no-geo-block
```

This is intentional. It keeps Russian destinations off the proxy path, which reduces the chance of your VPS IP appearing in Russian-service logs and later getting blocked.

Leave it enabled if:
- Russian sites already work normally without the VPN
- You want the safer default for a shared server

Disable it if:
- You need `.ru` sites to open through Meridian
- You want all traffic to go through the VPN with no exceptions

The interactive wizard asks about this explicitly. The equivalent Xray rules are `geosite:category-ru` and `geoip:ru`.

## Adding a relay node

After deploying your exit server, add a relay node for resilience when the exit IP gets blocked. See the [Relay guide](/docs/en/relay/) for full setup instructions.

```bash
meridian relay deploy RELAY_IP --exit YOUR_EXIT_IP
```

## Management panel

Meridian deploys [3x-ui](https://github.com/MHSanaei/3x-ui) as the web management panel for Xray. You can access it directly in your browser to monitor traffic, view inbound configs, and check server status.

The panel URL and credentials are stored in your local credentials file:

```
cat ~/.meridian/credentials/<IP>/proxy.yml
```

The `panel` section contains everything you need:

```yaml
panel:
  username: a1b2c3d4e5f6
  password: Xk9mP2qR7vW4nL8jF3hT6yBs
  web_base_path: n7kx2m9qp4wj8vh3rf6tby5e
  url: https://198.51.100.1/n7kx2m9qp4wj8vh3rf6tby5e/
```

Open the `url` in your browser and log in with the username and password.

The panel path is randomized for security — treat it like a password. All `meridian` CLI commands use this same panel API under the hood, so anything you can do in the CLI is also visible in the panel.

> **Note:** If you modify settings directly in the panel, they may be overwritten on the next `meridian deploy`.
