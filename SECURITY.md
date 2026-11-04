# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Meridian, please report it responsibly:

1. **Do NOT open a public issue** for security vulnerabilities
2. Report it privately through [GitHub private vulnerability reporting](https://github.com/getmeridian/meridian/security/advisories/new)
3. Include steps to reproduce and potential impact

We aim to respond within 48 hours and will credit reporters in the fix.

## Security Design

- **No telemetry**: no analytics, ads, or uploaded reports. The CLI's update check is an HTTPS GET to PyPI's public JSON API. Connection pages, the web panel, and the website load no third-party assets.
- **Your computer holds the fleet**: `~/.config/meridian` keeps the topology, SSH key, and admin token with owner-only permissions. Secrets never reach a shell command unquoted, and `meridian doctor` redacts them.
- **Control service and web panel**: Meridian's own service runs on the control server behind nginx at a secret HTTPS path; subscription links and connection pages use a separate path and never reveal it. The panel has one owner, signs in with a password and an optional authenticator code, or with a passkey, enforces a strict Content Security Policy, and makes no external requests. Scripts get their own scoped, revocable tokens.
- **Nodes**: each server's agent dials out to the control service over HTTPS, so nodes open no management port. An agent joins with a single-use token, then holds a certificate from the fleet's own authority, and applies only documents the control service signed, never an older one than it already runs.
- **Servers**: key-only SSH, fail2ban, and a firewall that opens only the SSH ports and what the server's role needs. Xray's TCP inbounds listen on localhost behind nginx, which routes port 443 by SNI without decrypting, so a probe without the key reaches a real website and its genuine certificate.
- **Pinned downloads**: Xray, Realm, acme.sh, the node agent, and uv are pinned and checked against SHA-256 before they run, and every release attaches `SHA256SUMS` for the installer.
- **Privacy and backups**: no client IP addresses or connection logs are stored; device tracking and daily traffic history are opt-in. Snapshots are encrypted with [age](https://age-encryption.org/) to your devices and a recovery key only you hold, and carry no signing keys, so a seized control server cannot sign for a restored fleet.

Every outbound request, download check, and design choice is listed on the [Security](https://getmeridian.org/en/security/) page; [Privacy](https://getmeridian.org/en/privacy/) covers what each setting stores and what a seized server reveals.

## What Meridian Does Not Protect Against

- **A compromised computer that manages the fleet.** It holds the credentials for every server; file permissions do not stop malware running as you.
- **Your hosting provider and the exit.** Traffic reaches the internet through an exit server, so its provider can see where it goes, and Cloudflare sees anything you route through WARP. Choose providers you trust.
- **DNS resolver operators.** Exits look names up over DNS over HTTPS with Cloudflare, Quad9, and Google, which see the names but not which person asked.
- **Blocking.** Reality, camouflage, and relays make a server hard to identify, not impossible to block. The [recovery guide](https://getmeridian.org/en/recovery/) covers what to do when an address is blocked.
- **Compromised devices or apps** of the people who connect.

## Scope

This project configures VPN servers — it does NOT implement cryptographic protocols. The underlying security depends on [Xray-core](https://github.com/XTLS/Xray-core), [nginx](https://nginx.org/), [acme.sh](https://github.com/acmesh-official/acme.sh), [Realm](https://github.com/zhboner/realm), and [age](https://age-encryption.org/).
