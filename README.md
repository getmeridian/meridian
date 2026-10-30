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

<p align="center"><b>Deploy it right. Share it easily.</b><br>One command turns a fresh VPS into a hardened self-hosted VPN server, with a built-in web panel and a connection page for each person.</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/overview.webp" width="760" alt="meridian deploy sets up the firewall, SSH, nginx SNI routing on port 443, Xray (Reality, XHTTP, Hysteria2), Meridian's control service and web panel, and a Let's Encrypt IP certificate, then gives your people a connection page and subscription URL">
</p>

A VPN that connects can still give itself away: an open admin port, a self-signed certificate, a container that skips the firewall. Meridian sets up every layer on a fresh Debian or Ubuntu VPS, tests the server with real traffic, and gives you two interfaces: a **connection page** for each person who connects and a **web panel** to manage people and servers from any browser. When an address gets blocked, add a relay, turn on domain mode, or move an exit; apps pick up the change on their next refresh.

<p align="center">
  <a href="https://getmeridian.org/#video"><img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/video/meridian-setup-thumbnail.jpg" width="640" alt="Video: a full meridian setup, start to finish"></a>
  <br><sub><a href="https://getmeridian.org/#video">Watch a full <code>meridian setup</code></a>, start to finish (3½ min) · <a href="https://getmeridian.org/video/meridian-setup.mp4">download the MP4</a> · <a href="https://getmeridian.org/ru/#video">по-русски</a></sub>
</p>

## Web panel and connection pages

Each person gets a connection page at their subscription URL; you get a self-hosted web panel (owner console) on the control server. Both demos below run in your browser on invented data: click around, nothing is real and nothing is sent anywhere.

<table>
  <tr>
    <td width="30%" valign="top"><a href="https://getmeridian.org/demo/"><img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/showcase/connection-page-phone.webp" alt="A person's connection page on a phone: pick a platform and app, add the connection with one tap, then connect"></a></td>
    <td width="70%" valign="top"><a href="https://getmeridian.org/demo/console/"><img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/showcase/console-people.webp" alt="The web panel's People list: each person's expiry, last time online, status, and traffic against their limit"></a></td>
  </tr>
  <tr>
    <td valign="top"><a href="https://getmeridian.org/demo/"><img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/showcase/connection-page-account.webp" alt="The account panel on a connection page: status, traffic this period, and days left"></a></td>
    <td valign="top"><a href="https://getmeridian.org/demo/console/#/servers"><img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/showcase/console-servers.webp" alt="The web panel's Servers screen: each exit's health, versions, and monthly traffic allowance"></a></td>
  </tr>
</table>

<p align="center"><b><a href="https://getmeridian.org/demo/">Try the connection page</a></b> · <b><a href="https://getmeridian.org/demo/console/">Try the web panel</a></b></p>

- **Web panel (`meridian panel`):** runs on the control server behind a secret HTTPS path, protected by a password and optional authenticator code, with a strict CSP and no external requests. Add people, share links and QR codes, set expiry dates, traffic limits, and device caps, keep private notes and tags, reissue links, remove access, or cut off active sessions. Check server health, traffic, and monthly allowances, set the default page language and a notice for everyone, issue scoped API tokens, and choose privacy levels. Server and routing topology stays reviewed in the CLI.
- **Connection page:** served at every person's subscription URL in English, Russian, Persian, and Chinese. In a browser it shows recommended apps for their device, one-tap import, a QR code, account status, traffic, and plain help; in a VPN app the same URL delivers native profiles (`Xray JSON`, `Mihomo`, `sing-box`, or `Base64`).

## Install

macOS or Linux (on Windows, use WSL):

```bash
curl -sSf https://getmeridian.org/install.sh | bash
```

Or from PyPI: `uv tool install meridian-vpn` (or `pipx install meridian-vpn`).

You need a fresh Debian or Ubuntu VPS you can reach over SSH with a key, as root or a user with passwordless sudo. Meridian runs on your computer, not on the server.

## Quick start

```bash
meridian deploy 198.51.100.10 --client-name alice
```

When it prints "Done", send Alice the connection page link. They open it, install the recommended app, and tap once to connect. Then:

```bash
meridian panel                     # web panel URL and a one-time setup link
meridian client add bob carol      # or add people from the CLI
meridian client show bob           # print their link and status
meridian test                      # real traffic through the delivered subscription
```

Re-run `deploy` at any time: keys, paths, and clients are kept. `meridian setup` asks the same questions as a guided wizard.

## What one deploy sets up

- **Methods:** VLESS+Reality and XHTTP on TCP 443, Hysteria2 on UDP 443 with salamander. Every subscription lists all of them (with Chrome and Firefox TLS fingerprints), and capable apps switch on their own.
- **Camouflage:** nginx routes TCP 443 by SNI without decrypting. A probe without the key reaches a real website and gets its genuine certificate. Deploy picks the target from the server's own network.
- **Hardening:** key-only SSH, fail2ban, BBR, UFW opening only SSH and 443, and a Let's Encrypt certificate for the IP. No domain needed.
- **Control service and web panel:** Meridian's own, on a secret HTTPS path: users, subscriptions, and the web panel. Shared links never reveal the path, and each server's agent dials out to it, so nodes open no extra ports. No Docker, no third-party panel.
- **Privacy and backups:** no client IPs or connection logs are stored; device and daily history tracking are opt-in and off by default. After every change your machine pulls an encrypted snapshot sealed to your device keys and a recovery key. If the control server is lost or seized, `meridian restore` rebuilds it on another server.

Options worth knowing:

```bash
meridian deploy 198.51.100.10 --domain vpn.example.com    # XHTTP + WSS through Cloudflare when the IP is blocked
meridian deploy 198.51.100.10 --display-name "Family VPN" # label on every connection page
meridian deploy 198.51.100.10 --no-geo-block              # allow Russian sites through the VPN (blocked by default)
meridian deploy local                                     # run on the server itself
```

## Grow the fleet

```bash
meridian node add 198.51.100.20 --name germany        # another exit
meridian relay add 203.0.113.10 --exit 198.51.100.10  # domestic entry point (Realm TCP forwarder)
meridian route add germany --preset ai --via warp     # route chosen destinations through WARP
meridian node replace EXIT_ID 198.51.100.30           # move a blocked exit, keys and clients included
meridian setup                                        # any shape: relay chains, routing gateways
```

Every change edits one topology in `~/.config/meridian/cluster.yml`. `meridian plan` shows what would change; `meridian apply` applies exactly that, with checkpoints, so an interrupted run resumes. Back up that folder and the recovery key deploy shows once: together they bring the fleet back on a new machine.

## When something breaks

```bash
meridian test 198.51.100.10           # which step fails: network, TLS, subscription, or tunnel
meridian test 198.51.100.10 --probe   # the server as a censor sees it (run from inside the censored network)
meridian check                        # server health over SSH
meridian doctor --ai                  # redacted report, ready to paste into an AI chat
```

IP blocked? Follow the [recovery guide](https://getmeridian.org/en/recovery/). Users can run "Check my connection" on their own page.

## Commands

| | |
|---|---|
| `deploy`, `setup` | Set up one server, or any fleet with the wizard |
| `panel` | Print the web panel address and a one-time owner setup or reset link |
| `client add\|show\|list\|set\|extend\|reset-traffic\|devices\|reissue\|enable\|disable\|remove` | The people who connect |
| `node add\|list\|restart\|replace\|sni\|remove`, `relay add\|list\|sni\|remove` | Exits and relays |
| `route add\|list\|remove`, `server add\|list\|set\|remove` | Route chosen sites; save servers, allowances, and payment dates |
| `plan`, `apply`, `context list\|use\|rename\|remove` | Review and apply the topology; switch between fleets |
| `test`, `probe`, `check`, `scan`, `doctor`, `logs` | Verify traffic, probe camouflage, inspect health, scan SNI targets, view redacted logs |
| `fleet status\|inventory\|history\|events\|usage\|routing\|contact\|notice\|language\|upgrade\|export\|import` | Fleet overview, revisions, client routing, notices, language, upgrades |
| `privacy`, `backup`, `restore`, `migrate` | Choose what is stored, manage snapshots and the recovery key, rebuild a lost control server, move a Remnawave fleet |
| `teardown`, `update`, `api`, `help agents` | Remove Meridian from a server, update the CLI, inspect JSON contracts, explain Meridian to an AI agent |

Every command takes `--help`; most take `--json`. Full list: [CLI reference](https://getmeridian.org/en/cli-reference/).

## Client apps

| App | Platforms |
|-----|-----------|
| [Happ](https://happ.su/) | iOS, Android, Windows, macOS, Linux |
| [FlClashX](https://github.com/pluralplay/FlClashX/releases/latest) | Android, Windows, macOS, Linux |
| [Clash Mi](https://clashmi.app/download) | iOS, Android, Windows, macOS, Linux |
| [Throne](https://github.com/throneproj/Throne/releases/latest) | Windows, macOS, Linux |

The connection page recommends one for each device and links more.

## AI agents

```bash
meridian help agents
```

prints a guide for Claude Code, Codex, Cursor, and other agents: how Meridian works and how to drive it safely (`--yes`, `--json` envelopes, exit codes, `meridian api` contracts). Every docs page is also served as Markdown, indexed at [getmeridian.org/llms.txt](https://getmeridian.org/llms.txt).

## More

- **Docs:** [getmeridian.org](https://getmeridian.org) · [connection page demo](https://getmeridian.org/demo/) · [web panel demo](https://getmeridian.org/demo/console/) · [architecture](https://getmeridian.org/en/architecture/)
- **Upgrading from 3.x:** Meridian 4 replaces 3x-ui with its own control service and is not an in-place upgrade; every user gets a new link. [How to move](https://getmeridian.org/en/installation/#upgrading-from-3x). A fleet on an earlier Meridian 4 release that runs Remnawave moves in place with `meridian migrate`, keeping every link.
- **Security:** [SECURITY.md](SECURITY.md) covers the threat model, including what Meridian does not protect against.
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md) · [issues](https://github.com/getmeridian/meridian/issues) (attach `meridian doctor` output)
- **License:** [MIT](LICENSE)
