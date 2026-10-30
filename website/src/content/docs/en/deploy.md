---
title: Deploy Guide
description: Full deployment walkthrough with all configuration options.
---

This guide covers every option of `meridian deploy`. If this is your first deployment, start with [Getting Started](/en/getting-started/).

## Basic deployment

```
meridian deploy 198.51.100.10
```

One server becomes both the control server (running Meridian's control service) and the exit that carries traffic. Meridian builds the same reviewed topology that [`meridian setup`](/en/cli-reference/#meridian-setup) produces and applies it with checkpoints. Before it prints "Done", it pushes real traffic through the delivered subscription.

Run `meridian deploy` without an IP and it asks for one. Add `--yes` to skip confirmations:

```
meridian deploy 198.51.100.10 --client-name alice --yes
```

## Password-only servers

Meridian logs in with SSH keys. Many VPS providers hand you only a root password, so when the server refuses your keys but accepts a password, an interactive `meridian deploy` asks:

```
→ This server accepts only a password. Create a key for Meridian and install it now? [y/N]
```

Answer `y` and type the password (hidden). Meridian creates its own key in `~/.config/meridian/ssh/meridian_ed25519`, adds it to the server's `authorized_keys`, checks that it logs in, saves it in the server profile, and carries on. The password is used for that one command and is never saved or logged. The host key is verified first, so the password only goes to the server whose fingerprint you accepted. `meridian setup` offers the same when you add a server.

Without a terminal, with `--yes`, or with `--json`, Meridian never prompts: it stops with the manual command instead. Install your own key once, then rerun:

```
ssh-copy-id root@198.51.100.10
```

Hardening turns password login off after deploy, so keep the key: later commands use it automatically.

## All flags

| Flag | Default | Description |
|------|---------|-------------|
| `--sni HOST` | (auto) | Camouflage target. Without it, deploy prefers a site in the server's own subnet, then `www.google.com`, `dl.google.com`, `www.cloudflare.com` |
| `--domain DOMAIN` | (none) | Your domain: XHTTP moves onto it and WSS is added, both through Cloudflare's CDN |
| `--self-steal` | off | Camouflage with your own domain (`--sni`, else `--domain`) instead of someone else's site (see [Your own domain](#your-own-domain-self-steal)) |
| `--hysteria2/--no-hysteria2` | on | Offer Hysteria2 on UDP/443 as the last fallback |
| `--client-name NAME` | default | Name of the first client |
| `--display-name NAME` | (none) | Label for connection pages (e.g. "Alice's VPN") |
| `--icon EMOJI_OR_URL` | (none) | Page icon — emoji or image URL |
| `--color PALETTE` | ocean | Page color theme (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH user (non-root users need passwordless sudo) |
| `--ssh-port PORT` | 22 | SSH port, if not 22 |
| `--harden / --no-harden` | on | SSH key-only login, fail2ban, and UFW firewall. Use `--no-harden` on a server shared with other services |
| `--warp / --no-warp` | off | Send outgoing traffic through Cloudflare WARP |
| `--geo-block / --no-geo-block` | on | Block Russian domains and IPs on the server |
| `--country CC` | looked up | Country shown in connection names, such as `DE`. Meridian looks it up from the address registry; set it if that is wrong |
| `--yes` | | Skip confirmation prompts |

Flags for scripts and UIs (`--json`, `--events`, `--request`, `--dry-run`) are described in the [CLI reference](/en/cli-reference/#meridian-deploy).

## Choosing a camouflage target

The camouflage target (SNI) is the website your server impersonates. Usually it is **not** a domain you own — it is a real site with TLS 1.3, and anyone who probes your server sees that site's real certificate. To use a domain you own instead, see [Your own domain](#your-own-domain-self-steal).

The best target lives next to your server. A censor sees the SNI together with your server's IP; when the site's DNS really points into the same network, the pair looks like ordinary traffic, while a famous foreign site on a VPS address stands out.

Without `--sni`, a first deploy scans the server's own subnet for such sites (about a minute) and keeps those whose DNS points into the subnet and that pass Reality's TLS 1.3 and HTTP/2 check. Then it falls back to `www.google.com`, `dl.google.com`, and `www.cloudflare.com`. It uses the first target that real traffic passes through. `meridian node add` picks a neighbor the same way. A target you pass with `--sni` is never swapped; if traffic fails, deploy stops and tells you. A redeploy keeps the saved target.

To see the candidates yourself, scan the server's IPv4 neighborhood:

```
meridian scan 198.51.100.10
```

The scan runs a pinned, checksum-verified build of RealiTLScanner on the server and lists certificate domains it found nearby. Then deploy with your pick:

```
meridian deploy 198.51.100.10 --sni dl.google.com
```

To check one target by hand, run `meridian check 198.51.100.10 --sni HOST`. Avoid `apple.com` and `icloud.com` — their networks don't match VPS providers.

### Changing the target later

When `meridian test --probe` reports the camouflage SNI blocked, switch the exit to another target without redeploying. Meridian refuses `apple.com`, `icloud.com`, and `www.microsoft.com` (its certificate is too large for Reality), checks that the exit reaches the new site with TLS 1.3 and HTTP/2, shows the review, and applies it. The old name is replaced at once rather than kept alongside, because a Reality listener imitates exactly one site. Apps switch at their next subscription refresh (every 6 hours) or when the user refreshes the subscription. For a relay, use `meridian relay sni RELAY NEW_SNI`.

```
meridian node sni 198.51.100.10 www.cloudflare.com
```

### Your own domain (self-steal)

Instead of borrowing someone else's site, the server can imitate a domain you own. Point a DNS-only A record (grey cloud in Cloudflare, not proxied) at the server, then deploy with `--self-steal`:

```
meridian deploy 198.51.100.10 --sni www.example.com --self-steal
```

Meridian issues a Let's Encrypt certificate for the name and serves it from the server itself. A probe without the Reality key, or with any unknown SNI, gets that certificate and nginx's stock 404 page, the same as any nginx server with nothing published yet. The name resolves to this server and its certificate is genuine, so there's no mismatch to spot, and no outside site that can go down, change its certificate, or get blocked on its own.

Before changing anything, Meridian checks where the name points from the server. It stops when the name doesn't resolve (Let's Encrypt couldn't issue the certificate), and warns when it points somewhere else, such as Cloudflare's proxy.

With `--domain` and no `--sni`, the domain does both jobs: Reality imitates it, and XHTTP and WSS keep working behind it. A domain proxied through Cloudflare resolves to Cloudflare, though, so for self-steal plus the CDN use two names: a DNS-only one for `--sni` and a proxied one for `--domain`. `meridian node add` and `meridian node sni` take `--self-steal` too. Relays with their own SNI keep borrowing theirs.

### Camouflage target vs. domain

- **Camouflage target** (`--sni`) — a popular website you **don't** own, which Reality impersonates; or, with `--self-steal`, a name you do own that points at this server.
- **Domain** (`--domain`) — a domain you **do** own, pointed at your server. XHTTP and Hysteria2 move to it, and it adds a WSS path through Cloudflare's CDN. See [Domain mode](/en/domain-mode/).

## Hysteria2

```
meridian deploy 198.51.100.10 --no-hysteria2
```

Every deploy offers Hysteria2 on UDP/443 with your domain's certificate, or the server's IP certificate without a domain. Its traffic is wrapped in salamander obfuscation, so censors see random UDP rather than QUIC. It helps on lossy or high-latency networks; where UDP is blocked, apps fall back to the TCP methods listed before it. `--no-hysteria2` turns it off. Hysteria2 doesn't pass through relays.

## Branding

Personalize connection pages so recipients know who set up their VPN:

```
meridian deploy 198.51.100.10 --display-name "Alice's VPN" --icon 🚀 --color sunset
```

- **`--display-name`** — shown in the page header and title
- **`--icon`** — an emoji or image URL shown at the top of the page
- **`--color`** — accent palette: `ocean` (default), `sunset`, `forest`, `lavender`, `rose`, `slate`

Branding is saved in `cluster.yml` and applies to every client's connection page.

## Redeploying

To redeploy, run the same command again. Meridian keeps the saved paths, keys, and users, so existing links keep working. A redeploy without `--sni` keeps the saved camouflage target.

At the end of every deploy, Meridian prints the exact redeploy command with your current settings. Running it again with different flags changes the server to match: adding or dropping `--domain` or `--no-hysteria2` adds or removes those paths.

`meridian deploy` only redeploys a server that is already an exit in your fleet. To add another server, use `meridian node add NEW_IP`. On a fleet that still runs the Remnawave panel, deploy offers to move it to the control service first (see [Moving from Remnawave](#moving-from-remnawave)).

## Edit the fleet as a file

To change several things at once, or to keep your fleet's layout in version control, export it, edit it, and import it back:

```bash
meridian fleet export > fleet.yml
$EDITOR fleet.yml
meridian fleet import fleet.yml
```

Import shows each change before anything happens:

```
  + client carol
  ~ exit-2 sni www.example.com -> www.example.org
  → Apply? [y/N]
```

The file names servers by their saved names and holds no keys, credentials, links, or transport paths, so it is safe to keep in a repository. Servers it names must be saved first (`meridian server add`). See [`fleet import`](/en/cli-reference/#meridian-fleet) for what it refuses.

## Pre-flight check

Not sure a server is suitable?

```
meridian check 198.51.100.10
```

Checks the camouflage target, whether port 443 is free and reachable from outside, optional domain DNS, OS, disk space, and clock skew, without installing anything.

## Non-root deployment

```
meridian deploy 198.51.100.10 --user ubuntu
```

Commands run through `sudo`, so the user needs passwordless sudo.

## Local deployment

If you are logged into the server as root:

```
meridian deploy local
```

This skips SSH. `check local`, `scan local`, and `doctor local` work the same way, and once deployed, later commands on the server run locally too. Running Meridian from your own machine is still recommended, because `meridian test` needs an outside vantage point.

## Cloudflare WARP

WARP sends your server's outgoing traffic through Cloudflare, so websites see a Cloudflare address instead of your VPS IP.

```
meridian deploy 198.51.100.10 --warp
```

**When WARP helps:** sites that block datacenter IP ranges, or when you want to hide the VPS address from destination sites.

**When you don't need it:** most sites work fine without it, and it adds one hop (VPS → Cloudflare → destination).

Meridian installs the `cloudflare-warp` client in proxy mode (SOCKS5 on `127.0.0.1:40000`) and makes it Xray's outbound. Incoming connections are not affected. WARP is free and needs no Cloudflare account.

### WARP for some sites only

To send only chosen sites through WARP and keep everything else direct, add a route on the exit instead of turning WARP on for all traffic. Meridian sets up WARP on the exit the first time a route needs it:

```
meridian route add exit-1 --via warp chatgpt.com netflix.com
meridian route add exit-1 --via block geosite:category-ads-all
meridian route list
meridian route remove exit-1 warp
```

Values are domains (subdomains included), `geosite:NAME` lists, or `geoip:NAME` lists from the stock v2fly data files; one route holds one kind. `--via direct` does the opposite on a WARP exit: those destinations skip WARP. Routes apply in the order you added them, before the exit's default egress and before geo-blocking. These routes live on the server; they are separate from the client-side rules that send Russian sites direct on the user's device, and from routing-gateway rules, which can send traffic directly from a domestic gateway ([Split routing](/en/split-routing/#through-a-routing-gateway)).

### AI services

AI services (ChatGPT, Claude, Gemini, Cursor, and others) refuse some hosting addresses, and a session whose address jumps between countries, or briefly shows a home connection in Russia, can end in a logout or a suspended account. One route sends them all through WARP:

```
meridian route add exit-1 --preset ai
```

The preset is a single route named `ai` covering `geosite:category-ai-!cn`, `anthropic`, `openai`, `cursor`, and `google-gemini`; add `--via direct` to keep it on the exit's own address. WARP leaves in the exit's region, so keep AI users on exits in one country. A server route cannot catch traffic the device sends outside the VPN: on macOS, Happ may send traffic directly for a moment after the lid opens. If Google services still treat you as being in Russia, the "change the country associated with your account" request in Google Account help usually fixes it within hours.

## Geo-blocking

By default, the server blocks Russian domains (`geosite:category-ru`) and Russian IP ranges (`geoip:ru`) through the VPN. This keeps your VPS IP out of Russian services' logs, which lowers the chance of it being found and blocked.

Turn it off if your users need those sites through Meridian:

```
meridian deploy 198.51.100.10 --no-geo-block
```

For a way to open Russian sites directly while keeping the block, see [Split routing](/en/split-routing/).

## Client addresses

The control service never stores client IP addresses or connection logs. Every TCP connection reaches Xray through nginx on the server. By default the service keeps lifetime traffic totals and last seen; [Privacy](/en/privacy/) lets you opt in to daily history or device limits with a plain disclosure of what they reveal.

## Adding a relay

After the exit is running, add a relay on a domestic server so users keep a way in if the exit IP gets blocked. See the [Relay guide](/en/relay/).

```
meridian relay add 203.0.113.10 --exit 198.51.100.10
```

## Web panel

The control service comes with a built-in web panel (owner console) for one owner: you. It sits on a random secret path, separate from the connection-page path, so links you share never reveal it. `meridian panel` prints its address, and until the panel has an owner, a one-time setup link:

```
  Meridian web panel
  URL         https://198.51.100.10/<secret-path>/
  Setup link  https://198.51.100.10/<secret-path>/#setup=…
              Works once, for 15 minutes. Open it to choose the owner's password.
```

After choosing a password you can turn on a sign-in code from an authenticator app. In the web panel you add people, share their links and QR codes, set expiry dates, traffic limits, and device caps, keep private notes and tags, post a notice for everyone, choose privacy levels, and issue scoped API tokens; server health, traffic, monthly allowances, revisions, and topology settings are shown read-only, with the `meridian` command that changes them. See [Web panel](/en/client-management/#web-panel).

Treat the panel address like a password. If you lose the password or the authenticator, `meridian panel --setup-link` prints a reset link: it sets a new password, turns the sign-in code off, and signs out every device.

## Backups

At the end of your first deploy, Meridian prints a recovery key, once:

```
  Recovery key (shown once; Meridian keeps no copy)

    AGE-SECRET-KEY-1…

  Write it down or print it, and keep it offline, away from this computer.
```

Store it somewhere offline, away from the machines that manage the fleet. After every change, Meridian saves an encrypted snapshot of the control service on your machine, readable with your machine's own key or the recovery key. If the control server is ever lost, [`meridian restore`](/en/recovery/#control-server-lost) rebuilds it on a new server from the newest snapshot.

## Upgrades

The control service and the node agent ship with Meridian. After `meridian update`, run `meridian fleet upgrade`: it shows what will change, then moves the control service to the new version and upgrades each agent in place, keeping every node's identity. It also moves relays to the newest Realm release without waiting for a Meridian update. Any `meridian apply` brings the control service and agents up to the installed Meridian too.

## Moving from Remnawave

A fleet deployed by an earlier Meridian 4 release runs the Remnawave panel. `meridian migrate` moves it to the control service in place, on the same servers:

```
meridian migrate
```

It backs up the panel's database (to your machine and to the control server), carries every client over with their keys, status, limits, expiry, and traffic, replaces each node's container with the agent, and checks that every subscription link is unchanged before it removes Remnawave. Clients keep their links and notice nothing. Panel-only data such as HWID device lists, per-day usage history, and plugin settings is not carried over, and client IP forwarding (which served the panel's per-user IP limits) is turned off; the review lists it. The move is resumable: if it stops, run `meridian migrate` again. See [`meridian migrate`](/en/cli-reference/#meridian-migrate).
