---
title: Security
description: Security design, vulnerability reporting, and scope.
---

## Reporting vulnerabilities

If you discover a security vulnerability in Meridian:

1. **Do NOT open a public issue**
2. Report it privately through [GitHub private vulnerability reporting](https://github.com/getmeridian/meridian/security/advisories/new)
3. Include steps to reproduce and potential impact

We aim to respond within 48 hours and will credit reporters in the fix.

## Security design

- **Credentials**: stored with `0600` permissions in `~/.config/meridian/cluster.yml`, secrets never passed through shell commands without `shlex.quote()`, redacted from `meridian doctor` output. Directory permissions are `0700` (owner-only). Any application running as your user can read these files — this is inherent to all CLI tools that store credentials locally (SSH keys, cloud CLI tokens, etc.). If your local machine is compromised at the user level, credential encryption would not help: malware can keylog the passphrase or intercept SSH sessions. Protect your local machine.
- **Control service access**: the control service is reverse-proxied by nginx at a secret HTTPS path — no SSH tunnel required. Connection pages and subscription links use a separate path and never reveal it. The CLI holds its admin token in `~/.config/meridian/cluster.yml`; scripts get their own revocable, scoped tokens
- **Web console**: one owner, with a password and an optional authenticator code; sessions expire after 30 minutes idle and 12 hours at most, and token creation or privacy changes need a recent password confirmation. The console manages people and privacy, never servers, and holds no SSH keys. It loads nothing from other sites and runs under a strict Content Security Policy
- **Nodes**: agents connect out to the control service over HTTPS, so nodes open no management port. Each joins with a single-use enrollment token and then holds a certificate from the fleet's own authority. Every document a node runs is signed by the control service and verified by the agent, which refuses anything older than what it already applied
- **Privacy**: device limits and usage history default to off: the control service keeps lifetime traffic totals and last seen. You can opt in to keyed device fingerprints, full device details, or daily traffic with bounded retention. No level stores client IP addresses, connection logs, or request paths. See [Privacy](/en/privacy/) for what a seized server reveals
- **Backups**: snapshots are encrypted with [age](https://age-encryption.org/) to each managing machine's key and to a recovery key whose private half only you hold. They carry no signing keys, sessions, or console password: a restore makes new ones, so a seized control server can't sign anything a restored fleet accepts
- **SSH**: password authentication disabled and fail2ban enabled by default (`--no-harden` skips both, plus the firewall, for servers shared with other services)
- **Firewall**: UFW denies inbound traffic except the server's SSH ports and the ports its role needs — TCP/443 for proxy traffic, TCP/80 where certificates are issued, UDP/443 only when Hysteria2 is enabled, and the listen port on relays
- **Exit DNS**: exits look up every destination over encrypted DNS (DNS over HTTPS to Cloudflare, Quad9, and Google at once, reached by IP address), never through the hosting provider's resolver, so no destination name leaves the server in plain text. If none of the three answers, sites by name fail through that exit instead of falling back to plain DNS; `meridian check` reports a resolver the server cannot reach
- **Pinned software**: the control service installs the exact Meridian release through a checksum-verified uv; the agent ships inside the Meridian package; Xray, Realm, and RealiTLScanner downloads are version-pinned and checksum-verified
- **TLS**: [acme.sh](https://github.com/acmesh-official/acme.sh) handles certificates via Let's Encrypt, served by nginx

## Scope

Meridian configures proxy servers — it does **not** implement cryptographic protocols. The underlying security depends on:

- [Xray-core](https://github.com/XTLS/Xray-core) — VLESS+Reality, XHTTP, WSS, and Hysteria2 protocols
- [age](https://age-encryption.org/) (through pyrage) — snapshot encryption
- [nginx](https://nginx.org/) — SNI routing and TLS termination
- [acme.sh](https://github.com/acmesh-official/acme.sh) — Let's Encrypt certificate issuance and renewal
- [Realm](https://github.com/zhboner/realm) — TCP forwarding on relays
