---
title: Getting Started
description: Install Meridian and deploy your first proxy server in two minutes.
order: 1
section: guides
---

One command turns a fresh VPS into a hardened, undetectable proxy and gives you a link to share. This guide takes you from zero to a working connection in a few minutes.

## Prerequisites

You need:
- A **VPS** running Debian or Ubuntu that you can reach over SSH with a key, as root or a user with passwordless sudo
- A **terminal** on your own machine (macOS, Linux, or [WSL on Windows](/docs/en/wsl/))

## Install the CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

This installs the `meridian` command with [uv](https://docs.astral.sh/uv/) (or pipx as a fallback). See [Installation](/docs/en/installation/) for other options.

Run Meridian on your own machine rather than on the VPS: it connects over SSH, keeps credentials on your device, and lets `meridian test` check the server from the outside.

> Coming from Meridian 3.x? 4.0 is not an in-place upgrade. Read [Upgrading from 3.x](/docs/en/installation/#upgrading-from-3x) first.

## Deploy

```
meridian deploy 198.51.100.10
```

Run `meridian deploy` without an IP and it asks for one. If you are logged into the VPS itself as root, `meridian deploy local` skips SSH.

## What happens

1. **Hardens the server** — SSH key-only login, fail2ban, a UFW firewall that opens only what the deployment needs, BBR congestion control
2. **Installs the control plane** — the [Remnawave](https://remna.st/) panel, plus a node running Xray, all in Docker
3. **Picks a camouflage target** — the server tries `www.google.com`, `dl.google.com`, and `www.cloudflare.com` and uses the first one it can reach (pass `--sni` to choose your own)
4. **Configures VLESS+Reality** on port 443 behind nginx, with a Let's Encrypt certificate for the server's IP
5. **Verifies real traffic** — Meridian pushes traffic through the delivered subscription before saying "Done", and moves to the next camouflage target if traffic doesn't pass
6. **Publishes a connection page** for the first client

The deploy is checkpointed. If it is interrupted, run the same command again and it finishes where it stopped.

## Connect

When the deploy finishes, it prints:

- **A connection page link** with a QR code — send this to the person who will connect. The page has app download links, QR codes, and one-tap "Open in app" buttons.
- **A subscription URL** — paste it into any Xray/V2Ray app. Apps refresh it, so new servers and relays show up automatically.

Recommended apps:

| Platform | App |
|----------|-----|
| iOS | [v2RayTun](https://apps.apple.com/app/v2raytun/id6476628951) |
| Android | [v2rayNG](https://github.com/2dust/v2rayNG/releases/latest) |
| Windows | [v2rayN](https://github.com/2dust/v2rayN/releases/latest) |
| All platforms | [Hiddify](https://github.com/hiddify/hiddify-app/releases/latest) |

Check the server from your machine at any time:

```
meridian test 198.51.100.10
```

## Add more people

```
meridian client add alice
```

Each person gets their own key, connection page, and subscription URL. `meridian client list` shows everyone, and `meridian client disable alice` cuts someone off immediately. See [Client management](/docs/en/client-management/).

## Where things live

| What | Where |
|------|-------|
| Topology, panel access, and keys | `~/.config/meridian/cluster.yml` on your machine |
| Saved SSH servers | `~/.config/meridian/servers.json` on your machine |
| Proxy services | Docker, Xray, nginx on the VPS |

Client commands talk to the Remnawave panel API recorded in `cluster.yml`; they don't need SSH. Back up `~/.config/meridian/` — it is how Meridian manages your servers.

## More than one server

`meridian deploy` covers a single server. For several exits, relays through a domestic server, or routing gateways, use the guided wizard:

```
meridian setup
```

You can also grow a deployment one step at a time with `meridian node add` and `meridian relay add`. Every change goes through the same reviewed topology; `meridian plan` shows what would change and `meridian apply` applies it.

## Next steps

- [Deploy guide](/docs/en/deploy/) — every deploy option, camouflage targets, redeploys
- [Domain mode](/docs/en/domain-mode/) — add a Cloudflare CDN fallback with your own domain
- [Relay nodes](/docs/en/relay/) — give users a domestic entry point
- [IP blocked?](/docs/en/recovery/) — what to do when the server gets blocked
- [Troubleshooting](/docs/en/troubleshooting/) — common issues and fixes
