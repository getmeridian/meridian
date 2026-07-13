---
title: Getting Started
description: Install Meridian and deploy your first proxy server in two minutes.
order: 1
section: guides
---

One command sets up an undetectable proxy server with hardened defaults. This guide gets you from zero to a running deployment in about five minutes.

## Prerequisites

You need:
- A **VPS** running Debian or Ubuntu (root SSH key access)
- A **terminal** on your local machine (macOS, Linux, or WSL)

## Install the CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

This installs the `meridian` command via [uv](https://docs.astral.sh/uv/) (preferred) or pipx.

You can run this on your local machine or directly on the VPS. A local machine is recommended — it lets you run `meridian test` from outside the server and keeps credentials on your own device.

## Deploy

```
meridian deploy
```

The interactive wizard asks for your server IP, SSH user, and camouflage target (SNI). Smart defaults are provided for everything.

Or specify everything upfront:

```
meridian deploy 198.51.100.10 --sni www.microsoft.com
```

If you're running directly on the VPS as root, skip SSH entirely:

```
meridian deploy local
```

## What happens

1. **Installs Docker** and deploys Xray managed by the Remnawave panel
2. **Generates x25519 keypair** — unique keys for Reality authentication
3. **Hardens the server** — UFW firewall, SSH key-only auth, BBR congestion control
4. **Configures VLESS+Reality** on port 443 — impersonates a real TLS server
5. **Enables XHTTP transport** — additional stealth layer, routed through nginx
6. **Deploys a shareable PWA** with QR codes and subscription import

## Where things live

Meridian connects to the VPS via SSH (or runs directly on it with `deploy local`). After deploy, state is cached locally:

| What | Where |
|------|-------|
| Topology, panel token, and keys | `~/.meridian/cluster.yml` on your machine |
| SSH server profiles | `~/.meridian/servers.json` on your machine |
| Proxy services | Docker, Xray, nginx on the VPS |

When you run `meridian client add alice`, Meridian uses the Remnawave API recorded in `cluster.yml`; client operations do not require SSH. With multiple servers, target a specific SSH host with `--server NAME` for server-touching commands.

## Connect

The deploy command outputs:
- A **shareable PWA URL** with QR codes and app links
- A **subscription URL** for compatible clients

Install one of these apps, then scan the QR code or tap "Open in App":

| Platform | App |
|----------|-----|
| iOS | [v2RayTun](https://apps.apple.com/app/v2raytun/id6476628951) |
| Android | [v2rayNG](https://github.com/2dust/v2rayNG/releases/latest) |
| Windows | [v2rayN](https://github.com/2dust/v2rayN/releases/latest) |
| All platforms | [Hiddify](https://github.com/hiddify/hiddify-app/releases/latest) |

## Add more users

```
meridian client add alice
```

Each client gets their own key and connection page. List clients with `meridian client list`, revoke with `meridian client remove alice`.

## Manage SSH profiles

Save reusable SSH targets for diagnostics and maintenance:

```
meridian server list                # view all managed servers
meridian server add 198.51.100.11  # add an existing server
meridian server remove finland     # remove from registry
```

Server-touching commands such as `preflight`, `doctor`, and `teardown` accept `--server NAME`. Client commands always use the cluster's panel and do not accept `--server`.

## Declarative workflow

Once you have more than one server, you can manage the whole fleet declaratively. Describe the desired state in `~/.meridian/cluster.yml`, run `meridian plan` to preview the diff, and `meridian apply` to converge.

A minimal desired-state block:

```yaml
desired_nodes:
  - host: 198.51.100.10
    name: germany-1
    sni: www.microsoft.com
  - host: 198.51.100.11
    name: finland-1
    sni: www.microsoft.com

desired_relays:
  - host: 203.0.113.10
    name: moscow-relay
    exit_node: germany-1          # name or IP

desired_clients:
  - alice
  - bob

subscription_page:
  enabled: true                    # self-hosted Remnawave subscription page
```

```
meridian plan                      # shows a Terraform-style diff: + adds, - removes, ~ updates
meridian apply --yes               # converge (skips confirmations; panel-only drift is preserved unless --prune-extras=yes)
```

Each section is independent. Omitting `desired_clients` entirely leaves client management imperative (`meridian client add/remove` still works); listing it with `[]` tells Meridian to remove every client. Same pattern for `desired_nodes` and `desired_relays`.

`meridian plan` exits `0` when the cluster is converged, `2` when changes are pending — so you can gate CI workflows on it. Use `meridian plan --json` to inspect the typed plan, and `meridian apply --json --yes` when a process or UI client needs the final execution result. Both use the `meridian.output/v1` envelope. See [CLI reference](/docs/en/cli-reference/#meridian-plan) for full options.

For an annotated reference of every field cluster.yml accepts (state, desired-state, branding, inbound cache), see the [`cluster.example.yml`](https://github.com/getmeridian/meridian/blob/v4/cluster.example.yml) at the repo root. Imperative commands (`meridian client add`, `meridian node add`, etc.) automatically mirror their effect into the matching `desired_*` list when that list is non-null — so mixing imperative and declarative is safe.

## Next steps

- [Deploy guide](/docs/en/deploy/) — full deployment walkthrough with all options
- [Relay nodes](/docs/en/relay/) — route through a domestic IP for resilience when the exit IP gets blocked
- [Domain mode](/docs/en/domain-mode/) — add CDN fallback via Cloudflare
- [IP blocked?](/docs/en/recovery/) — step-by-step recovery when your server gets blocked
- [Troubleshooting](/docs/en/troubleshooting/) — common issues and fixes
