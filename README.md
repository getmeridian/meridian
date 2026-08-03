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

Most proxy setups leak — an open port here, a TLS mismatch there, a fingerprint that gives the server away. Meridian locks every layer down automatically: firewall, certificates, SNI routing, fingerprinting. You deploy in one command. Your people connect via QR code. That's it.

When your IP gets blocked, redeploy and be back in minutes.

Whether you're the "tech friend" setting up VPN for people you care about, a power user managing multiple servers, or an NGO providing access in a censored region — Meridian handles the complexity so you can focus on staying connected.

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

Meridian is an **orchestrator** — it configures Docker, Xray, nginx, firewall, BBR, and TLS certificates automatically. You focus on deploying and sharing access, not debugging configs.

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

## Quick start

```bash
meridian setup                        # resumable guided V4 topology setup
meridian deploy                       # interactive wizard
meridian deploy 198.51.100.10         # deploy to server
meridian deploy local                 # deploy on this server (no SSH needed)
meridian deploy 198.51.100.10 --domain d.io # with CDN fallback
```

After setup, your server is a fully functional proxy. Share access:

```bash
meridian client add alice            # generate keys for a friend
meridian client list                 # see all clients
meridian client remove alice         # permanently revoke legacy access (V4 removal is not yet supported)
```

By default, the deploy wizard blocks `.ru` domains and Russian IPs through the proxy to reduce exposure of your VPS IP in Russian-service logs. If you need those sites over Meridian, deploy with `--no-geo-block`.

Each client gets a canonical subscription. Legacy deploys also host a connection page with QR codes and one-tap links when its upload succeeds; V4 setup does not fabricate that legacy page.

## How deployment works

The typical workflow: run Meridian on **your machine** and it connects to the VPS via SSH. You can also run it directly on the VPS (`deploy local`).

```
Your machine                       VPS
┌────────────────┐    SSH    ┌────────────────┐
│  meridian CLI  │ ────────→ │  proxy server  │
│  ~/.meridian/  │           │                │
└────────────────┘           └────────────────┘
```

After `meridian deploy 198.51.100.10`, topology and panel credentials are stored in `~/.meridian/cluster.yml`. Later client commands use that panel directly — no server selector is needed.

Add more exit nodes to the same fleet with names:

```bash
meridian deploy 198.51.100.10 --display-name finland
meridian node add 198.51.100.11 --name germany
```

<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/connection-page.png" width="360" alt="Connection page with QR codes">
</p>

## How it works

Meridian deploys [VLESS+Reality](https://github.com/XTLS/Xray-core) — a protocol that makes your server indistinguishable from a legitimate website:

| Censorship method | How Meridian beats it |
|---|---|
| **Deep Packet Inspection** | Traffic is byte-for-byte identical to normal HTTPS. No proxy signatures. |
| **Active probing** | Censors connecting to your server get a real TLS certificate from microsoft.com. Only clients with your private key reach the proxy. |
| **TLS fingerprinting** | uTLS impersonates Chrome's exact Client Hello, matching billions of real devices. |
| **IP blocking** | Domain mode routes through Cloudflare CDN as a fallback — no direct IP exposure. |

## Architecture

<p align="center">
  <img src="https://raw.githubusercontent.com/getmeridian/meridian/main/website/public/img/architecture.svg" width="720" alt="Meridian architecture — nginx SNI routing, TLS, Xray Reality">
</p>

**Standalone mode** — nginx on port 443 routes Reality traffic to Xray via SNI inspection. nginx also provides TLS (Let's Encrypt IP certificate via acme.sh) for hosted connection pages, panel access, and XHTTP transport. No domain needed.

**Domain mode** — Same architecture, plus nginx handles VLESS+WSS through Cloudflare CDN as a fallback when the server IP is blocked. XHTTP also benefits from the domain certificate for enhanced stealth.

**Relay mode** — A lightweight TCP forwarder (Realm) on a domestic server forwards port 443 to the exit server abroad with end-to-end encryption. Legacy Realm relays advertise the dedicated Reality route; HTTP transports remain on direct/domain exits.

## What you need

- A VPS (Debian/Ubuntu) with root SSH key access — $5/month from any provider
- Recommended: Finland, Netherlands, Sweden, Germany (low latency, not flagged)
- Optional: a domain pointed to the server (for CDN fallback via Cloudflare)

## Commands

| Command | Description |
|---------|-------------|
| `meridian setup` | Configure a complete V4 topology with a resumable guided wizard |
| `meridian deploy [IP\|local]` | Deploy proxy server (interactive wizard if no IP) |
| `meridian studio` | Open the localhost-only Studio and Engine API |
| `meridian plan` | Show the reconciliation plan — diff between `cluster.yml` and actual panel state |
| `meridian apply [--yes]` | Converge the cluster to the desired state in `cluster.yml` |
| `meridian client add NAME` | Add a named client key |
| `meridian client show NAME` | Show managed-client subscription and evidenced handoff links |
| `meridian client list` | List managed access clients |
| `meridian client remove NAME` | Remove a legacy client key; V4 managed-user retirement is not yet supported |
| `meridian client enable NAME` | Resume a suspended client |
| `meridian client disable NAME` | Suspend a client without deleting it |
| `meridian server add IP` | Add a server to the local registry |
| `meridian server list` | List known servers |
| `meridian server remove NAME` | Remove an unused server profile |
| `meridian node add IP` | Provision another exit node |
| `meridian node list` | List exit nodes and live status |
| `meridian node check NODE` | Check one node's panel, SSH, service, port, and TLS health |
| `meridian node remove NODE` | Remove an exit node after dependency checks |
| `meridian relay deploy RELAY_IP --exit EXIT` | Deploy a relay node (TCP forwarder) |
| `meridian relay list` | List relay nodes |
| `meridian relay remove RELAY_IP` | Remove a legacy relay; use setup for V4 chains |
| `meridian relay check RELAY_IP` | Check legacy relay health; use test for V4 routes |
| `meridian fleet status` | Collect live panel, node, relay, and client health |
| `meridian fleet inventory` | Inspect configured and desired topology |
| `meridian fleet recover` | Recover legacy-compatible local state from a live panel |
| `meridian api schemas` | List meridian-core JSON schemas for automation/UI clients |
| `meridian api commands` | List migrated command contracts and schema bindings |
| `meridian api schema NAME` | Print one JSON Schema |
| `meridian api workflow NAME` | Print a machine-renderable workflow contract |
| `meridian preflight [IP]` | Pre-flight server validation (ports, SNI, OS, disk) |
| `meridian scan [IP]` | Find optimal SNI targets on server's network |
| `meridian test [IP\|DOMAIN] [--json]` | Execute the delivered subscription and verify actual proxy traffic |
| `meridian probe [IP\|DOMAIN] [--json]` | Probe exposure and fingerprinting from a censor's perspective |
| `meridian doctor [IP]` | Collect info for bug reports (alias: `rage`) |
| `meridian teardown [IP]` | Remove an unreferenced deployment after dependency checks |
| `meridian update` | Update Meridian to the latest version |

## Automation contract

Meridian is moving toward **meridian-core**: typed install/control APIs with the CLI as one client. Machine output is standardized around a Pydantic-backed `meridian.output/v1` envelope (`schema`, `command`, `operation_id`, `status`, `summary`, `data`, `warnings`, `errors`). For example, `meridian plan --json`, `meridian apply --json`, `meridian test --json`, `meridian probe --json`, `meridian client list --json`, `meridian client show --json`, `meridian fleet status --json`, and `meridian fleet inventory --json` return the same top-level shape, with command-specific fields under `data`.

Use top-level `status` for completed command execution, `summary.changed` for plan/apply changes, and command-specific health fields such as `data.summary.health` / `data.summary.needs_attention` for fleet state. Verification exits `0` on a complete pass, `4` on completed negative findings, and `3` when required evidence is unavailable; `fleet status` uses the same codes for healthy, degraded, and unknown. `fleet inventory` is topology inventory only; use `plan --json` as the drift authority and `apply --json` for execution results. Keep using process exit codes for shell control flow. Ctrl-C/SIGINT exits `130` and does not promise a JSON envelope on stdout; command catalog entries expose this as `interrupt_behavior: "exit_130_without_envelope"`. Secrets are redacted before JSON leaves the process. Run `meridian api commands --json` to discover command contracts, and `meridian api schema plan-envelope --json` to inspect command-specific schemas.

See the [full CLI reference](https://getmeridian.org/docs/en/cli-reference/) for all commands and flags.

## Client apps

After setup, connect with any of these apps:

| Platform | App |
|----------|-----|
| iOS | [v2RayTun](https://apps.apple.com/app/v2raytun/id6476628951) |
| Android | [v2rayNG](https://github.com/2dust/v2rayNG/releases/latest) |
| Windows | [v2rayN](https://github.com/2dust/v2rayN/releases/latest) |
| All platforms | [Hiddify](https://github.com/hiddify/hiddify-app/releases/latest) |

## Common scenarios

**My IP got blocked** — Try the XHTTP fallback first. If the panel is still reachable, get a new VPS and run `meridian node add NEW_IP`; existing clients receive the new exit on their next subscription refresh. In domain mode, update the DNS A record. See the [recovery guide](https://getmeridian.org/docs/en/recovery/) for relay and panel-host cases.

**Sharing with family** — After `meridian client add alice`, send the canonical subscription URL. Legacy deployments may also provide a verified self-hosted share page with a QR code.

**First-time VPS setup** — Rent a VPS from any provider (DigitalOcean, Hetzner, Vultr — $4–6/month). Choose Debian 12 or Ubuntu 22.04+. Make sure you have SSH key access (not just password). Then run `meridian deploy YOUR_SERVER_IP`.

## Troubleshooting

Not connecting? Run `meridian test` to check if the server is reachable, or use the [web-based ping tool](https://getmeridian.org/ping).

Something else not working? Get instant AI-powered help:

```bash
meridian doctor --ai        # copies an AI-ready prompt to clipboard
```

Paste the prompt into ChatGPT, Claude, or any AI assistant for personalized troubleshooting.

Or [open an issue](https://github.com/getmeridian/meridian/issues) with `meridian doctor` output.

## Docs

Full documentation, interactive command builder, and setup guides:

**[getmeridian.org](https://getmeridian.org)** · [Connection page demo](https://getmeridian.org/demo)

## Community

- [Вастрик.Клуб](https://vas3k.club/project/31264/) — launch post (78 upvotes, 3k+ views)
