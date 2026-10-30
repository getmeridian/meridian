---
title: Connecting
description: You received a Meridian link. Install an app, add the connection, and get it working again when it stops.
---

Someone who runs a Meridian server sent you a link. This page is for you: you need no command line and no server knowledge.

## Open the link

Open the link on the device you want to connect. It leads to your **connection page**, which recognizes your device and walks you through three steps:

1. **Install an app.** The page recommends apps for your platform and links to their downloads.
2. **Add the connection.** Tap **Add to …** for the app you installed. The app opens with your connection already in it.
3. **Connect.** Turn on the connection in the app and allow VPN permission if your device asks.

To set up a phone from a computer, open the link on the computer and scan the QR code beside the steps with the phone's camera or the app's scanner.

Keep the link to yourself. It is your personal access, and anyone who has it can connect as you.

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

1. **Refresh the subscription.** Your app updates it on its own; to update it now, open the subscription (or profile) list in the app and tap **Update**. This picks up new servers and routes.
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

## When an app shows a different list

Apps receive the format they support, so their lists can differ. If a method is missing, tell the owner which app and version you use. The owner can preview the format and see why an entry was omitted without sharing your connection keys. Refresh the subscription after the owner changes the settings.
