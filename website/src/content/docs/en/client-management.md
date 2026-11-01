---
title: Client Management
description: Manage people from the web panel or CLI, share connection pages, and set limits and access keys.
---

You can manage people from any browser in the built-in [web panel](#web-panel) (`meridian panel`) or from the terminal with `meridian client`. Both work on the same accounts in the control service.

## Add a client

In the web panel, open **People** and click **Add person** to create an account, set an optional traffic limit, expiry date, device cap, language, note, and tag, and copy their connection page link or QR code.

From the terminal:

```
meridian client add alice
```

This gives "alice" their own key and prints two things to share:

- **A connection page link** with a QR code — the easiest thing to send
- **A subscription URL** for importing directly into a VPN app

Add several people at once:

```
meridian client add alice bob charlie
```

Clients belong to your whole fleet, not to one server. Every exit and relay you have shows up in their subscription.

### What the recipient sees

The connection page is built from the person's real subscription, so it lists exactly what their apps receive: direct exits, relays, and every enabled method. It has:

- Three steps: install an app recommended for their platform (Happ first, then FlClashX and Clash Mi, plus Throne on desktop), then add the connection with one tap, then follow the app’s Connect instructions
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

This prints the personal URL, plus status, traffic, expiry, and when the client was last seen. The same URL opens the connection page in a browser and supplies configs to an app. The control service serves pages automatically for every person, including those added in the web panel.

## List clients

```
meridian client list
```

People, `client list`, and bulk selection use one server query: username/display-name search, status, tag, expiry within 1–365 days, quota share at or above 1–100%, never used, attention reason, and lifecycle (`pending`, `renewing`, `fixed`, `unlimited`). Counts respect search and the other filters. Sort by name, period usage, expiry, or last activity; missing dates stay last. The console shows 100 rows per page and preserves row identity during refresh. Filters and order stay in the URL fragment. Query parameters are sent to the service, never saved as request history. Cursor pages exclude people inserted after the first page and expire after 15 minutes; returning to the first page starts a fresh view.

Background refreshes keep selection, bulk actions, pagination, and keyboard focus available. A failed refresh keeps the current rows and controls usable; failed Next or Previous requests retain the last loaded page so the pager can retry.

A lost commit or undo reply is checked by reading the receipt; undo is confirmed only when the receipt records that it was undone. The console keeps that updated receipt and reads it again when you reopen it. `client bulk undo ID --dry-run` checks that the receipt is reversible, has not been undone, and is still within its undo window without sending a write; the final undo also checks live people and inbound capacity.

A separate **Stopped using** reason (`stopped`) flags a person who previously used the service but has no accepted traffic for more than three days. **Never used** (`unused`) remains a separate reason. `stopped_days` is an independent API threshold from 1–365 days, default 3; `unused_days` keeps its existing bounds and default. Period resets do not erase this evidence, and daily history can stay off. Imported use with no known last traffic time is not guessed to have stopped.

## Private notes and tags

Keep a private note and one tag for each person, including people added in the web panel:

```bash
meridian client set sample --note "Temporary invitation" --tag TRIAL_7
meridian client show sample
meridian client list --tag TRIAL_7
meridian client set sample --note "" --tag none
```

Notes are plain text of up to 500 characters and may contain newlines. Tags use 1–16 Latin letters, digits, or underscores and are saved in uppercase (`family` becomes `FAMILY`). Empty notes and `--tag none` clear the fields; omitted options preserve them. `client list` has a tag column, and `--tag none` finds people without a tag.

The web panel's person view edits the same fields. The API accepts `PATCH /api/users` with `username`, `note`, and `tag`, or `PATCH /api/users/NAME` without `username`. These fields require `people` or `admin` scope to read and edit; lower scopes receive empty strings. They live in the control service, ride encrypted snapshots with the person, and stay out of subscriptions, connection pages, events, and logs. Applies preserve operator edits. Migration carries Remnawave descriptions and valid tags, reporting descriptions truncated to 500 characters and invalid tags left behind.

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

The app’s `Subscription-Userinfo` header, `/info`, the API’s `period_traffic`, and the web panel share the same period upload/download counters. Both manual and scheduled resets clear the period while leaving lifetime totals untouched. Imports and older periods with no recoverable split keep every byte: unclassified use is included in download, exposed as `unattributed_bytes` (`trafficUnattributedBytes` on `/info`), and disclosed in the panel until the next reset. A native lifetime period with no reset keeps its known split.

`client show` prints where they stand:

```
  Status    ● active
  Traffic   12.4 GB / 50 GB (resets monthly)
  Expires   2026-12-31
```

Apply, redeploys, and fleet changes never change a client's limits or expiry.

## Account schedules

Renewals and anniversary resets clear both period upload and download, including imported use with an unknown direction. Lifetime traffic stays intact.

`--reset month` resets on the first day of each UTC month. `--reset month_rolling` resets on the account’s monthly anniversary: its stored UTC anchor day defaults to the creation day. Set it with `--reset-day 1–31`, or use `default` to return to the creation day. Day 31 becomes February 28 or 29, then March 31. After downtime, the service resets once before counting new reported traffic. Remnawave migration keeps `MONTH_ROLLING` and the original creation-day anchor; a missing creation date is refused.

`--duration N` gives 1–3650 days from first use, with each day lasting 24 hours in UTC. Only the first accepted traffic reported by an agent activates it; opening the connection page or fetching a subscription never does. Before activation, the CLI, panel, and page say “valid for 30 days from first use”; afterwards they show the actual expiry. Choose a duration or `--expires`, not both. Setting a duration on an already-used account uses its recorded first traffic; if that historical time is unknown, set a fixed expiry. `--duration none` clears the duration while keeping an existing activated expiry. Extending an unused account adds days to its pending duration without activating it.

```bash
meridian client add example --duration 30 --limit 50GiB --reset month_rolling --reset-day 31
meridian client set example --renew-months 1 --renewals 3 --dry-run --json
meridian client set example --renew-months 1 --renewals 3 --yes --json
meridian client set example --renew-days 7 --renewals unlimited --yes
meridian client set example --no-renew --yes
```

Automatic renewal uses `--renew-days N` (1–3650 UTC days) or `--renew-months N` (1–120 calendar months). It advances from the original expiry, retaining its UTC time and original month day through short months. `--renewals N` caps the series at 1–10000 renewals; `unlimited` removes the cap. Every missed boundary counts toward the cap; downtime is caught up in one transaction and resets period traffic once, preserving lifetime traffic. A disabled account still follows its schedule and stays disabled. A limited account becomes active only when expiry and quota allow it. Pending accounts wait for first use. The CLI, panel, and page show the next reset, next renewal, and renewals left.

`client set --dry-run` reads a service-calculated date preview and saves nothing. JSON returns `data.schedule_preview` with `applied: false`. Schedule edits require `--yes` in JSON mode; interactive edits show the dates before confirmation. `--no-renew` turns renewal off. Changing the expiry, duration, or renewal interval starts a new series; editing only the cap preserves the completed count. A manual extension after activation also starts a new series. The panel’s expiry and limit editors offer the same choices, with browser-local dates and exact UTC. These fields survive fleet documents, import batches, encrypted backup and restore; later applies preserve operator edits.

`POST /api/users/preview` requires people/admin scope or the authenticated owner session. Omit `username` to preview creation; include it to preview an edit. Requests use typed `traffic_reset_anchor_day`, `activation_duration_days`, `renewal_interval_days`, `renewal_interval_months`, `renewal_limit`, `expire_at`, and `traffic_limit_strategy`. The same settings travel in `POST /api/users` and `PATCH /api/users/{username}`. Omitted fields preserve values; explicit null clears a choice. Read-only `renewals_completed`, `renewal_anchor_at`, and `schedule` describe progress and computed dates. Previews store no account changes, device data, or request details.

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
meridian client set alice --devices unlimited
```

Use `--devices unlimited` (or `0`) to exempt a person from the fleet cap. `--devices default` inherits it; these are separate choices in the panel. The API uses `device_limit: 0` for unlimited and `null` for inheritance. The privacy level still applies. Remnawave migration preserves each cap, including zero, without importing device registrations.

Read or forget registrations:

```
meridian client devices alice
meridian client devices alice --json
meridian client devices alice --remove ID
meridian client devices alice --clear
```

If the owner enables device limits, Your account shows used / allowed device slots. When full, new devices get a limit notice on subscription refresh. Apps that do not report a device are not counted. Only counts appear; they are not saved in your browser.

Private mode shows only “N of M devices”; JSON also returns fingerprint IDs for removing one. Full mode lists IDs, systems and versions, models, apps, and first/last fetch times. The person's view in the web panel has the same count or list, with a clear button and, in full mode, individual remove buttons.

A known device stays allowed when you lower its cap. A new device over the cap gets a subscription placeholder asking the person to remove a device or request a higher cap. Counting needs a valid `x-hwid` header from the app; browsers, connection-page fetches, and apps that send no supported HWID are always served and never counted. Removing a registration frees a slot; the device can register again on its next refresh. Limits govern subscription refreshes and do not revoke a configuration already imported into an app. `client reissue` replaces the credentials and clears all registrations while keeping the cap. Remnawave's device registrations are never imported by `migrate`.

## Suspend and resume a client

```
meridian client disable alice
meridian client enable alice
```

A disable blocks new connections after each node applies the change; `enable` restores the same links. The person view shows which nodes have confirmed blocking.

A disable lasts until `client enable`: apply, redeploys, and fleet changes leave it alone.


Live rules block only the revoked credential on its inbound. Active credentials still follow normal DNS, private and country IP blocks, and IP egress rules. Re-enabling the same credential removes its block; a newly rotated credential never matches an older block. Restarting Xray ends old sessions and discards their temporary rules and pending retries.

One Xray run retains at most 4096 credential/inbound block rules. At the limit, credentials are still removed, but the node stays pending and asks you to restart Xray. This bounds retained state and never blocks active credentials.


The agent writes the new credentials to `core.json` before changing live accounts. A restart during apply then loads the current keys; if the core instance changes around the write, the agent reconciles it before confirming the change.


Revocation receipts show the old key generation and the reason on each node. Separate pending generations remain visible; restoring the same key clears its matching receipt, even when an import advanced the counter. Private key fingerprints stay in the service database and never appear in the API, events, or console. Older confirmed receipts are discarded when a newer receipt exists.


A node confirms revocation from fresh checks of its running core, bound to its applied document. A core restart closes the old sessions, so a fresh core needs no temporary revocation rules and clears an earlier pending guard status.


A fresh report confirms a receipt when its applied revision is at or after the revocation revision in the same fleet epoch. Unrelated revisions and agent hellos preserve that evidence. Hellos do not refresh the age of the last runtime report; stale reports stay pending, and flow events are emitted only when a receipt changes status.

## Rotate connection keys keeping the link

Use `meridian client rotate-credentials NAME --yes` when connection credentials need replacing. It generates a new VLESS UUID and Hysteria2 auth while keeping the subscription token, page URL, device registrations, status, limits, expiry, traffic, notes, tags, and language. Apps receive the keys on their next subscription refresh; ask the person to refresh now. Old identities lose new streams as each node applies the change. A leaked subscription URL requires `client reissue`, which issues a new URL and clears device registrations.

```bash
meridian client rotate-credentials alice --yes
```

The web panel offers **Rotate connection keys** for people added there. Declared people use the CLI, following the same ownership rule as reissue. CLI JSON requires `--yes` and returns `client-rotate-credentials`, including the generation and per-node `flow_blocks`. Optional `--cut-off` also ends existing flows with the disclosed shared-inbound reset or node-wide core restart.


## Replacing a leaked link

If a client's link or connection page reaches someone it shouldn't, replace it:

```
meridian client reissue alice
```

In the web panel, reissue and removal use the same review: the session cut-off checkbox starts off, affected nodes are listed, and you confirm before anything changes. Repeated reissue clicks submit one change. Reissue refreshes the new link and shows per-node results separately; a failed or timed-out cut-off does not undo the new credentials. Declared people show the CLI command.

The control service gives the client a new subscription ID and new keys. The old subscription URL and connection page return 404, and the servers refuse the old keys. Meridian prints the new subscription URL, which also opens the new connection page. They import the new link once: their apps can't follow a subscription URL that no longer exists. Status, traffic limit and usage, expiry, and the device cap stay. Registered devices are cleared.

With `--json`, pass `--yes`. The new links appear only in that command's output.

## Removing a client

`meridian client remove NAME` removes the client permanently: their links stop working and their connection page stops responding. A fleet keeps at least one client.

Disable, expiry, quota suspension, removal, and credential replacement block new TCP streams and UDP associations for the affected identity, including new streams on an already authenticated Hysteria2 QUIC or VLESS mux connection. Other people keep their sessions. The person view and `GET /api/users/{username}/flow-blocks` report each node: `blocked`, `pending_node`, or `agent_too_old`. Pending nodes can keep serving their previous configuration until they receive and apply the change; this includes control-service outages. Update an old agent with `meridian apply`. Events record the node and outcome without addresses or device data.

A TCP flow dispatched before revocation, a Hysteria2 UDP association, or a VLESS XUDP association may continue until it ends. The pinned core has no per-user close operation. **`--cut-off`** on `client remove`, `client reissue`, or `client rotate-credentials` waits for the credential change to apply. The review lists each node: a core restart reconnects everyone using any Xray inbound or bridge on that node; an inbound reset reconnects users of the listed inbounds. If a reset fails, the fallback core restart reconnects every inbound and bridge on that node. VLESS nodes use a core restart; Hysteria2-only nodes reset the affected inbounds. JSON reports `done`, `core_restart`, `failed`, or `timed_out` under `data.cut_off`. The wait is up to five minutes; failures exit `4`, timeouts `3`. The web panel offers the same choice for removal, reissue, and rotation of service-owned people.

## Subscription links

Xray JSON lists **Automatic** first, then one named profile for each primary connection. Each named profile keeps the subscription's routing rules and uses only that connection. Fingerprint alternatives remain in separate automatic groups.

Subscription URLs look like `https://198.51.100.10/<page-path>/sub/<id>`. They live under the connection-page path; the control service's web panel stays on a separate secret path that shared links never reveal.

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

Client accounts (keys, status, limits, traffic) live in the control service's database on your control server. The list of clients Meridian manages is part of your topology in `~/.config/meridian/cluster.yml`. Client commands use the control service's API, and the service serves connection pages directly from its database. After every change, your machine saves an encrypted snapshot of that database (see [Backups](/en/deploy/#backups)).

## Web panel

The control service includes a built-in web panel (also called the management panel or owner console) where you can manage people from any browser, including your phone. `meridian panel` prints its address and, the first time, a one-time setup link where you choose the owner's password. You can add a sign-in code from an authenticator app afterwards, in **Settings**.

| Change in the web panel | Shown read-only (change with `meridian`) |
|---|---|
| **People**: add, share a link and QR code, enable or disable, set expiry, traffic limit and reset period, device cap, language, private note and tag, reset traffic, clear devices, issue a new link, remove (with optional session cut-off) | **Servers**: every exit, relay, and gateway, whether its agent is online and up to date, its version, traffic, monthly allowance, and payment date; **Restart Xray** on a node |
| **Settings**: default page and app language, owner notice for everyone, subscription format preview, and **Privacy** (device limits and daily usage history) | **Revisions** and **Settings**: every change applied from your computer, plus the subscription title, client routing policy, and public owner contact |
| **Tokens**: create and revoke scoped API tokens, password, and authenticator sign-in code | |

Changes in the web panel and in the CLI are the same changes: `meridian client list` shows people added in the web panel too, and applies never undo a status, limit, or expiry set in either. People you added with `meridian client add` stay yours on the CLI: the web panel can change their limits and status, but removing them or issuing a new link prints the `meridian client` command instead, because those people belong to the reviewed topology. Every person, including those added in the web panel, gets a connection page at their subscription URL.

The web panel never holds SSH keys, so a stolen session can't change your servers. Every sign-in expires after 30 minutes idle and 12 hours at most, and creating a token asks for your password again.

Changed person and password forms ask **Leave** or **Stay** before navigation, browser back, sign-out, or dialog dismissal. Reverting the fields removes the prompt; saving clears only that form's edits. Drafts stay in memory and are never saved in browser storage. Person status and usage refresh every 30 seconds while visible, preserving edited inputs and focus. Passive refresh does not extend the owner session's idle timeout.

Expiry editing uses native **Date** and **Time** fields in your browser's time zone and shows the exact UTC instant before saving. **+1 month** and **+3 months** fill a draft from the later of now and the account's current expiry, retaining UTC time and clamping the day to the end of a short month. Save applies only edited fields. **No end date** saves `null`; a new finite expiry must be in the future and before 2099.

The subscription QR and each individual QR offer **Download PNG** and **Download SVG**, captioned with the person's name and your fleet title. **Copy QR image** appears where image clipboard support is available; if copying is refused, the PNG downloads instead. Export uses local browser rendering and makes no outside request.

Open **Individual connections** beneath the main subscription handoff to copy a named connection or show its QR code in subscription order. The disclosure starts closed and also lists explicit Xray JSON, Mihomo, and sing-box URLs. These links and QR images grant access as that person; keep them private. `meridian client show NAME --connections` and `GET /api/users/{username}/connections` show the same list. The API requires people/admin scope or an owner session; ordinary read tokens cannot read the credentials.

Person actions name the person in their result. Enabling someone whose access is expired or whose quota is exhausted explains what still prevents connection. A result arriving after you navigate leaves the new screen in place.

### API tokens

Scripts and community bots, such as a Telegram bot that tells people how much traffic they have left, talk to the control service's API. Give each one its own token:

1. Open the web panel (`meridian panel` prints the address), go to **Tokens**, and choose **Create token**.
2. Name it after the bot and pick **Read**. The panel shows the token once; store it with the bot's secrets.
3. Point the bot at the panel address and send the token as `Authorization: Bearer <token>`. API paths go below that address, for example `https://198.51.100.10/<secret-path>/api/users/alice`. The address contains the secret path, so keep it as private as the token.

#### OpenAPI contract

Fetch **`GET /api/openapi.json`** below the panel address with the same `Authorization: Bearer <token>` header. Any valid API token can read the document, including `people`; expired or revoked tokens receive `401`. It is an English **OpenAPI 3.1** contract with the installed service's version, request and response schemas, errors, and required scopes. A checked-in copy lives at `contracts/meridian/v1/admin/openapi.json`; when using that copy, set your client's base URL to the panel address, including the secret path.

`x-meridian-scopes` lists alternative required scopes. `x-meridian-credentials: true` on an operation and its successful response means that response can carry credentials: keep it private and do not log it. User reads carry connection credentials only for `people` or `admin`; read tokens receive empty UUIDs and subscription tokens. Endpoint responses may carry a transport password, and encrypted snapshots contain credentials too. Agent enrollment, agent protocol, subscriptions, pages, and web panel sessions have separate surfaces and are excluded.

The operation tables below are checked against the contract. A read token sees:

| Endpoint | Returns |
|---|---|
| `GET /api/users/{username}` | One person's status, traffic used and limit, reset period, and expiry |
| `GET /api/users/{username}/flow-blocks` | Per-node evidence for blocking new flows; no connection addresses or device data |
| `GET /api/users` | Every person |
| `GET /api/traffic` | Traffic totals per person |
| `GET /api/health` | The service's version |
| `GET /api/openapi.json` | The admin API contract |
| `GET /api/events` | A bounded page of local lifecycle events |
| `GET /api/attention` | Derived account, node, and server warnings |

Read tokens never see anyone's keys or subscription ID. Choose **People** for a bot that manages accounts and shares connection credentials. Creating it requires the same password confirmation as any other web panel token.

| Scope | Access |
|---|---|
| `read` | View the fleet and people without connection credentials |
| `propose` | Read access, plus proposing revisions |
| `approve` | Propose access, plus approving other callers' revisions |
| `people` | Read and manage people, including their UUIDs and subscription tokens |
| `admin` | Both sets of permissions, including topology writes, enrollment, tokens, and backups; issued through the deployer's API access |

`people` is separate from the read → propose → approve chain. It can access `/api/users` and read `/api/openapi.json`: it cannot read or change nodes, endpoints, the apply fence, revisions, backups, enrollment, API tokens, privacy, or settings. Admin includes people access.

| People endpoint | Action |
|---|---|
| `GET /api/people` | Find people and return cursor pages and contextual counts |
| `POST /api/people/bulk/preview` | Freeze exact usernames or a query without account or receipt writes |
| `POST /api/people/bulk/commit` | Commit preview_id and preview_hash atomically; name stale people |
| `GET /api/people/bulk/receipts/{receipt_id}` | Read a receipt without notes or credentials |
| `POST /api/people/bulk/undo` | Undo unchanged reversible fields once within ten minutes |
| `POST /api/users/preview` | Preview creation or an edit’s UTC schedule dates without saving; people/admin or owner session |
| `POST /api/users` | Create a person with `username`, `status`, `expire_at`, `traffic_limit_bytes`, `traffic_limit_strategy`, and `device_limit` in one transaction |
| `PATCH /api/users/{username}` | Change the status, expiry, traffic limit or reset strategy, or device cap; omitted fields keep their values |
| `POST /api/users/{username}/reset-traffic` | Reset used traffic |
| `POST /api/users/{username}/extend` | Send `{"days": N}` (1–3650) to add days after the later of now and the current expiry; no expiry starts from now |
| `POST /api/users/{username}/reissue` | Replace connection credentials and clear device registrations |
| `POST /api/users/{username}/rotate-credentials` | Rotate the VLESS UUID and Hysteria2 auth while preserving the subscription URL and registrations; send `expected_generation` from the current user record |
| `DELETE /api/users/{username}` | Remove a person |
| `GET /api/users/{username}/devices` | Read registrations allowed by the owner's privacy level |
| `DELETE /api/users/{username}/devices` | Clear all registrations |
| `DELETE /api/users/{username}/devices/{device_key}` | Remove one registration |

Read `credential_generation` from `GET /api/users/{username}`, then send `{"expected_generation": N}` to `POST /api/users/{username}/rotate-credentials`. A stale generation returns `409 credentials_changed`, so an uncertain retry cannot rotate twice. The response contains `user`, `subscription_preserved: true`, `flow_blocks`, and `commands`. Adding `"cut_off": true` requires admin and queues the same revision-gated hard cut-off as reissue. Owner and people callers cannot rotate a declared person.

A people token cannot remove or reissue a person declared in the fleet file: the API returns `409 declared_user` with the CLI command to run. This also applies while an approved or failed apply still declares the person. An unapproved proposal does not declare anyone. Status, limits, expiry, and device caps remain editable.

Reissue and deletion accept optional `{"cut_off": true}`; rotation includes it alongside `expected_generation` in its request. This requires `admin`, as declared by `x-meridian-conditional-scopes`. Review each node's scope: core restarts affect every Xray inbound and bridge, while inbound resets affect the listed inbounds and can fall back to a whole-core restart. Reissue/deletion keep their usual response without a cut-off and include queued commands with it; rotation always returns its command list and receipts.

Choose **7, 30, or 90 days**, or **Never expires**; new web panel tokens default to 90 days. The list shows the expiry and marks expired tokens. Expiry and revocation both refuse the next request with the same `401 unauthenticated` response. Existing tokens keep no expiry. Treat a people token as a password, because it grants access to people's connection credentials.

`POST /api/tokens` accepts `expires_in_days` from 1 to 3650, or `null` for no expiry. Omitting it also means no expiry. You may instead supply a future UTC `expires_at`; choose one expiry field. `TokenRecord.expires_at` is always returned, including `null`. Token operations require admin API access or an owner web panel session; creation in the web panel also requires password confirmation. A people token cannot mint tokens. The `token-create`, `token-record`, `token-issued`, and `token-list` schemas are available through `meridian api schema NAME`.

## Owner contact on connection pages

`meridian fleet contact` reads the contact saved in your reviewed fleet intent. Set public instructions and a contact link with a review first:

```bash
meridian fleet contact --text "Ask for help" --link mailto:help@example.com --dry-run --json
meridian fleet contact --text "Ask for help" --link mailto:help@example.com --yes
meridian fleet contact --text "" --link none --yes
```

Instructions are one line of plain text, up to 120 characters. Links must use `https:`, `tg:`, or `mailto:`; Meridian validates and canonicalizes them. The fields are independent: omitted options keep their values, `--text ""` clears instructions, and `--link none` clears the link. A JSON change requires `--yes`; a dry run and a declined review preserve your fleet. Export/import carries `delivery.owner_contact.text` and `delivery.owner_contact.link`. Settings in the web panel shows the contact with the CLI command to change it.

The connection page's help section and the support page show these public instructions and a **Contact owner** link in all four interface languages. The link opens only when clicked; the pages use local assets. When contact is configured, the support page keeps it visible and offers **Open connection page**. Apps receive the link in `support-url`; without a link, that header keeps its local support-page URL. The personal page URL stays unchanged.

Inactive subscriptions may append the instructions to their status remarks when every remark still fits the 100-character bound. Longer instructions leave the placeholders unchanged and remain visible on the pages.

## Notices from the owner

```bash
meridian fleet notice set "Hello {name}: maintenance tonight" --until 2027-01-01T00:00:00Z --for person --yes
meridian fleet notice show --for person --json
meridian fleet notice clear --yes
```

One service-owned notice appears as a banner on connection pages and in apps that support the `announce` subscription header. Use one paragraph of plain text, up to 500 Unicode characters. The only substitutions are `{name}`, `{expires}`, `{days_left}`, `{used}`, and `{quota}`; unknown or malformed braces are refused with the allowed list. Substitution happens once for each person before UTF-8 Base64 encoding, with the `base64:` prefix apps expect. Rendered notices are bounded to 1,024 characters.

`--until` accepts a future UTC instant or a date (end of that day in UTC); omit it for no expiry. Expired notices stop being delivered. `--for NAME` renders real account values for the confirmation; without it the preview uses sample values. `show --for NAME` reads the saved notice and its preview. Saving and clearing require confirmation, and JSON changes require `--yes`. Settings in the web panel offers the same editor and rendered confirmation.

These are operator settings: reviewed applies preserve them. `GET /api/notice?for=NAME` needs read scope; `PUT /api/notice` needs admin or the owner session and accepts `{"notice":{"text":"Hello {name}","until":null},"for_user":"person","confirm":false}` to preview. Set `confirm` to `true` to save, or `notice` to `null` to clear. Previews do not change storage or register devices. Encrypted snapshots include the notice; `notice.changed` events record who changed it without its text. No push or messaging notification is sent.

## Default language

```bash
meridian fleet language ru --yes --json
meridian fleet language auto --yes --json
meridian client set person --language fa --json
meridian client set person --language default --json
```

The default is `auto`: connection and support pages follow each person's browser, while subscription status messages in apps stay in English. Choose `en`, `ru`, `fa`, or `zh` to set the starting language of pages and app messages. `meridian fleet language` reads the setting; a code saves it immediately, and JSON changes require `--yes`. Settings in the web panel offers the same choice.

Set one person's language with `client set NAME --language CODE` or the language select in their web panel settings. `--language default` follows the fleet again; an omitted flag preserves the current override. Client list/show JSON includes the nullable `language` field.

A saved page choice wins, then the person override, then the fleet default. Only `auto` uses the browser, falling back to English if its language is unsupported. People can always switch with the page's language buttons.

These service settings survive reviewed applies and encrypted snapshots. `GET /api/language` needs read scope and returns, for example, `{"language":"auto","changed":false}`. `PUT /api/language` with `{"language":"ru"}` or `{"language":"auto"}` needs admin or the owner session. `PATCH /api/users/NAME` accepts `{"language":"fa"}` or `{"language":null}` under people/admin access. Status codes remain `active`, `disabled`, `limited`, and `expired`; the fixed explanatory connections remain inert.

## Preview an app's subscription format

```bash
meridian client preview person --user-agent "Hiddify/2" --accept application/json --json
meridian client preview person --user-agent "Hiddify/2" --suffix json --json
```

The preview explains the selected format and the first matching rule: an explicit URL suffix, an app family from User-Agent, `Accept: text/html`, or the default Base64 answer. It lists included protocols and entries, plus every omitted endpoint and its reason: unsupported protocol or fingerprint copy, disabled endpoint, account status placeholder, or no primary connections. It uses the same selector and filters as live delivery. A browser page and `/info` account JSON are identified separately.

Two apps earn one inert extra entry at the end of their subscription, in the person's language: v2rayNG older than 1.9.17 (the first release with XHTTP) is asked to update, and desktop NekoRay (no longer maintained; Throne is its successor) is asked to switch. The decision reads only that request's User-Agent: nothing is stored or logged, and device limits, privacy levels, and format selection do not change. A suspended subscription or one with no connections shows only its status entries. The preview reports `stale_client` (`outdated` or `abandoned`) as a separate reason; the User-Agent is still not echoed.

Response headers are redacted: URLs are replaced, and encoded profile titles and notices show only their `base64:` prefix. The preview returns no body, URIs, keys, private notes, or tags. User-Agent is bounded to 512 Unicode characters and Accept to 256, each one line. Inputs are transient: they are neither stored nor echoed, never register a device, and trigger no backup write.

`POST /api/subscription/preview` needs read scope or the owner session. Send `{"username":"person","user_agent":"Hiddify/2","accept":"application/json","suffix":""}`; omitted headers default to empty strings. Settings in the web panel has the same form. No HWID or device-header input is accepted. For API-managed endpoints, `enabled: false` excludes the saved endpoint from subscriptions and appears as `disabled_endpoint`; true is the default and is omitted from serialized endpoints. A reviewed endpoint apply restores topology-defined delivery.

## Review changes for many people

Use Tab and Space to select rows; Shift+Space extends the selection on the current page. **Select all matching** includes other pages. Changing filters clears selection. Choose an action, review each person's before/after and warnings in pages of 100, and inspect the count of changed node documents. A preview freezes exact people and row versions for ten minutes; new matches are excluded. Any changed person, enforcement result, or node specification refuses the whole commit. Stale people are named. Every change and receipt lands in one transaction, with one document composition for the batch.

For bulk expiry changes, choose **No end date** to clear the expiry. When that choice is unchecked, the date and time must be valid, in the future, and before 2099; a blank or invalid value stops the preview and never clears anyone's expiry.

Extend by whole days or calendar months from the later of now and expiry, with short months clamped. Pending first-use durations grow without activation; calendar months add the days between the preview's UTC date and that date after the selected months. Set or clear expiry, quota and reset strategy, tag, device cap (inherit/unlimited/N), or renewal; enable, disable, reset period traffic, or delete. Omitted fields preserve values; reset strategy and renewal cap can be edited independently. The existing node contract permits 10,000 active people per managed inbound; previews reject changes that exceed it.

Durable receipts retain action, parameters, counts, and per-person outcomes. Reversible fields can be undone once within ten minutes, even after a service restart, only while people and the reviewed enforcement outcome remain unchanged. Traffic reset, deletion, and schedule changes that reset traffic are irreversible and disclosed before commit. Resets preserve lifetime totals. Receipts contain no private notes, credentials, client addresses, or device identifiers. Events contain the action and counts plus the existing content-free per-person events. People declared in `topology_intent.access.users` may receive service-owned edits; their deletion returns the exact `meridian client remove USERNAME` command for a separate topology review and never deletes them in the service batch.

`GET /api/people` requires people/admin or an owner session. It returns `users`, `total`, `population`, contextual `facets`, and `next_cursor`. Query keys: `q`, `status`, `tag`, `expires`, `quota`, `never` (0/1), `attention`, `lifecycle`, `sort`, `order`, `limit` (1–500), `cursor`. Keep filters and order when continuing a cursor. The plain `GET /api/users` route remains available. Query responses omit credentials and private notes; CLI JSON retains its existing note view through the authorized per-person read.

When bulk disabling, deletion, or an expiry change removes access, the service records the same flow-block receipts for each person and affected node as an individual edit. Connection keys and subscription URLs stay unchanged. New streams are blocked after the node confirms the applied document; transfers and UDP sessions started earlier may continue. Deleting an account keeps its revocation receipts so later node reports can confirm blocking.
