---
title: Connecting
description: You received a Meridian link. Install an app, add the connection, and get it working again when it stops.
---

Someone who runs a Meridian server sent you a link. This page is for you: you need no command line and no server knowledge.


An account may be valid for a number of days from first use. Opening this page or refreshing the subscription does not start that time: it starts when the server’s agent first reports your traffic. Before then the account remains ready to connect; afterwards the account panel shows the actual expiry, in your browser’s time zone with UTC. If automatic renewal is configured, the panel also shows the next renewal, next traffic reset, and renewals left.

## Open the link

Open the link on the device you want to connect. It leads to your **connection page**, which recognizes your device and walks you through three steps:

1. **Install an app.** The page recommends apps for your platform and links to their downloads.
2. **Add the connection.** Tap **Add to …** for the app you installed. The app opens with your connection already in it.
3. **Connect.** Turn on the connection in the app and allow VPN permission if your device asks.

To set up a phone from a computer, open the link on the computer and scan the QR code beside the steps with the phone's camera or the app's scanner.

Keep the link to yourself. It is your personal access, and anyone who has it can connect as you.

If the owner enables device limits, Your account shows used / allowed device slots. When full, new devices get a limit notice on subscription refresh. Apps that do not report a device are not counted. Only counts appear; they are not saved in your browser.

## Language

Your saved page choice comes first, then your account's language, then the owner's default. With automatic language, connection and support pages follow your browser and fall back to English when needed.

Use the language buttons at any time; your choice is saved in this browser and applies to the support page too. Persian uses a right-to-left layout.

## Apps

These apps work with Meridian. Your connection page shows the ones for your device first.

<!-- apps -->

Your personal link contains `/sub/` and is also your subscription URL. Open it in a browser for the connection page, or copy it into the app as a subscription, usually with a **+** button and "Import from clipboard" or "Add from URL".

## Turn on VPN (TUN) mode

Most apps run either as a proxy or as a VPN. As a proxy only, some apps bypass the connection, and websites in your browser can sometimes see your real IP address (a WebRTC leak). Turn on the app's **VPN** or **TUN** mode so all traffic goes through the connection. To confirm, open any leak-check site: the address it shows should differ from your usual one.

## The entries in your app

Your app lists several entries, named by country and method, such as "🇩🇪 DE · Reality · Chrome" or "🇩🇪 DE · XHTTP · Chrome". They are different routes to the same service; one may work on a network where another is blocked. Entries marked "via relay" go through an extra server first. If your app offers an automatic entry, use it; it switches to a working route by itself.

## When it stops working

A red or “n/a” ping in your app does not mean the connection is broken: apps test with their own probe, which can fail while the tunnel works. Connect and open a website first. If it really does not load, try these in order. The same tips are on your connection page under **Not connecting?**.

1. **Refresh the subscription.** Your app updates it on its own; to update it now, open the subscription (or profile) list in the app and tap **Update**. This picks up new servers and routes. If your app has a setting like “update subscription on launch”, turn it on.
2. **Try another entry.** Pick one with a different method, or one "via relay".
3. **Check your device's clock.** A clock that is off by more than a minute can make connections fail; turn on automatic date and time.
4. **Run the connection check.** Turn your VPN app off so the check sees your real network. Open your connection page, expand **Not connecting?** and **Check my connection**, and tap **Run check**. Then tap **Copy report** and send it to the person who gave you the link; the report contains no links or keys.
5. **Ask for help.** If the connection page itself no longer opens, your network may be blocking the server. Tell the person who runs it; [recovering from a block](/en/recovery/) is part of their job, and your app picks up the new route on its next refresh.

## Contact the owner

Your connection page's **Not connecting?** section may include the owner's instructions and a **Contact owner** button. Use it when access has expired, the traffic allowance is used up, or the connection check still leaves you stuck. The button opens the owner's chosen website, messaging app, or email app after you click it.

An app's support button may open the same contact link. If it opens a Meridian support page instead, follow the owner's instructions there or choose **Open connection page**. These instructions are provided by the person who runs your connection.

## Messages from the owner

Your connection page may show a notice from the person who runs the service, such as a maintenance time. Some apps show the same notice after refreshing the subscription. Account values in it are filled in for you. An expired notice disappears; the page loads it from the same service and makes no outside requests.

## Status messages in your app

If access is disabled, has expired, or has reached its traffic allowance, the entries in your app explain the reason and ask you to contact the owner. The messages follow your account language or the owner's default; automatic language keeps them in English. Your page's language buttons save a preference in this browser. These explanatory entries cannot connect; ask the owner to restore access.

## An update message in your app

If your app is too old to use everything the service offers, or is no longer maintained, it can list one extra entry at the end that asks you to update it, or to switch from NekoRay to Throne. This is decided from the app's name and version alone, at the moment it fetches the subscription; nothing is saved. The entry cannot connect, and your real entries stay as they were. Install the newer app from the apps list on your connection page, then refresh the subscription.

## When an app shows a different list

Apps receive the format they support, so their lists can differ. If a method is missing, tell the owner which app and version you use. The owner can preview the format and see why an entry was omitted without sharing your connection keys. Refresh the subscription after the owner changes the settings.

## Granted connections

Your page and every app subscription list only the connections your operator grants. Several profiles can combine access, but their names and membership remain private operator data. When access changes, refresh the subscription; the page uses the current granted list. Losing a connection also removes its authentication access after the node applies the change. Existing transfers may continue until they finish or the operator performs a shared-node cut-off. Your subscription URL and connection keys stay the same.


## Preview and explain delivery

`meridian client preview person --suffix singbox --json` and the console's subscription preview show the effective policy, its source, every format's ordered rules and DNS, and omitted features with reasons. The optional Destination field explains the selected format. Xray omissions distinguish individual and fingerprint profiles that cannot reach a requested exit. Policy rule numbers count LAN first, then custom rules, then the preset tail; they are shared across formats even when a client needs several serialized rules.

```bash
meridian fleet routing set work --dry-run --json
meridian fleet routing explain chat.example.test:443 --person person --format singbox --json
meridian fleet routing explain 198.51.100.10 --format xray-json --port 443 --network tcp --protocol other --json
```

`fleet routing explain` reads the currently applied service settings and optional private person assignment. It returns the first matched rule and action, for example “this destination goes via exit west because rule 2 matched.” Omit `--person` to explain the fleet default. `--format` selects Xray JSON, Mihomo/Clash, Stash, sing-box, or Base64. TCP and ordinary traffic are assumed unless supplied; `--protocol bittorrent` supplies synthetic detected-protocol evidence. No DNS, reachability, or network lookup occurs. A missing port or cached pinned geodata is reported as unknown when an earlier rule needs it. A hostname whose Xray rule needs a resolved IP also remains unknown. Actual app selection and reachability can change which connection an automatic group uses.

| Format / apps | Destination rules | DNS | fake-IP | Via exit | BitTorrent |
| --- | --- | --- | --- | --- | --- |
| Xray JSON apps | All eight typed matchers | DoH, plain | Omitted | Yes, within the delivered profile | Best-effort detection |
| Mihomo / Clash Meta | All eight | DoH, DoT, plain | Yes | Primary connections only | Omitted |
| Stash | All eight | DoH, DoT, plain | Native mode; custom fake-IP omitted | Primary connections only | Omitted |
| sing-box / Hiddify | Inline domains, CIDR, ports, TCP/UDP, private IPs | DoH, DoT, plain | Yes | Supported connections only | Omitted |
| Base64 link apps | No rules | No settings | No | No rule-based choice | No |

The service reads geodata only from already-cached Xray archives whose hash matches Meridian's pin. Explanations never start a download. Clients never receive remote rule sets. Xray sends configured public DNS through its automatic group and retains direct LAN resolvers; other clients use their represented DNS settings. Choosing an unsupported transport never converts it to plain DNS. Existing preset bytes and default fleet corpus answers stay unchanged.

`POST /api/delivery-policies/explain` accepts `destination`, optional `person`, `format`, `port`, `network`, and `protocol`; it needs `delivery.explain`, plus `people.read` and `people.private` with a person. Subscription previews require private permission for policy details or a destination explanation; lower grants receive the existing metadata only. Connection-page `/info` may include `user.deliveryCapabilities`: format names, support flags, and omission codes, without policy names, assignments, memberships, destinations, or endpoint details. The page keeps this fresh data in memory.
