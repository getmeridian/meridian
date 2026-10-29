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

<p align="center"><b>Deploy it right. Share it easily.</b><br>One command turns a fresh VPS into a hardened VLESS+Reality proxy and gives you a link to share.</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/overview.webp" width="760" alt="meridian deploy sets up the firewall, SSH, nginx SNI routing on port 443, Xray (Reality, XHTTP, Hysteria2), Meridian's control service, and a Let's Encrypt IP certificate, then gives your people a connection page and subscription URL">
</p>

A proxy that works can still give itself away: an open admin port, a self-signed certificate, a container that skips the firewall. Meridian sets up every layer correctly, tests the server with real traffic, and hands each person a connection page with a QR code. When an address gets blocked, add or move a server; apps pick up the change on their next refresh.

<p align="center">
  <a href="https://getmeridian.org/#video"><img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/video/meridian-setup-thumbnail.jpg" width="640" alt="Video: a full meridian setup, start to finish"></a>
  <br><sub><a href="https://getmeridian.org/#video">Watch a full <code>meridian setup</code></a>, start to finish (3½ min) · <a href="https://getmeridian.org/video/meridian-setup.mp4">download the MP4</a> · <a href="https://getmeridian.org/ru/#video">по-русски</a></sub>
</p>

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
meridian client add bob carol      # one client per person
meridian client show bob           # print their link again
meridian test                      # real traffic through the delivered subscription
```

Re-run `deploy` at any time: keys, paths, and clients are kept. `meridian setup` asks the same questions as a guided wizard.

## What one deploy sets up

- **Methods:** VLESS+Reality and XHTTP on TCP 443, Hysteria2 on UDP 443 with salamander. Every subscription lists all of them, and capable apps switch on their own.
- **Camouflage:** nginx routes TCP 443 by SNI without decrypting. A probe without the key reaches a real website and gets its genuine certificate. Deploy picks the target from the server's own network.
- **Hardening:** key-only SSH, fail2ban, UFW opening only SSH and 443, no public container ports, a Let's Encrypt certificate for the IP. No domain needed.
- **Control service:** Meridian's own, on a secret HTTPS path: users, subscriptions, and a web console. Shared links never reveal it, and each server's agent dials out to it, so nodes open no extra ports. No Docker, no third-party panel.
- **Backups:** after every change your machine pulls an encrypted snapshot of the control service. If the control server is lost or seized, `meridian restore` rebuilds it on another server.

Options worth knowing:

```bash
meridian deploy 198.51.100.10 --domain vpn.example.com   # XHTTP + WSS through Cloudflare when the IP is blocked
meridian deploy 198.51.100.10 --display-name "Family VPN" # label on every connection page
meridian deploy 198.51.100.10 --no-geo-block             # allow Russian sites through the proxy (blocked by default)
meridian deploy local                                    # run on the server itself
```

## Grow the fleet

```bash
meridian node add 198.51.100.20 --name germany        # another exit
meridian relay add 203.0.113.10 --exit 198.51.100.10  # domestic entry point (Realm TCP forwarder)
meridian node replace EXIT_ID 198.51.100.30           # move a blocked exit, keys and clients included
meridian setup                                        # any shape: relay chains, routing gateways
```

Every change edits one topology in `~/.config/meridian/cluster.yml`. `meridian plan` shows what would change; `meridian apply` applies exactly that, with checkpoints, so an interrupted run resumes. Back up that folder and the recovery key deploy shows once: together they bring the fleet back on a new machine.

## When something breaks

```bash
meridian test 198.51.100.10           # which step fails: network, TLS, subscription, or proxy
meridian test 198.51.100.10 --probe   # the server as a censor sees it (run from inside the censored network)
meridian check                        # server health over SSH
meridian doctor --ai                  # redacted report, ready to paste into an AI chat
```

IP blocked? Follow the [recovery guide](https://getmeridian.org/en/recovery/). Users can run "Check my connection" on their own page.

## Commands

| | |
|---|---|
| `deploy`, `setup` | Set up one server, or any fleet with the wizard |
| `client add\|show\|list\|reissue\|enable\|disable\|remove` | The people who connect |
| `node add\|list\|replace\|sni\|remove`, `relay add\|list\|sni\|remove` | Exits and relays |
| `route add\|list\|remove` | Send chosen sites through WARP, directly, or nowhere |
| `plan`, `apply` | Review and apply the topology |
| `test`, `check`, `scan`, `doctor`, `logs` | Verify, inspect, find camouflage targets, report |
| `fleet status\|inventory\|history\|upgrade`, `panel` | Fleet overview, applied revisions, Realm upgrades, the web console |
| `backup`, `restore`, `migrate` | Snapshots and the recovery key, rebuild a lost control server, move a Remnawave fleet across |
| `teardown`, `update` | Remove Meridian from a server; update the CLI |
| `help agents` | Explain Meridian to an AI agent |

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

- **Docs:** [getmeridian.org](https://getmeridian.org) · [connection page demo](https://getmeridian.org/demo) · [architecture](https://getmeridian.org/en/architecture/)
- **Upgrading from 3.x:** Meridian 4 replaces 3x-ui with its own control service and is not an in-place upgrade; every user gets a new link. [How to move](https://getmeridian.org/en/installation/#upgrading-from-3x). A fleet on an earlier Meridian 4 release that runs Remnawave moves in place with `meridian migrate`, keeping every link.
- **Security:** [SECURITY.md](SECURITY.md) covers the threat model, including what Meridian does not protect against.
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md) · [issues](https://github.com/getmeridian/meridian/issues) (attach `meridian doctor` output)
- **License:** [MIT](LICENSE)
