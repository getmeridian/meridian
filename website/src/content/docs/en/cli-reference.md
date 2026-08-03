---
title: CLI Reference
description: Complete reference for all Meridian CLI commands and flags.
order: 10
section: reference
---

## Commands

### meridian setup

Configure a complete V4 topology with the resumable guided wizard.

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--intent FILE` | (none) | Start at review from a complete `SetupIntent` JSON document; use `-` for stdin |
| `--restart` | | Explicitly discard saved setup progress before starting |
| `--yes`, `-y` | | Approve the reviewed plan and apply without another confirmation |

Setup checkpoints non-secret progress in `~/.meridian/setup.json`, so rerunning the command resumes the last complete stage. Credentials and private keys are never stored in that draft. An existing draft is not replaced by `--intent` unless `--restart` is also present.

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
| `--warp / --no-warp` | disabled | Route outgoing traffic through Cloudflare WARP |
| `--server NAME` | | Target server (name or IP) |
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
meridian client show NAME [--repair-page] [--json]
meridian client list [--json]
meridian client remove NAME [--yes] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit result as a `meridian.output/v1` envelope (all client commands) |
| `--yes`, `-y` | | Skip removal confirmation (applies to `client remove`) |
| `--repair-page` | | Recreate a missing legacy connection page (applies to `client show`) |

**`client add`** — pass multiple names to add several clients at once (e.g. `meridian client add alice bob charlie`). V4 adds the users to `topology_intent.access.users` and applies that reviewed topology; legacy deployments create users directly. With `--json`, `data.clients[]` retains every successful creation even when a later legacy creation, handoff, or local state save fails. The completed remote mutations are preserved, retryable failures appear in `warnings[]`, and the command exits `3` in both human and JSON modes.

**`client list`** — with `--json`, returns `data.summary` status counts and `data.clients[]` records with username, UUID, status, traffic counters, creation time, and last seen time. V4 exposes only users declared in `topology_intent.access.users`; internal routing service accounts are never client output.

**`client show`** — with `--json`, returns one managed `data.client` record plus `data.handoff.*` availability metadata. Subscription URLs are shown when available. Legacy share-page URLs are shown only after Meridian has deployment evidence; `--repair-page` explicitly regenerates a missing page. If that repair fails, the client and handoff evidence are still returned with a warning and exit `3`. V4 service accounts are rejected.

**`client enable`** — resumes a previously suspended client so they can connect again. With `--json`, returns `data.client` with username and status.

**`client disable`** — temporarily suspends a client. On V4, this is an operational pause only: the next topology apply restores every declared access user. Human and JSON output include that warning.

**`client remove`** — removes a client directly only on legacy deployments. V4 refuses the command with exit `2` because safe managed-user retirement is not implemented yet; use `client disable` for immediate temporary revocation and do not delete the panel user directly. If the legacy panel user is deleted but local state persistence or share-page cleanup remains incomplete, the removal is a typed partial result with exit `3`. JSON preserves the removed record in `data.client` and reports the incomplete follow-up work in `warnings[]`.

**Mutating JSON commands** are non-interactive. `client remove --json` requires `--yes`; without it Meridian returns a typed confirmation error instead of prompting.

### meridian server

Manage known servers.

```
meridian server add IP
meridian server list
meridian server remove NAME_OR_IP [--yes]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--name NAME` | (auto) | Display name for the server |
| `--user/-u USER` | root | SSH user on the server |
| `--ssh-port PORT` | 22 | SSH port on the server (if non-standard) |
| `--yes`, `-y` | | Skip the removal confirmation (applies to `server remove`) |

Server names, hosts, and stable IDs must be unique; `server add` refuses cross-field collisions instead of retargeting an existing identity. `server remove` asks for confirmation by default and deletes only the local profile. It refuses while that profile is referenced by legacy state or V4 topology intent; remove the server's role through setup/apply first.

### meridian node

Manage additional exit nodes in a multi-node fleet. The first server (panel host) is deployed with `meridian deploy`; subsequent exit nodes are added with `meridian node add`.

```
meridian node add IP [flags]
meridian node list [--json]
meridian node remove NAME_OR_IP [--yes] [--force]
meridian node check NAME_OR_IP
```

| Flag | Default | Description |
|------|---------|-------------|
| `--user USER` | root | SSH user on the node |
| `--ssh-port PORT` | 22 | SSH port on the node (if non-standard) |
| `--name NAME` | (auto, from IP) | Friendly name shown in panel / subscription |
| `--domain DOMAIN` | (none) | Per-node domain for WSS/CDN fallback |
| `--sni HOST` | www.microsoft.com | Reality camouflage target for this node |
| `--harden / --no-harden` | enabled | OS + SSH + firewall hardening; V4 rejects `--no-harden` |
| `--yes` | | Skip confirmation prompts (applies to `node add` and `node remove`) |
| `--force` | | On `node remove`, remove dependent relays first, then the node |
| `--json` | | Emit `node list` as a `meridian.output/v1` envelope |

**How it works**: on V4, `node add` registers the server, adds a reviewed exit to `topology_intent`, and applies it. IP mode adds a Reality path; specifying `--domain` also enables the domain-backed transport selected by the compiler. On legacy deployments it provisions a Remnawave node directly and mirrors the result into `desired_nodes[]` only when that legacy list is managed.

**Health checks**: `meridian node check` runs panel status, SSH, container, port, and TLS checks. For V4 topology it checks the public ports declared by the configured protocol paths instead of assuming port 443. When a check fails, it prints a remediation hint (e.g. `Run: docker compose up -d`). Exit `0` is healthy, `4` means completed findings, and `3` means required evidence was unavailable. A missing inspection tool or lost SSH transport is unavailable evidence, not a negative finding.

**Removal**: legacy `node remove` stops the remote node and refuses dependent relays unless `--force` can remove them first. V4 rejects direct removal with exit `2`; remove the exit in `meridian setup`, review the plan, and apply it.

**JSON output**: `node list --json` uses the `meridian.output/v1` envelope. `data.nodes[]` includes `ip`, `name`, `uuid`, `role`, `is_panel_host`, status, Xray version, and traffic bytes. If panel status is unavailable, configured rows remain in the output with `status: "unknown"`; Meridian emits a warning and exits `3` instead of discarding the local inventory.

### meridian fleet

Inspect and repair the fleet from the live panel API.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet recover --legacy --panel-url URL [--api-token-file FILE]
                       [--profile NAME_OR_UUID] [--squad NAME_OR_UUID]
                       [--panel-node SELECTOR]
                       [--panel-server IP] [--user USER] [--ssh-port PORT] [--force]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit fleet state as JSON (for scripting / CI) |
| `--panel-url URL` | required | Complete HTTPS panel URL, including its secret path |
| `--api-token-file FILE` | secure prompt or environment | Read the token from a mode-600 file instead of `MERIDIAN_API_TOKEN` or the prompt |
| `--profile NAME_OR_UUID` | auto if unique | Select the exact legacy config profile when the panel has more than one |
| `--squad NAME_OR_UUID` | auto if one is eligible | Select the exact legacy access squad when multiple squads grant all recovered inbounds |
| `--panel-node SELECTOR` | auto if unambiguous | Identify the panel host by exact node name, UUID, or address |
| `--panel-server IP` | panel URL IP | Public SSH IPv4/IPv6 of the Remnawave host; required when the panel URL uses a domain |
| `--user`, `-u USER` | root | SSH user for the public panel server |
| `--ssh-port PORT` | 22 | SSH port for the public panel server, from 1 to 65535 |
| `--legacy` | required | Acknowledge that the target is a legacy shared-profile deployment |
| `--force` | | Back up and replace an existing local `cluster.yml` |

**`fleet status`** — shows panel health, node heartbeats, public relay listeners, internal V4 relay hops, and managed-user counts. Missing or non-active declared V4 access users degrade health and appear in `data.summary.missing_access_users` / `nonactive_access_users`. A reachable relay listener is TCP evidence only, so any otherwise-unverified relay route keeps fleet health `unknown`; use `meridian test` to certify the complete encrypted path. Exit `0` means fully observed healthy, `4` degraded, and `3` unknown or unavailable.

**`fleet inventory`** — shows the configured panel, nodes, relays, desired topology, and live panel node status when reachable. If live panel evidence is unavailable, configured inventory remains in the result, affected sources are marked unavailable, warnings explain the gap, and the command exits `3` instead of discarding partial data. It never prints the panel API token or secret URL paths. With `--json`, output uses the `meridian.output/v1` envelope. Stable field access inside `data` includes `data.sources.*`, `data.servers[].roles`, `data.summary.*`, `data.nodes[].desired`, `data.nodes[].protocols`, `data.relays[].exit_node_*`, and `data.desired_nodes[].present`. Inventory presence fields are not reconciliation truth; use `plan --json` for drift/apply decisions.

**`fleet recover`** — imports one unambiguous legacy shared profile from a live panel. `--legacy` is mandatory and `--panel-url` must contain the real secret path, not the site root. Recovery proves the access squad and panel node, reads the share path over SSH, repopulates `servers.json`, and refuses WSS metadata unless exactly one valid domain can be assigned safely. Domain panel URLs require `--panel-server`. V4 intent cannot be reconstructed; restore a backup or rerun `meridian setup`. Tokens come from a secure prompt, `MERIDIAN_API_TOKEN`, or a mode-600 file. Existing state is replaced only with `--force`, after a backup.

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
| `--include-schemas` | | Include full JSON Schemas and imply JSON envelope output for `api schemas` or `api commands` |
| `--envelope`, `--json` | | Wrap `api schema NAME` in a `meridian.output/v1` envelope instead of printing raw JSON Schema |

**`api schemas`** — lists stable schema names such as `output-envelope`, `apply-envelope`, `client-list-envelope`, `client-show-envelope`, `deploy-envelope`, `deploy-command-data`, `deploy-request`, `deploy-workflow-answers`, `deploy-result`, `deploy-plan`, `workflow-plan`, `input-field`, `remote-target`, `command-spec`, `remote-command-result`, `plan-envelope`, `fleet-status-envelope`, `fleet-inventory-envelope`, `event`, `apply`, `plan-result`, `fleet-status`, and `fleet-inventory`. Command envelope schemas include a `commands` entry in the catalog.

**`api commands`** — lists migrated command contracts with `command`, `argv`, `envelope_schema`, `data_schema`, possible `statuses`, structured `outcomes`, exit-code meanings, machine flags, stability, and `interrupt_behavior`. The listed `statuses` and `outcomes` describe completed JSON envelopes only. `interrupt_behavior: "exit_130_without_envelope"` means Ctrl-C/SIGINT exits `130` without promising any JSON on stdout. Use this before wiring a UI to decide which command payload schema validates a given envelope. `deploy` advertises `--json`, `--events=jsonl`, `--request`, and `--dry-run`.

**`api schema NAME`** — prints one JSON Schema. Example: `meridian api schema output-envelope`.

**`api workflow NAME`** — prints a UI-renderable workflow plan. Example: `meridian api workflow deploy --json` returns deploy wizard sections and fields.

### meridian studio

Open Meridian Studio and its local Engine API. The server binds only to `127.0.0.1`.

```
meridian studio [--port PORT] [--assets-dir DIRECTORY] [--no-open]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port PORT` | 0 | Localhost port from 0 to 65535; `0` chooses an available high port |
| `--assets-dir DIRECTORY` | bundled assets | Use a built Studio asset directory instead of the packaged copy |
| `--no-open` | | Print the local URL without opening a browser |

Studio fails clearly if the requested port is occupied or if packaged/explicit assets are missing. An explicit asset directory must contain `studio/index.html`.

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
| `--sni HOST` | (auto) | Legacy: scan a relay-local Reality SNI. V4: when omitted, inherit the exit Reality path |
| `--user/-u USER` | root | SSH user on relay |
| `--ssh-port PORT` | 22 | SSH port on the relay server (if non-standard) |
| `--yes/-y` | | Skip confirmation prompts |
| `--json` | | Emit `relay list` as a `meridian.output/v1` envelope |

**How relays work**: a client connects to the relay's public IP and Realm forwards encrypted traffic toward an exit. Legacy Realm relays advertise only Reality. V4 relay chains are topology-managed: `relay remove` and `relay check` refuse them with exit `2`; edit chains with `meridian setup` and use `meridian test` for end-to-end health. Legacy `relay check` uses exit `0` for healthy, `4` for completed findings, `3` for unavailable evidence, and `2` for invalid targets. Missing remote inspection tools, command timeouts, and lost SSH transport are unavailable evidence and therefore exit `3`.

**JSON output**: `meridian --json relay list` uses the `meridian.output/v1` envelope with `data.relays[]`.

### meridian plan

Show what `meridian apply` would do to converge the cluster.

V4 reads the compiled `topology_intent` created by setup. Its human output reports the compiled plan hash and resource counts, then any resource repairs, unavailable observations, and saved-generation state changes. Its JSON data exposes `plan_hash`, `resources[]` with desired hashes, `drifted_resources[]`, `observation_errors[]`, and `state_changes[]`; it does not use generic Terraform action symbols.

Legacy clusters read the optional `desired_nodes`, `desired_relays`, `desired_clients`, and `subscription_page` fields. Only legacy plan prints a Terraform-style action diff with `+` for adds, `-` for removes, and `~` for updates.

```
meridian plan [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | | Emit the plan as a `meridian.output/v1` JSON envelope for CI/CD and UI clients. Same exit codes; human plan output is suppressed |

**Exit codes**:
- `0` — converged (no changes needed)
- `2` — changes pending (run `meridian apply` to converge)
- `3` — required observation evidence was unavailable, including when legacy subscription-page SSH inspection times out, loses transport, or cannot run its inspection command
- errors also use non-zero exits; process clients should treat JSON `status` and `errors[].category` as authoritative because `2` can also mean a user/config error when `status` is `failed`

**Legacy JSON shape** (`--json` mode):
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

In the legacy result, `data.actions[].from_extras: true` flags resources that exist on the panel but are missing from `cluster.yml` — the inputs `meridian apply --prune-extras` operates on. `execution_order` shows the order `apply` will use, which may differ from display order for replacement safety. `operation: "replace"` marks destructive replacements such as relay reprovisioning. In both plan contracts, `status` is `no_changes` when converged and `changed` when apply has work to do; observation failures instead use `failed` and exit `3`. `data.exit_code` mirrors the process exit code.

See [Declarative workflow](/docs/en/getting-started/#declarative-workflow) for the V4 setup/plan/apply flow.

### meridian apply

Converge the cluster to the desired state declared in `cluster.yml`. Legacy apply runs the action plan, shows its diff, asks for confirmation, then executes actions in dependency order. V4 applies the reviewed compiled resource graph and reports the plan hash, generation, and per-resource observation status.

```
meridian apply [--yes] [--parallel N] [--prune-extras=ask|yes|no] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--yes`, `-y` | | Skip confirmation prompts |
| `--parallel N` | 4 | Legacy only: max parallel node provisioning threads, from 1 to 32 (each gets its own SSH session and panel client) |
| `--prune-extras` | `ask` | Legacy only: how to handle resources present on the panel but missing from `cluster.yml`. `ask` prompts per-resource (downgraded to `no` under `--yes` for safety); `yes` auto-removes; `no` skips and prints a one-line summary |
| `--json` | | Emit a `meridian.output/v1` final apply result with per-action execution status |

V4 accepts `--yes` and `--json`. It rejects non-default legacy settings (`--parallel` other than `4` or `--prune-extras` other than `ask`) as a user error with exit `2` before apply begins.

V4 saves provenance-tagged local checkpoints throughout apply. If a checkpoint or convergence-state save fails, Meridian returns a system error with exit `3` and warns that remote state may have changed. Repair local state persistence, then rerun `meridian plan` and `meridian apply` to observe and reconcile the result.

Destructive actions (removals, UPDATE_RELAY re-provisioning) print a warning and require a separate confirmation. A failure early in the plan skips remaining destructive actions — `cluster.yml` stays truthful.

In legacy `--json` output, `data.plan` contains the typed plan and `data.actions[]` contains execution results with `status: "succeeded" | "failed" | "skipped"`. V4 instead returns compiled actions with `status: "converged" | "applied" | "failed" | "unknown" | "skipped"`. JSON mode is non-interactive: if changes need confirmation and `--yes` is missing, Meridian returns `MERIDIAN_CONFIRMATION_REQUIRED` with the computed plan or compiled-plan preview. Legacy panel-only drift with `--prune-extras=ask` returns `MERIDIAN_DRIFT_DECISION_REQUIRED`; pass `--prune-extras=no` to keep drift or `--prune-extras=yes` to remove it. If legacy actions ran but the post-mutation state save fails, including a concurrent `cluster.yml` edit, the envelope retains those action results and returns exit `3` with `MERIDIAN_STATE_SAVE_FAILED`. The JSON contract reports execution; it does not make destructive operations transactional. UI clients should inspect failed/skipped actions and rerun idempotently after fixing the underlying issue.

**Legacy drift example:** if `cluster.yml` lists `desired_clients: ['alice']` but the panel also has `bob`, `meridian plan` shows `- remove client: bob`. With default `--prune-extras=ask` you are asked whether to remove `bob`. `--yes --prune-extras=yes` removes it non-interactively; `--yes` alone skips unmanaged extras.

### meridian preflight

Pre-flight server validation. Tests SNI, ports, DNS, OS, disk, and clock state without installing anything. DNS uses the server's configured resolver; Meridian does not send the server address to a third-party ASN service. Exit `0` means all requirements passed, `4` means actionable findings were detected, and `3` means required evidence was unavailable.

```
meridian preflight [IP] [--domain NAME] [--sni HOST] [--user USER]
                   [--ai] [--server NAME]
```

`--domain` validates that the domain resolves to the target server. `--sni` overrides the camouflage target, `--user` overrides the saved SSH user, and `--ai` formats the findings as an AI-ready diagnostic prompt.

Preflight accepts a public IP or resolvable hostname, creates a short-lived listener when port 443 is unused, and never treats missing evidence as success. Exit `0` means compatible, `4` means completed negative findings, `3` means required evidence was unavailable, and `2` means invalid input.

### meridian scan

Find optimal SNI targets on the server's network using the pinned RealiTLScanner release. Meridian verifies the published SHA-256 digest before executing it and removes the isolated remote workspace after the scan.

```
meridian scan [IP] [--user USER] [--server NAME]
```

RealiTLScanner supports IPv4 ranges; an IPv6-only target exits `3` with a clear unsupported-evidence message. The selected target is saved only for a tracked legacy node. For V4, Meridian reports the selection without changing reviewed intent; use `meridian setup` to apply it. A blank skip exits `0`, an invalid non-empty selection exits `2`, no valid candidates exits `4`, and scanner/download failures exit `3`.

### meridian test

Test proxy reachability and verify actual connections from the client device. No SSH is needed.

The default full test selects a deterministic active client, fetches the exact Xray JSON delivered by Remnawave, and executes automatic fallback plus every supported outbound for the selected target. V4 selection is restricted to `topology_intent.access.users`; service users cannot be selected explicitly or by fallback. The first run may spend up to 120 seconds downloading and verifying the pinned Xray runtime from GitHub. Traffic checks rotate independent IP observers and compare them with a direct control request, so an observer outage is inconclusive rather than a false protocol failure. `MERIDIAN_CONNECT_TEST_URL` can override the observer for controlled environments.

During a full test, a panel transport failure, HTTP `429`, or HTTP `5xx` response makes canonical-subscription evidence unavailable (`PANEL_UNAVAILABLE`), so the run is inconclusive with exit `3`. A deterministic non-auth subscription HTTP `4xx` rejection, such as `400` or `422` and excluding `429`, is a completed negative finding (`PANEL_REQUEST_FAILED`) and exits `4`.

`--client` selects a specific active managed client. `--basic` limits the run to network/TLS observations and cannot certify UDP-only traffic. `--basic` and `--client NAME` are incompatible; using them together is a user error that exits `2` before any network I/O. `--timeout` defaults to `5` seconds and accepts `1` through `30`; it bounds each network operation, separate from first-run Xray bootstrap.

```
meridian test [IP|DOMAIN] [--server NAME] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--timeout SECONDS] [--json]
```

Exit `0` means every required check passed, `4` means the test completed with negative findings, and `3` means required evidence was unavailable. `--json` emits the typed `meridian.output/v1` envelope with every check and finding.

### meridian probe

Probe a server as a censor would — check if the deployment is detectable. No SSH is needed. It works on arbitrary IPs/domains and automatically applies stricter policy when the target belongs to the saved Meridian topology.

Checks are selected from the real deployment shape: public and internal port exposure, HTTP/TLS behavior, SNI consistency and camouflage, common proxy/panel paths, WebSocket upgrades, reverse DNS, HTTP/2, legacy TLS, hardened roots, and configured domains. `--sni` overrides generic TLS SNI. UDP-only listeners are not guessed from an empty datagram; the probe reports them as requiring the canonical full connection test.

```
meridian probe [IP|DOMAIN] [--server NAME] [--sni NAME]
               [--timeout SECONDS] [--json]
```

Probe uses the same exit contract as `test`: `0` passed, `4` completed with findings, and `3` inconclusive. `--timeout` defaults to `5` seconds and accepts `1` through `30`. Skipped evidence is never reported as a pass.

### meridian doctor

Collect system diagnostics for debugging. Alias: `meridian rage`.

```
meridian doctor [IP] [--sni HOST] [--user USER] [--ai] [--server NAME]
```

`--sni` selects the camouflage target to diagnose, `--user` overrides the saved SSH user, and `--ai` copies an AI-ready redacted report instead of printing the normal issue template.

Doctor derives sections and listener ports from the selected server's roles and V4 configured public ports. A relay-only server is not diagnosed as an exit, and routing gateways are labeled separately. Missing inspection tools or lost SSH transport make the report inconclusive and exit `3`; already collected sections are still shown.

### meridian teardown

Remove proxy from server.

```
meridian teardown [IP] [--user USER] [--server NAME] [--yes]
```

`--yes` skips the destructive confirmation. Ownership and dependency checks still run first. Target selection is non-interactive: an unknown `--server`, or zero or multiple implicit candidates, exits `2` instead of prompting for an IP. Declining the destructive confirmation exits `1`. V4 refuses any server still referenced by topology intent. A panel host cannot be removed while another node or relay remains; remove those deployments first. Teardown of the panel host unlinks the local cluster state after successful remote cleanup.

### meridian update

Update the CLI to the latest PyPI release. Meridian tries compatible uv/pipx/pip managers and accepts success only after the active `meridian` executable reports the expected version. Exit `0` means already current or verified updated; exit `3` means discovery, upgrade, or active-CLI verification failed.

```
meridian update
```

## Global options

Global options must appear before the command, for example `meridian --quiet doctor`. Command-local options such as `test --json` stay after the command.

| Flag | Description |
|------|-------------|
| `--version`, `-v` | Show the CLI version and exit |
| `--verbose` | Enable debug logging |
| `--quiet`, `-q` | Suppress progress output |
| `--json` | Request a typed JSON envelope from a supported command; unsupported commands fail explicitly |
| `--install-completion` | Install shell completion for the current shell |
| `--show-completion` | Print the completion script for the current shell |

Global `--json` is supported by deploy, plan, apply, test, probe, all client commands, fleet status/inventory, node/relay list, and API commands. The same option can follow commands that expose a command-local `--json`. Run `meridian api commands --json` to discover stable machine contracts.

## Common server options

These names recur across server-related commands, but each command exposes only the options shown in its own section:

| Flag | Used by |
|------|---------|
| `--server NAME` | deploy, preflight, scan, test, probe, doctor, teardown |
| `--user`, `-u USER` | deploy, node add/check, relay commands, preflight, scan, doctor, teardown |
| `--sni HOST` | deploy, node add, relay deploy, preflight, test, probe, doctor |
| `--domain DOMAIN` | deploy, node add, preflight, test |

Client commands operate on the cluster panel directly and do not accept `--server`. `test` and `probe` run from the client device and do not use SSH.

## Server resolution

For SSH commands, use a positional target or `--server NAME`. A positional target takes priority where both forms are accepted. With neither, Meridian uses local-mode detection or exactly one eligible saved server; zero or multiple candidates exit `2` and do not open an interactive selector. `test` and `probe` explicitly reject supplying both a positional target and `--server`.
