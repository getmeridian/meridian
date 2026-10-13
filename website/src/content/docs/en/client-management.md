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

This gives "alice" her own key and prints two things to share:

- **A connection page link** with a QR code — the easiest thing to send
- **A subscription URL** for importing directly into an Xray/V2Ray app

Add several people at once:

```
meridian client add alice bob charlie
```

Clients belong to your whole fleet, not to one server. Every exit and relay you have shows up in their subscription.

### What the recipient sees

The connection page is built from the person's real subscription, so it lists exactly the paths their apps receive: direct exits, relays, and Hysteria2 when enabled. It has:

- Step-by-step instructions for installing an app (v2RayTun, v2rayNG, Hiddify, or v2rayN)
- A QR code for each connection path
- One-tap "Open in app" links

Send the link through any messenger. The recipient opens it, installs the app, scans the QR code, and connects.

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

A disable is temporary: the next apply turns every declared client back on. Apply runs for `meridian apply`, a redeploy, and when you add clients, nodes, or relays. Disable again after those if needed.

## Removing a client

`meridian client remove NAME` removes the client permanently: their links stop working and their connection page is deleted. A fleet keeps at least one client. Don't delete users in the Remnawave panel directly — Meridian recreates any client still in the topology on the next apply.

## Subscription links

Subscription URLs look like `https://198.51.100.10/<page-path>/sub/<id>`. They live under the connection-page path; the Remnawave admin panel stays on a separate secret path that shared links never reveal.

Apps refresh subscriptions on their own, so when you add an exit or relay, clients pick it up without a new link.

## Where client data lives

Client accounts (keys, status, traffic) live in the Remnawave panel database on your control-plane server. The list of clients Meridian manages is part of your topology in `~/.meridian/cluster.yml`. Client commands use the panel API and don't need SSH; publishing a connection page uses SSH to the control-plane server.

## Web panel

The [Remnawave](https://remna.st/) admin panel shows traffic, users, and nodes in a browser, and you are welcome to use it. `meridian panel` prints its URL and admin username; `meridian panel --show-password` also prints the password.

Meridian and the panel split the work so your edits don't get overwritten:

| Meridian manages (the next apply restores its version) | Yours to change (apply never touches these) |
|---|---|
| Nodes, config profiles, inbounds, published Hosts, squads | Each client's status: enable/disable, traffic limits, expiry, HWID limits, description |
| Subscription templates and the settings Meridian sets | Users you create yourself in the panel |
| Which clients exist, their names, and their squads | Notifications, plugins, IP control, and other panel settings |

`meridian plan` shows any difference in what Meridian manages before anything changes. Users you add in the panel are never listed, changed, or deleted by Meridian; to give someone a Meridian-managed link and connection page, use `meridian client add` instead.
