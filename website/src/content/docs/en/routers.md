---
title: Use Meridian on a router
description: Connect a home router to your Meridian server with podkop on OpenWrt or XKeen on Keenetic, so every device is covered without an app.
order: 6.7
section: guides
---

A router that runs a proxy client covers every device in the house, including TVs and consoles that can't run an app. This guide shows two common setups: **podkop** on OpenWrt and **XKeen** on Keenetic. Both are community projects, so check their pages for the current steps before you start.

## Get the link

Give the router its own client, so you can disable it without cutting anyone else off:

```
meridian client add home-router
meridian client show home-router
```

`client show` prints the connection page and the subscription URL. Open the connection page: every path (Reality, XHTTP, WSS, and each relay) has a **Copy link** button that copies a `vless://` link.

Which one to paste:

- **Reality first.** It is the primary path and the one every router client below can run.
- **XHTTP** needs an Xray-based client that is recent and close to the server's Xray version. Stock sing-box, and so podkop, has no XHTTP transport; XKeen runs Xray and can use it.
- **Relay entries** are Reality links too. If your router sits in Russia and the exit's IP is blocked, paste the relay's link instead.
- **Hysteria2** is UDP and does not pass through relays. Use it only if your router client supports it.

A pasted link is a snapshot. Subscription URLs update on their own, but podkop's FAQ says it has no built-in subscription support, so when you add an exit or relay later, paste the new link.

### Client versions

Recent Xray servers turn away sing-box (podkop) and Mihomo clients by default, because those clients announce an older version number. Meridian's exits explicitly accept every client that speaks Reality, so this doesn't affect you. If a router still never connects while `meridian test` passes from your laptop, update its proxy core. On Keenetic, `xkeen -ux` updates XKeen's Xray.

## OpenWrt with podkop

[podkop](https://podkop.net/) routes only the domains and lists you choose through a proxy, and sends everything else directly. That suits Russian users: domestic sites stay on the normal connection with no extra rules.

**Requirements** (from the [podkop docs](https://podkop.net/docs/install)): OpenWrt 24.10 (25.12 works through apk), and about 20 MB of free flash. If sing-box isn't installed yet, podkop needs 40 MB free in `/overlay`. When space is short, the podkop FAQ suggests `sing-box-tiny`.

**Install** from the router's SSH shell:

```
sh <(wget -O - https://raw.githubusercontent.com/itdoginfo/podkop/refs/heads/main/install.sh)
```

If GitHub isn't reachable from the router, podkop documents a mirror bootstrap on its install page.

**Configure** in LuCI, on the Podkop page:

1. In the `Main` section, set **Connection Type** to `Proxy`.
2. Set **Configuration Type** to `Connection URL` and paste the Reality `vless://` link.
3. Select the community lists you want to send through Meridian, or add your own domains.
4. **Save & Apply**, then open the Diagnostics tab. Run it from the LAN, not through a remote port forward.

To fail over between the direct Reality link and a relay link, choose **URLTest** instead and add both links. sing-box then probes them and uses a working one.

## Keenetic with XKeen

[XKeen](https://github.com/jameszeroX/XKeen) installs and manages Xray on Keenetic routers through Entware. The actively maintained fork also supports Mihomo.

**Requirements:** a USB drive formatted as EXT4 with Entware, and the router's Netfilter kernel module components enabled. The XKeen guide recommends a swap partition on the drive and turning off RAM compression in the router's system settings.

**Install** from the Entware SSH shell as root:

```
opkg update && opkg upgrade && opkg install curl tar && cd /tmp
sh -c "$(curl -sSL https://raw.githubusercontent.com/jameszeroX/XKeen/main/install.sh)"
```

**Configure** the Xray files in `/opt/etc/xray/configs/`. Put the Reality link's values into `04_outbounds.json`:

```json
{
  "outbounds": [
    {
      "tag": "vless-reality",
      "protocol": "vless",
      "settings": {
        "vnext": [{
          "address": "198.51.100.10",
          "port": 443,
          "users": [{ "id": "UUID", "flow": "xtls-rprx-vision", "encryption": "none" }]
        }]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "fingerprint": "chrome",
          "serverName": "www.google.com",
          "publicKey": "PBK",
          "shortId": "SID"
        }
      }
    }
  ]
}
```

The link carries every value: the part before `@` is the UUID, then the address and port. In the query, `pbk` is `publicKey`, `sid` is `shortId`, `sni` is `serverName`, and `fp` is `fingerprint`. Keep `flow` as `xtls-rprx-vision` when the link has it.

Set routing in `05_routing.json`. The XKeen guide describes two ways: send only chosen domains and IP lists through the proxy, or send `.ru`, `.su`, and `.рф` direct and everything else through it. Then check and start:

```
xkeen -xtest
xkeen -start
```

## Domestic sites stay direct

By default the exit refuses traffic to Russian domains and IP ranges (`--geo-block`). A router that sends everything through the tunnel would break Russian sites. Send them directly from the router instead; podkop does this by design, and with XKeen you choose the second routing mode above. [Split routing](/docs/en/split-routing/) explains the layers.

## Pitfalls

- **Flash, RAM, and CPU.** podkop needs room for sing-box in flash, and a router that is already full will fail the install. A weak CPU caps throughput well below your line speed, so measure through the router before you rely on it.
- **Wrong clock.** Many routers boot with the wrong time until NTP answers. TLS certificate checks then fail for the subscription page, XHTTP, WSS, and package downloads. The podkop FAQ tells you to check the router's date first when its installer fails with a certificate error.
- **DNS leaks.** podkop routes by domain name, so devices must use the router as their DNS server and browsers must not bypass it with their own secure DNS. A device on another DNS server will resolve to the real address and skip the tunnel. Per the podkop docs, this is the most common cause of "works on one device, not another".
- **VPN apps on the device.** A phone running its own VPN app skips the router's routing.
- **A disabled client.** `meridian client disable home-router` cuts the router off until the next apply turns it back on.

## Verify

From a device behind the router, open an IP-checking page: it should show your exit's address. From your own machine, `meridian test` checks the delivered subscription end to end, and `meridian check` confirms the server side. See [Troubleshooting](/docs/en/troubleshooting/) if they disagree.
