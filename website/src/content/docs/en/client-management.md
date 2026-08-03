---
title: Client Management
description: Add users, share connection details, and manage access keys.
order: 5
section: guides
---

## Add a client

```
meridian client add alice
```

This creates a unique connection key for "alice" and displays the canonical subscription URL. Legacy deployments also show a QR code and self-hosted connection-page URL when the page upload has been verified. V4 access is managed through `topology_intent`, so no legacy PWA is fabricated.

Pass multiple names to add several clients at once:

```
meridian client add alice bob charlie
```

Each client gets their own key. Legacy batch failures are reported per client and successful creations are kept.

### What the recipient sees

When a legacy connection page is available, its shareable URL opens a page with:
- Step-by-step instructions for installing a VPN app (v2RayTun, v2rayNG, Hiddify, or v2rayN)
- QR codes for each connection protocol
- One-tap "Open in App" deep links
- Connection status and usage stats

Send the URL by email, iMessage, Telegram, or any messenger. The recipient opens it, installs the app, scans the QR code, and connects. No technical knowledge needed.

## Show connection details

To re-display connection info for an existing client at any time:

```
meridian client show alice
```

This shows the usable subscription and any evidenced legacy share-page link without creating a new key. If a legacy page is missing, repair it explicitly:

```bash
meridian client show alice --repair-page
```

Use `client show` when:
- You need to re-share the connection page with someone
- You lost the original QR code or URL
- You want to verify what a client's connection looks like

## List clients

```
meridian client list
```

Shows managed clients with status, traffic, creation time, and last-seen metadata. On V4, the list is restricted to `topology_intent.access.users`; internal routing accounts never appear.

## Remove a client

```
meridian client remove alice
```

Legacy deployments revoke access immediately. V4 refuses direct removal with exit `2` because safe managed-user retirement is not implemented yet. Use `client disable` for immediate temporary revocation, which lasts until the next `meridian apply`.

## Suspend a client

```
meridian client disable alice
```

Temporarily blocks the client without deleting keys. On V4, the next topology apply restores every declared access user, so `disable` is an operational pause rather than durable desired state.

To re-enable: `meridian client enable alice`

## Re-enable a client

```
meridian client enable alice
```

Resumes a previously suspended client. They can connect again immediately using their existing keys and subscription URL.

## Where credentials are stored

Meridian stores fleet topology locally in `~/.meridian/cluster.yml` — panel URL, API token, admin credentials, nodes, and relays. Client state (users, UUIDs, traffic) lives in the Remnawave panel's PostgreSQL database, which is the source of truth.

```
~/.meridian/cluster.yml                 # fleet topology + panel access
```

Routine client state uses the panel REST API. Legacy connection-page upload and explicit `--repair-page` also require SSH to the panel host; subscription-only V4 operations do not.

`meridian fleet recover` can import one legacy shared profile after local state is lost. It cannot reconstruct V4 topology intent; restore a backup or rerun `meridian setup` for V4 deployments.

## Web panel

Meridian deploys the [Remnawave](https://remna.st/) admin panel for traffic monitoring, user management, and advanced configuration. It is reverse-proxied by nginx at a randomized HTTPS path — no SSH tunnel needed. Find the URL and admin credentials in `~/.meridian/cluster.yml`:

```
grep -A6 "^panel:" ~/.meridian/cluster.yml
```

Relevant fields:

```yaml
panel:
  url: https://<your-server-ip>/<secret_path>/
  admin_user: admin
  admin_pass: <generated>
  api_token: <JWT used by Meridian CLI>
  secret_path: <random>
  sub_path: <random>   # subscription page path
```

Open `url` in a browser and log in with `admin_user` / `admin_pass`.

Panel-side edits surface as drift. V4 converges against compiled `topology_intent`; legacy deployments may use the optional `desired_*` fields. Review `meridian plan` before applying.

## How it works

Each Meridian client is a single Remnawave user (one UUID in the `users` table). The user is assigned to Meridian's default Internal Squad, which grants visibility over every inbound the panel knows about (`vless-reality`, `vless-xhttp`, and `vless-xhttp-ws` in domain mode). The subscription URL — `https://<ip>/<sub_path>/<short_uuid>` — is served by the Remnawave subscription-page container and contains all inbound endpoints the client can use.

Client apps (v2rayNG, Streisand, Hiddify, sing-box) treat the subscription URL as a single source of truth: refreshing it pulls in new inbounds when you deploy a new exit, add a relay, or rotate Reality keys.

## Declarative client list

V4 stores managed users in `topology_intent.access.users`. Use `client add` to expand that intent and apply it. Direct V4 removal is intentionally unavailable until safe managed-user retirement is implemented; do not delete the panel user directly.

Legacy clusters can opt into the older `desired_clients` list in `~/.meridian/cluster.yml`:

```yaml
desired_clients:
  - alice
  - bob
  - charlie
```

Then `meridian plan` shows the legacy diff against the panel and `meridian apply` converges it. This field is not the authority for V4 topology.
