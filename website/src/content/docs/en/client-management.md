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

This creates a unique connection key for "alice" and displays:
- A **QR code** in the terminal — scan it with a VPN app to connect instantly
- **Connection URLs** — VLESS links for each protocol (Reality, XHTTP, and WSS if domain mode is enabled)
- A **shareable page URL** — hosted on your server, ready to send via any messenger
- An **HTML file** saved locally — backup for offline sharing

Pass multiple names to add several clients at once:

```
meridian client add alice bob charlie
```

Each client gets their own key and connection page. Failures are reported per-client — successfully created clients are kept even if some fail.

### What the recipient sees

The shareable URL opens a connection page with:
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

This outputs the same QR code, connection URLs, and shareable page link — without creating a new key. Use this when:
- You need to re-share the connection page with someone
- You lost the original QR code or HTML file
- You want to verify what a client's connection looks like

## List clients

```
meridian client list
```

Shows all clients with their protocol connections (Reality, XHTTP, WSS).

## Remove a client

```
meridian client remove alice
```

Revokes access immediately. The client's UUID is removed from all inbounds on the server.

## Suspend a client

```
meridian client disable alice
```

Temporarily blocks the client from connecting. Their configuration stays intact — no keys are deleted, and their subscription URL remains valid. Use this to pause access without losing the client's setup.

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

Client commands talk directly to the panel's REST API using the stored API token. No SSH is needed for client operations.

If you need to recover after losing the local file, `meridian fleet recover` rebuilds `cluster.yml` from the live panel API.

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

Panel-side edits (e.g. renaming a user, disabling a host) surface in Meridian as drift — the next `meridian plan` shows the diff between the panel's actual state and your `cluster.yml` desired state. Use `meridian apply` to converge either way.

## How it works

Each Meridian client is a single Remnawave user (one UUID in the `users` table). The user is assigned to Meridian's default Internal Squad, which grants visibility over every inbound the panel knows about (`vless-reality`, `vless-xhttp`, and `vless-xhttp-ws` in domain mode). The subscription URL — `https://<ip>/<sub_path>/<short_uuid>` — is served by the Remnawave subscription-page container and contains all inbound endpoints the client can use.

Client apps (v2rayNG, Streisand, Hiddify, sing-box) treat the subscription URL as a single source of truth: refreshing it pulls in new inbounds when you deploy a new exit, add a relay, or rotate Reality keys.

## Declarative client list

For fleet-wide setups you can manage clients declaratively instead of imperatively. Add a `desired_clients` list to `~/.meridian/cluster.yml`:

```yaml
desired_clients:
  - alice
  - bob
  - charlie
```

Then `meridian plan` shows the diff against the panel's actual user list, and `meridian apply` converges — adds any missing clients, removes any extra ones. `meridian client add/remove` still works alongside this; the two approaches coexist. See the [declarative workflow](/docs/en/getting-started/#declarative-workflow) for the full story.
