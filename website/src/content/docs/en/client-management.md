---
title: Client Management
description: Add users, share connection details, and manage access keys.
---

## Add a client

```
meridian client add alice
```

This gives "alice" her own key and prints two things to share:

- **A connection page link** with a QR code — the easiest thing to send
- **A subscription URL** for importing directly into an Xray/V2Ray app

Add several people at once:

```
meridian client add alice bob charlie
```

Clients belong to your whole fleet, not to one server. Every exit and relay you have shows up in their subscription.

### What the recipient sees

The connection page is built from the person's real subscription, so it lists exactly what their apps receive: direct exits, relays, and every enabled method. It has:

- Two steps: install an app recommended for their platform (Happ first, then FlClashX and Clash Mi, plus Throne on desktop), then add the connection with one tap
- A QR code for the subscription, shown beside the steps on a computer so they can set up a phone
- Every individual connection with its own Open, Copy, and QR buttons, folded away until needed
- A "Not connecting?" section with tips and a "Check my connection" test that copies a link-free report they can send you

In the app, entries are named by the exit's country and method: "🇩🇪 DE · Reality · Chrome", "🇩🇪 DE · XHTTP · Chrome", "🇩🇪 DE · WebSocket · CDN", "🇩🇪 DE · Hysteria2 · UDP", with "via relay" added for relay entries. Two exits in one country show their names after the flag ("🇩🇪 main"); an exit without a known country shows its name when you have several. Reality and XHTTP also come as "· Firefox" copies with another TLS fingerprint, for when one fingerprint gets blocked. Apps that switch automatically (Xray JSON apps such as Happ, Mihomo, and sing-box) pick a working entry by themselves but never switch between fingerprints; Happ shows a separate "Automatic · Firefox" entry for that.

Send the link through any messenger. The recipient opens it, installs the app, taps "Add to …" (or scans the QR code from another device), and connects. [Connecting](/en/connecting/) walks them through it and through what to do when it stops working; send them that page too.

## Show connection details

To share a client's links again:

```
meridian client show alice
```

This prints the connection page and subscription URL, plus status, traffic, and when the client was last seen. If the page is missing (for example, the upload failed), republish it:

```
meridian client show alice --repair-page
```

## List clients

```
meridian client list
```

Shows every client with status, traffic, creation time, and last seen.

## Suspend and resume a client

```
meridian client disable alice
meridian client enable alice
```

`disable` cuts the client off immediately; `enable` lets them back in with the same links.

A disable lasts until `client enable`: apply, redeploys, and fleet changes leave it alone.

## Removing a client

`meridian client remove NAME` removes the client permanently: their links stop working and their connection page is deleted. A fleet keeps at least one client. Don't delete users in the Remnawave panel directly — Meridian recreates any client still in the topology on the next apply.

## Subscription links

Subscription URLs look like `https://198.51.100.10/<page-path>/sub/<id>`. They live under the connection-page path; the Remnawave admin panel stays on a separate secret path that shared links never reveal.

Every subscription asks apps to refresh it every 6 hours (the `profile-update-interval` header, which apps such as Happ and Clash Verge follow), so when you add an exit or relay, clients pick it up within a few hours without a new link. Apps that ignore the header refresh on their own schedule; a manual refresh in the app picks up changes at once.

### Tested apps and engines

One subscription URL serves each app the format it reads. Every change to Meridian runs a multi-node test lab that fetches each format from a real Remnawave panel and checks it with the engine those apps are built on:

| Format | Apps | Engine tested | What the lab checks |
|--------|------|---------------|---------------------|
| Xray JSON | Happ | Xray 26.7.28 | Traffic passes through every entry and through automatic failover; a wrong key is refused |
| Mihomo | FlClashX, Clash Mi | Mihomo 1.19.32 | `mihomo -t` loads the profile, its rules, and its fallback group |
| sing-box | sing-box (SFA, SFI), Karing | sing-box 1.14.3 | `sing-box check` loads the profile, and its automatic group is the default route |
| Base64 links | any Xray/V2Ray app | — | Every link parses and names the same servers and key as the other formats |

Apps ship their own engine updates, so an app may run a slightly older or newer engine than the one tested.

## Where client data lives

Client accounts (keys, status, traffic) live in the Remnawave panel database on your control-plane server. The list of clients Meridian manages is part of your topology in `~/.config/meridian/cluster.yml`. Client commands use the panel API and don't need SSH; publishing a connection page uses SSH to the control-plane server.

## Web panel

The [Remnawave](https://remna.st/) admin panel shows traffic, users, and nodes in a browser, and you are welcome to use it. `meridian panel` prints its URL and admin username; `meridian panel --show-password` also prints the password.

Meridian and the panel split the work so your edits don't get overwritten:

| Meridian manages (the next apply restores its version) | Yours to change (apply never touches these) |
|---|---|
| Nodes, config profiles, inbounds, published Hosts, squads | Each client's status: enable/disable, traffic limits, expiry, HWID limits, description |
| Subscription templates and the settings Meridian sets (title, refresh interval) | Users you create yourself in the panel |
| Which clients exist, their names, and their squads | Notifications, plugins, IP control, and other panel settings |

`meridian plan` shows any difference in what Meridian manages before anything changes. Users you add in the panel are never listed, changed, or deleted by Meridian; to give someone a Meridian-managed link and connection page, use `meridian client add` instead.

### Node plugins

Remnawave's node plugins (Ingress Filter, Egress Filter, Torrent Blocker, Connection Drop) are optional and live in the panel. To use one, open **Node Plugins** in the admin UI, create a plugin configuration, turn on the plugins you want, and attach it to your nodes. Meridian already runs every node with the `NET_ADMIN` capability the plugins need, and never changes or removes your plugin configurations.

The plugins write nftables rules on the host, so upstream also requires a Linux kernel 5.7 or newer; on an older one they silently do nothing. `meridian check` reports this as **Node plugin readiness** on every exit and gateway, and warns about an old kernel (move to Ubuntu 22.04+ or Debian 11+). If you don't use plugins, you can ignore that warning.

Torrent Blocker recognizes BitTorrent through traffic sniffing, which Meridian turns on (route-only, so destinations are never rewritten) on every inbound it renders. Turn the plugin on in the panel and it works on every exit.

### Connect a bot to the panel

Community bots, such as a Telegram bot that tells people how much traffic they have left, talk to the Remnawave API directly. Give each bot its own token with only the access it needs:

1. Open the panel (`meridian panel` prints the URL), go to **Remnawave Settings → API tokens**, and choose **Create API token**.
2. Name the token after the bot and set **Expires in (days)** to a period you will actually rotate, such as 30 or 90 days.
3. Start from the **Read only** preset, or pick single endpoints, and grant only what the bot uses. The panel shows the token once; store it with the bot's secrets.
4. Point the bot at the panel URL from `meridian panel` and send the token as `Authorization: Bearer <token>`. API paths go below that URL, for example `https://198.51.100.10/<secret-path>/api/users/by-username/alice`. The URL contains the panel's secret path, so keep it as private as the token.

Scopes are named `resource:endpoint`; `resource:read` grants every read endpoint of a resource and `resource:write` every write. A typical read-only bot needs:

| Scope | Endpoint | Returns |
|---|---|---|
| `users:by-username` | `GET /api/users/by-username/{username}` | One user's status, traffic used and limit, and expiry |
| `users:by-short-uuid` | `GET /api/users/by-short-uuid/{shortUuid}` | The same, found from the ID in the user's subscription link |
| `users:list` | `GET /api/users` | Every user, paginated |
| `system:stats` | `GET /api/system/stats` | Panel-wide user and traffic totals |
| `nodes:list` | `GET /api/nodes` | Each node's status and traffic |

Add a write scope only when the bot changes accounts, for example `users:extend` (`POST /api/users/{id}/actions/extend`) for a payment bot that extends expiry. Status, limits, and expiry are yours to change (see the table above), so the next apply keeps a bot's edits. A bot must not rename or delete Meridian's clients: the next apply recreates them.

Don't hand a bot Meridian's own token (`meridian-provisioner` in the token list). It has full access to the panel and practically never expires, so a leak from the bot's host would expose every node, key, and user. A separate token limits the damage to what the bot can do, expires on its own, and can be deleted without affecting Meridian. Leave `meridian-provisioner` alone; every client and apply command uses it.
