---
title: Client Management
description: Add users, share connection details, and manage access keys.
---

## Add a client

```
meridian client add alice
```

This gives "alice" their own key and prints two things to share:

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

This prints the connection page and subscription URL, plus status, traffic, expiry, and when the client was last seen. If the page is missing (for example, the upload failed), republish it:

```
meridian client show alice --repair-page
```

## List clients

```
meridian client list
```

Shows every client with status, traffic, expiry, and last seen.

## Limit traffic and set an expiry

Give a client a monthly traffic allowance and an end date:

```
meridian client set alice --limit 50GB --reset month --expires 2026-12-31
```

The same options work when you add someone (`meridian client add alice --limit 50GB --reset month`). Later, only the options you pass change:

```
meridian client set alice --expires 2027-06-30
meridian client set alice --limit none --expires never
```

- `--limit` takes a size such as `500MB`, `50GB`, or `1.5TB` (1 GB is 1024³ bytes, as the control service counts); `none` removes the limit.
- `--reset` starts used traffic from zero every `day`, `week`, or `month`; `never` keeps counting.
- `--expires` takes a date (access ends when that day ends, in UTC), an ISO time such as `2026-12-31T18:00:00+03:00`, or `never`.

A client who reaches the limit shows as `limited`, and one past the expiry as `expired`. They stay cut off until the next reset, or until you raise the limit or move the date. To give the allowance back right away:

```
meridian client reset-traffic alice
```

`client show` prints where they stand:

```
  Status    ● active
  Traffic   12.4 GB / 50 GB (resets monthly)
  Expires   2026-12-31
```

Apply, redeploys, and fleet changes never change a client's limits or expiry.

## Limit devices

Device limits are **off by default**: Meridian reads and stores no device information. Choose `private` or `full` in **Settings → Privacy** when you need a cap. The privacy level and each person's cap live in the control service; applies leave them alone.

- **Private:** Each person's devices are counted by an unreadable fingerprint. Meridian stores no device names, systems, or apps. Someone who takes the control server can only check whether a device they already have was used.
- **Full:** Meridian stores each device's ID, system, model, and app, and when it last fetched the subscription. Someone who takes the control server learns which devices each person uses. Addresses are never stored.

Private mode also stores no first/last fetch times. Its fingerprints ride encrypted snapshots, but their fleet key stays on the control server. After a restore, private registrations are forgotten; devices register again on their next subscription refresh. Lowering from full to private replaces IDs with fingerprints and deletes their metadata; switching off deletes all registrations.

Set a cap from 1 to 100, at creation or later. `default` uses the fleet cap (initially 3):

```
meridian client add alice --devices 2
meridian client set alice --devices 4
meridian client set alice --devices default
```

Read or forget registrations:

```
meridian client devices alice
meridian client devices alice --json
meridian client devices alice --remove ID
meridian client devices alice --clear
```

Private mode shows only “N of M devices”; JSON also returns fingerprint IDs for removing one. Full mode lists IDs, systems and versions, models, apps, and first/last fetch times. The person's console view has the same count or list, with a clear button and, in full mode, individual remove buttons.

A known device stays allowed when you lower its cap. A new device over the cap gets a subscription placeholder asking the person to remove a device or request a higher cap. Counting needs a valid `x-hwid` header from the app; browsers, connection-page fetches, and apps that send no supported HWID are always served and never counted. Removing a registration frees a slot; the device can register again on its next refresh. Limits govern subscription refreshes and do not revoke a configuration already imported into an app. `client reissue` replaces the credentials and clears all registrations while keeping the cap. Remnawave's device registrations are never imported by `migrate`.

## Suspend and resume a client

```
meridian client disable alice
meridian client enable alice
```

`disable` cuts the client off immediately; `enable` lets them back in with the same links.

A disable lasts until `client enable`: apply, redeploys, and fleet changes leave it alone.

## Replacing a leaked link

If a client's link or connection page reaches someone it shouldn't, replace it:

```
meridian client reissue alice
```

The control service gives the client a new subscription ID and new keys. The old subscription URL and connection page return 404, and the servers refuse the old keys. Meridian deletes the old page, publishes a new one, and prints the new page and subscription URL to send the client. They import the new link once: their apps can't follow a subscription URL that no longer exists. Status, traffic limit and usage, expiry, and the device cap stay. Registered devices are cleared.

With `--json`, pass `--yes`. The new links appear only in that command's output.

## Removing a client

`meridian client remove NAME` removes the client permanently: their links stop working and their connection page is deleted. A fleet keeps at least one client.

## Subscription links

Subscription URLs look like `https://198.51.100.10/<page-path>/sub/<id>`. They live under the connection-page path; the control service's console stays on a separate secret path that shared links never reveal.

Every subscription asks apps to refresh it every 6 hours (the `profile-update-interval` header, which apps such as Happ and Clash Verge follow), so when you add an exit or relay, clients pick it up within a few hours without a new link. Apps that ignore the header refresh on their own schedule; a manual refresh in the app picks up changes at once.

An app's support button opens that person's connection page, with its "Not connecting?" help.

### Tested apps and engines

One subscription URL serves each app the format it reads; Stash gets a profile in its own dialect. Every change to Meridian checks each format the control service serves with the engine those apps are built on:

| Format | Apps | Engine tested | What the lab checks |
|--------|------|---------------|---------------------|
| Xray JSON | Happ, v2rayNG, v2rayN, Streisand, V2Box | Xray 26.7.28 | Traffic passes through every entry and through automatic failover; a wrong key is refused |
| Mihomo | FlClashX, Clash Mi | Mihomo 1.19.32 | `mihomo -t` loads the profile, its rules, and its fallback group |
| sing-box | sing-box (SFA, SFI), Karing | sing-box 1.14.3 | `sing-box check` loads the profile, and its automatic group is the default route |
| Base64 links | other Xray/V2Ray apps (v2RayTun, Shadowrocket) | — | Every link parses and names the same servers and key as the other formats |

Apps ship their own engine updates, so an app may run a slightly older or newer engine than the one tested.

### BitTorrent in Xray apps

Xray JSON subscriptions (Happ and other Xray-based apps) send recognized BitTorrent peer connections directly from the device, so torrents use the person's own connection instead of the exit's address. This is best effort: encrypted peers and UDP DHT/uTP may still go through the VPN. Mihomo, sing-box, and Base64 subscriptions don't get this rule.

## Where client data lives

Client accounts (keys, status, limits, traffic) live in the control service's database on your control server. The list of clients Meridian manages is part of your topology in `~/.config/meridian/cluster.yml`. Client commands use the control service's API and don't need SSH; publishing a connection page uses SSH to the control server. After every change, your machine saves an encrypted snapshot of that database (see [Backups](/en/deploy/#backups)).

## Web console

The control service has a web console where you can manage people from a browser, including your phone. `meridian panel` prints its address and, the first time, a one-time setup link where you choose the owner's password. You can add a sign-in code from an authenticator app afterwards, in **Settings**.

| Change in the console | Shown read-only (change with `meridian`) |
|---|---|
| **People**: add, share a link and QR code, enable or disable, set expiry, traffic limit and reset period, reset traffic, issue a new link, remove | **Servers**: every exit and gateway, whether its agent is online and up to date, its version, and its traffic |
| **Tokens**: create and revoke API tokens | **Revisions**: every change applied from your computer, with its plan |
| Your password and sign-in code | The subscription title shown in apps |

Changes in the console and in the CLI are the same changes: `meridian client list` shows people added in the console too, and applies never undo a status, limit, or expiry set in either. People you added with `meridian client add` stay yours on the CLI: the console can change their limits and status, but removing them or issuing a new link prints the `meridian client` command instead, because their connection page lives on the server. People added in the console get a subscription link and QR code, but no connection page.

The console never holds SSH keys, so a stolen session can't change your servers. Every sign-in expires after 30 minutes idle and 12 hours at most, and creating a token asks for your password again.

### API tokens

Scripts and community bots, such as a Telegram bot that tells people how much traffic they have left, talk to the control service's API. Give each one its own token:

1. Open the console (`meridian panel` prints the address), go to **Tokens**, and choose **Create token**.
2. Name it after the bot and pick **Read**. The console shows the token once; store it with the bot's secrets.
3. Point the bot at the console address and send the token as `Authorization: Bearer <token>`. API paths go below that address, for example `https://198.51.100.10/<secret-path>/api/users/alice`. The address contains the secret path, so keep it as private as the token.

A read token sees:

| Endpoint | Returns |
|---|---|
| `GET /api/users/{username}` | One person's status, traffic used and limit, reset period, and expiry |
| `GET /api/users` | Every person |
| `GET /api/traffic` | Traffic totals per person |
| `GET /api/health` | The service's version |

Read tokens never see anyone's keys or subscription ID. Choose **People** for a bot that manages accounts and shares connection credentials. Creating it requires the same password confirmation as any other console token.

| Scope | Access |
|---|---|
| `read` | View the fleet and people without connection credentials |
| `propose` | Read access, plus proposing revisions |
| `approve` | Propose access, plus approving other callers' revisions |
| `people` | Read and manage people, including their UUIDs and subscription tokens |
| `admin` | Both sets of permissions, including topology writes, enrollment, tokens, and backups; issued through the deployer's API access |

`people` is separate from the read → propose → approve chain. It can access only `/api/users`: it cannot read or change nodes, endpoints, the apply fence, revisions, backups, enrollment, API tokens, privacy, or settings. Admin includes people access.

| People endpoint | Action |
|---|---|
| `POST /api/users` | Create a person with `username`, `status`, `expire_at`, `traffic_limit_bytes`, `traffic_limit_strategy`, and `device_limit` in one transaction |
| `PATCH /api/users/{username}` | Change the status, expiry, traffic limit or reset strategy, or device cap; omitted fields keep their values |
| `POST /api/users/{username}/reset-traffic` | Reset used traffic |
| `POST /api/users/{username}/reissue` | Replace connection credentials and clear device registrations |
| `DELETE /api/users/{username}` | Remove a person |
| `GET /api/users/{username}/devices` | Read registrations allowed by the owner's privacy level |
| `DELETE /api/users/{username}/devices[/{device_key}]` | Clear all registrations, or remove one |

A people token cannot remove or reissue a person declared in the fleet file: the API returns `409 declared_user` with the CLI command to run. This also applies while an approved or failed apply still declares the person. An unapproved proposal does not declare anyone. Status, limits, expiry, and device caps remain editable.

Tokens don't expire on their own: revoke one in **Tokens** and anything using it loses access at once. Treat a people token as a password, because it grants access to people's connection credentials.
