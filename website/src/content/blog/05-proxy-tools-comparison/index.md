---
title: "Self-hosted proxy tools compared: panels, apps, scripts, and Meridian"
date: 2026-03-21
author: "Meridian Team"
category: "Comparison"
description: "How 3x-ui, Marzban, Hiddify Manager, Remnawave, Outline, Amnezia, reality-ezpz, and Meridian differ in what they install, what they leave to you, and how users get connected."
tags:
  - comparison
  - 3x-ui
  - remnawave
  - outline
  - amnezia
  - self-hosted
readingTime: 5
---

![Self-hosted proxy tools compared](./hero.webp)

These tools differ less in protocols than in who does the server work. Five questions separate them:

1. What do you install, and where?
2. Who sets up certificates, the firewall, and port 443?
3. How do users get their connection?
4. Can one setup span several servers?
5. How do you see a change before it happens?

## Web panels: 3x-ui, Marzban, Hiddify Manager

[3x-ui](https://github.com/MHSanaei/3x-ui) is a web panel for Xray-core. You install it with a one-line script or Docker. The installer generates a random username, password, and panel path. It covers a long list of protocols and transports (VLESS, VMess, Trojan, Shadowsocks, WireGuard, AmneziaWG, Hysteria2, Reality, XHTTP) and adds per-client quotas, expiry dates, IP limits, subscriptions, a REST API, bots, and multi-node management. Certificates and UFW rules are options in the `x-ui` menu. Its docs tell you to open only the ports you use; which ports those are is your call. Pick it if you want maximum protocol choice and per-user controls in a browser.

[Marzban](https://github.com/Gozargah/Marzban) is a Docker-based panel with a REST API, extra servers through Marzban Node, and VMess, VLESS, Trojan, and Shadowsocks with TLS or Reality. Its dashboard is not served on a bare IP: you obtain a certificate for a domain or reach it through an SSH tunnel. Remnawave ships a migration tool for existing Marzban installs.

[Hiddify Manager](https://github.com/hiddify/Hiddify-Manager) bundles more than 20 protocol configurations across Xray and sing-box, plus Telegram proxies, per-user pages, automatic Cloudflare setup, multiple domains, and its own client app. It aims to be the widest toolbox. Its README asks you to close every port except 22, 80, and 443 yourself.

## Remnawave on its own

[Remnawave](https://docs.rw/) is the panel Meridian deploys. Used directly, it gives you the full product: a panel with an API, nodes on as many servers as you like, a subscription page, and squads that decide which users see which inbounds. You also assemble it. You install the panel with Docker Compose, choose a reverse proxy (its docs cover Caddy, nginx, and Traefik; the Caddy and nginx guides assume a domain), then write each node's Config Profile, which is a complete Xray configuration. Hosts and squads come after that. If you want every knob and already run nginx or Caddy, Remnawave alone is the right tool.

## App-first: Outline and Amnezia

[Outline](https://getoutline.org/) keeps the deployer in a desktop app. Outline Manager sets up a Shadowsocks server on a cloud provider or your own machine and hands out access keys that the Outline client opens. For networks where regular Shadowsocks is blocked, Outline's docs offer Shadowsocks over WebSockets, which you set up by hand: you run `outline-ss-server` behind a TLS web server or tunnel and write the access keys yourself.

[Amnezia](https://amnezia.org/) also drives everything from its app. You enter a server IP and SSH login, and it installs Docker containers for the protocols you pick: WireGuard, OpenVPN, IKEv2, OpenVPN over Cloak or Shadowsocks, AmneziaWG, or Xray. You share guest access as a `vpn://` key for the Amnezia app, or as native files, such as AmneziaWG configs for other apps and routers. It also has split tunneling. If you need WireGuard-family protocols or router configs, Amnezia covers ground Meridian does not.

## Install scripts: reality-ezpz

[reality-ezpz](https://github.com/aleskxyz/reality-ezpz) installs Docker and a VLESS server (Reality or TLS) with Xray or sing-box in one command, with TUIC, Hysteria2, and ShadowTLS as options. Users are managed from a CLI, a text UI, or a Telegram bot. It suits one server and a few people; there is no web panel.

## Meridian

[Meridian](/en/architecture/) is a CLI on your own machine that builds a Remnawave deployment for you. `meridian deploy` on a fresh Debian or Ubuntu server installs pinned Remnawave images and puts nginx in front of TCP/443. nginx routes connections by SNI without terminating Reality's TLS and serves the panel, connection pages, and subscriptions with a Let's Encrypt certificate for the server's IP, so no domain is needed.

What lands on the server:

- **Methods:** VLESS+Reality, XHTTP, and Hysteria2 on UDP/443 with salamander obfuscation. With `--domain`, XHTTP and WSS run through Cloudflare's CDN as a fallback when the IP is blocked.
- **Hardening:** UFW opens only SSH and the ports the server's role needs. SSH is key-only, with fail2ban.
- **Camouflage:** deploy looks for a Reality target in the server's own subnet before falling back to common defaults.
- **Verification:** deploy pushes real traffic through the delivered subscription before it reports success.

Users get a connection page with a QR code, a recommended app for their platform, and one-tap import. They also get a subscription URL that picks up new servers on refresh. The Remnawave panel sits on a separate secret path that shared links never reveal.

Fleets grow from the same CLI. `meridian node add` adds exits. `meridian relay add` puts a Realm TCP forwarder on a domestic IP in front of an exit. `meridian setup` adds multi-hop chains and routing gateways. Everything is one topology in `cluster.yml`. `meridian plan` compares it with the live servers and panel and shows a plan hash. `meridian apply` runs exactly that plan, with checkpoints, so an interrupted apply resumes. See [Relay nodes](/en/relay/) and [Security](/en/security/) for details.

## Where Meridian is the wrong choice

- **You need other protocols.** No VMess, Trojan, plain Shadowsocks, WireGuard, AmneziaWG, or OpenVPN. Use 3x-ui, Hiddify, or Amnezia.
- **You want to manage the setup from a browser.** Meridian owns the nodes, profiles, Hosts, squads, and templates it creates, and the next apply reverts panel edits to them. Client status, traffic limits, expiry, and users you create in the panel are left alone. If you want to shape inbounds by hand, run Remnawave directly.
- **The server already hosts other services.** Meridian expects a clean Debian or Ubuntu VPS and takes over port 443.
- **You'd rather not use a terminal.** Outline and Amnezia run setup from a desktop app.
- **You want one client app for everything.** Meridian ships none. Users connect with existing Xray and Clash apps such as Happ, FlClashX, and Clash Mi.

## How to choose

![Start from what you need: 3x-ui for many protocols in a browser, Remnawave for every knob with your own proxy and domain, Outline or Amnezia for setup from a desktop app, reality-ezpz for one Reality server, Meridian for a correctly set up fleet with relays and reviewed plans.](./how-to-choose.webp)

- Many protocols and per-user controls in a browser: 3x-ui.
- Every Remnawave knob, with your own reverse proxy and domain: Remnawave.
- Setup from a desktop app: Outline or Amnezia.
- One Reality server for a few people: reality-ezpz.
- Reality, XHTTP, Hysteria2, and CDN fallback set up correctly on one or many servers, with relays and a reviewed plan for every change: Meridian.

Start with the [Deploy guide](/en/deploy/).
