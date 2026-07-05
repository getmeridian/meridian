---
title: CLI Reference
description: Complete reference for all Meridian CLI commands and flags.
order: 10
section: reference
---

## Commands

### meridian deploy

Deploy proxy server to a VPS.

```
meridian deploy [IP] [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sni HOST` | www.cloudflare.com | TLS camouflage target |
| `--domain DOMAIN` | (none) | Cloudflare CDN fallback domain |
| `--client-name NAME` | default | Name for the first client |
| `--display-name NAME` | (none) | Label for connection pages |
| `--icon EMOJI_OR_URL` | (none) | Page icon — emoji or image URL |
| `--color PALETTE` | ocean | Page color theme (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH user |
| `--harden / --no-harden` | enabled | Harden SSH + firewall |
| `--pq / --no-pq` | disabled | Post-quantum encryption — ML-KEM-768 hybrid (experimental) |
| `--warp / --no-warp` | disabled | Route outgoing traffic through Cloudflare WARP |
| `--server NAME` | | Target server (name or IP) |
| `--decoy MODE` | none | Decoy response for unknown paths (`none` / `403`) |
| `--yes` | | Skip confirmation prompts |

### meridian client

Manage client access keys and connection details.

```
meridian client add NAME [--server NAME]
meridian client show NAME [--server NAME]
meridian client list [--server NAME]
meridian client remove NAME [--server NAME]
```

### meridian server

Manage known servers.

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name NAME` | (auto) | Display name for the server |

### meridian relay

Manage relay nodes — lightweight TCP forwarders that route traffic through a domestic server to an exit server abroad.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--exit/-e EXIT` | (required for deploy) | Exit server IP or name |
| `--name NAME` | (auto) | Friendly name for the relay (e.g., "ru-moscow") |
| `--port/-p PORT` | 443 | Listen port on relay server |
| `--user/-u USER` | root | SSH user on relay |
| `--yes/-y` | | Skip confirmation prompts |

**How relays work**: Client connects to the relay's domestic IP. Relay forwards raw TCP to the exit server abroad. All encryption is end-to-end between client and exit — the relay never sees plaintext. All protocols (Reality, XHTTP, WSS) work through the relay.

### meridian preflight

Pre-flight server validation. Tests SNI, ports, DNS, OS, disk, ASN without installing anything.

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

Find optimal SNI targets on the server's network using RealiTLScanner.

```
meridian scan [IP] [--server NAME]
```

### meridian test

Test proxy reachability and verify actual connections from the client device. No SSH needed.

First checks basic reachability (TCP, TLS handshake, domain HTTPS). Then downloads a local xray client binary (cached after first use), connects through the proxy for each active protocol (Reality, XHTTP, WSS), and confirms traffic flows end-to-end.

```
meridian test [IP] [--server NAME]
```

### meridian probe

Probe a server as a censor would — check if the deployment is detectable. No SSH needed. Works on any server, not just Meridian deployments. Accepts IP addresses or domain names.

Runs 9 checks: port surface, HTTP response, TLS certificate, SNI consistency, proxy path probing, WebSocket upgrade, reverse DNS, HTTP/2 support, and legacy TLS versions.

```
meridian probe [IP|DOMAIN] [--server NAME]
```

### meridian doctor

Collect system diagnostics for debugging. Alias: `meridian rage`.

```
meridian doctor [IP] [--ai] [--server NAME]
```

### meridian teardown

Remove proxy from server.

```
meridian teardown [IP] [--server NAME] [--yes]
```

### meridian update

Update CLI to latest version.

```
meridian update
```

### meridian --version

Show CLI version.

```
meridian --version
meridian -v
```

## Global flags

These flags are available on most commands that interact with a server:

| Flag | Description |
|------|-------------|
| `--server NAME` | Target a specific named server |
| `--user/-u USER` | SSH user (default: root, non-root gets sudo automatically) |
| `--sni HOST` | TLS camouflage target (used by deploy, preflight, test, doctor) |
| `--domain DOMAIN` | Cloudflare CDN fallback domain (used by deploy, preflight, test) |

## Server resolution

Commands that need a server follow this priority:
1. Explicit IP argument or `local` keyword (deploy on this server without SSH)
2. `--server NAME` flag (also accepts `--server local`)
3. Local mode detection (running on the server itself)
4. Single server auto-select (if only one saved)
5. Interactive prompt
