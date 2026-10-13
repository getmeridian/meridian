---
title: Security
description: Security design, vulnerability reporting, and scope.
order: 11
section: reference
---

## Reporting vulnerabilities

If you discover a security vulnerability in Meridian:

1. **Do NOT open a public issue**
2. Report it privately through [GitHub private vulnerability reporting](https://github.com/getmeridian/meridian/security/advisories/new)
3. Include steps to reproduce and potential impact

We aim to respond within 48 hours and will credit reporters in the fix.

## Security design

- **Credentials**: stored with `0600` permissions in `~/.config/meridian/cluster.yml`, secrets never passed through shell commands without `shlex.quote()`, redacted from `meridian doctor` output. Directory permissions are `0700` (owner-only). Any application running as your user can read these files — this is inherent to all CLI tools that store credentials locally (SSH keys, cloud CLI tokens, etc.). If your local machine is compromised at the user level, credential encryption would not help: malware can keylog the passphrase or intercept SSH sessions. Protect your local machine.
- **Panel access**: the Remnawave admin UI is reverse-proxied by nginx at a secret HTTPS path — no SSH tunnel required. Connection pages and subscription links use a separate path and never reveal the panel's. The URL and admin credentials are in the `panel` section of `~/.config/meridian/cluster.yml`
- **SSH**: password authentication disabled and fail2ban enabled by default (`--no-harden` skips both, plus the firewall, for servers shared with other services)
- **Firewall**: UFW denies inbound traffic except the server's SSH ports and the ports its role needs — TCP/443 for proxy traffic, TCP/80 where certificates are issued, UDP/443 only when Hysteria2 is enabled, and the listen port on relays
- **Pinned software**: Remnawave images are pinned to tested versions; Realm, Xray (for `meridian test`), and RealiTLScanner downloads are version-pinned and checksum-verified
- **TLS**: [acme.sh](https://github.com/acmesh-official/acme.sh) handles certificates via Let's Encrypt, served by nginx

## Scope

Meridian configures proxy servers — it does **not** implement cryptographic protocols. The underlying security depends on:

- [Xray-core](https://github.com/XTLS/Xray-core) — VLESS+Reality protocol
- [Remnawave](https://remna.st/) — panel + node management stack
- [nginx](https://nginx.org/) — SNI routing and TLS termination
