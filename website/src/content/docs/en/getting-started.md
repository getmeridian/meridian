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
6. **Deploys the legacy share page** when using the deploy flow; V4 setup hands off the canonical subscription directly

## Where things live

Meridian connects to the VPS via SSH (or runs directly on it with `deploy local`). After deploy, state is cached locally:

| What | Where |
|------|-------|
| Topology, panel token, and keys | `~/.meridian/cluster.yml` on your machine |
| SSH server profiles | `~/.meridian/servers.json` on your machine |
| Proxy services | Docker, Xray, nginx on the VPS |

When you run `meridian client add alice`, Meridian uses the Remnawave API recorded in `cluster.yml`; client operations do not require SSH. With multiple servers, target a specific SSH host with `--server NAME` for server-touching commands.

## Connect

The deploy command outputs a subscription URL and, when its legacy page upload succeeds, a shareable PWA URL with QR codes and app links. V4 setup always returns the canonical subscription and does not claim a legacy page.

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

Each client gets their own key. List clients with `meridian client list`. Legacy clients can be revoked with `client remove`; V4 managed-user retirement is not yet supported, so use `client disable` for immediate temporary revocation.

## Manage SSH profiles

Save reusable SSH targets for diagnostics and maintenance:

```
meridian server list                # view all managed servers
meridian server add 198.51.100.11  # add an existing server
meridian server remove finland     # remove from registry
```

Server-touching commands such as `preflight`, `doctor`, and `teardown` accept `--server NAME`. Client commands always use the cluster's panel and do not accept `--server`.

## Declarative workflow

V4 stores the reviewed fleet model as `topology_intent` in `cluster.yml`. Build or revise it with `meridian setup`; the intent covers the control plane, exits, routing chains, protocol paths, and access users. Run `plan` to preview compiled resource changes and `apply` to converge them.

```
meridian plan                      # preview compiled resource changes
meridian apply --yes               # converge the reviewed topology
```

`meridian plan` exits `0` when the cluster is converged, `2` when changes are pending — so you can gate CI workflows on it. Use `meridian plan --json` to inspect the typed plan, and `meridian apply --json --yes` when a process or UI client needs the final execution result. Both use the `meridian.output/v1` envelope. See [CLI reference](/docs/en/cli-reference/#meridian-plan) for full options.

Legacy clusters retain optional `desired_nodes`, `desired_relays`, and `desired_clients` reconciliation. Those fields are not V4 authority. For an annotated state reference, see [`cluster.example.yml`](https://github.com/getmeridian/meridian/blob/v4/cluster.example.yml).

## Next steps

- [Deploy guide](/docs/en/deploy/) — full deployment walkthrough with all options
- [Relay nodes](/docs/en/relay/) — route through a domestic IP for resilience when the exit IP gets blocked
- [Domain mode](/docs/en/domain-mode/) — add CDN fallback via Cloudflare
- [IP blocked?](/docs/en/recovery/) — step-by-step recovery when your server gets blocked
- [Troubleshooting](/docs/en/troubleshooting/) — common issues and fixes
