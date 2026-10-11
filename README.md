<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/logo-512.png" width="80" alt="Meridian">
</p>

<h1 align="center">Meridian</h1>

<p align="center">
  <a href="https://github.com/getmeridian/meridian/actions/workflows/ci.yml"><img src="https://github.com/getmeridian/meridian/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/meridian-vpn/"><img src="https://img.shields.io/pypi/v/meridian-vpn" alt="PyPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/getmeridian/meridian/stargazers"><img src="https://img.shields.io/github/stars/getmeridian/meridian" alt="GitHub stars"></a>
</p>

<p align="center">Deploy it right. Share it easily.<br>One command sets up an undetectable proxy — firewall, TLS, routing, all hardened by default.</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/deploy-terminal.svg" width="720" alt="Meridian deploy terminal output">
</p>

## What is this

Most proxy setups leak — an open port here, a TLS mismatch there, a fingerprint that gives the server away. Meridian locks every layer down automatically: firewall, certificates, SNI routing, fingerprinting. You deploy in one command. Your people connect by scanning a QR code. That's it.

When an IP gets blocked, add a new server with one command; your users pick it up on their next subscription refresh.

Whether you're the "tech friend" setting up a VPN for people you care about, a power user running several servers, or an NGO providing access in a censored region, Meridian handles the complexity so you can focus on staying connected.

See [SECURITY.md](SECURITY.md) for the threat model and what Meridian protects against (and what it doesn't).

## Why Meridian?

Meridian ships the strongest available protocol — today that's VLESS+Reality — and configures it so your server is indistinguishable from any other website. Nothing left open, nothing to give it away.

| | Meridian | Raw Remnawave | Marzban | Hiddify Manager |
|---|---|---|---|---|
| Install | One command | Manual Docker + panel config | Docker + CLI | Script + web UI |
| Client handoff | QR + hosted page | Manual URL sharing | Panel-only | Panel-only |
| Architecture | nginx+Xray+Remnawave (hardened) | Remnawave panel+node | Xray+Nginx | Xray+Nginx |
| Relay support | Built-in L4 relay | Manual | No | Manual |
| Rebuild workflow | `node add NEW_IP` | Start over | Reconfigure | Reconfigure |

## Install

Works on **macOS and Linux**. Windows users: use WSL.

```bash
curl -sSf https://getmeridian.org/install.sh | bash
```

Or install directly from PyPI:

```bash
uv tool install meridian-vpn    # recommended
pipx install meridian-vpn       # alternative
```

> **Upgrading from 3.x?** Meridian 4.0 is not an in-place upgrade: it replaces 3x-ui with Remnawave, and every user needs a new link. Your 3.x servers keep working. See [Upgrading from 3.x](https://getmeridian.org/docs/en/installation/#upgrading-from-3x).

## Quick start

```bash
meridian deploy 198.51.100.10
```

That's the whole setup. Meridian hardens the server, installs the Remnawave panel and Xray, picks a camouflage target that works from your server, verifies real traffic, and prints a **connection page** (QR codes, app links) plus a **subscription URL** to share.

<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/connection-page.png" width="360" alt="Connection page with QR codes">
</p>

Give more people access:

```bash
meridian client add alice            # her own connection page and subscription
meridian client list                 # everyone, with status and traffic
meridian client disable alice        # cut off access immediately
```

Options worth knowing:

```bash
meridian deploy 198.51.100.10 --domain vpn.example.com   # add a Cloudflare CDN fallback
meridian deploy 198.51.100.10 --no-harden                # server shared with other services
meridian deploy local                                    # run on the server itself
```

By default the server blocks Russian domains and IPs through the proxy, to keep your VPS IP out of Russian services' logs. Deploy with `--no-geo-block` if your users need those sites through Meridian.

To redeploy, run the same command again — paths, keys, and users are kept.

## More than one server

```bash
meridian setup                                          # guided wizard: exits, relays, routing, users
meridian node add 198.51.100.11 --name germany          # another exit
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 # domestic entry point
```

Every change goes through one reviewed topology stored in `~/.meridian/cluster.yml`. `meridian plan` shows what would change; `meridian apply` applies it with checkpoints, so an interrupted run resumes where it stopped.

## How it works

Meridian deploys [VLESS+Reality](https://github.com/XTLS/Xray-core) — a protocol that makes your server indistinguishable from a legitimate website:

| Censorship method | How Meridian beats it |
|---|---|
| **Deep Packet Inspection** | Traffic looks like ordinary HTTPS. No proxy signatures. |
| **Active probing** | Anyone probing your server sees the real certificate of a popular site (such as Google's). Only clients with your key reach the proxy. |
| **TLS fingerprinting** | uTLS copies Chrome's exact Client Hello, matching billions of real devices. |
| **IP blocking** | Domain mode adds XHTTP and WSS through Cloudflare's CDN; relays add a domestic entry point. |

<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/architecture.svg" width="720" alt="Meridian architecture — nginx SNI routing, TLS, Xray Reality">
</p>

- **Single server** — nginx on port 443 routes Reality traffic to Xray by SNI and serves the panel, connection pages, and subscriptions with a Let's Encrypt IP certificate. No domain needed.
- **Domain mode** — adds XHTTP and WSS on your domain, carried by Cloudflare's CDN when the server IP is blocked. Hysteria2 over UDP/443 is available as an opt-in.
- **Relays** — a lightweight TCP forwarder (Realm) on a domestic server passes Reality traffic to the exit abroad, encrypted end to end.

## What you need

- A VPS (Debian/Ubuntu) with SSH key access as root or a sudo user — about $5/month from most providers
- Optional: a domain on Cloudflare for the CDN fallback

## Commands

| Command | Description |
|---------|-------------|
| `meridian deploy [IP\|local]` | Deploy one server and get a shareable link |
| `meridian setup` | Guided, resumable wizard for several servers |
| `meridian plan` / `meridian apply` | Review and apply the topology in `cluster.yml` |
| `meridian client add\|show\|list\|enable\|disable` | Manage the people who connect |
| `meridian node add\|list\|check` | Manage exit servers |
| `meridian relay deploy\|list` | Manage relays |
| `meridian fleet status\|inventory` | Fleet health and inventory |
| `meridian test [IP]` | Push real traffic through the delivered subscription (no SSH) |
| `meridian probe [IP]` | Probe your server as a censor would |
| `meridian preflight [IP]` / `meridian scan [IP]` | Check a server / find camouflage targets near it |
| `meridian doctor [IP]` | Collect diagnostics for bug reports |
| `meridian teardown [IP]` | Remove Meridian from a server |
| `meridian update` | Update the CLI |

Removing a client, node, or relay isn't available yet; `client disable` revokes access immediately. Most commands support `--json` for scripting. See the [CLI reference](https://getmeridian.org/docs/en/cli-reference/) for every command and flag.

## Client apps

| Platform | App |
|----------|-----|
| iOS | [v2RayTun](https://apps.apple.com/app/v2raytun/id6476628951) |
| Android | [v2rayNG](https://github.com/2dust/v2rayNG/releases/latest) |
| Windows | [v2rayN](https://github.com/2dust/v2rayN/releases/latest) |
| All platforms | [Hiddify](https://github.com/hiddify/hiddify-app/releases/latest) |

## Troubleshooting

Not connecting? Run `meridian test 198.51.100.10` from your machine, or use the [web-based ping tool](https://getmeridian.org/ping). IP blocked? See the [recovery guide](https://getmeridian.org/docs/en/recovery/).

Something else? Get AI-ready diagnostics:

```bash
meridian doctor --ai        # copies a redacted, AI-ready prompt to your clipboard
```

Or [open an issue](https://github.com/getmeridian/meridian/issues) with `meridian doctor` output.

## Docs

Full documentation, interactive command builder, and setup guides:

**[getmeridian.org](https://getmeridian.org)** · [Connection page demo](https://getmeridian.org/demo)

## Community

- [Вастрик.Клуб](https://vas3k.club/project/31264/) — launch post (78 upvotes, 3k+ views)
