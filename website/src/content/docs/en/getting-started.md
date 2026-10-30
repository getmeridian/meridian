---
title: Getting Started
description: Install Meridian and deploy your first self-hosted VPN server.
---

One command turns a fresh VPS into a hardened VPN server, with a built-in web panel for you and a connection page for each person. This guide takes you from zero to a working connection in a few minutes.

Someone sent you a link? You don't need any of this: see [Connecting](/en/connecting/).

## Prerequisites

You need:
- A **VPS** running Debian or Ubuntu that you can reach over SSH with a key, as root or a user with passwordless sudo
- A **terminal** on your own machine (macOS, Linux, or [WSL on Windows](/en/wsl/))

## Install the CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

This installs the `meridian` command with [uv](https://docs.astral.sh/uv/) (or pipx as a fallback). See [Installation](/en/installation/) for other options.

Run Meridian on your own machine rather than on the VPS: it connects over SSH, keeps credentials on your device, and lets `meridian test` check the server from the outside.

> Coming from Meridian 3.x? Meridian 4 is not an in-place upgrade. Read [Upgrading from 3.x](/en/installation/#upgrading-from-3x) first.

## Deploy

```
meridian deploy 198.51.100.10
```

Run `meridian deploy` without an IP and it asks for one. If you are logged into the VPS itself as root, `meridian deploy local` skips SSH.

## What happens

1. **Hardens the server** — SSH key-only login, fail2ban, a UFW firewall that opens only what the deployment needs, BBR congestion control
2. **Installs the control service and web panel** — Meridian's control service keeps your users, serves their subscriptions, and hosts the web panel; a node agent on the server runs Xray. No Docker, no third-party panel
3. **Picks a camouflage target** — it looks for a suitable TLS 1.3 site on the server's own network, then falls back to `www.google.com`, `dl.google.com`, and `www.cloudflare.com` (pass `--sni` to choose your own)
4. **Configures the connection methods** — VLESS+Reality and XHTTP on TCP 443 behind nginx, with a Let's Encrypt certificate for the server's IP, and Hysteria2 on UDP 443
5. **Verifies real traffic** — Meridian pushes traffic through the delivered subscription before saying "Done", and moves to the next camouflage target if traffic doesn't pass
6. **Publishes a connection page** for the first person and prepares your web panel
7. **Shows a recovery key once** — keep it offline; with it, a lost control server can be rebuilt (see [Backups](/en/deploy/#backups))

The deploy is checkpointed. If it is interrupted, run the same command again and it finishes where it stopped.

## Connect

When the deploy finishes, it prints:

- **A connection page link** with a QR code — send this to the person who will connect. The page has app download links, QR codes, and one-tap "Open in app" buttons.
- **A subscription URL** — paste it into any Xray/V2Ray app. Apps refresh it, so new servers and relays show up automatically.

Recommended apps:

| App | Platforms |
|-----|-----------|
| [Happ](https://happ.su/) | iOS, Android, Windows, macOS, Linux |
| [FlClashX](https://github.com/pluralplay/FlClashX/releases/latest) | Android, Windows, macOS, Linux |
| [Clash Mi](https://clashmi.app/download) | iOS, Android, Windows, macOS, Linux |
| [Throne](https://github.com/throneproj/Throne/releases/latest) | Windows, macOS, Linux |

The connection page lists these first and links more apps for each platform.

Check the server from your machine at any time:

```
meridian test 198.51.100.10
```

## Manage people: web panel or CLI

Open the built-in web panel (owner console) in your browser:

```
meridian panel
```

The first time, it prints the panel address and a one-time setup link where you choose a password. From the web panel you can add people, share their connection pages and QR codes, set expiry dates, traffic limits, and device caps, leave private notes and tags, post a notice for everyone, and check server health and monthly allowances.

Or manage people from the terminal:

```
meridian client add alice
```

Each person gets their own key, connection page, and subscription URL. `meridian client list` shows everyone, and `meridian client disable alice` cuts someone off immediately. See [Client management](/en/client-management/).

## Where things live

| What | Where |
|------|-------|
| Topology, control service access, and keys | `~/.config/meridian/cluster.yml` on your machine |
| Saved SSH servers | `~/.config/meridian/servers.json` on your machine |
| Encrypted snapshots of the control service | `~/.config/meridian/snapshots/` on your machine |
| Users, limits, traffic, and the web panel | The control service on the VPS |
| VPN services | Xray, nginx, and the node agent on the VPS |

Client commands talk to the control service's API recorded in `cluster.yml`; they don't need SSH. `meridian panel` opens the same people and settings in the web panel. Back up `~/.config/meridian/` — it is how Meridian manages your servers.

## More than one server

`meridian deploy` covers a single server. For several exits, relays through a domestic server, or routing gateways, use the guided wizard:

```
meridian setup
```

You can also grow a deployment one step at a time with `meridian node add` and `meridian relay add`. Every change goes through the same reviewed topology; `meridian plan` shows what would change and `meridian apply` applies it.

## Separate fleets

To keep fleets apart, say one for family and one for work, give each its own context. The first deploy into a new context creates it; `context use` makes it current, and `--context` runs one command on another fleet:

```
meridian deploy 203.0.113.5 --context work
meridian context list
meridian context use work
meridian --context default client list
```

Your first fleet is the `default` context and stays in `~/.config/meridian/`; every other context keeps its own `cluster.yml` and saved servers in `~/.config/meridian/contexts/NAME/`. Meridian's SSH key is shared, and a server belongs to one fleet at a time. `MERIDIAN_CONTEXT=NAME` chooses a context for one shell; `MERIDIAN_HOME` pins a single fleet and turns contexts off. See [meridian context](/en/cli-reference/#meridian-context).

## Next steps

- [Deploy guide](/en/deploy/) — every deploy option, camouflage targets, redeploys
- [Domain mode](/en/domain-mode/) — add a Cloudflare CDN fallback with your own domain
- [Relay nodes](/en/relay/) — give users a domestic entry point
- [IP blocked?](/en/recovery/) — what to do when the server gets blocked
- [Troubleshooting](/en/troubleshooting/) — common issues and fixes
