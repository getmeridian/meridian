---
title: Client Management
description: Manage people from the web panel or CLI, share connection pages, and set limits and access keys.
---

You can manage people from any browser in the built-in [web panel](#web-panel) (`meridian panel`) or from the terminal with `meridian client`. Both work on the same accounts in the control service.

People are added and removed immediately in the control service, without an apply or SSH. Names can use any script. An exact username wins; otherwise a case-folded username or display name must identify one person. Ambiguous names are refused. A batch add checks every name before writing, then reports one service request per person. Existing names are refused.

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

In **People**, search and filter chips sit together. Open a chip to choose its filter; remove an active chip with its × button, or use **Clear filters** to reset the view. Rows show the name, tag, status, compact period usage, and expiry in words. Last activity, note presence, **From your computer**, and attention reasons sit on a second line. Click anywhere in a row or focus it and press Enter to open the person. Phones show cards, with one horizontally scrolling filter row below search; active chips stay visible and removable. **Add person** stays visible in both views; an empty list offers it too, while no matches offers **Clear filters**.

```
meridian client list
```

People, `client list`, and bulk selection use one server query: username/display-name search, status, tag, expiry within 1–365 days, quota share at or above 1–100%, never used, attention reason, and lifecycle (`pending`, `renewing`, `fixed`, `unlimited`). Counts respect search and the other filters. Sort by name, period usage, expiry, or last activity; missing dates stay last. The console shows 100 rows per page and preserves row identity during refresh. Filters and order stay in the URL fragment. Query parameters are sent to the service, never saved as request history. Cursor pages exclude people inserted after the first page and expire after 15 minutes; returning to the first page starts a fresh view.

Filter by **Access profile** in People, `meridian client list --profile limited`, or `meridian client bulk disable --match-profile limited --dry-run`. This matches saved membership; `default` selects people explicitly assigned full fleet access. Profile counts apply search and all other filters, count each person once per profile, and include unused profiles with zero. A person in several profiles is still one row and one bulk target. The all-profiles count is a person count, not the sum of memberships. Profile filtering and counts keep the existing `people.query` plus `people.private` permissions; notes are never searched.

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

The web panel offers **Rotate connection keys** for every person, using the same service operation as the CLI. JSON requires `--yes` and returns `client-rotate-credentials`, including the generation and per-node `flow_blocks`. Optional `--cut-off` also ends existing flows with the disclosed shared-inbound reset or node-wide core restart.


## Replacing a leaked link

If a client's link or connection page reaches someone it shouldn't, replace it:

```
meridian client reissue alice
```

In the web panel, reissue and removal use the same review: the session cut-off checkbox starts off, affected nodes are listed, and you confirm before anything changes. Repeated reissue clicks submit one change. Reissue refreshes the new link and shows per-node results separately; a failed or timed-out cut-off does not undo the new credentials.

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

The control service includes a built-in web panel (also called the management panel or owner console) where you can manage people from any browser, including your phone. `meridian panel` prints its address and, the first time, a one-time setup link where you choose the owner's password. You can add a sign-in code from an authenticator app afterwards, in **Settings → Security**.

The panel opens on **Overview**. A fresh browser uses **Simple** view: Overview, People, Servers, and Settings. **Detailed** adds Revisions and Tokens, a daily traffic chart, and recent revisions and recorded owner edits. The switch in the header remembers the choice in this browser and only changes what is displayed. Shared routes and drafts stay in place; switching to Simple from Revisions or Tokens returns to Overview. A direct link to either screen still opens in Simple, with **Shown in Detailed view** and a button to switch. In Simple, open the labeled disclosures for server capacity and billing. Restart sits under **More**. The **Account** menu holds theme, console language, and sign out; the subscription's default language stays in Settings.

**Settings** has **General**, **Notice**, **Language**, **Privacy**, and **Security** sections. The navigation is a left column on desktop and a horizontally scrolling chip row on phones; arrow keys and Home/End select sections. The fragment identifies the section, such as `#/settings/privacy`; `#/settings` opens General. General shows the title, public address, owner contact, and client routing read-only with copyable `meridian` commands. Privacy keeps both levels, their disclosures, and the change history together. Security holds the password, sign-in code, passkeys, and signed-in devices. **Advanced**, in Detailed, holds the subscription format preview, routing facts, and reviewed app response rules. A direct `#/settings/advanced` link opens it in Simple with **Shown in Detailed view** and a switch; switching from Advanced to Simple returns to Overview through Leave/Stay. Unsaved edits guard section changes; mode and theme changes keep the inputs and focus. Captions stay short, with the full explanations under **Learn more**. Privacy links in history-off notices and recent Settings edits open the relevant section.

On phones, the logo, fleet title, view switch, and **Account** menu share one header row; long fleet titles use an ellipsis. The main navigation scrolls horizontally in both views, with the active page brought into view.

**Tokens** and **Revisions** are in Detailed. Rows show a name and status, followed by muted scope, author, date, and usage facts as relevant. **More** opens a token’s effective permissions or its revocation confirmation; a revision’s **View changes** opens the comparison of recorded intents. An empty Tokens list offers **Create token**; an empty Revisions list points to reviewed changes from your computer. Token creation reviews permissions and exact expiry before issuance, and the secret is shown once without browser storage.

Overview actions sit beside the title on desktop. On phones, a solid bottom bar keeps **Add person** and **Share a link** within reach, with room to scroll the last card clear of it.

Simple server cards put health, country, applied configuration, and counted people traffic first. Open **More** for host metrics and versions; Detailed shows the full fact list. Restart remains under **More** in both views.

Server attention links open **Allowance and payments** and scroll to it in either view. After arrival, refreshes keep your choice to leave the section open or close it.

Overview links to people expiring within seven days, people using at least 90% of their quota, reached traffic limits, disabled access, and servers with offline, missing health, or unapplied configuration reports. It also shows upcoming and overdue server payments; Detailed includes people who have never connected. **Add person** opens the existing form. **Share a link** lets you search and page through people, then opens the same local QR, copy-link, and PNG/SVG export view as the person page. Counted traffic uses the returned window of up to 30 UTC days, bounded by history retention. Disabled or unavailable daily history is shown explicitly and is never treated as zero; server host traffic is separate. Overview refreshes every 30 seconds while visible without extending owner idle time. Failed refreshes keep the last figures and offer retry with backoff; turning daily history off clears its cached window.

On a person's page, the summary stays visible while the selected tab scrolls: name, status, tag, period usage, reset and expiry together below the meter, and **Share**, **Extend**, and **Disable/Enable**. Hover or focus the expiry for the exact browser-local and UTC date. **Share** opens the QR, link, page, PNG/SVG export, image-copy, and individual-connection controls in a dialog.

**Detailed** shows **Access & limits**, **Usage**, **Devices**, **Routing & profiles**, and **Keys & removal**. Tabs stay in the fragment, such as `#/people/sample/usage`; `#/people/sample` opens Access & limits. Arrow keys and Home/End select tabs; phone tabs scroll horizontally. **Simple** shows Access & limits directly without a tab strip; returning here moves keyboard focus to the panel. The note, tag, devices, removal, usage, and routing stay under **More**. A direct link to another tab opens it in Simple with **Shown in Detailed view** and a switch. Switching from a Detailed-only tab to Simple returns to Overview through Leave/Stay.

The current traffic meter appears only in the summary. **Usage** keeps upload and download, lifetime use, reset facts, and daily history. Visible tabs also label their panels; Simple keeps the **Access & limits** heading.

Access & limits starts with read rows. **Edit** opens one inline editor; **Save** sends only that editor's changed fields and closes only that editor. **Cancel**, tab switches, and navigation use Leave/Stay for drafts. Expiry holds the fixed-date, first-use, and renewal choices; Traffic holds the cap, reset period, and monthly reset day. Date and traffic previews update as you edit; reset-day errors appear next to the field. Saving checks the service preview and keeps that editor locked until the reply. Exact UTC is one secondary line in the expiry editor. Untouched editors follow saved changes; edited drafts and focus survive passive refresh and mode changes. Draft previews refresh after edits or saves; passive polling keeps the last reviewed dates.

**Passkeys:** when the panel has a reviewed HTTPS domain, open **Settings → Security → Passkeys → Add passkey**, choose a name, and unlock the key on your device. You can save up to ten keys, rename them, and see when each was added and last used. The sign-in screen offers **Sign in with a passkey** once a key is saved. A PIN, fingerprint, or face check is required; a saved passkey can also confirm a sensitive change. Adding, renaming, and removing keys require identity confirmation within five minutes.

Passkeys use the domain from your reviewed server settings, never a request header. Browsers do not allow them on a bare IP address: use your password and optional authenticator code there. A domain change needs a passkey for the new domain. Requests expire after five minutes and work once. The service keeps the credential ID, public key, counter, chosen name, and times; it stores no device identity or address and makes no external request.

During many simultaneous sign-in attempts, the oldest anonymous passkey prompts may expire; start sign-in again. Registration and identity confirmation in a signed-in session remain available. Each session keeps its own current request for each purpose.

If identity confirmation expires during the device prompt, confirm again; the panel retries the completed request while its five-minute challenge remains valid. A request refused because another key filled the list can also be retried after freeing a slot, within the same deadline.

Passkeys also work for sign-in and identity confirmation when the authenticator returns no account identifier.

Removing a passkey prevents new sign-ins and signs out every other browser session. Your password and sign-in code remain available. If all methods are lost, run `meridian panel --setup-link` on the managing machine: the single-use root-issued recovery link replaces the password, disables TOTP, removes every passkey, and ends every session. Owner credentials, including passkeys, are excluded from snapshots; a restored service needs a fresh setup link.

| Change in the web panel | Shown read-only (change with `meridian`) |
|---|---|
| **People**: add, share a link and QR code, enable or disable, set expiry, traffic limit and reset period, device cap, language, private note and tag, reset traffic, clear devices, issue a new link, remove (with optional session cut-off) | **Servers**: every exit, relay, and gateway, whether its agent is online and up to date, its version, traffic, monthly allowance, and payment date; **Restart Xray** on a node |
| **Settings**: default page and app language, owner notice for everyone, subscription format preview, and **Privacy** (device limits and daily usage history); **Security** holds password, sign-in code, passkeys, and signed-in devices | **Revisions** and **Settings**: every change applied from your computer, plus the subscription title, public address, client routing policy, and public owner contact |
| **Tokens**: create and revoke scoped API tokens | |

The web panel and CLI manage the same service-owned people. `meridian client list` includes people added in either surface. Both can add, remove, reissue and rotate every person; applies never undo status, limits or expiry. Every person gets a connection page at their subscription URL.

The web panel never holds SSH keys, so a stolen session can't change your servers. Every sign-in expires after 30 minutes idle and 12 hours at most, and creating a token requires recent password/TOTP or passkey confirmation.

Changed person and Settings forms ask **Leave** or **Stay** before navigation, browser back, sign-out, or dialog dismissal. Reverting the fields removes the prompt; saving clears only that form's edits. Drafts stay in memory and are never saved in browser storage. Person status and usage refresh every 30 seconds while visible, preserving edited inputs and focus. Passive refresh does not extend the owner session's idle timeout.

Open **Extend → +1 month**, **+3 months**, or **Custom date** in the summary to fill an expiry draft. Opening **Custom date** alone does not create an unsaved change. Unused first-use access extends its pending duration by the service-calculated number of days without starting it. Expiry editing uses native **Date** and **Time** fields in your browser's time zone and shows the exact UTC instant before saving. **+1 month** and **+3 months** fill a draft from the later of now and the account's current expiry, retaining UTC time and clamping the day to the end of a short month. Save applies only edited fields. **No end date** saves `null`; a new finite expiry must be in the future and before 2099.

The subscription QR and each individual QR offer **Download PNG** and **Download SVG**, captioned with the person's name and your fleet title. **Copy QR image** appears where image clipboard support is available; if copying is refused, the PNG downloads instead. Export uses local browser rendering and makes no outside request.

In the **Share** dialog, open **Individual connections** to copy a named connection or show its QR code in subscription order. The disclosure starts closed and also lists explicit Xray JSON, Mihomo, and sing-box URLs. These links and QR images grant access as that person; keep them private. `meridian client show NAME --connections` and `GET /api/users/{username}/connections` show the same list. The API requires people/admin scope or an owner session; ordinary read tokens cannot read the credentials.

Person actions name the person in their result. Enabling someone whose access is expired or whose quota is exhausted explains what still prevents connection. A result arriving after you navigate leaves the new screen in place.

### Server capacity

**Servers** shows logical CPUs, load per CPU, used/total memory, host receive/send rates, and Xray process memory and uptime. Expand **Capacity over the last day** for hourly averages and peak markers. Shaded intervals were not measured; a measured zero stays zero. Host traffic includes overhead and other services; **Counted people traffic** remains a separate aggregate. Capacity collection changes no device or personal-history setting.

Reports are current for 180 seconds from the last accepted collection received by the service. Older agents show **Not reported**, collection failures show **Unavailable**, and old evidence shows **Stale**. The charts cover the current UTC hour and its preceding 23 buckets and disclose the measured network duration. One hour of recent samples stays in memory; 168 hourly buckets survive restarts. See [Privacy](/en/privacy/) for retention and snapshots.

`GET /api/nodes/{node_id}/capacity?hours=24` needs `fleet.read` and accepts one `hours` integer from 1 to 168. Unknown or repeated query options are refused. `GET /api/nodes/{node_id}` and `meridian fleet status --json` carry the same current `capacity` projection; use `meridian api schema node-capacity` for the history contract.

### API tokens

Give each integration its own revocable token. In **Tokens**, select individual actions or a compatible preset, choose an expiry, then click **Review token**. The service shows the effective permissions and exact expiry before **Create token**. Editing the choices requires another review. The secret is shown once and stays out of browser storage; an uncertain response reloads metadata and explains that the secret cannot be recovered.

From the managing machine:

```bash
meridian token capabilities
meridian token create "Account reader" --capability people.read --expires-days 30 --dry-run --json
meridian token create "Account reader" --capability people.read --expires-days 30 --yes --json
meridian token list --json
meridian token revoke TOKEN_ID --yes --json
```

Actions are independent. `people.read` reads account state and device records permitted by the privacy level; it exposes no connection credentials or private notes. `people.private` permits notes/tags, and `people.credentials` permits keys, subscription links, and individual connections. `people.edit`, `people.reset`, `people.reissue`, `people.remove`, `people.devices`, and `people.bulk` separately allow account edits, period resets, rotation, removal, device-registration removal, and credential import. Mutation responses follow the same redaction as reads.

People queries require `people.query` and `people.private`; bulk previews, commits, receipts, and undo require `people.batch` and `people.private`, because these views include tags. Credential import keeps its separate `people.bulk` grant. The people/admin presets retain their previous query and bulk access; read/propose/approve presets cannot call those routes.

`commands.restart` permits Xray unit restarts; other maintenance commands need `commands.run`. Reissue, removal or rotation with `cut_off: true` also needs `commands.cut_off`. All people use the same account permissions.

The console can issue the existing read/propose/approve/people presets and narrow permissions for its service-owned edits and restarts. Fleet administration and backup permissions use the CLI or an authorized API token. Creation requires recent password/TOTP or passkey confirmation. A token can delegate only permissions it holds, cannot outlive its issuer, and a revoked or expired issuer cannot mint through a cached request.

New API, CLI, and console tokens default to **90 days**. `expires_in_days` accepts 1–3650, or use a future UTC `expires_at`. To choose no expiry, explicitly send `never_expires: true` or use `--never-expires`; null alone is refused. Existing expiry values stay unchanged. The compatible `read`, `propose`, `approve`, `people`, and `admin` presets are expanded once into equivalent stored grants; future catalog changes do not re-expand issued tokens. Read presets keep their existing node/endpoint access, including endpoint credentials, while people’s UUIDs and subscription tokens remain blank. A custom token has `scope: null`; every record lists its effective `capabilities`. Expiry and revocation refuse the next request with `401 unauthenticated`. Protect any token that grants credentials like a password.

#### Prometheus metrics

Scrape the existing control ingress with a dedicated **`metrics.read`** token. Select it explicitly in the console’s token review or use the CLI below; compatible presets, including `admin`, keep their frozen grants and do not gain it. The grant reads only node aggregates and service runtime metrics, with no account reads or writes. The unlisted root credential can issue the scraper token.

```bash
meridian token create "Metrics scraper" --capability metrics.read --expires-days 90 --dry-run --json
meridian token create "Metrics scraper" --capability metrics.read --expires-days 90 --yes --json
```

Creation JSON reveals the one-time secret at `data.issued.token`. Save only that value in a file readable by Prometheus, such as `/etc/prometheus/meridian.token`. Replace `CONTROL_PATH` with the private console path from `meridian panel`, and use the control server’s address. HTTPS stays on the existing port 443; keep certificate verification enabled.

```yaml
scrape_configs:
  - job_name: meridian
    scheme: https
    metrics_path: /CONTROL_PATH/metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/meridian.token
    static_configs:
      - targets: ["198.51.100.10:443"]
```

`/metrics` is relative to the private control path, requires a bearer token, supports GET/HEAD, and returns Prometheus 0.0.4 text with `Cache-Control: no-store`. There is no additional listener. Node labels contain only topology IDs and fixed state values; service labels use fixed route IDs, status classes, latency buckets, and task names. No people, addresses, devices, URLs, secret paths, or credentials are exported.

The text includes CPU/load/memory, epoch-local host receive/send counters and rates, Xray uptime/memory, and the existing durable counted-people traffic total per node. Host counters include overhead and other services. `meridian_node_capacity_status` names current/stale/unavailable/not-reported states; nullable or stale numeric readings are omitted. Counter resets start another epoch; initial or unmeasured rates stay absent. Service counters and latency histograms are bounded in memory and reset on restart; task started/healthy/error and success times distinguish missing work from failures. `meridian_control_sqlite_bytes` includes the database, WAL, and shared-memory file.

#### OpenAPI contract

Read **OpenAPI 3.1** from `GET /api/openapi.json`, or the generated `contracts/meridian/v1/admin/openapi.json`. Send `Authorization: Bearer <token>`. All `x-meridian-capabilities` are required; `x-meridian-conditional-capabilities` describes body choices, and `x-meridian-extra-capabilities` describes credential/private-field and self-approval gates. `x-meridian-scopes` and `x-meridian-capability-grants` document compatible presets. `x-meridian-credentials: true` marks responses that may contain credentials: keep them private and do not log them. Agent enrollment/protocol, subscriptions, pages, and console authentication are separate surfaces.

| Operation | Purpose |
|---|---|
| `GET /api/users/{username}/flow-blocks` | Per-node evidence for blocking new flows; no connection addresses or device data |
| `GET /api/users` | Every person |
| `GET /api/traffic` | Traffic totals per person |
| `GET /api/nodes/{node_id}/capacity` | Current server capacity and bounded recent/hourly history; `fleet.read`, `hours=1..168` |
| `GET /api/health` | The service's version |
| `GET /api/openapi.json` | The admin API contract |
| `GET /api/events` | A bounded page of local lifecycle events |
| `GET /api/attention` | Derived account, node, and server warnings |
| `GET /api/token-capabilities` | Actions, compatible presets, and permission labels in all four languages |
| `POST /api/tokens/preview` | Effective permissions and expiry; creates no token, event, or backup pull |
| `POST /api/tokens` | Issue with `name` and either `scope` or a nonempty `capabilities` array |
| `GET /api/tokens` | Metadata without secrets; `tokens.read` |
| `DELETE /api/tokens/{token_id}` | Revoke; `tokens.revoke` |
| `GET /api/users/{username}` | Account state; `people.read`, with separately granted private/credential fields |
| `PATCH /api/users/{username}` | Edited account fields; `people.edit` |
| `POST /api/users/{username}/reset-traffic` | Reset period use; `people.reset` |
| `POST /api/users/{username}/reissue` | Rotate credentials; `people.reissue` |
| `DELETE /api/users/{username}` | Remove; `people.remove` |

The `token-create`, `token-record`, `token-issued`, `token-list`, `token-preview`, and `token-capabilities` schemas are available through `meridian api schema NAME`. CLI creation JSON contains `data.preview` and, after issuance, `data.issued.token`; listings and later responses never recover that secret. Grants have a separate immutable model so later access-profile bounds can compose with them; profile bounds are not yet accepted or silently ignored.

| People endpoint | Action |
|---|---|
| `GET /api/people` | Find people and return cursor pages and contextual counts |
| `POST /api/people/bulk/preview` | Freeze exact usernames or a query without account or receipt writes |
| `POST /api/people/bulk/commit` | Commit preview_id and preview_hash atomically; name stale people |
| `GET /api/people/bulk/receipts/{receipt_id}` | Read a receipt without notes or credentials |
| `POST /api/people/bulk/undo` | Undo unchanged reversible fields once within ten minutes |
| `POST /api/users/preview` | Preview creation or an edit’s UTC schedule dates without saving; people/admin or owner session |
| `POST /api/users` | Create a person with `username`, `display_name`, `status`, `expire_at`, `traffic_limit_bytes`, `traffic_limit_strategy`, and `device_limit` in one transaction |
| `PATCH /api/users/{username}` | Change the status, expiry, traffic limit or reset strategy, or device cap; omitted fields keep their values |
| `POST /api/users/{username}/reset-traffic` | Reset used traffic |
| `POST /api/users/{username}/extend` | Send `{"days": N}` (1–3650) to add days after the later of now and the current expiry; no expiry starts from now |
| `POST /api/users/{username}/reissue` | Replace connection credentials and clear device registrations |
| `POST /api/users/{username}/rotate-credentials` | Rotate the VLESS UUID and Hysteria2 auth while preserving the subscription URL and registrations; send `expected_generation` from the current user record |
| `DELETE /api/users/{username}` | Remove a person |
| `GET /api/users/{username}/devices` | Read registrations allowed by the owner's privacy level |
| `DELETE /api/users/{username}/devices` | Clear all registrations |
| `DELETE /api/users/{username}/devices/{device_key}` | Remove one registration |

`display_name` is a public person name in any script, stored by the service and visible on connection pages, in the panel, and in person reads even without `people.private`. Send a `username`, a nonempty `display_name`, or both to `POST /api/users`; with only a display name, the service assigns a Latin username around existing names. The username stays the stable identifier. `PATCH /api/users/{username}` and `PATCH /api/users` preserve an omitted `display_name`; `"display_name": ""` clears it to show the username. `POST /api/import/users` carries the field too. A name or username matching another person’s name or username after Unicode casefold returns `409 name_conflict` (“Each person needs a different name”); an exact existing username on creation keeps `409 user_exists`. Applies set names only at creation. Bulk previews show names; receipts keep usernames and never store display-name text.

Read `credential_generation` from `GET /api/users/{username}`, then send `{"expected_generation": N}` to `POST /api/users/{username}/rotate-credentials` with `people.reissue`. A stale generation is refused. Rotation preserves the subscription URL and device registrations for every person.

Reissue and deletion accept optional `{"cut_off": true}`; rotation includes it alongside `expected_generation` in its request. This requires `commands.cut_off`, as declared by `x-meridian-conditional-capabilities`; the admin preset includes it. Review each node's scope: core restarts affect every Xray inbound and bridge, while inbound resets affect the listed inbounds and can fall back to a whole-core restart. Reissue/deletion keep their usual response without a cut-off and include queued commands with it; rotation always returns its command list and receipts.

## Owner contact on connection pages

`meridian fleet contact` reads the contact saved in your reviewed fleet intent. Set public instructions and a contact link with a review first:

```bash
meridian fleet contact --text "Ask for help" --link mailto:help@example.com --dry-run --json
meridian fleet contact --text "Ask for help" --link mailto:help@example.com --yes
meridian fleet contact --text "" --link none --yes
```

Instructions are one line of plain text, up to 120 characters. Links must use `https:`, `tg:`, or `mailto:`; Meridian validates and canonicalizes them. The fields are independent: omitted options keep their values, `--text ""` clears instructions, and `--link none` clears the link. A JSON change requires `--yes`; a dry run and a declined review preserve your fleet. Export/import carries `delivery.owner_contact.text` and `delivery.owner_contact.link`. **Settings → General** in the web panel shows the contact with the CLI command to change it.

The connection page's help section and the support page show these public instructions and a **Contact owner** link in all four interface languages. The link opens only when clicked; the pages use local assets. When contact is configured, the support page keeps it visible and offers **Open connection page**. Apps receive the link in `support-url`; without a link, that header keeps its local support-page URL. The personal page URL stays unchanged.

Inactive subscriptions may append the instructions to their status remarks when every remark still fits the 100-character bound. Longer instructions leave the placeholders unchanged and remain visible on the pages.

## Notices from the owner

```bash
meridian fleet notice set "Hello {name}: maintenance tonight" --until 2027-01-01T00:00:00Z --for person --yes
meridian fleet notice show --for person --json
meridian fleet notice clear --yes
```

One service-owned notice appears as a banner on connection pages and in apps that support the `announce` subscription header. Use one paragraph of plain text, up to 500 Unicode characters. The only substitutions are `{name}`, `{expires}`, `{days_left}`, `{used}`, and `{quota}`; unknown or malformed braces are refused with the allowed list. Substitution happens once for each person before UTF-8 Base64 encoding, with the `base64:` prefix apps expect. Rendered notices are bounded to 1,024 characters.

`--until` accepts a future UTC instant or a date (end of that day in UTC); omit it for no expiry. Expired notices stop being delivered. `--for NAME` renders real account values for the confirmation; without it the preview uses sample values. `show --for NAME` reads the saved notice and its preview. Saving and clearing require confirmation, and JSON changes require `--yes`. **Settings → Notice** in the web panel offers the same editor and rendered confirmation.

These are operator settings: reviewed applies preserve them. `GET /api/notice?for=NAME` needs read scope; `PUT /api/notice` needs admin or the owner session and accepts `{"notice":{"text":"Hello {name}","until":null},"for_user":"person","confirm":false}` to preview. Set `confirm` to `true` to save, or `notice` to `null` to clear. Previews do not change storage or register devices. Encrypted snapshots include the notice; `notice.changed` events record who changed it without its text. No push or messaging notification is sent.

## Default language

```bash
meridian fleet language ru --yes --json
meridian fleet language auto --yes --json
meridian client set person --language fa --json
meridian client set person --language default --json
```

The default is `auto`: connection and support pages follow each person's browser, while subscription status messages in apps stay in English. Choose `en`, `ru`, `fa`, or `zh` to set the starting language of pages and app messages. `meridian fleet language` reads the setting; a code saves it immediately, and JSON changes require `--yes`. **Settings → Language** in the web panel offers the same choice.

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

`POST /api/subscription/preview` accepts read or people scope, or the owner session. When access profiles are configured, it requires people/admin or the owner session. Send `{"username":"person","user_agent":"Hiddify/2","accept":"application/json","suffix":""}`; omitted headers default to empty strings. **Settings → Advanced** in the web panel has the same form. No HWID or device-header input is accepted. For API-managed endpoints, `enabled: false` excludes the saved endpoint from subscriptions and appears as `disabled_endpoint`; true is the default and is omitted from serialized endpoints. A reviewed endpoint apply restores topology-defined delivery.

The subscription preview also identifies reviewed app rules, their conditions, and refusal outcomes. **Settings → Advanced** lists response rules read-only; leave Person blank in the tester to read fleet defaults. Edit `delivery.response_rules` through a reviewed fleet import. See [Response rules by app](/en/split-routing/#response-rules-by-app) for the typed fields, header limits, and policy precedence.

## Review changes for many people

In **Simple** view, click **Select** to show checkboxes; **Done selecting** clears the selection and hides them. **Detailed** shows checkboxes directly. The action bar appears only after selection: as one compact row at the top of the list on desktop and at the bottom on phones, with space reserved above the safe area. **Extend** and **Disable** are direct actions; the other bulk actions sit under **More**. Select every row on the page before **Select all N matching** appears. Mode switches and background refreshes keep the selection. The **Operation receipt** button stays in the list header after selection clears.

Use Tab and Space to select rows; Shift+Space extends the selection on the current page. **Select all matching** includes other pages. Changing filters clears selection. Choose an action, review each person's before/after and warnings in pages of 100, and inspect the count of changed node documents. A preview freezes exact people and row versions for ten minutes; new matches are excluded. Any changed person, enforcement result, or node specification refuses the whole commit. Stale people are named. Every change and receipt lands in one transaction, with one document composition for the batch.

For bulk expiry changes, choose **No end date** to clear the expiry. When that choice is unchecked, the date and time must be valid, in the future, and before 2099; a blank or invalid value stops the preview and never clears anyone's expiry.

Extend by whole days or calendar months from the later of now and expiry, with short months clamped. Pending first-use durations grow without activation; calendar months add the days between the preview's UTC date and that date after the selected months. Set or clear expiry, quota and reset strategy, tag, device cap (inherit/unlimited/N), or renewal; enable, disable, reset period traffic, or delete. Omitted fields preserve values; reset strategy and renewal cap can be edited independently. The existing node contract permits 10,000 active people per managed inbound; previews reject changes that exceed it.

Bulk previews freeze the selected people and row versions for ten minutes. Commit applies every reviewed change in one transaction and returns a durable receipt without notes or credentials. Reversible edits may be undone once within ten minutes; deletion and traffic reset are irreversible. Every person has the same service ownership.

People are added and removed immediately in the control service, without an apply or SSH. Names can use any script. An exact username wins; otherwise a case-folded username or display name must identify one person. Ambiguous names are refused. A batch add checks every name before writing, then reports one service request per person. Existing names are refused.

Use `meridian client remove sample-a sample-b --dry-run --json` to preview, then `meridian client remove sample-a sample-b --yes --json` to commit. Declining the review writes nothing. Zero people is allowed; `meridian test` without an active person reports unavailable evidence (exit 3). `--proposal` is retired. Use single-person `client remove NAME --cut-off` to disclose and close existing sessions; earlier transfers may otherwise continue.

`GET /api/people` requires people/admin or an owner session. It returns `users`, `total`, `population`, contextual `facets`, and `next_cursor`. Query keys: `q`, `status`, `tag`, `profile`, `expires`, `quota`, `never` (0/1), `attention`, `lifecycle`, `sort`, `order`, `limit` (1–500), `cursor`. Keep filters and order when continuing a cursor. The plain `GET /api/users` route remains available. Query responses omit credentials and private notes; CLI JSON retains its existing note view through the authorized per-person read.

When bulk disabling, deletion, or an expiry change removes access, the service records the same flow-block receipts for each person and affected node as an individual edit. Connection keys and subscription URLs stay unchanged. New streams are blocked after the node confirms the applied document; transfers and UDP sessions started earlier may continue. Deleting an account keeps its revocation receipts so later node reports can confirm blocking.

`has_notes` in `GET /api/people` rows reports private note presence with a boolean; missing or null means presence was not reported. The query never returns note text and needs no extra person reads.

## Access profiles

Any non-default membership requires review, even when the live profile map and declaration fingerprint are missing. Previews and settings writes refuse a map that omits a profile still assigned to a person. Restore the missing profile through a reviewed edit, or assign the affected people to other profiles first.

Profiles are reviewed topology; membership belongs to the operator in the service. The built-in `default` profile grants every exit and path, including future additions. A person may belong to several profiles; their grants are combined. Fleets without named profiles keep the same node documents and subscriptions.

Access profiles require Meridian's control service. On a Remnawave fleet, run `meridian migrate` first; adding, importing, or applying profiles is refused before server or panel changes.

Single-person `client set --profile` and bulk `client bulk set-profiles` also refuse Remnawave before looking up people or contacting a service, including `--dry-run` and assignment to `default`.

Create or replace grants with `meridian profile add|set`; `profile list` shows grants and membership counts. References are `exit:ID`, `gateway:ID`, or the compiler's stable `inbound:...` and `endpoint:...` IDs. Unknown and retired references fail review. Gateway bridge inbounds cannot be assigned to people. A public endpoint grants its authentication inbound, so every other public connection sharing that inbound is granted too. `--empty` creates a profile with no connections.

Use `client set person --profile limited --yes`, repeating `--profile` for every membership to keep. This replaces the membership list; choose `default` to restore full access. Profile edits have their own review, separate from account limits, notes, and language. The access-profile section in **Routing & profiles** shows granted connections and the profiles that permit them, preserves unsaved choices, and opens the same frozen review. **Set profiles** is also a bulk action; its preview shows gained and lost inbounds and rejects changed people or topology at commit.

Single and bulk profile previews, `--dry-run`, and undo dry runs resolve each person's delivery policy before returning a review. Conflicting profile defaults are refused unless the person has a compatible private override. Initial memberships in setup creation seeds are validated before review and again during atomic service creation. An apply never replaces an existing person's memberships.

The CLI reports a service access error if it cannot read the person. A profile loss review prints the revocation warning once.

Bulk undo checks the saved people, grants, and nodes before previewing or writing. Changed access or a deleted profile refuses the whole undo and names the affected people, including with `--dry-run`. Undo previews show the reversed gains, losses, and hard cut-off scope. The API accepts `{"receipt_id":"...","dry_run":true}` at `POST /api/people/bulk/undo`, returning projected `undo_people` without a database change, event, or backup pull. Older receipts without saved access preconditions require a new bulk review.

Losing a grant removes the connection from every subscription format, individual links, and the connection page, and removes the person from the node's inbound. After that node applies the revision, the existing per-person flow block stops new connections and new streams from open sessions. Connection keys, subscription URLs, and device registrations stay the same. Transfers and UDP associations already dispatched may continue. The review names the affected nodes and the hard cut-off boundary: a VLESS cut-off restarts Xray and reconnects every inbound and bridge on that node; an inbound reset affects the listed inbounds, with whole-core fallback on failure. A reviewed `meridian node restart NODE --core --yes` ends existing transfers by reconnecting everyone on that node.

`GET /api/users/{username}/cut-off` requires `people.read` and `people.private`. Its review includes inbounds lost through a profile change while their flow-block receipts remain unresolved, even if the person is still active. A subsequent `--cut-off` resets those inbounds too.

Every unresolved receipt contributes its inbounds, regardless of its current reason. Rotating connection keys, reissuing a link, replacing keys and profiles together through import, or re-enabling a person after narrowing access keeps lost inbounds in the cut-off review until blocking is confirmed or the same key regains access.

Removing a profile still used by people is refused with its membership count. Move those people to other profiles first. Ordinary applies never replace membership. Snapshots preserve every person's memberships; a replacing user import preserves them when `profiles` is omitted. Fleet export carries topology and profile grants; every person's memberships remain service data in the snapshot.

Restore also preserves earlier profile use and refuses snapshot profiles that differ from local intent before staging or target access. It compares stored declarations, or the exact compiled map for an unstamped snapshot. Restore matching state first, then review the desired saved change with `meridian apply`; `--force` cannot bypass the match. See [Recovery](/en/recovery/).

`plan` and `apply` review profiles held by the service as well as those in the proposed topology. Removing the last profile is reviewed and fenced, and is refused while anyone still belongs to it.

Every apply path, including `setup --intent`, restarting setup, and imperative commands such as `node add` or `fleet contact`, checks declared profile IDs and grants against the service before saving topology or writing to servers. A saved but unapplied profile edit is refused: edit profiles with `meridian profile` or `meridian fleet import`, or run `meridian apply` to review the saved change. Changing protocols or relay camouflage may expand an unchanged `exit:` or `gateway:` grant; the topology command's own review covers that change.

A fresh fleet can introduce profiles through `setup --intent`. Bootstrap is allowed only while the service has no declaration fingerprint, no named profiles, and no non-default memberships. If the service holds profiles without a fingerprint, run `meridian apply` to review them before other topology commands.

Meridian remembers that your fleet has used named profiles. Saving a configuration with profiles records this fact, and removing the last profile does not reset it. Current configuration edits also count when checking whether an outage requires review.

If another managing copy causes an apply to refuse, local topology and forgotten-server records stay unchanged. Once an apply is accepted, Meridian remembers profile use even if a later deployment step fails.

`fleet import` uses the same profile check, including when both the saved and imported topology have no profiles. If service settings are unavailable, `plan` still returns its compiled result with unavailable evidence and exit `3`. An apply or import can proceed without the profile review only for a fresh fleet or when saved generation evidence proves no profiles were recorded. Otherwise, restore control-service access and retry. Import dry runs require live evidence.

An apply stores inbound and endpoint references before activating profile grants, then creates accounts. Adding a person in that apply cannot invalidate its own access review, including when removing the last profile.

Membership is private operator data: `people.private` reveals `profiles` in person and revision results. Effective-access and membership-count reads require both `people.read` and `people.private`; policy reviews require `fleet.apply` and `people.private`. Account edits use `people.edit`, and bulk assignment uses `people.batch` and `people.private`. Format previews require `people.read`, plus `people.private` while profiles are configured. The people/admin presets include the necessary private permissions; other presets receive an empty `profiles` list and no creation memberships. Profiles store membership only, with no client IP, device, or connection observations.

JSON reviews and fleet exports preserve valid profile IDs and usernames such as `token` and `secret`, including nested grant and creation-setting lists. Credential fields remain redacted.

| Operation | Meaning |
| --- | --- |
| `GET /api/access-profiles` | Reviewed grants and private membership counts; `people.read` and `people.private` |
| `GET /api/users/{username}/access` | Effective inbounds and connections with `granted_by`; `people.read` and `people.private`, no credentials |
| `POST /api/access-profiles/preview` | Review a compiled `access_profiles` map and future `inbound_refs`; `fleet.apply` and `people.private`, no writes |
| `PATCH /api/users/{username}` | Set a nonempty `profiles` list with `people.edit`; omitted membership is preserved |

For a frozen assignment, use `POST /api/people/bulk/preview` with `{"usernames":["person"],"action":{"action":"set-profiles","profiles":["limited"]}}`, then commit its ID and hash. The profile preview returns `access_hash`; `PUT /api/settings` accepts it as `expected_access_hash` and refuses membership changes since review. Optional `existing_inbounds` identifies the current node/tag references when upgrading older specs. These previews persist no request data.

Settings carry `access_profile_declarations_hash`, the SHA-256 of normalized intent-level profile declarations. Reviewed writes set it under `expected_access_hash`; writes keeping the same declarations send `expected_profile_declarations_hash`, which the service checks in the settings transaction. Removing the last profile keeps the fingerprint of empty declarations. `GET /api/access-profiles` includes `declarations_hash` and, when true, `has_non_default_memberships`; these reads keep the existing private permissions. Replacing an established fingerprint or grant map without either fence is refused.

### Private person routing

`meridian client routing person` reads the effective policy. Use `meridian client routing person work --dry-run --json`, then `meridian client routing person work --yes --json` to review and save a private override; `meridian client routing person --inherit --yes` clears it. The **Routing & profiles** tab uses the same service review and keeps drafts in memory. Person overrides win over matched app response rules, followed by profile defaults and the fleet default. Applies and credential reissue preserve overrides; encrypted snapshots carry them, and deletion removes them. This choice changes subscription delivery at app refresh, with no account or node-access edit.

Policy reads need `people.read` and `people.private`; assignment additionally needs `people.delivery`. `delivery.read` reads the reviewed catalog. Existing issued tokens keep their frozen grants; issue a token with the new grant to assign policies. Overrides are blank without private permission and never appear in subscriptions, events, or reviewed intent. A preview writes nothing. Confirmation requires its `review_hash`; a changed override, membership, or catalog needs another preview.

| Operation | Meaning |
| --- | --- |
| `GET /api/delivery-policies` | Reviewed catalog and profile defaults; `delivery.read` |
| `GET /api/users/{username}/delivery-policy` | Private override and effective policy; `people.read` and `people.private` |
| `PUT /api/users/{username}/delivery-policy` | `{ "policy": "work", "confirm": false }` previews; confirm with its `review_hash`; `people.delivery`, `people.read`, `people.private`. Explicit `policy: null` inherits. |

| `POST /api/response-rules/test` | Transient app matcher; `delivery.test`, plus `people.read` and `people.private` with a person |
| `POST /api/delivery-policies/explain` | Offline first-match explanation; `delivery.explain`, plus `people.read` and `people.private` with a person |

## Preview and explain delivery

`meridian client preview person --suffix singbox --json` and the console's subscription preview show the effective policy, its source, every format's ordered rules and DNS, and omitted features with reasons. The optional Destination field explains the selected format. Xray omissions distinguish individual and fingerprint profiles that cannot reach a requested exit. Policy rule numbers count LAN first, then custom rules, then the preset tail; they are shared across formats even when a client needs several serialized rules.

```bash
meridian fleet routing set work --dry-run --json
meridian fleet routing explain chat.example.test:443 --person person --format singbox --json
meridian fleet routing explain 198.51.100.10 --format xray-json --port 443 --network tcp --protocol other --json
```

`fleet routing explain` reads the currently applied service settings and optional private person assignment. It returns the first matched rule and action, for example “this destination goes via exit west because rule 2 matched.” Omit `--person` to explain the fleet default. `--format` selects Xray JSON, Mihomo/Clash, Stash, sing-box, or Base64. TCP and ordinary traffic are assumed unless supplied; `--protocol bittorrent` supplies synthetic detected-protocol evidence. No DNS, reachability, or network lookup occurs. A missing port or cached pinned geodata is reported as unknown when an earlier rule needs it. Xray checks names with `IPIfNonMatch`; its final TCP/UDP rule sends unmatched names through the tunnel without a local DNS routing pass. IP rules match connections addressed by IP. Actual app selection and reachability can change which connection an automatic group uses.

| Format / apps | Destination rules | DNS | fake-IP | Via exit | BitTorrent |
| --- | --- | --- | --- | --- | --- |
| Xray JSON apps | All eight typed matchers | DoH, plain | Omitted | Yes, within the delivered profile | Best-effort detection |
| Mihomo / Clash Meta | All eight | DoH, DoT, plain | Yes | Primary connections only | Omitted |
| Stash | All eight | DoH, DoT, plain | Native mode; custom fake-IP omitted | Primary connections only | Omitted |
| sing-box / Hiddify | Inline domains, CIDR, ports, TCP/UDP, private IPs | DoH, DoT, plain | Yes | Supported connections only | Omitted |
| Base64 link apps | No rules | No settings | No | No rule-based choice | No |

The service reads geodata only from already-cached Xray archives whose hash matches Meridian's pin. Explanations never start a download. Clients never receive remote rule sets. Xray sends configured public DNS through its automatic group and retains direct LAN resolvers; other clients use their represented DNS settings. Choosing an unsupported transport never converts it to plain DNS. Existing preset bytes and default fleet corpus answers stay unchanged.

`POST /api/delivery-policies/explain` accepts `destination`, optional `person`, `format`, `port`, `network`, and `protocol`; it needs `delivery.explain`, plus `people.read` and `people.private` with a person. Subscription previews require private permission for policy details or a destination explanation; lower grants receive the existing metadata only. Connection-page `/info` may include `user.deliveryCapabilities`: format names, support flags, and omission codes, without policy names, assignments, memberships, destinations, or endpoint details. The page keeps this fresh data in memory.
