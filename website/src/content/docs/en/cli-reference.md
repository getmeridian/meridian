---
title: CLI Reference
description: Complete reference for all Meridian CLI commands and flags.
---

## Commands

### meridian deploy

Deploy one server and get a shareable connection link. The server becomes both the control server (running Meridian's control service) and an exit. Deploy builds the same reviewed topology as `meridian setup`, applies it with checkpoints, verifies real traffic through the delivered subscription, and prints a connection page and subscription URL. Re-running the same command resumes an interrupted deploy or redeploys, keeping paths, keys, and users. The first deploy of a fleet ends by showing its recovery key once (see [meridian backup](#meridian-backup)).

```
meridian deploy [SERVER] [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sni HOST` | (auto) | Camouflage target. Without it, a first deploy tries sites in the server's own subnet, then `www.google.com`, `dl.google.com`, `www.cloudflare.com`, and uses the first that carries traffic; a redeploy keeps the saved target. An explicit `--sni` is never swapped |
| `--domain DOMAIN` | (none) | Your domain: XHTTP moves onto it and WSS is added, both through Cloudflare's CDN (XHTTP works without one, on the IP). The control service and connection pages stay on the IP certificate |
| `--self-steal` | off | Camouflage with your own domain (`--sni`, else `--domain`), pointed at the server with a DNS-only record: Meridian issues its Let's Encrypt certificate and the server answers probes itself, with nginx's stock 404. Deploy first checks where the name resolves: it stops when it doesn't resolve and warns when it points elsewhere. A redeploy keeps it; a new `--sni` without the flag goes back to a borrowed site |
| `--hysteria2/--no-hysteria2` | on | Offer Hysteria2 on UDP/443, disguised with salamander, as the last fallback. Uses the domain's certificate, or the IP certificate without one |
| `--fingerprint NAME` | chrome | TLS fingerprint clients present on Reality, XHTTP, and WSS (`chrome`, `firefox`, `safari`, `ios`, `android`, `edge`, `random`, `randomized`). Change it if your region starts flagging the current one; a redeploy keeps the saved value |
| `--client-name NAME` | default | Name of the first client |
| `--display-name NAME` | (none) | Label for connection pages |
| `--icon EMOJI_OR_URL` | (none) | Page icon — emoji or image URL |
| `--color PALETTE` | ocean | Page color theme (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | saved, else root | SSH user (non-root needs passwordless sudo) |
| `--ssh-port PORT` | saved, else 22 | SSH port |
| `--harden / --no-harden` | on | SSH key-only login, fail2ban, and UFW firewall. `--no-harden` is for servers shared with other services |
| `--warp / --no-warp` | off | Send outgoing traffic through Cloudflare WARP |
| `--geo-block` / `--no-geo-block` | on | Refuse Russian domains and IPs (`geosite:category-ru` + `geoip:ru`) at the exit. Xray JSON and Mihomo subscriptions already send them directly from the device |
| `--country CC` | address registry | Two-letter country shown in connection names and on the console's server list. Without it, a first deploy asks the server address's Regional Internet Registry (via IANA's RDAP bootstrap, no geo-IP service); a redeploy keeps the saved country |
| `--yes` | | Skip confirmation prompts |
| `--json` | | Emit the final deploy result as a `meridian.output/v1` envelope |
| `--events=jsonl` | | Stream typed progress events as JSONL on stderr |
| `--request FILE` | | Read a `deploy-request` JSON payload from a file; use `-` for stdin |
| `--dry-run` | | Compile and preview the deploy without SSH or local changes |

Without an IP, deploy asks for one. `local` deploys on the machine you are running on. Deploy only redeploys a server that is already an exit in your fleet; add other servers with `meridian node add`. On a fleet that still runs the Remnawave panel, deploy offers to run [`meridian migrate`](#meridian-migrate) first. Running it again with different flags updates that exit to match (for example, adding or dropping `--domain` or `--no-hysteria2`) while keeping your other exits, relays, gateways, and clients.

**Machine/UI flow**: `meridian api workflow deploy --json` returns a renderable wizard contract. A UI collects those fields, validates them against `deploy-request`, then runs `meridian deploy --request deploy.json --json --events=jsonl`. Machine deploys are non-interactive: the request must contain `yes: true` after the user confirms. `--dry-run --json` returns `deploy-plan` under `data`, so a UI can preview the mode, ports, and generated paths before opening SSH.

### meridian setup

A resumable guided wizard for one server or many: exits, Realm relays, routing gateways with failover pools, and users. It is written for people who have never set up a VPN: every question uses the real term (exit, relay, SNI) and explains it in one plain sentence, with a safe default you can accept by pressing Enter.

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--intent FILE` | (none) | Start at review from a complete topology JSON document; use `-` for stdin |
| `--restart` | | Discard saved setup progress before starting |
| `--yes`, `-y` | | Approve the review and apply without asking |

The first question is the language (English, Русский, فارسی, 中文), preselected from your terminal and remembered in `~/.config/meridian/preferences.json`; `--lang` or `MERIDIAN_LANG` skips it. Then the wizard walks through servers (an IP, `user@IP:port`, or `local` for this machine), what each server does, connection methods, routing (only asked when you have a routing gateway: each rule sends matching traffic to an exit or pool, directly from the gateway, or nowhere), users, a plain-language summary, and apply. Choices are lists: move with the arrow keys (or press an option's number), toggle checklist items with Space, press `?` for details, Enter to confirm, Esc to go back, and Ctrl+C to quit. Wherever a list offers "Something else", you can type your own value. In text fields you can also type `back`, `details`, `save`, or `quit` (or the same words in your language). For the camouflage site, the wizard scans the exit's own network first (about a minute) and lists those sites ahead of the well-known ones. Set `MERIDIAN_PLAIN_PROMPTS=1` for numbered prompts (they are used automatically when the terminal can't draw lists). Running `meridian` with no command in a terminal, before any VPN exists, offers to start the wizard. Progress is saved in `~/.config/meridian/setup.json` after each stage, so running `meridian setup` again resumes; passwords and private keys are never stored there. `--intent` does not replace an existing draft unless `--restart` is also given. When apply succeeds, setup verifies every node and subscription and prints each user's connection page.

### meridian plan

Show what `meridian apply` would change, comparing the topology in `~/.config/meridian/cluster.yml` with the live servers and the control service. Nothing is changed.

```
meridian plan [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit the plan as a `meridian.output/v1` envelope for CI and UI clients |

The output shows the plan hash, resource counts by kind, and any resources that need repair or could not be observed. JSON `data` contains `plan_hash`, `converged`, `resources[]`, `drifted_resources[]`, `observation_errors[]`, and `state_changes[]`.

**Exit codes**:
- `0` — converged, nothing to do
- `1` — nothing to plan (no fleet yet, or unusable local state)
- `2` — changes pending (run `meridian apply`)
- `3` — required evidence (the control service, SSH, local state) was unavailable

Don't edit the topology in `cluster.yml` by hand. Change it with `meridian setup`, `deploy`, `client add`, `node add`, or `relay add`.

### meridian apply

Apply the reviewed topology in `cluster.yml` to your servers. Apply shows the plan hash and resource count, asks for confirmation, then applies the exact plan it showed, with checkpoints. An interrupted apply resumes where it stopped.

```
meridian apply [--yes] [--forget-unreachable] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--yes`, `-y` | | Skip confirmation |
| `--forget-unreachable` | | Forget what was deployed on a removed server that doesn't answer over SSH (blocked or deleted) instead of failing its cleanup; it keeps serving its old users until you delete it |
| `--json` | | Emit the final apply result as a `meridian.output/v1` envelope |

JSON `data` contains `plan_hash`, `generation`, and `actions[]` with `status: "converged" | "applied" | "failed" | "unknown" | "skipped"`. JSON mode never prompts: without `--yes` it returns `MERIDIAN_CONFIRMATION_REQUIRED` with the plan hash and exits `2`. A failed resource exits `3`; fix the cause and run apply again. If local state cannot be saved after remote changes, apply exits `3` and says so; repair `~/.config/meridian` permissions, then run `plan` and `apply` again.

Removals are applied too: whatever left the topology (users, protocol paths, exits, relays, routing gateways) is deleted after everything still in the plan has converged, and `plan` lists it first. The control server leaves only with `meridian teardown`, or moves with `meridian restore`.

Every apply is recorded in the control service as a revision (see `meridian fleet history`), and holds a lease there while it runs, so two machines never apply at once.

### meridian client

Manage the people who connect.

```
meridian client add NAME [NAME...] [--limit SIZE] [--reset PERIOD] [--expires DATE] [--json]
meridian client show NAME [--repair-page] [--json]
meridian client usage NAME [--days N] [--json]
meridian client list [--json]
meridian client set NAME [--limit SIZE] [--reset PERIOD] [--expires DATE] [--json]
meridian client reset-traffic NAME [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
meridian client reissue NAME [--yes] [--json]
meridian client remove NAME [--yes] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--days N` | 30 | UTC days for `client usage` or `fleet usage`, 1–730, capped at the saved retention |
| `--json` | | Emit the result as a `meridian.output/v1` envelope (all client commands) |
| `--repair-page` | | Republish the client's connection page (`client show`) |
| `--limit SIZE` | none | Traffic limit (`client add`, `client set`): `500MB`, `50GB`, `1.5TB`; `none` or `0` removes it. Units are binary (1 GB = 1024³ bytes), as the control service counts |
| `--reset PERIOD` | never | When used traffic starts again from zero: `day`, `week`, `month`, or `never`, by calendar day, week, or month |
| `--expires DATE` | never | When access ends: `YYYY-MM-DD` (the end of that day, UTC), an ISO time such as `2026-12-31T18:00:00+03:00` (a time without an offset is UTC), or `never`. Must be in the future |
| `--yes`, `-y` | | Confirm `client reissue` or `client remove`; required with `--json` |

**`client add`** — adds one or more people to the topology, applies it, and prints each person's connection page and subscription URL. The page is built from their real subscription, so relays and Hysteria2 appear when present. Names already in the topology or already present in the control service are refused. Names can be in any script (`client add Биба 王小明`): pages and output show them as typed, while the control service, which accepts only Latin letters, digits, `.`, `-`, and `_`, gets a transliterated username (`biba`, `wangxiaoming`). Other client commands accept either form. If a client's subscription link can't be built, the client is still added and the command exits `3`; a missing connection page alone is a warning (repair it with `client show --repair-page`). `--limit`, `--reset`, and `--expires` give every added name the same limits as `client set`, once the clients exist; if that fails, the clients are still added, and the command warns and exits `3`.

**`client show`** — prints the connection page, subscription URL, status, traffic against the limit with its reset period, expiry, and last-seen time. `--repair-page` republishes a missing page; if that fails, the links are still shown with a warning and exit `3`.

**`client list`** — lists clients with status, traffic, expiry, and last seen, including people added in the web console (`declared: false` in JSON). With `--json`, returns `data.summary` counts and `data.clients[]`.

**`client set`** — changes a client's traffic limit, reset period, or expiry; only the options you pass change, and at least one is required. A client their limit or expiry has cut off (status `limited` or `expired`) can connect again as soon as the new values allow it. Changes take effect at once, and applies never touch them. A bad value exits `2` before Meridian connects anywhere. With `--json`, returns the client as the control service now has it in `data.client`, including `traffic_limit_bytes`, `traffic_reset`, and `expires_at` (empty means never).

**`client reset-traffic`** — starts the client's used traffic from zero, as a new period would; a client their limit has cut off can connect again.

**`client enable` / `client disable`** — resume or suspend a client immediately. A disable lasts until you run `client enable`; applies (including redeploys and `client add`, `node add`, `relay add`) leave a client's status alone.

**`client reissue`** — replaces a client's links after a leak. The control service issues a new subscription ID and new keys, so the old subscription URL, connection page, and links stop working. Meridian deletes the old page first, then publishes the new one and prints the new links. Status, limits, and expiry stay as set. Asks for confirmation unless `--yes`, and refuses names that aren't your clients. If the new page can't be published, the links are still shown with a warning and exit `3` (repair it with `client show --repair-page`).

**`client remove`** — removes the client from the topology, deletes their account in the control service and their connection page. Asks for confirmation unless `--yes`. The last client cannot be removed. People added in the web console are removed there.

**`client usage`** — shows daily upload, download, and total bytes for any person in this control service, including console people. Daily history must be enabled in [Privacy](/en/privacy/); otherwise it exits `2` with the opt-in command. `client show` adds the last seven UTC days when history is on (`data.usage` in JSON, `null` when off). Empty days mean no recorded traffic; history is never backfilled.

### meridian privacy

Choose what the control service stores. Both device limits and daily history default to off; applying the fleet never changes these settings. See [Privacy](/en/privacy/) for the disclosures and seizure consequences.

```bash
meridian privacy [--json]
meridian privacy show [--json]
meridian privacy set [--devices off|private|full] [--device-limit N] [--history off|daily] [--history-days N] [--yes] [--json]
```

| Flag | Default | Description |
| --- | --- | --- |
| `--devices LEVEL` | unchanged | Device limits: `off`, `private` keyed fingerprints, or `full` device details |
| `--device-limit N` | unchanged (initially 3) | Default per-person cap when their own is unset, 1–100 |
| `--history LEVEL` | unchanged | `off` lifetime totals and last seen, or `daily` per-person and per-server UTC totals |
| `--history-days N` | unchanged (initially 90) | Retain 7–730 UTC days, including today |
| `--yes`, `-y` | | Skip confirmation; required for a JSON change |
| `--json` | | Emit a `meridian.output/v1` envelope |

Bare `privacy` is `privacy show`: settings, disclosures, and the last 50 changes. `set` changes only options you pass, shows what a raised level stores and what a lowered one removes, and asks for confirmation. Identical values are a no-op. JSON `data` includes the settings and changes; a set also reports `deleted` row counts and `rekeyed_devices`. The change records the token's name, `owner`, or `root`, the time, and before/after values. Private records are cleared after a restore because the fleet fingerprint key is never snapshotted.

### meridian server

Manage saved SSH servers (`~/.config/meridian/servers.json`).

```
meridian server add [USER@]IP[:PORT]|local [--name NAME] [--user USER] [--ssh-port PORT]
meridian server list
meridian server remove NAME_OR_IP [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name NAME` | (none) | Display name for the server |
| `--user/-u USER` | root | SSH user |
| `--ssh-port PORT` | 22 | SSH port |
| `--yes`, `-y` | | Skip the removal confirmation (`server remove`) |

`server add local` saves the machine you are on; later commands run on it directly instead of over SSH. Names and addresses must be unique; `server add` refuses collisions instead of retargeting an existing server. `server remove` deletes only the saved profile and refuses while the server still has a role in your topology.

### meridian node

Manage exit servers. The first server is deployed with `meridian deploy` (or `meridian setup`); add more exits with `meridian node add`.

```
meridian node add [USER@]IP[:PORT] [flags]
meridian node list [--json]
meridian node remove NAME_OR_IP [--yes] [--force] [--forget-unreachable]
meridian node replace EXIT [USER@]NEW_IP[:PORT] [--name NAME] [--yes] [--forget-unreachable]
meridian node sni EXIT NEW_SNI [--self-steal] [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name NAME` | (auto) | Friendly name for the node |
| `--user USER` | saved, else root | SSH user on the node |
| `--ssh-port PORT` | saved, else 22 | SSH port on the node (`node add`, `node replace`) |
| `--sni HOST` | (auto) | Reality camouflage target for this node (without it, prefers a site in the node's own subnet, else `www.google.com`) |
| `--domain DOMAIN` | (none) | Exit's domain: XHTTP moves onto it and WSS is added, both through Cloudflare's CDN; Hysteria2 uses its certificate |
| `--self-steal` | off | `node add`: camouflage with your own domain (`--sni`, else `--domain`) pointed at the node. `node sni`: `NEW_SNI` is your own domain |
| `--country CC` | address registry | Country shown in connection names; set it when the registry is wrong (anycast or leased ranges) |
| `--harden / --no-harden` | on | OS, SSH, and firewall hardening. Fleet servers always keep it; `--no-harden` is refused |
| `--yes` | | Skip confirmation prompts |
| `--forget-unreachable` | | `node remove`, `node replace`: Forget what was deployed on a removed server that doesn't answer over SSH (blocked or deleted) instead of failing its cleanup; it keeps serving its old users until you delete it |
| `--force` | | `node remove`: also remove the relay chains that route to this exit |
| `--json` | | Emit `node list` as a `meridian.output/v1` envelope |

**`node add`** — connects over SSH, adds the server as an exit in your topology, and applies the reviewed plan (hardening, the node agent and Xray, enrollment with the control service). Existing clients receive the new exit on their next subscription refresh.

**`node list`** — lists nodes with their agent's live status, version, and traffic. If the control service is unreachable, configured nodes are shown as `unknown` and the command exits `3`.

**`node remove`** — removes an exit or routing gateway from your topology. It lists every resource that will be deleted (the node's record and document, its agent and Xray, firewall rules, client endpoints), asks for confirmation, and applies exactly that review; the server stays in your saved servers. It refuses for the control server (use `meridian teardown`), while relays still route to the exit (remove them first or pass `--force`), while a routing gateway enters the fleet through it, and when the result would break the topology (for example an empty egress pool or the default egress). Routing rules scoped to a removed gateway are dropped with it.

**`node replace`** — moves one exit to another server. `EXIT` is the exit ID or its server's saved name or IP; the new server uses the same target syntax as `node add` (a saved name or `local` works too) and is saved like `server add`. The exit keeps its ID, Reality keys, protocol paths, SNI, WARP and geo settings, and every relay that points at it, so relay users keep working and direct users get the new address on their next subscription refresh. The review lists what will be deployed on the new server and every resource cleaned up on the old one (the agent and Xray, nginx files, firewall rules, and certificates); the saved server stays. It refuses for the control server (moving it would change every subscription URL; use `meridian restore` when it is lost) and for a new server that already has a role.

**`node sni`** — gives an exit's Reality path a new camouflage SNI. `EXIT` is the exit ID or its server's saved name or IP. It refuses `apple.com` and `icloud.com` names and `www.microsoft.com`, checks over SSH that the exit reaches the new site with TLS 1.3 and HTTP/2, shows the review (relays that follow the exit's SNI, relays that keep their own, routing gateways built on the path, and what gets cleaned up), confirms, and applies it. The old name is replaced, not kept alongside: apps switch at their next subscription refresh (every 6 hours) or a manual refresh, and until then they cannot connect over Reality. XHTTP, WSS, and Hysteria2 don't change. Use it when `meridian test --probe` reports the camouflage SNI blocked. With `--self-steal`, `NEW_SNI` is a domain you own: instead of the reachability check, it checks that the name resolves to the exit (stopping when it doesn't resolve, warning when it points elsewhere), and the exit gets a Let's Encrypt certificate for it and answers probes itself.

### meridian relay

Manage relays — Realm TCP forwarders that give users a domestic entry point to an exit abroad. Relays carry Reality traffic.

```
meridian relay add [USER@]RELAY_IP[:PORT] --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay remove RELAY [--exit EXIT] [--yes] [--forget-unreachable]
meridian relay sni RELAY NEW_SNI [--exit EXIT] [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--exit/-e EXIT` | (required for add) | Exit server IP or name |
| `--name NAME` | (auto) | Friendly name for the relay (e.g. "ru-moscow") |
| `--port/-p PORT` | 443 | Listen port on the relay server |
| `--sni HOST` | exit's target | Reality camouflage target for this relay; omit to use the exit's |
| `--user/-u USER` | saved, else root | SSH user on the relay |
| `--ssh-port PORT` | saved, else 22 | SSH port on the relay server (`relay add`) |
| `--yes/-y` | | Skip confirmation prompts |
| `--forget-unreachable` | | `relay remove`: Forget what was deployed on a removed server that doesn't answer over SSH (blocked or deleted) instead of failing its cleanup; it keeps serving its old users until you delete it |
| `--json` | | Emit `relay list` (`data.relays[]`) as a `meridian.output/v1` envelope |

**`relay add`** — adds a single-hop relay to your topology and applies it. Clients receive it on their next subscription refresh. For chains with more than one hop, use `meridian setup`.

**`relay remove`** — `RELAY` is a relay server's IP or saved name, or a relay chain ID. It drops every chain through that server (narrow it with `--exit`), lists what will be deleted (Realm units, firewall rules, the exit-side listener, client endpoints), confirms, and applies the reviewed removal. The servers stay in your saved servers.

**`relay sni`** — gives the relay chains through a server (or one chain by ID; narrow it with `--exit`) a new camouflage SNI. It refuses the same names as `node sni`, checks that the exit reaches the site (a Reality listener on the exit serves the relay's own name), reviews, confirms, and applies. Passing the exit's own SNI makes the relay follow the exit again. Apps switch at their next subscription refresh.

To check a relay's hops, run `meridian check RELAY`.

### meridian route

Send chosen destinations from an exit through Cloudflare WARP, directly, or nowhere, ahead of the exit's default egress.

```
meridian route add EXIT VALUE... --via warp|direct|block [--name ID] [--json]
meridian route add EXIT --preset ai [--via warp|direct|block] [--name ID] [--json]
meridian route list [EXIT] [--json]
meridian route remove EXIT ROUTE [--yes] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--via ACTION` | `warp` with `--preset`, else required for add | `warp`, `direct`, or `block` |
| `--preset NAME` | | Add a named destination set instead of `VALUE`s: `ai` (AI services) |
| `--name ID` | the preset, or the action, numbered | Route ID shown by `route list` and used by `route remove` |
| `--yes/-y` | | Skip the removal confirmation (required with `--json`) |
| `--json` | | Emit a `meridian.output/v1` envelope (`data.override` for add/remove, `data.exits[]` for list) |

`EXIT` is an exit ID, saved server name, or IP. Each `VALUE` is a domain (subdomains included), `geosite:NAME`, or `geoip:NAME`; one route holds one kind.

**`route add`** — adds the route to the exit in your topology and applies it. Routes match in the order they were added. A `warp` route on an exit without WARP sets WARP up for that route only; other traffic stays direct.

**`route list`** — shows each exit's default egress and its routes in match order.

**`route remove`** — removes one route and applies the change; its destinations follow the exit's default egress again. WARP stays installed on the server but carries nothing until a route or `--warp` uses it again.

### meridian fleet

Fleet-wide health and inventory from the live control service, its history of applies, upgrades, and the fleet as an editable file.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet history [--json]
meridian fleet usage [--days N] [--json]
meridian fleet upgrade [--tested] [--yes]
meridian fleet export [--json]
meridian fleet import FILE [--dry-run] [--yes] [--forget-unreachable] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--days N` | 30 | UTC days for `client usage` or `fleet usage`, 1–730, capped at the saved retention |
| `--json` | | Emit the result as a `meridian.output/v1` envelope (`status`, `inventory`, `history`, `export`, `import`) |
| `--tested` | | `fleet upgrade`: skip GitHub; Realm stays on the tested version or what you already upgraded to |
| `--yes`, `-y` | | `fleet upgrade`, `fleet import`: skip the confirmation |
| `--dry-run` | | `fleet import`: show the changes and the review, apply nothing |
| `--forget-unreachable` | | `fleet import`: forget what was deployed on a removed server whose SSH port does not answer |

**`fleet status`** — control service health, each agent's last report and whether it applied the current configuration, relay listeners, and client counts. Missing declared clients degrade health; disabled, limited, or expired clients are counted but are your choice, not a fault. A reachable relay listener only proves TCP, so unverified relay routes keep health `unknown`; use `meridian test` to check the full path. Exit `0` means healthy, `4` degraded, and `3` unknown.

**`fleet inventory`** — your configured servers, roles, nodes, and relays, with live status when the control service is reachable. It never prints API tokens or secret paths. If live status is unavailable, the configured inventory is still shown, with warnings, and the command exits `3`. Use `plan --json` to decide whether apply has work to do.

**`fleet history`** — every reviewed apply the control service recorded, newest first: its number, time, author (the machine's token name, `root` for the CLI's own token, `owner` for the console), status (`proposed`, `approved`, `applied`, or `failed`), and the start of its plan hash. `--json` returns `data.revisions[]`.

**`fleet upgrade`** — brings the fleet up to date in one reviewed apply. The control service and every node agent move to the build this Meridian ships, and each agent upgrades in place, keeping its node's identity; run `meridian update` first to get newer ones. Relays move to the newest Realm release without waiting for a Meridian update: GitHub's newest release in the tested major, recorded in `cluster.yml` with the sha256 GitHub publishes for each archive, which every relay checks before installing. It shows what each server runs as `current` or `upgrade`, then, after you confirm, applies the reviewed plan and runs `meridian check` on every server. A version that cannot be read stops the command with exit `3` before anything changes.

**`fleet export`** — prints your fleet's reviewed topology as a YAML file you can edit: exits and their protocol paths, relays, gateways, egress pools, routing rules, routes, clients, and fleet settings. Servers appear by their saved name (or address when unnamed), never by internal ID, and the file starts with `schema: meridian.fleet/v1`. Reality keys, API tokens, client links, and the XHTTP and WSS transport paths stay in Meridian and are not listed. `--json` returns the same document under `data.document`.

**`fleet import`** — reads an edited file (or `-` for standard input) and shows what it changes, one line each: `+` adds, `-` removes, and `~` changes a field (`~ exit-2 sni www.example.com -> www.example.org`). It then lists every resource the change deletes and the review hash, asks, and applies exactly that review, like `apply`. A file with no edits prints "No changes". Removing a client from `access.users` removes them like `client remove`: their links stop working and their connection page is deleted. Adding one creates them like `client add` and publishes their page; `client show NAME` prints their links. New camouflage SNIs get the checks `node sni` runs. Import refuses another `schema`, a server you haven't saved (save it with `meridian server add`), transport paths in the file, changes to the control server, its domain, or its backend, removing or renaming the exit on the control server, and moving an exit to another server (use `node replace`). Keys and credentials stay as they are. Without a terminal, pass `--yes`; `--dry-run` shows the changes and the review without applying anything.

**`fleet usage`** — shows per-server byte totals for each UTC day, including retained use of removed servers. `--days N` chooses 1–730 days within retention. Daily history must be on; JSON returns fleet totals and `nodes[].daily` in `data`.

### meridian context

Keep several separate fleets on one machine, for example one for family and one for work, and choose which one commands work on.

```
meridian context list [--json]
meridian context use NAME
meridian context rename OLD NEW
meridian context remove NAME [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | `context list`: emit a `meridian.output/v1` envelope |
| `--yes`, `-y` | | `context remove`: remove a context whose fleet still has servers without asking |

**`context list`** — every context with its control server and how many exits, relays, and routing gateways it has, read from this machine only; `*` marks the current one:

```
  * default   198.51.100.10    2 exits, 1 relay
    family    198.51.100.40    1 exit
```

**`context use`** — makes NAME the fleet every later command works on. `meridian deploy IP --context NAME` creates a new context, and any command takes `--context NAME` to work on another fleet once.

**`context rename`** — renames a context; its fleet and servers are untouched.

**`context remove`** — deletes the context's files from this machine and never contacts a server: the servers keep running, but this machine can no longer manage them. It refuses the current context, and while the fleet still has servers it asks first (`--yes` skips the question). The `default` context is Meridian's home directory itself, so it cannot be renamed, and it can be removed only once another context has taken its fleet over. Such a copy is marked superseded in `context list` and refuses every change; removing `default` then deletes only the fleet's files and keeps the SSH key and preferences.

### meridian check

Check a server's health, or whether a new server can run Meridian. What runs depends on the server's role in your fleet:

- **Not in the fleet yet** — pre-flight checks: camouflage target reachability and DNS, port 443 availability and outside reachability, domain DNS, OS, disk, and clock. Nothing is installed; a short-lived listener tests the firewall when port 443 is free. DNS uses the server's own resolver.
- **Control, exit, or routing gateway** — the control service (it answers, belongs to this fleet, and runs a compatible version), each node's agent (online, applied the current configuration, current build), SSH (or local access), every compiled public and internal port, TLS certificates, and disk space. Run from another machine, the control server also gets a data-flow check: a ~50 KB download that warns when it freezes after about 16–24 KB (throttling, measured from where you are).
- **Relay** — every Realm hop on the server: the service is active and can reach its next hop, and the chain's public entry accepts TCP from your machine.

With no server, every server in the fleet is checked.

```
meridian check [SERVER] [--domain NAME] [--sni HOST] [--user USER]
               [--ssh-port PORT] [--ai] [--json]
```

`--domain` and `--sni` apply to servers not deployed yet: they check that the domain resolves to the server and that a specific camouflage target is reachable. `--user` and `--ssh-port` override the saved SSH user and port. `--ai` copies an AI-ready diagnostic prompt to the clipboard. `--json` emits one `meridian.output/v1` envelope with a report per server and scope.

Missing evidence is never treated as success. Exit `0` means every check passed, `4` means problems were found, `3` means required evidence was unavailable, and `2` means invalid input. Each failure prints its fix. Use `meridian test` to push real traffic through the whole route.

### meridian scan

Find camouflage targets on the server's own network using a pinned RealiTLScanner release. Meridian verifies its SHA-256 checksum before running it and removes the remote workspace afterwards.

```
meridian scan [SERVER] [--user USER] [--ssh-port PORT]
```

RealiTLScanner scans IPv4 ranges; an IPv6-only server exits `3`. Picking a result prints the command to use it — it doesn't change a deployed topology. Skipping exits `0`, an invalid choice exits `2`, no suitable targets exits `4`, and scanner or download failures exit `3`.

### meridian test

Test delivered proxy traffic from your device. No SSH is needed.

A full test picks an active client, fetches the exact Xray JSON that the control service delivers to them, and pushes real traffic through the automatic fallback and through every path of the target server. The first run may spend up to 120 seconds downloading and verifying the pinned Xray runtime. Traffic checks rotate independent IP observers and compare them with a direct request, so an observer outage is inconclusive rather than a false failure. `MERIDIAN_CONNECT_TEST_URL` overrides the observer for controlled environments.

Each path reports its time to first byte. When a path fails, Meridian opens a plain TCP connection to its endpoint to say whether it **connects, but no traffic passes** (the server answers but the proxy session carries nothing: wrong credentials or a session being cut) or is **unreachable** (nothing answers on that port). With `--json`, `data.transports` lists every path's outcome (`ok`, `no_traffic`, `unreachable`, `failed`, `egress_mismatch`, `inconclusive`, `invalid`) and `first_byte_ms`.

A control service outage, HTTP `429`, or HTTP `5xx` makes the subscription unavailable, so the run exits `3`. Any other subscription rejection such as `400` or `422` is a completed failure and exits `4`.

```
meridian test [SERVER|DOMAIN] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--probe] [--timeout SECONDS] [--json]
```

Without a target, `test` tests the saved server it runs on, or your only saved server. With several, it tests every public entry of your fleet in turn (each exit, then each relay's entry) and exits with the worst result; `--json` then returns one result per server in `data.results`.

`--client` tests a specific active client. `--basic` runs only network and TLS checks, without downloading Xray, and cannot verify UDP-only paths; it cannot be combined with `--client` (exit `2`). `--timeout` (default `5`, range `1`–`30`) limits each network operation, separate from the first-run Xray download.

Exit `0` means every check passed, `4` means the test found problems, and `3` means required evidence was unavailable. `--json` emits a `meridian.output/v1` envelope with every check and finding.

#### Censor's view: `meridian test --probe`

`--probe` looks at a server the way an active prober would, to check whether the deployment gives itself away, instead of sending traffic. It works on any IP or domain and applies stricter checks when the target belongs to your saved topology.

Checks follow the real deployment: public and internal port exposure, HTTP/TLS behavior, SNI consistency and camouflage, common proxy and management-panel paths, WebSocket upgrades, reverse DNS, HTTP/2, legacy TLS versions, and configured domains. `--sni` overrides the SNI for generic TLS probes. UDP-only listeners are reported as needing a full `meridian test` instead of being guessed.

`--probe` also answers "what is blocked?". It opens TLS to the server with its camouflage SNI; if that fails, it retries without the SNI and opens the real camouflage site directly, then reports whether the **server IP** or the **camouflage SNI** looks blocked. If the real site can't be reached either, the result is inconclusive rather than a finding. With a self-steal camouflage the real site is the server itself, so it is no independent comparison: when nothing gets through, the result is inconclusive. On the control server it also downloads the connection page's ~50 KB script and watches for a transfer that freezes after about 16–24 KB, which is how throttled foreign IPs behave. When a WSS path goes through a CDN, the same download runs through the CDN. These results only mean something when you run them from inside the censored network. `--probe` cannot be combined with `--basic`, `--client`, or `--domain`. Exit codes are the same as `test`; skipped checks are never reported as passes.

`meridian probe [SERVER|DOMAIN] [--sni NAME] [--timeout SECONDS] [--json]` is the same as `meridian test IP --probe`.

### meridian doctor

Collect system diagnostics for bug reports.

```
meridian doctor [SERVER] [--sni HOST] [--user USER] [--ssh-port PORT] [--ai]
```

`--sni` picks the camouflage target to diagnose, `--user` and `--ssh-port` override the saved SSH user and port, and `--ai` copies a redacted, AI-ready report to the clipboard instead of printing the issue template.

Doctor follows the server's roles: a relay-only server isn't diagnosed as an exit, and routing gateways are labeled separately. Missing tools or a lost SSH connection make the report inconclusive (exit `3`); sections already collected are still shown.

### meridian logs

Show a server's recent logs, redacted, without logging in to it.

```
meridian logs [SERVER] [--lines N] [--grep TEXT] [--component NAME]
              [--user USER] [--ssh-port PORT]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--lines`, `-n N` | `100` | Lines to show from each log (`1`–`10000`) |
| `--grep`, `-g TEXT` | | Only lines containing this text; case-insensitive, matched literally rather than as a regex |
| `--component`, `-c NAME` | all | Only `node`, `panel`, `nginx`, or `relay`; repeat to pick several |

`logs` reads what the server's roles run: exits and routing gateways show the agent (`netagent`) and Xray (`netcore`) under `node`, the control server shows the control service (`ctld`) under `panel`, both show the nginx error log, and relays show their Realm units' journal. `--user` and `--ssh-port` override the saved SSH user and port.

Every line passes through the same redaction as `meridian doctor`: tokens, passwords, keys, UUIDs, subscription IDs, and the control service's and connection page's secret paths are replaced with `[REDACTED]`, so the output is safe to paste into a bug report. Lines print as plain text with terminal control characters removed.

A server without a fleet role, or a `--component` it doesn't run, exits `2` before connecting. A log that can't be read (a missing service, a lost SSH connection) is shown as unavailable and the command exits `3`.

### meridian panel

Show the web console's address, and a one-time setup link while it has no owner.

```
meridian panel [--setup-link]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--setup-link` | | Print a reset link even though the console has an owner: it sets a new password, turns the sign-in code off, and signs out every device |

The setup link works once, for 15 minutes; open it to choose the owner's password. Once the console has an owner, `panel` prints the address and whether a sign-in code is on. Treat both the address and a setup link like passwords. In the console you manage people and API tokens; servers, revisions, and the subscription title are read-only there. See [Web console](/en/client-management/#web-console).

### meridian teardown

Remove Meridian from a server.

```
meridian teardown [SERVER] [--user USER] [--ssh-port PORT] [--yes]
```

On a single-server deployment, teardown removes everything Meridian created: the control service and its database, the agent and Xray, nginx configuration, certificates, relays, and firewall rules on the server, then the local `cluster.yml` and saved server profile. System packages, SSH hardening, and fail2ban stay, and nginx keeps running if it still serves your own sites. It also removes 3x-ui from Meridian 3.x. In a multi-server fleet, teardown refuses a server that still has a role in the topology.

`--yes` skips the confirmation; the role checks still run first. Declining exits `1`. An unknown server name, or zero or several possible targets, exits `2` instead of prompting.

### meridian backup

Encrypted snapshots of the control service's state, the machines that receive them, and the recovery key.

```
meridian backup pull [--name NAME]
meridian backup recovery-key [--yes]
meridian backup devices
meridian backup revoke ID
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name NAME` | this computer's host name | `backup pull`: name this machine's key gets when it is first registered |
| `--yes`, `-y` | | `backup recovery-key`: replace an existing recovery key without asking |

After every command that changes the fleet, Meridian saves the control service's newest snapshot in `snapshots/` beside `cluster.yml` (the newest few are kept). A snapshot holds users, limits, traffic, revisions, API tokens, and a copy of this machine's `cluster.yml` and `servers.json`, so it can rebuild the fleet on a machine with nothing else. It is encrypted to every registered machine's key and to the recovery key, and never carries signing keys, sessions, or the console password. A failed save warns and never fails the command that triggered it.

**`backup pull`** — saves a snapshot now. The first pull on a machine creates its key (`device.key`, readable only by you) and registers it with the control service.

**`backup recovery-key`** — makes the fleet's recovery key, or replaces the existing one after asking, and prints it once. Meridian keeps only its public half, so write it down or print it and keep it offline. The first deploy of a fleet already shows one. Not available with `--json`.

**`backup devices`** — lists the machines whose keys snapshots are sealed to, with their IDs and when each was registered, and whether the fleet has a recovery key.

**`backup revoke`** — stops sealing new snapshots to one machine's key, for example a lost laptop. Snapshots it already holds stay readable with its key; revoke it before it can pull a new one.

### meridian restore

Rebuild a lost or seized control server on another server from a snapshot.

```
meridian restore --to SERVER [--snapshot FILE] [--recovery-key] [--force]
                 [--name NAME] [--user USER] [--ssh-port PORT] [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--to SERVER` | (required) | The new control server: an IP, `user@IP:port`, or a saved name |
| `--snapshot FILE` | newest saved | Snapshot to restore |
| `--recovery-key` | | Open the snapshot with the recovery key instead of this machine's key; it is asked for (hidden) or read from one line of standard input |
| `--force` | | Restore even though the old control service still answers |
| `--name NAME` | (none) | Name for the new server |
| `--user/-u USER` | root | SSH user on the new server |
| `--ssh-port PORT` | 22 | SSH port on the new server |
| `--yes`, `-y` | | Skip the confirmation |

Restore opens the snapshot, reviews what it will do, and asks. It then installs the control service on the new server from the snapshot, moves the control server (and the old server's exit, if it had one) to the new address in your topology, and runs the normal reviewed apply. The restored service gets a new fleet authority, new signing keys, and a new API token, so every agent enrolls again and a seized server can't sign anything your nodes accept. Users keep their accounts, limits, and traffic. The console owner sets a new password with a link from `meridian panel`.

On a bare IP every subscription URL contains the control server's address, so each link changes: the review says so and the end prints every client's new link. With a domain, links stay the same once its DNS record points at the new server; the review names the records.

Restore refuses while the old control server still answers, because two control services would split the fleet; `--force` overrides it, for example when the old server was seized. It warns when `--snapshot` names an older file than the newest saved one: an older snapshot brings back tokens, machines, and people you have since removed. A restore that stops can be run again and continues where it stopped.

On a machine without the fleet, pass the snapshot file and `--recovery-key`: the snapshot's own copy of the fleet's files is restored first. If this machine's copy is older than the snapshot's, restore puts the fleet in a new context and, after the review names it, takes the fleet over from the old one, which is then marked superseded and refuses changes until you `meridian context remove` it.

### meridian migrate

Move a fleet that runs the Remnawave panel to Meridian's control service, in place, keeping every link.

```
meridian migrate [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--yes`, `-y` | | Skip the confirmation |

Migrate first reviews the move: the clients it carries over, anything about a client it can't keep, and the panel data nobody keeps (the panel's admin account and API tokens, per-day usage history, HWID device registrations, panel plugins and response rules Meridian didn't set, client IP forwarding (it served the panel's per-user IP limits), and the Remnawave subscription page; Meridian's own connection pages stay). After you confirm, it:

1. Backs up the panel's database, to `backups/` on your machine and to `/var/backups/meridian-migration/` on the control server, with your fleet files
2. Installs the control service beside the panel and imports every client with the same keys, subscription ID, status, limits, expiry, and traffic
3. Stops the panel (its data stays) and removes each node's container
4. Applies the fleet: every exit gets the agent, enrolled with the control service
5. Checks that every client's subscription link is exactly what it was
6. Removes Remnawave

There is no rollback; the backup is the way back. Each step is saved, so a migration that stops resumes when you run it again, and a finished one does nothing. If the links check fails, Remnawave is left in place, stopped, and the command stops with an error. Without a terminal, pass `--yes`; otherwise migrate stops after the review. `meridian deploy` offers to migrate when it finds a Remnawave fleet.

### meridian update

Update the CLI to the latest release of its major version on PyPI. A newer major version is only announced; `--major` moves to it after you have read the release notes. A beta build follows newer betas of its major version and then the stable release. Meridian tries uv, pipx, and pip, and only reports success when the active `meridian` command shows the new version. Exit `0` means already current or updated; `3` means the update or its verification failed. Updating the CLI does not change your servers.

```
meridian update [--major]
```

### meridian studio

Open Meridian Studio, an experimental browser UI, with its local Engine API. The server binds only to `127.0.0.1`.

```
meridian studio [--port PORT] [--assets-dir DIRECTORY] [--no-open]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port PORT` | 7391 | Localhost port from 0 to 65535; `0` picks any free port |
| `--assets-dir DIRECTORY` | bundled assets | Use a built Studio asset directory instead of the packaged copy |
| `--no-open` | | Print the local URL without opening a browser |

Studio prints and opens an address like `http://127.0.0.1:7391/studio/#k=…`. The part after `#k=` is a key that changes on every launch; only a browser tab opened from that address can use the Engine, so other programs and other users on the same computer cannot. Keep it private. After restarting Studio, open the new address it prints.

Without `--port`, Studio uses port 7391, or the next free port up to 7400, so the draft your browser saves for Studio is still there next time. An explicit `--port` that is taken fails clearly, as do missing assets. An explicit asset directory must contain `studio/index.html`.

### meridian serve

Run the control service. Meridian installs it on the control server as the `ctld` systemd unit, which runs this command; you don't run it yourself.

```
meridian serve [--config FILE]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--config FILE` | `/etc/ctld/config.json` | Service configuration, written by Meridian |

The service listens on `127.0.0.1:3000` behind nginx and keeps its state in one SQLite file under `/var/lib/ctld/`. It logs no requests, so subscription IDs and secret paths never reach the journal. `meridian logs SERVER -c panel` shows its log.

### meridian api

Inspect the machine-readable contracts behind JSON output, for scripts and UI clients.

```
meridian api schemas [--json] [--include-schemas]
meridian api commands [--json] [--include-schemas]
meridian api schema NAME [--envelope|--json]
meridian api workflow NAME [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit the catalog as a `meridian.output/v1` envelope |
| `--include-schemas` | | Include full JSON Schemas (implies JSON output) for `api schemas` or `api commands` |
| `--envelope`, `--json` | | Wrap `api schema NAME` in a `meridian.output/v1` envelope instead of printing the raw schema |

**`api schemas`** — lists schema names such as `output-envelope`, `deploy-request`, `deploy-result`, `deploy-plan`, `plan-envelope`, `apply-envelope`, `client-list-envelope`, `fleet-status-envelope`, and `event`.

**`api commands`** — lists command contracts: argv, envelope and data schemas, possible statuses and outcomes, exit-code meanings, every option the command accepts (`machine_flags`), and `interrupt_behavior`. Arguments, options, and descriptions are read from the CLI itself. Ctrl-C exits `130` without a JSON envelope (`interrupt_behavior: "exit_130_without_envelope"`).

**`api schema NAME`** — prints one JSON Schema, e.g. `meridian api schema output-envelope`.

**`api workflow NAME`** — prints a UI-renderable workflow, e.g. `meridian api workflow deploy --json`.

### meridian help

Explain Meridian to an AI agent, or show one command's help.

```
meridian help [agents | COMMAND...]
```

**`meridian help agents`** prints a Markdown guide for AI agents: what Meridian does, how the fleet and its reviewed topology fit together, where state lives, the rules for driving the CLI safely (non-interactive flags, JSON envelopes, plan before apply, confirmation before removal, links as secrets), exit codes, and common tasks. It always prints plain Markdown, so an agent can read it from a pipe.

**`meridian help COMMAND`** shows the same help as `meridian COMMAND --help`, e.g. `meridian help node add`. Without arguments it prints the top-level help. An unknown topic exits `2`.

## Global options

Global options go before the command, for example `meridian --quiet doctor`. Command options such as `test --json` go after it.

| Flag | Description |
|------|-------------|
| `--version`, `-v` | Show the CLI version and exit |
| `--verbose` | Enable debug logging |
| `--quiet`, `-q` | Suppress progress output |
| `--context NAME` | Work on another fleet ([meridian context](#meridian-context)); default: `MERIDIAN_CONTEXT`, else the current context. Also accepted after the command, as in `meridian deploy 203.0.113.5 --context work` |
| `--lang CODE` | Language for the setup wizard: `en`, `ru`, `fa`, or `zh` (default: saved choice, else asked) |
| `--json` | Request a JSON envelope from a command that supports it; other commands fail explicitly |
| `--install-completion` | Install shell completion for the current shell |
| `--show-completion` | Print the completion script for the current shell |

JSON output uses the `meridian.output/v1` envelope (`schema`, `command`, `operation_id`, `status`, `summary`, `data`, `warnings`, `errors`). It is supported by every command that lists `--json`: deploy, plan, apply, check, test, probe, all client and route commands, fleet status/inventory/history/export/import, node/relay list, and the api commands. The global `--json` and a command's own `--json` are interchangeable. Secrets are redacted before JSON leaves the process. Run `meridian api commands --json` to discover the contracts.

## Common server options

These recur across commands; each command accepts only the options listed in its own section.

| Flag | Used by |
|------|---------|
| `SERVER` argument | deploy, check, scan, test, doctor, logs, teardown, node add, relay add, server add |
| `--user`, `-u USER` | deploy, check, node add, relay add, server add, scan, doctor, logs, teardown, restore |
| `--ssh-port PORT` | deploy, check, node add, relay add, server add, scan, doctor, logs, teardown, restore |
| `--sni HOST` | deploy, check, node add, relay add, test, doctor |
| `--domain DOMAIN` | deploy, check, node add, test |
| `--self-steal` | deploy, node add, node sni |

Client commands use the control service's API directly and don't take a server. `test` runs from your device and doesn't use SSH.

## Server resolution

Name the server first, the way `ssh` does: an IP, a saved name, `user@host:port`, or `local` to run on the current machine without SSH. `--user` and `--ssh-port` fill in what the target leaves out and must not contradict it. An empty user or port means the saved value, else `root` and `22`. With no server, Meridian uses local mode (when running on a deployed server) or the only saved server. Zero or several candidates exit `2` rather than prompting.
