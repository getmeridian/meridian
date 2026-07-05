---
title: Architecture
description: System architecture, traffic flow, and service topology.
order: 9
section: reference
---

## Technology stack

- **VLESS+Reality** (Xray-core) — proxy protocol that impersonates a legitimate TLS website. Censors probing the server see a real certificate (e.g., from microsoft.com). Only clients with the correct private key can connect.
- **3x-ui** — web panel for managing Xray, deployed as a Docker container. Meridian controls it entirely via REST API.
- **nginx** — single-process web server handling both SNI routing and TLS. The stream module listens on port 443 and routes traffic by SNI hostname without terminating TLS. The http module on port 8443 terminates TLS, serves connection pages, reverse-proxies the panel, and proxies XHTTP/WSS traffic to Xray. Certificates are managed by acme.sh (Let's Encrypt IP certificate via ACME `shortlived` profile in standalone mode, domain certificate in domain mode).
- **Docker** — runs 3x-ui (which contains Xray). All proxy traffic flows through the container.
- **Pure-Python provisioner** — `src/meridian/provision/` executes deployment steps via SSH. Each step gets `(conn, ctx)` and returns a `StepResult`.
- **uTLS** — impersonates Chrome's TLS Client Hello fingerprint, making connections indistinguishable from real browser traffic.

## Service topology

### Standalone mode (no domain)

```mermaid
flowchart TD
    Internet((Internet)) -->|Port 443| Nginx[nginx stream<br>SNI Router]
    Nginx -->|"SNI = reality_sni"| Xray["Xray Reality<br>:10443"]
    Nginx -->|"SNI = server IP"| NginxHTTP["nginx http<br>:8443"]
    NginxHTTP -->|/info-path| Page[Connection Page]
    NginxHTTP -->|/panel-path| Panel[3x-ui Panel]
    NginxHTTP -->|/xhttp-path| XrayXHTTP["Xray XHTTP<br>localhost"]
    Internet -->|Port 80| NginxACME["nginx<br>ACME challenges"]
```

nginx stream **does not** terminate TLS. It reads the SNI hostname from the TLS Client Hello and forwards the raw TCP stream to the appropriate backend.

acme.sh requests a Let's Encrypt IP certificate via the ACME `shortlived` profile (6-day validity, auto-renewed). Falls back to self-signed if IP cert issuance is not supported.

XHTTP runs on a localhost-only port and is reverse-proxied by nginx — no extra external port exposed.

### Domain mode

```mermaid
flowchart TD
    Internet((Internet)) -->|Port 443| Nginx[nginx stream<br>SNI Router]
    Nginx -->|"SNI = reality_sni"| Xray["Xray Reality<br>:10443"]
    Nginx -->|"SNI = domain"| NginxHTTP["nginx http<br>:8443"]
    NginxHTTP -->|/info-path| Page[Connection Page]
    NginxHTTP -->|/panel-path| Panel[3x-ui Panel]
    NginxHTTP -->|/xhttp-path| XrayXHTTP["Xray XHTTP<br>localhost"]
    NginxHTTP -->|/ws-path| XrayWSS["Xray WSS<br>localhost"]
    Internet -->|Port 80| NginxACME["nginx<br>ACME challenges"]
    Internet -.->|"CDN (Cloudflare)"| NginxHTTP
```

Domain mode adds VLESS+WSS as a CDN fallback path. Traffic flows through Cloudflare's CDN via WebSocket, making the connection work even if the server's IP is blocked.

### Relay topology

```mermaid
flowchart LR
    Client([Client]) -->|Port 443| Relay["Relay<br>(Realm TCP)"]
    Relay -->|Port 443| Exit["Exit Server<br>(abroad)"]
    Exit --> Internet((Internet))
```

A relay node is a lightweight TCP forwarder running [Realm](https://github.com/zhboner/realm). The client connects to the relay's domestic IP, which forwards raw TCP to the exit server abroad. All encryption is end-to-end between client and exit — the relay never sees plaintext.

## How Reality protocol works

1. Server generates an **x25519 keypair**. Public key is shared with clients, private key stays on server.
2. Client connects on port 443 with a TLS Client Hello containing the camouflage domain (e.g., `www.cloudflare.com`) as SNI.
3. To any observer, this looks like a normal HTTPS connection to microsoft.com.
4. If a **prober** sends their own Client Hello, the server proxies the connection to the real microsoft.com — the prober sees a valid certificate.
5. If the client includes valid authentication (derived from the x25519 key), the server establishes the VLESS tunnel.
6. **uTLS** makes the Client Hello byte-for-byte identical to Chrome's, defeating TLS fingerprinting.

## Docker container structure

The `3x-ui` Docker container contains:
- **3x-ui web panel** — REST API on port 2053 (internal)
- **Xray binary** at `/app/bin/xray-linux-*` (architecture-dependent path)
- **Database** at `/etc/x-ui/x-ui.db` (SQLite, stores inbound configs and clients)
- **Xray config** managed by 3x-ui (not a static file)

Meridian manages 3x-ui entirely via its REST API:
- `POST /login` — authenticate (form-urlencoded, returns session cookie)
- `POST /panel/api/inbounds/add` — create VLESS inbound
- `GET /panel/api/inbounds/list` — list inbounds (check before creating)
- `POST /panel/setting/update` — configure panel settings
- `POST /panel/setting/updateUser` — change panel credentials

## Management panel (3x-ui)

Meridian uses [3x-ui](https://github.com/MHSanaei/3x-ui) as its management panel for Xray. While the CLI handles everything automatically, you can also access the web panel directly for monitoring and advanced configuration.

### How to access

The panel is reverse-proxied by nginx at a randomized secret HTTPS path — no SSH tunnel needed. Find the URL and credentials in your local credentials file:

```
cat ~/.meridian/credentials/<IP>/proxy.yml
```

Look for the `panel` section:

```yaml
panel:
  username: a1b2c3d4e5f6
  password: Xk9mP2qR7vW4nL8jF3hT6yBs
  web_base_path: n7kx2m9qp4wj8vh3rf6tby5e
  port: 2053
```

The panel URL is:

```
https://<your-server-ip>/n7kx2m9qp4wj8vh3rf6tby5e/
```

### What you can do

- **Monitor traffic** — per-client upload/download stats
- **View inbounds** — see all configured VLESS protocols (Reality, XHTTP, WSS)
- **Check Xray status** — verify the proxy engine is running
- **Advanced config** — modify Xray settings directly (for power users)

### Important notes

- The `web_base_path` is a random string — this is your panel's security. Don't share it.
- All management via `meridian` CLI (adding clients, relay setup, etc.) uses this same panel API under the hood.
- If you change settings in the panel directly, they may be overwritten on the next `meridian deploy`.

## nginx configuration pattern

Meridian writes to `/etc/nginx/conf.d/meridian-stream.conf` and `/etc/nginx/conf.d/meridian-http.conf` (never the main nginx.conf). This allows Meridian to coexist with user's own nginx configuration.

nginx handles:
- SNI routing on port 443 (stream module, no TLS termination)
- TLS termination on port 8443 (http module, certificates managed by acme.sh)
- Reverse proxy for the 3x-ui panel (at a random web base path)
- Connection info page serving (hosted pages with shareable URLs)
- Reverse proxy for XHTTP traffic to Xray (path-based routing, all modes when XHTTP enabled)
- Reverse proxy for WSS traffic to Xray (domain mode only)

## Port assignments

| Port | Service | Mode |
|------|---------|------|
| 443 | nginx stream (SNI router) | All |
| 80 | nginx (ACME challenges) | All |
| 10443 | Xray Reality (internal) | All |
| 8443 | nginx http (internal) | All |
| localhost | Xray XHTTP | When XHTTP enabled |
| localhost | Xray WSS | Domain mode |
| 2053 | 3x-ui panel (internal) | All |

XHTTP and WSS ports are localhost-only — nginx reverse-proxies to them on port 443.

## Provisioning pipeline

Steps execute sequentially via `build_setup_steps()`. Each step gets `(conn, ctx)` and returns a `StepResult`.

| # | Step | Module | Purpose |
|---|------|--------|---------|
| 1 | InstallPackages | `common.py` | OS packages |
| 2 | EnableAutoUpgrades | `common.py` | Unattended upgrades |
| 3 | SetTimezone | `common.py` | UTC |
| 4 | HardenSSH | `common.py` | Key-only auth |
| 5 | ConfigureBBR | `common.py` | TCP congestion control |
| 6 | ConfigureFirewall | `common.py` | UFW: 22 + 80 + 443 |
| 7 | InstallDocker | `docker.py` | Docker CE |
| 8 | Deploy3xui | `docker.py` | 3x-ui container |
| 9 | ConfigurePanel | `panel.py` | Panel credentials |
| 10 | LoginToPanel | `panel.py` | API auth |
| 11 | CreateRealityInbound | `xray.py` | VLESS+Reality |
| 12 | CreateXHTTPInbound | `xray.py` | VLESS+XHTTP |
| 13 | CreateWSSInbound | `xray.py` | VLESS+WSS (domain) |
| 14 | VerifyXray | `xray.py` | Health check |
| 15 | InstallNginx | `services.py` | SNI routing + TLS + reverse proxy |
| 16 | DeployConnectionPage | `services.py` | QR codes + page |

## Credential lifecycle

1. **Generate**: random credentials (panel password, x25519 keys, client UUID)
2. **Save locally**: `~/.meridian/credentials/<IP>/proxy.yml` — saved BEFORE applying to server
3. **Apply**: panel password changed, inbounds created
4. **Sync**: credentials copied to `/etc/meridian/proxy.yml` on server
5. **Re-runs**: loaded from cache, not regenerated (idempotent)
6. **Cross-machine**: `meridian server add IP` fetches from server via SSH
7. **Uninstall**: deleted from both server and local machine

## File locations

### On the server
- `/etc/meridian/proxy.yml` — credentials and client list
- `/etc/nginx/conf.d/meridian-stream.conf` — nginx stream config (SNI routing)
- `/etc/nginx/conf.d/meridian-http.conf` — nginx http config (TLS, reverse proxy)
- `/etc/ssl/meridian/` — TLS certificates (managed by acme.sh)
- Docker container `3x-ui` — Xray + panel

### On the local machine
- `~/.meridian/credentials/<IP>/` — cached credentials per server
- `~/.meridian/servers` — server registry
- `~/.meridian/cache/` — update check throttle cache
- `~/.local/bin/meridian` — CLI entry point (installed via uv/pipx)
