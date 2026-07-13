# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Meridian, please report it responsibly:

1. **Do NOT open a public issue** for security vulnerabilities
2. Email the maintainer or use [GitHub Security Advisories](https://github.com/getmeridian/meridian/security/advisories/new)
3. Include steps to reproduce and potential impact

We aim to respond within 48 hours and will credit reporters in the fix.

## Security Design

- **Credentials**: stored locally with `0600` permissions, secrets are never passed through shell command strings without `shlex.quote()` and are redacted from `meridian doctor` output
- **Panel access**: Remnawave admin UI reverse-proxied by nginx at a secret HTTPS path in all modes (no SSH tunnel required)
- **SSH**: password authentication disabled by default; fail2ban enabled for brute-force protection
- **Firewall**: UFW configured with deny-all-incoming, only ports 22 + 443 + 80 (ACME) opened by default. Xray inbounds and Remnawave panel/node APIs listen on localhost behind nginx — no extra ports exposed.
- **Docker images**: Remnawave backend, node, and subscription page are pinned to tested versions in `src/meridian/config.py` to prevent supply-chain drift
- **TLS**: [acme.sh](https://github.com/acmesh-official/acme.sh) issues Let's Encrypt certificates; nginx serves them. IP-only mode uses LE's short-lived IP certs; domain mode uses standard DNS-validated certs.
- **Update checks**: the CLI periodically checks [PyPI](https://pypi.org/project/meridian-vpn/) for new versions by reading the public JSON API. No telemetry, no user data, no tracking — only an outbound HTTPS GET to `pypi.org`

## Scope

This project configures proxy servers — it does NOT implement cryptographic protocols. The underlying security depends on [Xray-core](https://github.com/XTLS/Xray-core), the [Remnawave](https://remna.st/) panel + node stack, [nginx](https://nginx.org/), and [acme.sh](https://github.com/acmesh-official/acme.sh).
