---
title: CLI Reference
description: Complete reference for all Meridian CLI commands and flags.
order: 10
section: reference
---

## Commands

### meridian deploy

Deploy one server and get a shareable connection link. The server becomes both the control plane (Remnawave panel) and an exit. Deploy builds the same reviewed topology as `meridian setup`, applies it with checkpoints, verifies real traffic through the delivered subscription, and prints a connection page and subscription URL. Re-running the same command resumes an interrupted deploy or redeploys, keeping paths, keys, and users.

```
meridian deploy [SERVER] [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sni HOST` | (auto) | Camouflage target. Without it, the server tries `www.google.com`, `dl.google.com`, `www.cloudflare.com` and uses the first that carries traffic; a redeploy keeps the saved target. An explicit `--sni` is never swapped |
| `--domain DOMAIN` | (none) | Your domain for XHTTP and WSS (Cloudflare CDN fallback). The panel and connection pages stay on the IP certificate |
| `--hysteria2` | off | Also offer Hysteria2 on UDP/443. Needs `--domain`; QUIC is throttled in some regions |
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
| `--yes` | | Skip confirmation prompts |
| `--json` | | Emit the final deploy result as a `meridian.output/v1` envelope |
| `--events=jsonl` | | Stream typed progress events as JSONL on stderr |
| `--request FILE` | | Read a `deploy-request` JSON payload from a file; use `-` for stdin |
| `--dry-run` | | Compile and preview the deploy without SSH or local changes |

Without an IP, deploy asks for one. `local` deploys on the machine you are running on. Deploy only redeploys a server that is already an exit in your fleet; add other servers with `meridian node add`. Changes are additive: removing something already deployed (such as dropping `--domain`) is refused.

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

The first question is the language (English, Русский, فارسی, 中文), preselected from your terminal and remembered in `~/.meridian/preferences.json`; `--lang` or `MERIDIAN_LANG` skips it. Then the wizard walks through servers (an IP, `user@IP:port`, or `local` for this machine), what each server does, connection methods, routing (only asked when you have a routing gateway), users, a plain-language summary, and apply. At any prompt you can type `back`, `details`, `save`, or `quit` (or the same words in your language). Running `meridian` with no command in a terminal, before any VPN exists, offers to start the wizard. Progress is saved in `~/.meridian/setup.json` after each stage, so running `meridian setup` again resumes; passwords and private keys are never stored there. `--intent` does not replace an existing draft unless `--restart` is also given. When apply succeeds, setup verifies every node and subscription and prints each user's connection page.

### meridian plan

Show what `meridian apply` would change, comparing the topology in `~/.meridian/cluster.yml` with the live servers and panel. Nothing is changed.

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
- `3` — required evidence (panel, SSH, local state) was unavailable

Don't edit the topology in `cluster.yml` by hand. Change it with `meridian setup`, `deploy`, `client add`, `node add`, or `relay add`.

### meridian apply

Apply the reviewed topology in `cluster.yml` to your servers. Apply shows the plan hash and resource count, asks for confirmation, then applies the exact plan it showed, with checkpoints. An interrupted apply resumes where it stopped.

```
meridian apply [--yes] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--yes`, `-y` | | Skip confirmation |
| `--json` | | Emit the final apply result as a `meridian.output/v1` envelope |

JSON `data` contains `plan_hash`, `generation`, and `actions[]` with `status: "converged" | "applied" | "failed" | "unknown" | "skipped"`. JSON mode never prompts: without `--yes` it returns `MERIDIAN_CONFIRMATION_REQUIRED` with the plan hash and exits `2`. A failed resource exits `3`; fix the cause and run apply again. If local state cannot be saved after remote changes, apply exits `3` and says so; repair `~/.meridian` permissions, then run `plan` and `apply` again.

Removing users or protocol paths is applied: the removed resources are deleted after everything still in the plan has converged, and `plan` lists them first. Removing an exit, relay, or routing gateway is refused for now.

### meridian client

Manage the people who connect.

```
meridian client add NAME [NAME...] [--json]
meridian client show NAME [--repair-page] [--json]
meridian client list [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
meridian client remove NAME [--yes] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit the result as a `meridian.output/v1` envelope (all client commands) |
| `--repair-page` | | Republish the client's connection page (`client show`) |
| `--yes`, `-y` | | Confirm `client remove`; required with `--json` |

**`client add`** — adds one or more people to the topology, applies it, and prints each person's connection page and subscription URL. The page is built from their real subscription, so relays and Hysteria2 appear when present. Names already in the topology or already present in the panel are refused. If a client's subscription link can't be built, the client is still added and the command exits `3`; a missing connection page alone is a warning (repair it with `client show --repair-page`).

**`client show`** — prints the connection page, subscription URL, status, traffic, and last-seen time. `--repair-page` republishes a missing page; if that fails, the links are still shown with a warning and exit `3`.

**`client list`** — lists clients with status, traffic, creation time, and last seen. With `--json`, returns `data.summary` counts and `data.clients[]`.

**`client enable` / `client disable`** — resume or suspend a client immediately. A disable lasts until the next apply (including redeploys and `client add`, `node add`, `relay add`), which turns every declared client back on.

**`client remove`** — removes the client from the topology, deletes the panel user and their connection page. Asks for confirmation unless `--yes`. The last client cannot be removed. Don't delete users in the panel directly; Meridian recreates any client still in the topology on the next apply.

### meridian server

Manage saved SSH servers (`~/.meridian/servers.json`).

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
meridian node remove NAME_OR_IP [--yes] [--force]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name NAME` | (auto) | Friendly name for the node |
| `--user USER` | saved, else root | SSH user on the node |
| `--ssh-port PORT` | saved, else 22 | SSH port on the node (`node add`) |
| `--sni HOST` | www.google.com | Reality camouflage target for this node |
| `--domain DOMAIN` | (none) | Node domain; adds XHTTP, WSS, and Hysteria2 paths on it |
| `--harden / --no-harden` | on | OS, SSH, and firewall hardening. Fleet servers always keep it; `--no-harden` is refused |
| `--yes` | | Skip confirmation prompts |
| `--force` | | `node remove`: also remove the relay chains that route to this exit |
| `--json` | | Emit `node list` as a `meridian.output/v1` envelope |

**`node add`** — connects over SSH, adds the server as an exit in your topology, and applies the reviewed plan (hardening, Docker, Xray, panel registration). Existing clients receive the new exit on their next subscription refresh.

**`node list`** — lists nodes with live panel status, Xray version, and traffic. If panel status is unavailable, configured nodes are shown as `unknown` and the command exits `3`.

**`node remove`** — removes an exit or routing gateway from your topology. It lists every resource that will be deleted (panel node and profile, node container, firewall rules, published Hosts), asks for confirmation, and applies exactly that review; the server stays in your saved servers. It refuses for the control server (use `meridian teardown`), while relays still route to the exit (remove them first or pass `--force`), while a routing gateway enters the fleet through it, and when the result would break the topology (for example an empty egress pool or the default egress). Routing rules scoped to a removed gateway are dropped with it.

### meridian relay

Manage relays — Realm TCP forwarders that give users a domestic entry point to an exit abroad. Relays carry Reality traffic.

```
meridian relay add [USER@]RELAY_IP[:PORT] --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay remove RELAY [--exit EXIT] [--yes]
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
| `--json` | | Emit `relay list` (`data.relays[]`) as a `meridian.output/v1` envelope |

**`relay add`** — adds a single-hop relay to your topology and applies it. Clients receive it on their next subscription refresh. For chains with more than one hop, use `meridian setup`.

**`relay remove`** — `RELAY` is a relay server's IP or saved name, or a relay chain ID. It drops every chain through that server (narrow it with `--exit`), lists what will be deleted (Realm units, firewall rules, the exit-side listener, published Hosts), confirms, and applies the reviewed removal. The servers stay in your saved servers.

To check a relay's hops, run `meridian check RELAY`.

### meridian fleet

Fleet-wide health and inventory from the live panel.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit the result as a `meridian.output/v1` envelope |

**`fleet status`** — panel health, node heartbeats, relay listeners, and client counts. Missing declared clients degrade health; disabled, limited, or expired clients are counted but are your choice, not a fault. A reachable relay listener only proves TCP, so unverified relay routes keep health `unknown`; use `meridian test` to check the full path. Exit `0` means healthy, `4` degraded, and `3` unknown.

**`fleet inventory`** — your configured servers, roles, nodes, and relays, with live panel status when reachable. It never prints the panel API token or secret paths. If live status is unavailable, the configured inventory is still shown, with warnings, and the command exits `3`. Use `plan --json` to decide whether apply has work to do.

### meridian check

Check a server's health, or whether a new server can run Meridian. What runs depends on the server's role in your fleet:

- **Not in the fleet yet** — pre-flight checks: camouflage target reachability and DNS, port 443 availability and outside reachability, domain DNS, OS, disk, and clock. Nothing is installed; a short-lived listener tests the firewall when port 443 is free. DNS uses the server's own resolver.
- **Control, exit, or routing gateway** — panel connection, SSH (or local access), containers (including the subscription page's health), every compiled public and internal port, TLS certificates, and disk space. Run from another machine, the control server also gets a data-flow check: a ~50 KB download that warns when it freezes after about 16–24 KB (throttling, measured from where you are).
- **Relay** — every Realm hop on the server: the service is active and can reach its next hop, and the chain's public entry accepts TCP from your machine.

With no server, every server in the fleet is checked.

```
meridian check [SERVER] [--domain NAME] [--sni HOST] [--user USER]
               [--ssh-port PORT] [--ai] [--json]
```

`--domain` and `--sni` apply to servers not deployed yet: they check that the domain resolves to the server and that a specific camouflage target is reachable. `--user` and `--ssh-port` override the saved SSH user and port. `--ai` copies an AI-ready diagnostic prompt to the clipboard. `--json` emits one `meridian.output/v1` envelope with a report per server and scope.

Missing evidence is never treated as success. Exit `0` means every check passed, `4` means problems were found, `3` means required evidence was unavailable, and `2` means invalid input. Each failure prints its fix. Use `meridian test` to push real traffic through the whole route.

The older `preflight`, `node check`, and `relay check` commands still work for one release.

### meridian scan

Find camouflage targets on the server's own network using a pinned RealiTLScanner release. Meridian verifies its SHA-256 checksum before running it and removes the remote workspace afterwards.

```
meridian scan [SERVER] [--user USER] [--ssh-port PORT]
```

RealiTLScanner scans IPv4 ranges; an IPv6-only server exits `3`. Picking a result prints the command to use it — it doesn't change a deployed topology. Skipping exits `0`, an invalid choice exits `2`, no suitable targets exits `4`, and scanner or download failures exit `3`.

### meridian test

Test delivered proxy traffic from your device. No SSH is needed.

A full test picks an active client, fetches the exact Xray JSON that Remnawave delivers to them, and pushes real traffic through the automatic fallback and through every path of the target server. The first run may spend up to 120 seconds downloading and verifying the pinned Xray runtime. Traffic checks rotate independent IP observers and compare them with a direct request, so an observer outage is inconclusive rather than a false failure. `MERIDIAN_CONNECT_TEST_URL` overrides the observer for controlled environments.

Each path reports its time to first byte. When a path fails, Meridian opens a plain TCP connection to its endpoint to say whether it **connects, but no traffic passes** (the server answers but the proxy session carries nothing: wrong credentials or a session being cut) or is **unreachable** (nothing answers on that port). With `--json`, `data.transports` lists every path's outcome (`ok`, `no_traffic`, `unreachable`, `failed`, `egress_mismatch`, `inconclusive`, `invalid`) and `first_byte_ms`.

A panel outage, HTTP `429`, or HTTP `5xx` makes the subscription unavailable, so the run exits `3`. Any other subscription rejection such as `400` or `422` is a completed failure and exits `4`.

```
meridian test [SERVER|DOMAIN] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--probe] [--timeout SECONDS] [--json]
```

`--client` tests a specific active client. `--basic` runs only network and TLS checks, without downloading Xray, and cannot verify UDP-only paths; it cannot be combined with `--client` (exit `2`). `--timeout` (default `5`, range `1`–`30`) limits each network operation, separate from the first-run Xray download.

Exit `0` means every check passed, `4` means the test found problems, and `3` means required evidence was unavailable. `--json` emits a `meridian.output/v1` envelope with every check and finding.

#### Censor's view: `meridian test --probe`

`--probe` looks at a server the way an active prober would, to check whether the deployment gives itself away, instead of sending traffic. It works on any IP or domain and applies stricter checks when the target belongs to your saved topology.

Checks follow the real deployment: public and internal port exposure, HTTP/TLS behavior, SNI consistency and camouflage, common proxy and panel paths, WebSocket upgrades, reverse DNS, HTTP/2, legacy TLS versions, and configured domains. `--sni` overrides the SNI for generic TLS probes. UDP-only listeners are reported as needing a full `meridian test` instead of being guessed.

`--probe` also answers "what is blocked?". It opens TLS to the server with its camouflage SNI; if that fails, it retries without the SNI and opens the real camouflage site directly, then reports whether the **server IP** or the **camouflage SNI** looks blocked. If the real site can't be reached either, the result is inconclusive rather than a finding. On the control server it also downloads the connection page's ~50 KB script and watches for a transfer that freezes after about 16–24 KB, which is how throttled foreign IPs behave. When a WSS path goes through a CDN, the same download runs through the CDN. These results only mean something when you run them from inside the censored network. `--probe` cannot be combined with `--basic`, `--client`, or `--domain`. Exit codes are the same as `test`; skipped checks are never reported as passes. The older `meridian probe` command still works for one release.

### meridian doctor

Collect system diagnostics for bug reports. Alias: `meridian rage`.

```
meridian doctor [SERVER] [--sni HOST] [--user USER] [--ssh-port PORT] [--ai]
```

`--sni` picks the camouflage target to diagnose, `--user` and `--ssh-port` override the saved SSH user and port, and `--ai` copies a redacted, AI-ready report to the clipboard instead of printing the issue template.

Doctor follows the server's roles: a relay-only server isn't diagnosed as an exit, and routing gateways are labeled separately. Missing tools or a lost SSH connection make the report inconclusive (exit `3`); sections already collected are still shown.

### meridian panel

Show the Remnawave admin panel URL and admin username, and what Meridian manages there versus what is yours to change.

```
meridian panel [--show-password]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--show-password` | | Also print the admin password |

Edits to client status, traffic limits, expiry, and descriptions, and users you create in the panel, survive every apply. Edits to nodes, profiles, Hosts, squads, and templates Meridian created are reverted by the next apply; `meridian plan` shows them first.

### meridian teardown

Remove Meridian from a server.

```
meridian teardown [SERVER] [--user USER] [--ssh-port PORT] [--yes]
```

On a single-server deployment, teardown removes everything: the panel, node, nginx configuration, certificates, relays, and firewall rules on the server (Docker and system packages stay), then the local `cluster.yml` and saved server profile. It also removes 3x-ui from Meridian 3.x. In a multi-server fleet, teardown refuses a server that still has a role in the topology.

`--yes` skips the confirmation; the role checks still run first. Declining exits `1`. An unknown server name, or zero or several possible targets, exits `2` instead of prompting.

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
| `--port PORT` | 0 | Localhost port from 0 to 65535; `0` picks a free high port |
| `--assets-dir DIRECTORY` | bundled assets | Use a built Studio asset directory instead of the packaged copy |
| `--no-open` | | Print the local URL without opening a browser |

Studio fails clearly if the port is taken or the assets are missing. An explicit asset directory must contain `studio/index.html`.

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

**`api commands`** — lists command contracts: argv, envelope and data schemas, possible statuses and outcomes, exit-code meanings, machine flags, and `interrupt_behavior`. Ctrl-C exits `130` without a JSON envelope (`interrupt_behavior: "exit_130_without_envelope"`).

**`api schema NAME`** — prints one JSON Schema, e.g. `meridian api schema output-envelope`.

**`api workflow NAME`** — prints a UI-renderable workflow, e.g. `meridian api workflow deploy --json`.

## Global options

Global options go before the command, for example `meridian --quiet doctor`. Command options such as `test --json` go after it.

| Flag | Description |
|------|-------------|
| `--version`, `-v` | Show the CLI version and exit |
| `--verbose` | Enable debug logging |
| `--quiet`, `-q` | Suppress progress output |
| `--lang CODE` | Language for the setup wizard: `en`, `ru`, `fa`, or `zh` (default: saved choice, else asked) |
| `--json` | Request a JSON envelope from a command that supports it; other commands fail explicitly |
| `--install-completion` | Install shell completion for the current shell |
| `--show-completion` | Print the completion script for the current shell |

JSON output uses the `meridian.output/v1` envelope (`schema`, `command`, `operation_id`, `status`, `summary`, `data`, `warnings`, `errors`). It is supported by deploy, plan, apply, test, probe, all client commands, fleet status/inventory, node/relay list, and the api commands. Secrets are redacted before JSON leaves the process. Run `meridian api commands --json` to discover the contracts.

## Common server options

These recur across commands; each command accepts only the options listed in its own section.

| Flag | Used by |
|------|---------|
| `SERVER` argument | deploy, check, scan, test, doctor, teardown, node add, relay add, server add |
| `--user`, `-u USER` | deploy, check, node add, relay add, server add, scan, doctor, teardown |
| `--ssh-port PORT` | deploy, check, node add, relay add, server add, scan, doctor, teardown |
| `--sni HOST` | deploy, check, node add, relay add, test, doctor |
| `--domain DOMAIN` | deploy, check, node add, test |

Client commands use the panel API directly and don't take a server. `test` runs from your device and doesn't use SSH.

## Server resolution

Name the server first, the way `ssh` does: an IP, a saved name, `user@host:port`, or `local` to run on the current machine without SSH. `--user` and `--ssh-port` fill in what the target leaves out and must not contradict it. An empty user or port means the saved value, else `root` and `22`. With no server, Meridian uses local mode (when running on a deployed server) or the only saved server. Zero or several candidates exit `2` rather than prompting. The older `--server NAME` flag still works.
