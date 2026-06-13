---
title: CLI Reference
description: Complete reference for all Meridian CLI commands and flags.
order: 10
section: reference
---

## Commands

### meridian deploy

Deploy proxy server to a VPS.

```
meridian deploy [IP] [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sni HOST` | www.microsoft.com | TLS camouflage target |
| `--domain DOMAIN` | (none) | Cloudflare CDN fallback domain |
| `--client-name NAME` | default | Name for the first client |
| `--display-name NAME` | (none) | Label for connection pages |
| `--icon EMOJI_OR_URL` | (none) | Page icon — emoji or image URL |
| `--color PALETTE` | ocean | Page color theme (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH user |
| `--harden / --no-harden` | enabled | Harden SSH + firewall |
| `--pq / --no-pq` | disabled | Post-quantum encryption — ML-KEM-768 hybrid (experimental) |
| `--warp / --no-warp` | disabled | Route outgoing traffic through Cloudflare WARP |
| `--server NAME` | | Target server (name or IP) |
| `--decoy MODE` | ignored | Deprecated; unknown paths always use the hardened default response |
| `--geo-block` / `--no-geo-block` | enabled | Block Russian domains and IPs (geosite:category-ru + geoip:ru) |
| `--ssh-port PORT` | 22 | SSH port (if non-standard) |
| `--yes` | | Skip confirmation prompts |
| `--json` | | Emit the final deploy result as a `meridian.output/v1` envelope |
| `--events=jsonl` | | Stream typed progress events as JSONL on stderr |
| `--request FILE` | | Read a `deploy-request` JSON payload from a file; use `-` for stdin |
| `--dry-run` | | Validate and plan deploy without SSH or panel mutation |

**Machine/UI flow**: `meridian api workflow deploy --json` returns a renderable wizard contract. A UI collects those fields, validates against `deploy-request`, then runs `meridian deploy --request deploy.json --json --events=jsonl`. Machine deploys are non-interactive: the request must contain `yes: true` after the user confirms. `--dry-run --json` returns `deploy-plan` under `data` so a UI can preview mode, ports, and generated paths before opening SSH.

### meridian client

Manage client access keys and connection details.

```
meridian client add NAME [NAME...]  [--json]
meridian client show NAME [--json]
meridian client list [--json]
meridian client remove NAME [--yes] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit result as a `meridian.output/v1` envelope (all client commands) |
| `--yes`, `-y` | | Skip removal confirmation (applies to `client remove`) |

**`client add`** — pass multiple names to add several clients at once (e.g. `meridian client add alice bob charlie`). With `--json`, returns `data.clients[]` with username, UUID, and status for each created client.

**`client list`** — with `--json`, returns `data.summary` status counts and `data.clients[]` records with username, UUID, status, traffic counters, creation time, and last seen time.

**`client show`** — with `--json`, returns one `data.client` record plus `data.handoff.*` availability metadata. The human command still prints the usable subscription/share URLs.

**`client enable`** — resumes a previously suspended client so they can connect again. With `--json`, returns `data.client` with username and status.

**`client disable`** — temporarily suspends a client. Their configuration stays intact but they cannot connect until re-enabled. With `--json`, returns `data.client` with username and status.

### meridian server

Manage known servers.

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name NAME` | (auto) | Display name for the server |

### meridian node

Manage additional exit nodes in a multi-node fleet. The first server (panel host) is deployed with `meridian deploy`; subsequent exit nodes are added with `meridian node add`.

```
meridian node add IP [flags]
meridian node list [--json]
meridian node remove IP [--yes] [--force]
meridian node check IP
```

| Flag | Default | Description |
|------|---------|-------------|
| `--user USER` | root | SSH user on the node |
| `--ssh-port PORT` | 22 | SSH port on the node (if non-standard) |
| `--name NAME` | (auto, from IP) | Friendly name shown in panel / subscription |
| `--domain DOMAIN` | (none) | Per-node domain for WSS/CDN fallback |
| `--sni HOST` | www.microsoft.com | Reality camouflage target for this node |
| `--warp / --no-warp` | disabled | Route outgoing traffic through Cloudflare WARP on this node |
| `--harden / --no-harden` | enabled | OS + SSH + firewall hardening for the node |
| `--yes` | | Skip confirmation prompts (applies to `node remove`) |
| `--force` | | On `node remove`, proceed even if relays reference this node as their exit |
| `--json` | | Emit `node list` as a `meridian.output/v1` envelope |

**How it works**: `meridian node add` provisions the node host (OS packages, Docker, nginx, TLS, Remnawave node container), registers the node against the panel's REST API, and creates `reality` and `xhttp` host entries so clients automatically receive the new exit in their next subscription refresh. The new entry is added to `nodes[]` in `cluster.yml`; `desired_nodes[]` is also updated if that list is non-null (hybrid sync). Node deployment logic lives in `node_deploy.py`.

**Health checks**: `meridian node check` runs panel status, SSH, container, port, and TLS checks. When a check fails, it prints a remediation hint (e.g. `Run: docker compose up -d`).

**Removal**: `meridian node remove` SSHs into the node to stop containers before removing the node from the cluster and panel. It refuses to delete a node that is still the `exit_node` of one or more relays; pass `--force` to override.

**JSON output**: `node list --json` uses the `meridian.output/v1` envelope with `data.nodes[]` containing ip, name, uuid, status, xray_version, and traffic_bytes.

### meridian fleet

Inspect and repair the fleet from the live panel API.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet recover --panel-url URL --api-token TOKEN
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit fleet state as JSON (for scripting / CI) |
| `--panel-url URL` | required | Panel HTTPS URL (for `fleet recover`) |
| `--api-token TOKEN` | required | Remnawave API token (for `fleet recover`) |

**`fleet status`** — shows panel health, every node's connection + Xray version + traffic, every relay's upstream, and user counts. With `--json`, output uses the `meridian.output/v1` envelope. Stable field access inside `data`: `data.panel.url`, `data.panel.healthy`, `data.sources.*`, `data.servers[].roles`, `data.nodes[].status` (`"connected"`, `"disconnected"`, `"disabled"`, `"unknown"`), `data.relays[].health` (`"healthy"`, `"unhealthy"`, `"unknown"`), and `data.summary.health/needs_attention/active_users/disabled_users/unknown_nodes/unhealthy_relays`. `data.summary.health` is `"unknown"` when required live data could not be collected. Top-level `status` reports command execution, not fleet health.

**`fleet inventory`** — shows the configured panel, nodes, relays, desired topology, and live panel node status when reachable. It never prints the panel API token or secret URL paths. With `--json`, output uses the `meridian.output/v1` envelope. Stable field access inside `data` includes `data.sources.*`, `data.servers[].roles`, `data.summary.*`, `data.nodes[].desired`, `data.nodes[].protocols`, `data.relays[].exit_node_*`, and `data.desired_nodes[].present`. Inventory presence fields are not reconciliation truth; use `plan --json` for drift/apply decisions.

**`fleet recover`** — rebuilds `~/.meridian/cluster.yml` from the live panel. Use it when the local file is lost, or when picking up someone else's deployment. Connects via SSH to read stable server-side metadata, then queries the panel API for nodes, relays, inbounds, hosts, and users.

### meridian api

Inspect the machine-readable meridian-core contract used by JSON output and future UI clients.

```
meridian api schemas [--json] [--include-schemas]
meridian api commands [--json] [--include-schemas]
meridian api schema NAME [--envelope|--json]
meridian api workflow NAME [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit the schema catalog as a `meridian.output/v1` envelope |
| `--include-schemas` | | Include full JSON Schemas in `api schemas --json` or `api commands --json` output |
| `--envelope`, `--json` | | Wrap `api schema NAME` in a `meridian.output/v1` envelope instead of printing raw JSON Schema |

**`api schemas`** — lists stable schema names such as `output-envelope`, `apply-envelope`, `client-list-envelope`, `client-show-envelope`, `deploy-envelope`, `deploy-command-data`, `deploy-request`, `deploy-workflow-answers`, `deploy-result`, `deploy-plan`, `workflow-plan`, `input-field`, `remote-target`, `command-spec`, `remote-command-result`, `plan-envelope`, `fleet-status-envelope`, `fleet-inventory-envelope`, `event`, `apply`, `plan-result`, `fleet-status`, and `fleet-inventory`. Command envelope schemas include a `commands` entry in the catalog.

**`api commands`** — lists migrated command contracts with `command`, `argv`, `envelope_schema`, `data_schema`, possible `statuses`, structured `outcomes`, exit-code meanings, machine flags, and stability. Use this before wiring a UI to decide which command payload schema validates a given envelope. `deploy` advertises `--json`, `--events=jsonl`, `--request`, and `--dry-run`.

**`api schema NAME`** — prints one JSON Schema. Example: `meridian api schema output-envelope`.

**`api workflow NAME`** — prints a UI-renderable workflow plan. Example: `meridian api workflow deploy --json` returns deploy wizard sections and fields.

### meridian relay

Manage relay nodes — lightweight TCP forwarders that route traffic through a domestic server to an exit server abroad.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--exit/-e EXIT` | (required for deploy) | Exit server IP or name |
| `--name NAME` | (auto) | Friendly name for the relay (e.g., "ru-moscow") |
| `--port/-p PORT` | 443 | Listen port on relay server |
| `--user/-u USER` | root | SSH user on relay |
| `--ssh-port PORT` | 22 | SSH port on the relay server (if non-standard) |
| `--yes/-y` | | Skip confirmation prompts |
| `--json` | | Emit `relay list` as a `meridian.output/v1` envelope |

**How relays work**: Client connects to the relay's domestic IP. Relay forwards raw TCP to the exit server abroad. All encryption is end-to-end between client and exit — the relay never sees plaintext. All protocols (Reality, XHTTP, WSS) work through the relay.

**JSON output**: `relay list --json` uses the `meridian.output/v1` envelope with `data.relays[]`.

### meridian plan

Show the reconciliation plan — what `meridian apply` would do to converge the cluster to the desired state declared in `cluster.yml`.

Reads `desired_nodes`, `desired_relays`, `desired_clients`, and `subscription_page` from `cluster.yml`, fetches actual state from the panel, and prints a Terraform-style diff with `+` for adds, `-` for removes, `~` for updates.

```
meridian plan [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit the plan as a `meridian.output/v1` JSON envelope for CI/CD and UI clients. Same exit codes; human plan output is suppressed |

**Exit codes**:
- `0` — converged (no changes needed)
- `2` — changes pending (run `meridian apply` to converge)
- errors also use non-zero exits; process clients should treat JSON `status` and `errors[].category` as authoritative because `2` can also mean a user/config error when `status` is `failed`

**JSON shape** (`--json` mode):
```json
{
  "schema": "meridian.output/v1",
  "meridian_version": "4.x.x",
  "command": "plan",
  "operation_id": "9d0f...",
  "started_at": "2026-05-04T21:00:00Z",
  "duration_ms": 128,
  "status": "changed",
  "exit_code": 2,
  "summary": {
    "text": "Plan: 1 to add, 1 to remove",
    "changed": true,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1}
  },
  "data": {
    "converged": false,
    "summary": "Plan: 1 to add, 1 to remove",
    "exit_code": 2,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1},
    "actions": [
      {"plan_index": 0, "execution_order": 2, "kind": "add_client", "operation": "add", "resource_type": "client",
       "resource_id": "alice", "target": "alice", "detail": "create client alice",
       "phase": "provision", "requires_confirmation": false,
       "destructive": false, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "", "from_extras": false,
       "change_set": [], "symbol": "+", "can_run_parallel": false},
      {"plan_index": 1, "execution_order": 1, "kind": "remove_client", "operation": "remove", "resource_type": "client",
       "resource_id": "ghost", "target": "ghost", "detail": "delete client ghost",
       "phase": "deprovision", "requires_confirmation": true,
       "destructive": true, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "delete client ghost",
       "from_extras": true, "change_set": [], "symbol": "-", "can_run_parallel": false}
    ]
  },
  "warnings": [],
  "errors": []
}
```

`data.actions[].from_extras: true` flags resources that exist on the panel but are missing from `cluster.yml` — the inputs `meridian apply --prune-extras` operates on. `execution_order` shows the order `apply` will use, which may differ from display order for replacement safety. `operation: "replace"` marks destructive replacements such as relay reprovisioning. `status` is `no_changes` when converged and `changed` when apply has work to do; `data.exit_code` mirrors the process exit code.

See [Declarative workflow](/docs/en/getting-started/#declarative-workflow) for how to compose `cluster.yml`.

### meridian apply

Converge the cluster to the desired state declared in `cluster.yml`. Runs `plan` internally, shows the diff, asks for confirmation, then executes the actions in dependency order (removals first, then adds, then removals of nodes last).

```
meridian apply [--yes] [--prune-extras=ask|yes|no] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--yes`, `-y` | | Skip confirmation prompts |
| `--parallel N` | 4 | Max parallel node provisioning threads (each node gets its own SSH session and panel client) |
| `--prune-extras` | `ask` | How to handle drift — resources present on the panel but missing from `cluster.yml`. `ask` prompts per-resource (downgraded to `no` under `--yes` for safety); `yes` auto-removes; `no` skips and prints a one-line summary |
| `--json` | | Emit a `meridian.output/v1` final apply result with per-action execution status |

Destructive actions (removals, UPDATE_RELAY re-provisioning) print a warning and require a separate confirmation. A failure early in the plan skips remaining destructive actions — `cluster.yml` stays truthful.

With `--json`, `data.plan` contains the typed plan and `data.actions[]` contains execution results with `status: "succeeded" | "failed" | "skipped"`. JSON mode is non-interactive: if changes need confirmation and `--yes` is missing, Meridian returns `MERIDIAN_CONFIRMATION_REQUIRED` with the computed plan. If panel-only drift exists and `--prune-extras` is left at `ask`, Meridian returns `MERIDIAN_DRIFT_DECISION_REQUIRED`; pass `--prune-extras=no` to keep drift or `--prune-extras=yes` to remove it. The JSON contract reports execution; it does not make destructive operations transactional. UI clients should inspect failed/skipped actions and rerun idempotently after fixing the underlying issue.

**Drift handling example:** if `cluster.yml` lists `desired_clients: ['alice']` but the panel also has `bob` (e.g. created via the panel UI), `meridian plan` shows `- remove client: bob`. With default `--prune-extras=ask` you'll be asked whether to remove `bob` or keep him. `--yes --prune-extras=yes` runs the removal silently; `--yes` alone (no explicit `--prune-extras`) skips it.

### meridian preflight

Pre-flight server validation. Tests SNI, ports, DNS, OS, disk, ASN without installing anything.

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

Find optimal SNI targets on the server's network using RealiTLScanner.

```
meridian scan [IP] [--server NAME]
```

### meridian test

Test proxy reachability and verify actual connections from the client device. No SSH needed.

First checks basic reachability (TCP, TLS handshake, domain HTTPS). Then downloads a local xray client binary (cached after first use), connects through the proxy for each active protocol (Reality, XHTTP, WSS), and confirms traffic flows end-to-end.

```
meridian test [IP] [--server NAME]
```

### meridian probe

Probe a server as a censor would — check if the deployment is detectable. No SSH needed. Works on any server, not just Meridian deployments. Accepts IP addresses or domain names.

Runs 9 checks: port surface, HTTP response, TLS certificate, SNI consistency, proxy path probing, WebSocket upgrade, reverse DNS, HTTP/2 support, and legacy TLS versions.

```
meridian probe [IP|DOMAIN] [--server NAME]
```

### meridian doctor

Collect system diagnostics for debugging. Alias: `meridian rage`.

```
meridian doctor [IP] [--ai] [--server NAME]
```

### meridian teardown

Remove proxy from server.

```
meridian teardown [IP] [--server NAME] [--yes]
```

### meridian update

Update CLI to latest version.

```
meridian update
```

### meridian --version

Show CLI version.

```
meridian --version
meridian -v
```

## Global flags

These flags are available on commands that interact with a server via SSH (deploy, node, relay, preflight, test, probe, doctor, teardown):

| Flag | Description |
|------|-------------|
| `--server NAME` | Target a specific named server |
| `--user/-u USER` | SSH user (default: root, non-root gets sudo automatically) |
| `--sni HOST` | TLS camouflage target (used by deploy, preflight, test, doctor) |
| `--domain DOMAIN` | Cloudflare CDN fallback domain (used by deploy, preflight, test) |

Client commands (`client add/show/list/remove/enable/disable`) operate on the cluster's panel directly and do not accept `--server`.

## Server resolution

Commands that need a server follow this priority:
1. Explicit IP argument or `local` keyword (deploy on this server without SSH)
2. `--server NAME` flag (also accepts `--server local`)
3. Local mode detection (running on the server itself)
4. Single server auto-select (if only one saved)
5. Interactive prompt
