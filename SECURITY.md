# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Meridian, please report it responsibly:

1. **Do NOT open a public issue** for security vulnerabilities
2. Report it privately through [GitHub private vulnerability reporting](https://github.com/getmeridian/meridian/security/advisories/new)
3. Include steps to reproduce and potential impact

We aim to respond within 48 hours and will credit reporters in the fix.

## Security Design

- **Credentials**: stored locally with `0600` permissions, secrets are never passed through shell command strings without `shlex.quote()` and are redacted from `meridian doctor` output
- **Panel access**: Remnawave admin UI reverse-proxied by nginx at a secret HTTPS path in all modes (no SSH tunnel required)
- **SSH**: password authentication disabled by default; fail2ban enabled for brute-force protection
- **Firewall**: UFW denies incoming traffic by default and opens only the port(s) sshd actually listens on, TCP 443, UDP 443 for Hysteria2 (unless `--no-hysteria2`), TCP 80 for ACME challenges on servers that get a certificate, and the listen port on relays. Xray's TCP inbounds, the Remnawave panel, and the subscription page listen on localhost behind nginx; an exit's node API port is open only to the control server.
- **Docker images**: Remnawave backend, node, and subscription page, plus PostgreSQL and Valkey, are pinned to tested tags and multi-arch digests in `src/meridian/config.py`, so a re-pushed tag cannot change what a fresh server runs
- **TLS**: [acme.sh](https://github.com/acmesh-official/acme.sh) issues Let's Encrypt certificates with HTTP-01 challenges served from an nginx webroot on port 80; nginx serves the certificates. Without a domain the server gets a short-lived certificate for its IP address, renewed automatically; a domain gets a standard 90-day certificate. Nothing is DNS-validated, so Meridian never needs DNS provider credentials.
- **Update checks**: the CLI periodically checks [PyPI](https://pypi.org/project/meridian-vpn/) for new versions by reading the public JSON API. No telemetry, no user data, no tracking — only an outbound HTTPS GET to `pypi.org`

## Scope

This project configures proxy servers — it does NOT implement cryptographic protocols. The underlying security depends on [Xray-core](https://github.com/XTLS/Xray-core), the [Remnawave](https://remna.st/) panel + node stack, [nginx](https://nginx.org/), and [acme.sh](https://github.com/acmesh-official/acme.sh).
