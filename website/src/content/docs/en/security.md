---
title: Security
description: What Meridian contacts, what it sends, how downloads are verified, and how to report vulnerabilities.
---

Meridian has **no telemetry, analytics, or ads**. It does not upload diagnostic reports. The connection page, support page, owner console, and website serve their own scripts, styles, fonts, icons, and QR rendering; they load **no third-party assets**. Opening an app store, support contact, or other external link is your choice.

Installation, certificate renewal, DNS, and connectivity checks do need network access. The requests below describe those boundaries so you can decide which services to use.

## Reporting vulnerabilities

If you discover a security vulnerability in Meridian:

1. **Do NOT open a public issue**
2. Report it privately through [GitHub private vulnerability reporting](https://github.com/getmeridian/meridian/security/advisories/new)
3. Include steps to reproduce and potential impact

We aim to respond within 48 hours and will credit reporters in the fix.

## Outbound requests

Every recipient can see the connecting machine's IP address and normal protocol headers. The tables name any additional data. Third parties do not receive your fleet configuration, client credentials, or usage records through these requests; those go only to your own control service and servers. There is no single switch that makes deployment offline. Downloads can follow the source's redirects, including GitHub's release-asset hosts. Installer and host-check names use the connecting machine's configured DNS.

### On your managing machine

| Request | When and what it sends | Your choice |
|---------|------------------------|-------------|
| **CLI installation → `getmeridian.org`, GitHub, and package sources** | You fetch `https://getmeridian.org/install.sh`. If uv is absent, the installer downloads its pinned script from astral-sh/uv and the matching binary archive, then installs Meridian and dependencies from package sources, normally PyPI, with managed Python if needed. Sends script, asset, package-version, and platform requests. | Only when you run the installer; an existing uv skips its bootstrap downloads. |
| **Update notice → PyPI** (`pypi.org/pypi/meridian-vpn/json`) | Human CLI invocations check the package's release list at most once per minute. The request identifies the package, with no fleet data. JSON output and the `serve` command skip it. | Set `MERIDIAN_DISABLE_UPDATE_CHECK=1`. This disables the background notice, not an explicit `meridian update`. |
| **`meridian update` → PyPI and package download sources** | Checks releases, then uses uv, pipx, or pip to install the chosen version and dependencies. The package manager sends package/version/platform requests to its configured index, normally PyPI and `files.pythonhosted.org`; uv may also download managed Python from GitHub. | Run it when you want an update. Package managers control their indexes, caches, and offline settings. |
| **`meridian fleet upgrade` → GitHub release API** | For fleets with relays, reads Realm releases and their published SHA-256 digests. Sends the public repository request and `GITHUB_TOKEN` if you set it. | `meridian fleet upgrade --tested` keeps the tested pins and skips this release lookup; installation may still need downloads. |
| **Country suggestion → IANA and the responsible RIR's RDAP service** | When adding an exit, downloads IANA's IPv4/IPv6 registry map, then requests the server IP's registration record. The RIR learns that IP; IANA receives only the map request. | Supply `--country` to `deploy` or `node add` to skip the lookup. The setup wizard also requests a suggestion unless it already has a saved region. |
| **Local public-IP detection → `ifconfig.me`, `api.ipify.org`, or `api64.ipify.org`** | Resolving a new `local` target asks an IP echo service for this machine's public address, trying alternatives on failure. | Use a saved target or an explicit server address to avoid fresh detection. |
| **Branding image → the image URL you supply** | An image icon is downloaded once on your machine, then stored as embedded image data. The image host receives its URL and a Meridian user-agent header. | Use an emoji or already embedded image data. The browser never downloads the original image URL. |
| **Fleet management → your servers and control service** | Live operations send SSH authentication, commands and files, or HTTPS API tokens and the settings, credentials, commands, and backup requests that operation needs. Migration reads only the old panel you specify. | Required for live management; local review and export operations use their documented local state. |
| **Xray test helper → GitHub's XTLS/Xray-core releases** | A connection test downloads the pinned archive for your OS and architecture when the local helper cache is incomplete. | A complete helper cache avoids another download. There is no download-disable flag for a test that needs the helper. |

### Checks and installation on servers

| Request | When and what it sends | Your choice |
|---------|------------------------|-------------|
| **Reachability, camouflage, and blocking checks → the tested addresses and sites** | `check`, `test`, `--probe`, SNI selection, and deployment verification make TCP/TLS/HTTP requests from your machine or servers. They send the selected SNI, HTTP path, or probe bytes. Forward/reverse hostname checks use the machine's DNS resolver. | Run optional diagnostics when needed. Deployment requires its verification; specifying an SNI avoids automatic neighbor discovery, but still checks that site. |
| **Connection tests → IP echo services** | Sends HTTPS requests through the tested proxy to `api.ipify.org`, `ipv4.icanhazip.com`, or `v4.ident.me`; IPv6 uses `api6.ipify.org`, `ipv6.icanhazip.com`, or `v6.ident.me`. The observer sees the exit IP. Failed tests may repeat the request directly, exposing the managing machine's IP to the observer. | Set `MERIDIAN_CONNECT_TEST_URL` to an endpoint you control that returns an IP address. Deployment also uses these traffic tests. |
| **Camouflage discovery → GitHub and neighboring TLS sites** | `scan` and automatic SNI suggestions download pinned RealiTLScanner from XTLS/RealiTLScanner, then probe neighboring addresses and validate certificate names with DNS and TLS. The sites see the server's probes. | Choose `--sni` explicitly to avoid automatic discovery; do not run `scan` if you do not want the scan. |
| **System packages and security updates → APT repositories** | Installation downloads packages from the OS's configured repositories. The baseline enables daily package-list refresh and unattended security updates. It installs nginx from `nginx.org` if the distribution package is insufficient. Requests identify distribution, architecture, and packages. | The server administrator controls APT sources and schedules; applying the baseline restores its update settings. `--no-harden` does not turn off package updates. |
| **Control service installation → GitHub and PyPI** | Downloads the pinned uv archive from astral-sh/uv, managed Python through uv, and the exact `meridian-vpn` release plus dependencies from PyPI or configured indexes. An editable checkout builds a wheel on the managing machine, which can fetch build dependencies from its package index, then uploads the wheel over SSH instead. | An already current installation or populated caches avoid downloads. Normal installation needs the package sources. |
| **Relay installation → GitHub's zhboner/realm releases** | Downloads the pinned Realm archive when installing or changing the relay binary. Sends only the public asset request. | An already installed matching binary needs no download. |
| **Certificate tool → `raw.githubusercontent.com`** | Downloads the pinned acme.sh script from acmesh-official/acme.sh when it is absent, before verifying and installing it. | An existing installation is reused. |
| **Certificate issuance and renewal → Let's Encrypt** | acme.sh makes ACME requests for your server IP or domain, sending certificate identifiers, public keys, account proofs, and a contact email if supplied. Renewal runs from cron. Certificate identifiers also appear in public Certificate Transparency logs. | Trusted certificates are required for normal deployment. `MERIDIAN_ACME_SERVER` selects another ACME CA; it does not disable certificates. |

### Control service and node agents

| Request | When and what it sends | Your choice |
|---------|------------------------|-------------|
| **Control service blob mirror → HTTPS artifact sources, normally GitHub's Xray releases** | At startup, after a node spec changes, or after an agent requests a missing blob, fetches missing pinned archives. Failed requests can retry, at most once a minute after a miss. Sends the public asset URL, with no user or usage data. | There is no mirror-disable setting. Verified blobs already in its cache need no upstream request. |
| **Node agent → your control service** | Enrolls, opens and renews sessions, polls signed documents and commands, reports applied revisions and health, sends per-user traffic totals, acknowledges commands, and fetches service blobs. Sends enrollment tokens or identity proofs, session tokens, node/build information, and the reports. | Required while an agent manages the node. Stopping it also stops configuration and traffic reporting. |
| **Node agent → pinned HTTPS binary sources, normally GitHub** | A signed desired-state document can require an Xray archive. The generated spec tries GitHub first and the service mirror second. The upstream sees the asset URL and the agent build in its user-agent; it never receives the service token. | Installed binaries and the artifact cache avoid downloads. The service mirror is the fallback when GitHub is unreachable; there is no CLI switch to prefer it. |

The **agent itself is not downloaded from GitHub by the node**. Meridian's wheel contains static musl builds for x86_64 and aarch64, with digest and build-version files. The managing machine verifies the bundled build and uploads it over SSH for installation or upgrade.

### Proxy traffic and VPN apps

| Request | When and what it sends | Your choice |
|---------|------------------------|-------------|
| **Exit → user destinations and the Reality camouflage site** | Carries the traffic users request. Reality and nginx's camouflage fallback also contact the selected site for borrowed TLS camouflage and unauthenticated connections. That site sees the exit's connection, SNI, and forwarded probe traffic. nginx resolves its camouflage upstream through the host's DNS resolver. | You choose the camouflage site; self-steal uses your own local site. Destination traffic is the VPN's function. |
| **Exit DNS → Cloudflare, Quad9, and Google** | User destination lookups use parallel DoH queries over reachable IPv4/IPv6 endpoints: Cloudflare `1.1.1.1` / `2606:4700:4700::1111`, Quad9 `9.9.9.10` / `2620:fe::10`, Google `8.8.8.8` / `2001:4860:4860::8888`, all at `/dns-query`. Each operator receiving a query can see the requested names and exit IP. `check` also sends a fixed example DNS query to these endpoints. | No CLI resolver toggle. If all fail, exit destination lookups fail without plain-DNS fallback. These queries go directly from the host even with WARP enabled. |
| **Optional WARP → Cloudflare** | Installation fetches the signing key and package from `pkg.cloudflareclient.com`; warp-cli registers the server and opens the WARP tunnel. Traffic routed to WARP leaves through Cloudflare, which sees the server registration and egress destinations. | WARP is optional; omit it to avoid its installation, registration, and tunnel. An already installed daemon must be stopped on the server to end its own network connections. |
| **Optional routing gateway → DNS services and pool probe URL** | Gateway profiles use Google DoH at `dns.google`, plain DNS at `8.8.8.8`, and the host resolver for direct traffic. An egress pool probes through its exits every 10 seconds, by default `https://www.apple.com/library/test/success.html`; the test site sees the exit IP and HTTP request. | Gateways and pools are optional. The reviewed pool's `probe_url` can name your own test endpoint. Gateway DNS does not have the exit's encrypted-only guarantee. |
| **Realm relay → the next hop or exit** | Forwards the client's encrypted TCP stream to the destinations in its reviewed configuration. Runs no agent, VPN core, or separate telemetry client. | You choose the relay chain and exit. |
| **VPN app → subscription service, DNS, and health-test site** | Your chosen app refreshes your subscription and may make its own DNS and connectivity requests. Meridian's Xray JSON profiles configure plain DNS at `1.1.1.1` / `8.8.8.8` and Apple test-page probes every minute; Mihomo fallback checks use that page every 5 minutes when active; sing-box configures Cloudflare DNS over TLS and UDP, with that page every 3 minutes. Probe sites see the path's egress IP, with no subscription credentials. | App behavior is separate from the browser page. Manage DNS and health checks in your app or its configuration. |

### Connection pages, console, and website

- **Connection and support pages:** only their own service origin, for page/configuration, account status, subscriptions, shared assets, and optional clock/transfer checks. The service receives the person's secret URL and normal browser headers. QR generation and image export happen locally. No third-party requests.
- **Owner console:** only its own service origin, for local assets, sign-in, session refresh, API reads/writes, and visible-screen refreshes. Passwords, authenticator codes, session cookies, and edits go to your service. No third-party requests.
- **Website:** only its own origin, including fonts, search files, Markdown copies, and demo assets. The console demo answers its API in the browser. No analytics, external fonts, embeds, or third-party requests.

External app, documentation, and support links open only when you follow them. They do not make the page fetch those sites in the background.

## Download verification

Xray, Realm, RealiTLScanner, acme.sh, and the server's uv bootstrap use pinned versions and **SHA-256 checks before execution**. The CLI installer also checks its pinned uv installer; that installer carries the archive checksums. Release assets include `SHA256SUMS` for checking the Meridian installer before running it. A mismatch stops the install.

The control service mirror and agent check an artifact's **exact size and SHA-256** before publishing or extracting it. The agent verifies the control service's signature on desired state before applying its pins. Bundled static musl agent builds are checked against the wheel's digest files and verified after SSH transfer. A fleet upgrade accepts a newer Realm release only when GitHub publishes SHA-256 digests for every required asset.

The control service pins the Meridian package version. Python dependencies use the package manager's index and integrity checks; OS packages use APT's signed repositories, with expected signing-key fingerprints checked for the added nginx and WARP repositories. Download integrity still depends on trusting the package or release source that supplies the pin.

## Security design

- **Credentials**: stored with `0600` permissions in `~/.config/meridian/cluster.yml`, secrets never passed through shell commands without `shlex.quote()`, redacted from `meridian doctor` output. Directory permissions are `0700` (owner-only). Any application running as your user can read these files — this is inherent to all CLI tools that store credentials locally (SSH keys, cloud CLI tokens, etc.). If your local machine is compromised at the user level, credential encryption would not help: malware can keylog the passphrase or intercept SSH sessions. Protect your local machine.
- **Control service access**: the control service is reverse-proxied by nginx at a secret HTTPS path — no SSH tunnel required. Connection pages and subscription links use a separate path and never reveal it. The CLI holds its admin token in `~/.config/meridian/cluster.yml`; scripts get their own revocable, scoped tokens
- **Web console**: one owner, with a password and an optional authenticator code; sessions expire after 30 minutes idle and 12 hours at most, and token creation or privacy changes need a recent password confirmation. The console manages people and privacy, never servers, and holds no SSH keys. It loads nothing from other sites and runs under a strict Content Security Policy
- **Nodes**: agents connect out to the control service over HTTPS, so nodes open no management port. Each joins with a single-use enrollment token and then holds a certificate from the fleet's own authority. Every document a node runs is signed by the control service and verified by the agent, which refuses anything older than what it already applied
- **Privacy**: device limits and usage history default to off: the control service keeps lifetime traffic totals and last seen. You can opt in to keyed device fingerprints, full device details, or daily traffic with bounded retention. No level stores client IP addresses, connection logs, or request paths. See [Privacy](/en/privacy/) for what a seized server reveals
- **Backups**: snapshots are encrypted with [age](https://age-encryption.org/) to each managing machine's key and to a recovery key whose private half only you hold. They carry no signing keys, sessions, or console password: a restore makes new ones, so a seized control server can't sign anything a restored fleet accepts
- **SSH**: password authentication disabled and fail2ban enabled by default (`--no-harden` skips both, plus the firewall, for servers shared with other services)
- **Firewall**: UFW denies inbound traffic except the server's SSH ports and the ports its role needs — TCP/443 for proxy traffic, TCP/80 where certificates are issued, UDP/443 only when Hysteria2 is enabled, and the listen port on relays
- **Exit DNS**: user destination lookups use parallel DNS over HTTPS to Cloudflare, Quad9, and Google by IP address. Those operators see the names; the hosting provider does not receive them as plain DNS. If none answers, exit lookups fail without a plain-DNS fallback. Setup checks, camouflage hostname resolution, gateway DNS, and client-app DNS have the separate behavior described above
- **Pinned software**: verified binary downloads, signed desired state, and bundled static musl agents protect installation as described above; the control service runs the exact Meridian package release
- **TLS**: [acme.sh](https://github.com/acmesh-official/acme.sh) handles certificates via Let's Encrypt, served by nginx

## Scope

Meridian configures proxy servers — it does **not** implement cryptographic protocols. The underlying security depends on:

- [Xray-core](https://github.com/XTLS/Xray-core) — VLESS+Reality, XHTTP, WSS, and Hysteria2 protocols
- [age](https://age-encryption.org/) (through pyrage) — snapshot encryption
- [nginx](https://nginx.org/) — SNI routing and TLS termination
- [acme.sh](https://github.com/acmesh-official/acme.sh) — Let's Encrypt certificate issuance and renewal
- [Realm](https://github.com/zhboner/realm) — TCP forwarding on relays
