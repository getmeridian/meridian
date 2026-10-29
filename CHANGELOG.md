# Changelog

All notable changes to Meridian are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [4.0.0] - Unreleased

Meridian 4 is a new foundation: the Remnawave panel replaces 3x-ui, and one
reviewed topology in `~/.config/meridian/cluster.yml` describes your whole fleet.

### Read first: this is not an in-place upgrade
- Meridian 4 cannot manage servers deployed by 3.x, and existing client links
  cannot carry over to Remnawave. Your 3.x servers keep working.
- To keep managing them, stay on 3.x: `uv tool install 'meridian-vpn<4'`.
- To move: deploy a fresh server with 4.0, give your users the new links, then
  `meridian teardown` the old server (it also removes 3x-ui).
- 3.x and 4.0 cannot share a server, since both need port 443. 4.0 refuses to
  deploy onto a server that still runs 3.x (or 2.x) instead of taking it over.
- Requires Python 3.11+.
- **Both versions fit on one computer** — 4.0 keeps its state in
  `~/.config/meridian` (or `$XDG_CONFIG_HOME/meridian`) and leaves 3.x state in
  `~/.meridian` alone. A fleet set up with a 4.0 beta in `~/.meridian-v4` or
  `~/.meridian` is found there, so the release manages it without moving files.

### Working in the Remnawave panel
- **`meridian panel`** — prints the admin panel URL and login (`--show-password`
  for the password) and lists what Meridian manages versus what is yours.
- **Your panel edits stick** — Meridian now owns only each client's existence,
  name, and squads. Disabling a client (with `client disable` or in the panel),
  traffic limits, expiry, and descriptions set in the panel survive every
  `apply`; before, the next apply re-enabled disabled or limited clients, and a
  description edit made every apply fail. Disabled, limited, or expired clients
  no longer mark `fleet status` as degraded.
- **`meridian check` says whether node plugins can work** — Remnawave's node
  plugins (Torrent Blocker, ingress and egress filters) need a 5.7+ kernel
  and otherwise silently do nothing; every exit and
  gateway now reports "Node plugin readiness" with the fix. The docs explain
  turning plugins on in the panel. Server inbounds now sniff traffic
  (route-only, destinations untouched), so Torrent Blocker can recognize
  BitTorrent once you enable it.
- **Connect a bot safely** — the client guide shows how to give a community bot
  its own scoped, expiring panel token and which endpoints a read-only bot
  needs, instead of sharing Meridian's full-access token.

### Deploy
- **Several fleets on one computer** — `meridian context list|use|rename|remove`
  keeps separate fleets apart, say family and work, kubectl-style:
  `meridian deploy IP --context work` starts one, `--context NAME` (before or
  after any command) or `MERIDIAN_CONTEXT` picks one for a run, and
  `context use` changes the current one. An existing fleet becomes the
  `default` context without moving a file; others live in `contexts/NAME/`
  with their own saved servers, sharing Meridian's SSH key. A server belongs
  to one fleet at a time, `context remove` deletes only local files (asking
  first while the fleet has servers), and `MERIDIAN_HOME` still pins one fleet.
- **One command, verified end to end** — `meridian deploy IP` sets up the panel
  and proxy, picks a camouflage target the server can actually use, pushes real
  traffic through the delivered subscription, and only then prints "Done" with a
  shareable connection page and QR code. Re-running it resumes or redeploys.
- **A setup wizard anyone can follow** — `meridian setup` is a guided,
  resumable wizard for one server or many (exits, Realm relays, routing
  gateways with failover). It speaks English, Russian, Persian, and Chinese
  (asked first, remembered, or `--lang`), explains each term in one plain
  sentence, accepts `local` for the machine you are on, skips routing questions
  unless you have a gateway, suggests five short server names (astra, boba,
  foxtrot…), and ends with a plain summary before anything
  changes. Running `meridian` alone in a terminal offers to start it.
  Choices are arrow-key lists and checklists (Space toggles, `?` explains,
  Esc goes back), typed answers are checked as you enter them, and the
  camouflage list leads with sites found in the exit's own network.
  `MERIDIAN_PLAIN_PROMPTS=1` keeps the numbered prompts. The final screen also
  shows the admin panel address and login (the password stays behind
  `meridian panel --show-password`).
- **Names in any script** — `setup` and `client add` take people's names as
  typed (Биба, مریم, 王小明); connection pages and output show them, and the
  panel gets a Latin username (`biba`). Other client commands accept either.
- **Camouflage scans work on cloud servers** — VPSes that get a `/32` address
  (Hetzner Cloud and others) now scan their surrounding `/24`; the scan used to
  cover only the server itself, so `deploy` and `node add` always fell back to
  a well-known site.
- **Review before change** — `meridian plan` shows what `meridian apply` would do
  (exit 0 converged, 1 config error, 2 changes pending, 3 evidence unavailable).
- **Paths** — Reality, XHTTP, and Hysteria2 on UDP/443 by default; `--domain`
  adds WSS (Cloudflare CDN fallback). Blocking Russian destinations stays on
  by default (`--no-geo-block` to turn it off).
- **Camouflage next door** — without `--sni`, a first deploy (and `node add`)
  scans the server's own subnet and prefers a site whose DNS really points
  there and that passes Reality's TLS 1.3 + HTTP/2 check, so the SNI and the
  server's IP look like they belong together. Well-known targets remain the
  fallback, and real traffic still decides.
- **Your own domain as camouflage (self-steal)** — `--self-steal` on `deploy`,
  `node add`, and `node sni` makes Reality imitate a domain you own, pointed
  at the server with a DNS-only record, instead of someone else's site. The
  exit gets a Let's Encrypt certificate for the name and answers probes and
  unknown SNIs itself with nginx's stock 404, so the name, the certificate,
  and the address all match, and no outside site can go down or change under
  you. With `--domain` alone the one domain also keeps XHTTP and WSS working
  behind it. Meridian first checks where the name resolves from the server:
  it stops when it doesn't resolve and warns when it points elsewhere (a
  Cloudflare-proxied record). The wizard offers it as "My own domain", and
  `test --probe` no longer treats a self-steal name as an independent site to
  compare against. `meridian scan` stopped promising that `--domain` alone did
  this.
- **Client IPs stay private unless you ask** — by default the panel records
  the server's own address for every TCP connection, so it never stores where
  users connect from. `--client-ips` (or `forward_client_ips` in the topology)
  passes real addresses to Xray and the panel, through the PROXY protocol and
  X-Forwarded-For, for Remnawave's per-user IP limits; the camouflage site
  never sees the header.
- **TLS fingerprint is a setting** — every Reality, XHTTP, and WSS link presents
  the `chrome` fingerprint by default (XHTTP and WSS used to send none, which
  exposed the client library); `--fingerprint` or the topology's path
  `fingerprint` switches it when a region starts flagging one.
- **Shrink a running fleet** — `meridian node remove` and `meridian relay remove`
  edit the topology, show every resource they will delete, and clean up the
  panel node, node container, Realm units, firewall rules, and published Hosts.
  `meridian plan` lists removals too, and the review covers them.
- **Move an exit to another server** — `meridian node replace EXIT SERVER`
  keeps the exit's ID, Reality keys, protocol paths, SNI, WARP and geo-block
  settings, and relays, so clients keep working, then cleans up the old server
  through the reviewed apply. The control server can't be moved yet.
- **Forget a server that no longer answers** — when a server a change removes
  doesn't answer over SSH (blocked from your network, or deleted at the
  provider), its cleanup failed on every apply. `node replace`, `node remove`,
  `relay remove`, and `apply` now offer to forget what was deployed there, or do
  it with `--forget-unreachable`; `--yes` alone never does. The panel still
  deletes what it held, and the server keeps serving the users it had until
  you delete or wipe it.
- **Change a camouflage SNI in one command** — `meridian node sni EXIT NEW_SNI`
  and `meridian relay sni RELAY NEW_SNI` switch an exit or relay to another
  site when `meridian test --probe` says the SNI is blocked. They check that
  the exit reaches the site over TLS 1.3 with HTTP/2, show the review, and
  apply it. The old name is replaced, not kept alongside; apps switch at their
  next subscription refresh (every 6 hours) or a manual refresh. They, `node
  add --sni`, and `relay add --sni` refuse Apple names and `www.microsoft.com`.
- **`meridian fleet upgrade`** — compares the Remnawave images each server runs
  with this CLI's pins, applies the reviewed plan, then runs `meridian check`.
  Newer or floating tags you chose are kept, and PostgreSQL never moves to a new
  major version in place. `meridian update` points to it when a fleet exists.
  With relays it also moves Realm to GitHub's newest release in the tested
  major, pinned by the sha256 GitHub publishes, so relay fixes need no Meridian
  release either. Relays get Realm's static build, which runs on any glibc, and
  a new binary replaces the running one only after it starts on that server.
- **Pins stay current between releases** — a weekly job opens a pull request
  raising the Realm, RealiTLScanner, acme.sh, and Remnawave defaults; the local
  Xray test client is now pinned by checksum too, and a Realm tarball without a
  pinned checksum is refused instead of installed unverified.
- **One health command: `meridian check`** — checks whatever the server is in
  your fleet: pre-flight readiness for a new server; panel connection,
  containers (including Docker health, so a crash-looping service is flagged),
  every compiled port, TLS, and disk for control, exit, and gateway servers;
  and each Realm hop for relays. With no server it checks the whole fleet, and
  `--json` returns one typed report. `meridian test --probe` (short form:
  `meridian probe`) shows a server the way a censor's scanner would.
  `preflight`, `rage`, `node check`, and `relay check` are gone: each now stops
  with a one-line pointer to the new command (exit 2, or a JSON error envelope
  with `--json`).
- **`meridian logs SERVER`** — shows a server's recent logs without logging in:
  the node, panel, and nginx on control, exit, and gateway servers, and the
  Realm units on relays. `--grep TEXT` keeps matching lines, `--lines N` sets
  how many, and `--component` picks node, panel, nginx, or relay. Every line is
  redacted like `meridian doctor` (tokens, keys, UUIDs, and the panel's secret
  path), so the output is safe to share; `doctor` now masks that path too.
- **`cluster.yml` stops growing with every apply** — each apply kept a full copy
  of every resource binding, so a small fleet's file passed 300 KB after a few
  dozen changes. Older copies of the same remote resource are now dropped when a
  change commits; the next apply shrinks existing files.
- **`client show` keeps the connection-page link** — `client add` and `deploy`
  printed each client's page link but never recorded that the page was
  published, so `client show` and `--json` later offered only the
  subscription URL.
- **`meridian plan` no longer calls pending changes unavailable** — after any
  topology edit (even one new client) it listed clients, hosts, and squads as
  "observation failed" and exited 3, because their dependencies only get
  their new identities during apply. It now shows them as `recheck … after`
  those dependencies and exits 2, as for any pending change.
- **`meridian doctor` reads the node again** — its node log section said
  "container not running" and its Xray process check failed on every exit
  (the node's logs aren't plain text and its image has no `pgrep`), so every
  report came out partial with exit 3.
- **"What is blocked?"** — `meridian test --probe` now tells a blocked server
  IP from a blocked camouflage SNI by comparing the server with and without the
  SNI against the real camouflage site, and inconclusive stays inconclusive.
  It also catches throttling: a ~50 KB download from the control server (and
  through the CDN when WSS uses one) that freezes after about 16–24 KB is
  flagged with a suggestion to change the IP. `meridian check` runs the
  download check too when it's run from another machine.
- **`meridian test` says how each path fails** — every delivered path reports
  its time to first byte, and a failed one is reported as "connects, but no
  traffic passes" or "unreachable". `--json` adds `data.transports`.
- **`meridian test` says what to do about a blocked exit** — when nothing on
  an exit answers from your network, it prints the `meridian node replace`
  command for that exit (`--json`: `data.recovery`). A blocked control server
  points to the recovery guide instead, since it can't be moved yet.
- **Name the server first, like ssh** — every server command takes one target:
  an IP, a saved name, `user@host:port`, or `local`. `--user` and `--ssh-port`
  default to the saved values (else `root` and `22`) everywhere, including
  `deploy`, `node add`, `relay add`, and `server add`. The old `--server NAME`
  option stops with a pointer to the positional target.
- **`relay deploy` is now `relay add`**, matching `node add`, `client add`, and
  `server add`. The old name stops with a pointer to the new one.
- **Run Meridian on the server itself** — after `meridian deploy local` (or on
  any saved server whose IP is on this machine), later commands such as
  `client add`, `doctor`, and `node add` run locally with sudo instead of
  SSHing back into the same machine. The same fleet copied to a laptop keeps
  using SSH.
- **Servers that came with only a password** — when a server refuses your SSH
  keys but accepts a password, `meridian deploy` and `meridian setup` ask
  whether to create a key for Meridian and install it. Say yes, type the
  password once (hidden, never saved), and the deploy continues; the key is
  saved with the server for later commands. Without a terminal, with `--yes`,
  or with `--json`, they stop with the `ssh-copy-id` command instead.

- **Per-domain routes on an exit** — `meridian route add EXIT --via
  warp|direct|block VALUES...` sends chosen domains, `geosite:` lists, or
  `geoip:` ranges through Cloudflare WARP, directly, or nowhere, ahead of the
  exit's default egress (for example, AI services that block datacenter IPs).
  `route list` and `route remove` complete it; the wizard keeps existing routes.
- **One route for AI services** — `meridian route add EXIT --preset ai` sends
  ChatGPT, Claude, Gemini, Cursor, and the rest of the AI site list through
  WARP as one route named `ai`, so they never see the exit's hosting address
  (`--via direct` keeps them on it). The deploy guide adds what a server route
  cannot fix: changing exit countries, device-side leaks, and Google's
  country-association request.
- **Gateway rules can send traffic directly** — a routing-gateway rule can
  send matching traffic out of the gateway itself instead of to an exit or
  pool, so Russian sites can leave from a gateway in Russia. The wizard offers
  "directly from the gateway" next to your exits and "nowhere"; in a topology
  file it is `"action": "direct"`. The setup summary lists each gateway rule
  with where it goes, and existing gateways compile unchanged.
- **Gateway rules match zones and site lists** — a domain rule takes a bare
  zone such as `ru`, `su`, or `рф` (every site under it), and a new site-list
  rule takes `geosite:` names such as `category-ru`, the same lists the Xray
  JSON and Mihomo subscriptions send directly. Both match the site name, so
  the gateway needs no lookup. Exit routes (`meridian route add`) take zones
  too.
- **Relays look like nothing in particular** — nothing a hosting provider can
  list on a relay (service, binary, config path, firewall comment) names
  Meridian, Realm, a relay, or a VPN. Existing relays switch on the next apply.
- **`meridian check` measures each relay leg** — every hop downloads the exit's
  camouflage page through the next hop with the clients' SNI and reports
  connect time, first byte, and bytes received; a freeze in the 8–32 KB
  throttling window suggests moving the relay to another provider.
- **`meridian test` tests the whole fleet** — with no target and several saved
  servers it tests every exit and relay entry in turn instead of asking which
  one, and exits with the worst result.
- `meridian test` names each path like its subscription entry ("Reality ·
  Chrome", "XHTTP · Firefox", "Hysteria2 · UDP") instead of calling every
  VLESS path "VLESS".
- With several exits, subscriptions list the first server's entries ahead of
  later ones within each method instead of sorting them by exit name.
- **A brief network stall no longer fails the apply** — every command shared one
  SSH connection, so a stall made each later command hang until its own timeout
  and the apply stopped. Now SSH notices a silent server within about 15 seconds,
  drops the stuck connection, and reconnects. Read-only checks and the control
  server's apply record retry for about a minute, and the progress shows
  "connection stalled, retrying…". A change whose outcome is unknown is checked
  on the server first and is never repeated blindly.

### Sharing
- **Traffic limits and expiry from the CLI** — `meridian client set alice
  --limit 50GB --reset month --expires 2026-12-31` gives a client an
  allowance, a reset period, and an end date (`--limit none` and
  `--expires never` lift them); `client add` takes the same options, and
  `client reset-traffic` refills the allowance at once. `client show` prints
  "12.4 GB / 50 GB (resets monthly)" and the expiry, `client list` gains an
  Expires column, and applies leave all of it alone. Sizes are binary
  (1 GB = 1024³ bytes, as the panel counts), and a bare date lasts to the end
  of that day, UTC.
- **`meridian client reissue NAME` replaces a leaked link** — Remnawave issues
  the client a new subscription ID and new keys, so the old subscription URL,
  connection page, and links stop working; Meridian deletes the old page,
  publishes a new one, and prints the new links. The client and their panel
  settings stay, including the HWID device limit, which the panel enforces.
- **Happ, v2rayNG, and other JSON-capable apps get the Xray JSON** — with it
  comes the automatic failover between entries; they used to get plain base64
  links. Meridian turns on Remnawave's JSON-at-base setting, which follows
  upstream's list of apps that read it (v2RayTun and Shadowrocket keep links).
- **The app's support button opens the person's connection page** — every
  subscription used to send Remnawave's placeholder `https://dummy.docs.rw`. A
  `support-url` you set yourself in the panel is kept.
- **Connection names show where traffic exits** — entries lead with the exit's
  flag and country ("🇩🇪 DE · Reality · Chrome"), and the panel shows the same
  flag on the node. Deploy, `node add`, and the setup wizard look the country up
  once from the address's Regional Internet Registry; set `--country` when it is
  wrong (anycast or leased ranges).
- **A redesigned connection page** — two clear steps (install an app, add the
  connection with one tap), the subscription QR beside them on a computer for
  setting up a phone, and everything else folded into quiet panels: every
  individual connection with Open, Copy, and QR, "Not connecting?" tips with
  the self-check, and the router guide. Russian pages no longer rename the
  first connection "Основной", and `meridian dev preview` shows a realistic
  page. Without `--display-name`, the header names the exit countries
  ("🇩🇪 Germany · 🇳🇱 Netherlands") in the page's language.
- **A WebRTC leak tip on the connection page** — "Not connecting?" now says that
  an app running as a proxy only can let websites in a browser see the real IP
  address, and to turn on the app's VPN (TUN) mode. Shown in English, Russian,
  Persian, and Chinese, with no external links.
- Connection pages are built from each user's real subscription, so relays and
  every enabled protocol appear.
- Subscription links live under the connection-page path and never reveal the
  admin panel's address.
- **Apps refresh subscriptions every 6 hours** — every subscription sends
  `profile-update-interval`, so Happ, Clash Verge, and other apps that read it
  pick up new exits and relays within hours. Meridian keeps the header set in
  the panel; other response headers you add stay yours.
- `meridian client add|show|list|set|reset-traffic|remove|enable|disable`.
  `client remove` deletes the user and their page; links stop working
  immediately.
- **Connection pages recommend apps per platform** — Happ first, then
  FlClashX and Clash Mi (plus Throne on desktop), with a link to the router
  guide and a note on subscription refresh and app updates. v2rayNG and
  Streisand import links are fixed, Throne import is added, and the broken
  v2rayN button is gone. App icons now ship once in a shared cached file, so
  each page loads about 70 KB less.
- **"Check my connection"** — the page tests the device clock, whether data
  keeps flowing (the throttling freeze), and the subscription from the
  visitor's own network, then copies a report without links or secrets that
  they can send you.
- **Every deploy offers every method** — without a domain, XHTTP
  and Hysteria2 now run on the server's IP address (Let's Encrypt certifies
  IPs), next to Reality, so a block on one method leaves the others. A domain
  carries XHTTP and adds WebSocket through the Cloudflare CDN. Hysteria2 is on
  by default (`--no-hysteria2` turns it off), and the wizard only asks for an
  optional domain instead of a list of backup methods.
- **Hysteria2 is disguised** — its UDP traffic is wrapped in salamander
  obfuscation, so censors see random packets instead of QUIC with a readable
  server name. Every subscription format carries the setting automatically.
- **Entries have readable names** — apps show "Reality · Chrome", "XHTTP ·
  Firefox", "WebSocket · CDN", or "Hysteria2 · UDP" (plus the exit's name when
  you have several, and "via relay" for relay entries) instead of internal IDs.
- **Every direct TLS method comes in two fingerprints** — Reality and XHTTP
  are offered with both a Chrome and a Firefox TLS fingerprint, so if
  censors block one fingerprint, the other entry keeps working. Automatic
  switching never mixes the two: Russian filtering reportedly stretches a
  2-minute freeze to 10 minutes when a new fingerprint arrives during it.
  Xray JSON apps get a separate "Automatic · Firefox" entry, Mihomo apps can
  pick the copies by hand, and sing-box apps (which can't keep them out of
  automatic switching) don't get them.
- **Russian sites work with the VPN on** — Xray JSON and Mihomo subscriptions
  send local-network, `.ru`/`.su`/`.рф`, `geosite:category-ru`, and `geoip:ru`
  traffic directly and refuse foreign QUIC, while the exit's block stays as a
  safety net. Plain Base64 imports still need the app's own routing settings.
- **Torrents skip the VPN in Xray apps** — Xray JSON subscriptions send
  recognized BitTorrent peer connections directly from the device, so a torrent
  client left running doesn't trip Torrent Blocker and cut off the person's VPN.
  Encrypted peers and UDP DHT/uTP may still use the tunnel; Mihomo, sing-box, and
  Base64 subscriptions are unchanged.
- **sing-box apps get a Meridian profile** — sing-box-based apps (SFA, SFI,
  Karing) pick the fastest working entry automatically, can still choose one
  by hand, and send local-network and `.ru`/`.su`/`.рф` sites directly. Nothing
  is downloaded at startup, so the app starts even when GitHub is blocked.
  sing-box has no XHTTP, so those entries are left out.

### Docs and agents
- **Reviewed translations** — the Russian, Persian, and Chinese CLI, docs, and
  connection pages were reviewed against the English source: mistranslations,
  machine-translation leftovers, and stray tokens fixed, one term per concept
  in each language, and docs claims checked against the code.
- **An agent guide** — `meridian help agents` prints a Markdown guide that
  explains how Meridian works and how to drive it safely: non-interactive
  flags, JSON envelopes, plan before apply, exit codes, and which outputs are
  secrets. `meridian help COMMAND` shows any command's help, git-style.
- **Docs for people and agents** — getmeridian.org's docs have search, a
  per-page table of contents, and proper right-to-left Persian. Every page is
  also served as Markdown (add `.md` to its address), with Copy page and Open
  in ChatGPT or Claude buttons, and `/llms.txt` indexes it all. Old `/docs/...`
  links redirect.
- **A page for the people you share with** — the docs' new Connecting page
  walks a recipient through installing an app, adding the link, turning on
  VPN (TUN) mode, and what to do when it stops working; send it along with
  the link.
- **Lighter docs pages** — diagrams arrive as images that follow the light or
  dark theme instead of ~3 MB of JavaScript, and docs pages prefetch a link
  only when you point at it instead of every page in the sidebar.
- **A lighter, readable website** — the home page is fully translated into
  Russian, Persian, and Chinese and works without JavaScript (Tor Browser's
  Safest mode included); choosing English sticks. Fonts download about 70%
  less, text meets WCAG AA contrast in light and dark themes, link previews
  load in messengers, and the blog has an RSS feed at `/blog/rss.xml`. The
  command builder only offers commands and flags the CLI actually has.

### Security and correctness
- **`meridian test` trusts valid certificates on macOS** — uv's bundled Python
  looked for root certificates at a Linux path, so every Let's Encrypt
  certificate was reported untrusted; certificate checks now use certifi.
- **A smaller download** — the package carries only Studio's own page, scripts,
  and fonts instead of the whole website: about 1 MB instead of 7.5 MB, which
  matters on slow or filtered connections.
- **Starts with current Typer** — Typer 0.26 stopped installing Click, and a
  fresh install of an earlier beta failed at launch with `No module named
  'click'`. Meridian now needs Typer 0.26 or later and uses the Click it ships.
- **The installer can be verified** — every release attaches `install.sh` and
  its `SHA256SUMS`, the installation guide shows how to check them, and the
  script pins uv's installer by version and SHA-256 instead of piping the
  latest one into a shell. `MERIDIAN_VERSION=4.0.0` installs one exact release.
- **Exits and the control server look like ordinary hosts** — containers and
  directories no longer name the proxy software: `docker ps` shows `ctlsvc-api`,
  `ctlsvc-db`, `ctlsvc-cache`, `ctlsvc-web`, and `netagent`, in `/opt/ctlsvc`
  and `/opt/netagent`, like relays' `netfwd`. Servers deployed with a 4.0 beta
  move over on the next `meridian apply`: the panel directory, with its database
  and secrets, is renamed in place, so users, client links, and API tokens stay.
  `meridian check` and `meridian doctor` name the role behind each container,
  and `meridian teardown` removes both the old and the new names.
- **Exits look up destinations over encrypted DNS only** — the exit's direct
  egress used the host's resolver, so the hosting provider saw every site its
  users opened in plain-text DNS, and a failed encrypted lookup fell back to
  plain `8.8.8.8`. Exits now resolve every name over DNS over HTTPS, asking
  Cloudflare, Quad9, and Google at once (each reached by IP address on IPv4 or
  IPv6), and never fall back to plain DNS: if none answers, sites by
  name fail through that server, and `meridian check` reports a resolver the
  server cannot reach. The system lab sniffs an exit during proxied traffic and
  requires no plain DNS question on the wire. Servers pick this up on the next
  `meridian apply`.
- **Large uploads over XHTTP never hit nginx's body limit** — nginx's 1 MB
  default cut long uploads off on 3.x when a client chose a streaming XHTTP
  mode. 4.0 serves packet-up only and now also lifts the limit.
- **Removing or moving an exit cleans up its certificate** — the acme.sh
  renewal, the certificate files, and (with the server's last certificate)
  the port-80 challenge listener go, instead of renewing forever.
- **A relay with its own camouflage site really imitates it** — `relay add
  --sni` gave clients the new name while the exit answered probes with its own
  site's certificate (a `www.google.com` certificate for `yandex.ru`), which
  gives the relay away. Each relay name now gets its own Reality listener on
  the exit, and anything else sent through the relay reaches that site.
- **Debian 12 deploys finish** — fail2ban there found no SSH log file and
  stopped right after starting, which failed server preparation; it now reads
  the system journal on every supported release, and a package Debian only
  remembers is no longer mistaken for an installed one.
- **Edits that restart an exit's node converge on the first apply** — the
  first WARP route on an exit restarts its node, and the connection check that
  followed could still catch the panel noticing that restart, failing the apply
  until a rerun. Node and gateway checks now wait up to a minute for the node
  to reconnect.
- **`node replace` moves a bare-IP exit's certificate address** — the moved
  exit kept the old server's IP as its XHTTP and Hysteria2 certificate name,
  so the new server could never get a certificate and the move failed. If a
  move already failed that way, rerun the same `meridian node replace`.
- **`node add NAME` keeps the saved name** — adding a saved server without
  `--name` resaved it under its IP, so the name stopped working; it now keeps
  the name and uses it for the exit too.
- **`meridian test NAME` and `meridian probe NAME` take saved names** — a name
  without a dot was refused as "not a full domain name".
- **Certificate failures say why** — the error kept acme.sh's first lines
  (timestamps and progress) and cut off its reason; it now shows the last ones.
- **Unknown geo lists are refused at review** — a gateway rule or exit override
  naming a `geosite:`/`geoip:` list or country the servers' Xray data lacks made
  Xray refuse its whole config, so the server stopped converging. Review now
  stops with the name and a close match (`categry-ru` → `category-ru`), and a
  saved fleet with such a name still loads so the rule can be removed.
- **1 GB control servers deploy** — without swap, the panel's first start used
  up the memory and the server stopped answering SSH; the deploy then failed on
  "Install system packages" though that step was never the cause. A control
  server with less than about 2 GB of memory now gets a 2 GB `/swapfile` before
  anything else runs. Servers also no longer install `python3-pip`, which
  pulled in a compiler toolchain nothing used.
- **Re-running a command resumes it** — `relay add` for a relay that is already
  declared finishes it instead of failing on a duplicate listener, and a bare
  `meridian test` after removing servers tests what is left instead of asking
  which saved server to use.
- **Page branding is checked before anything changes** — an unknown `--color`
  or an `--icon` that is neither an emoji nor a reachable image stops deploy
  with the valid choices; a redeploy that names only some branding flags keeps
  the rest. `client disable --json` no longer claims the next apply re-enables
  the client.
- No request logging on Meridian's web server; panel secrets are never rotated
  on restart; the live SSH port is never firewalled; teardown removes everything
  Meridian created and nothing it didn't: fail2ban stays on, and nginx keeps
  running when it serves your own sites, as do your own HAProxy/Caddy.
- Deploy checks the loopback ports its nginx serves the panel on (8443 and
  8444) before changing anything, and names the process holding one, instead
  of failing halfway through on `nginx -t`.
- An out-of-date copy of `cluster.yml` can't undo newer changes. The
  control server records the last change applied, and `apply` refuses to run
  from an older copy or while another machine is applying.
- Mihomo-based apps (FlClash, Clash Verge, ClashMi) and sing-box routers
  connect again. By default the Remnawave node's Xray rejects clients that
  announce a version below 26.3.27, and those apps announce 1.8.x, so their
  Reality connections failed with "authentication failed". Exits now accept any
  client that speaks Reality.
- Setup no longer opens an untagged `443/udp` firewall rule, and the next apply
  removes the one earlier v4 builds left. Removing or moving a node also deletes
  the `remnawave/node` image and its node API firewall rule; Docker itself stays
  installed.
- Panel and connection-page links typed or copied without the trailing slash
  now open; they used to redirect to an internal port and fail.
- Subscriptions list Reality first, then XHTTP, WSS, and Hysteria2 last. Apps
  use the first entry by default, and Hysteria2 (UDP, often blocked) used to
  lead. sing-box and Stash no longer show a duplicate "automatic fallback"
  server.
- XHTTP links work. Apps sending the default `chrome` fingerprint speak
  HTTP/2, and the server answered in HTTP/1.1, so no traffic passed.
- A fresh deploy with Hysteria2 converges. The node started before its
  certificate existed, so Xray refused to run and the deploy stopped.
- Stock Debian 13 servers deploy and join as exits. The baseline treated a
  missing `ufw` as an unreadable check, waited for a `dnsutils` package Debian
  no longer ships, and WARP needed `lsb_release`.
- Servers without apt (CentOS, Fedora, Alpine) are refused by name before
  anything changes, instead of failing halfway at "apt-get: command not found";
  `meridian check` reports them as a failure rather than a warning.
- Reality keys come from the panel instead of a Docker container or an
  unverified Xray download on the exit, so a fresh exit without `unzip` or
  Docker no longer fails at its Proxy profile.
- Adding or removing an exit renames existing subscription entries instead of
  leaving the old ones behind. Before, every entry appeared twice, and a stale
  Firefox copy slipped into the automatic failover group.
- `meridian check` and `meridian test` are fail-closed: skipped evidence is never
  reported as a pass.
- Mihomo and sing-box subscriptions are tested with the real engines, not only
  parsed: every change runs `mihomo -t` (Mihomo 1.19.32) and `sing-box check`
  (sing-box 1.14.3) on what a real panel delivers. The client-management docs
  list the tested apps and engine versions.
- Downloaded binaries (Xray, Realm, RealiTLScanner) are checksum-verified, and
  every container image is pinned to its tested multi-arch digest (amd64 and
  arm64), so a re-pushed tag cannot change what a fresh server runs.
- `meridian update` stays on the current major version; `--major` moves to the
  next one after you have read its release notes.

### Remnawave versions
Fresh deploys install the versions below. Meridian requires at least backend
3.4.0 and never downgrades a Remnawave you upgraded yourself; it warns when the
running version is newer than the tested one. `meridian fleet upgrade` moves
the backend, node, and subscription page to their newest digest-pinned
release in the same major without a Meridian update (`--tested` keeps the
versions below), and the choice is saved for later applies.

| Component | Version |
|---|---|
| `remnawave/backend` | `3.4.5` (minimum `3.4.0`) |
| `remnawave/node` | `3.4.2` |
| `remnawave/subscription-page` | `8.0.0` |
| `postgres` (panel database) | `17.11-alpine` |
| `valkey/valkey` (panel cache) | `9.1.2-alpine` |
| Xray-core (test client) | `26.7.28` |
| Realm (relay forwarder) | `2.9.3` |
| RealiTLScanner (`meridian scan`) | `0.2.3` |

### Not yet available
- `meridian studio` (local web UI) is experimental and needs `pip install 'meridian-vpn[studio]'`.
  It opens an address ending in `#k=…`, and only a tab opened from that address can use the
  local Engine. A running deploy can be cancelled; it stops after its current step and resumes
  on the next run. The default port, 7391, keeps your browser's draft across restarts.

### Removed
- 3x-ui, HAProxy and Caddy support; per-server `proxy.yml` credentials.
- The website's ping page: a browser cannot tell a blocked server from one that
  refuses the connection. Run `meridian test --probe` from the censored network,
  or open your connection page there.
- `meridian fleet recover`, `apply --parallel`/`--prune-extras`, and the
  `desired_*` lists in `cluster.yml`.

## [3.17.3] - 2026-10-12

### Changed
- **`meridian update` stays on 3.x** — it installs the newest 3.x release and explains that Meridian 4 is not an in-place upgrade (new panel, new links for every user). Moving to 4 is explicit: `meridian update --major`. Your 3.x servers keep working either way, but 3.x will eventually stop receiving updates
- **Default camouflage target is `www.cloudflare.com`** — `www.microsoft.com` failed Reality handshakes in testing (#82). A redeploy keeps a server's existing target unless you pass `--sni`, so existing links keep working
- **`meridian test` checks the exit IP over the server's address family** — IPv4 servers no longer fail the check when the observer answers over IPv6 (#82)

### Fixed
- **`meridian scan` works again** — RealiTLScanner renamed its release assets and changed its output format; scan now downloads the pinned 0.2.3 release, verifies its SHA-256, supports arm64 (#81), and reads results by column name (#78)
- **Relay XHTTP links** use the exit's TLS name instead of the relay's Reality SNI (#78)

### Added
- **`--exit-user`** for relay commands when the exit uses a different SSH user (#78)
- **`meridian preflight --ssh-port`** for servers on a non-standard SSH port (#84)

## [3.17.2] - 2026-04-20

### Fixed
- **SSH hardening failure on cloud-init VPS** — deploy failed with `effective sshd setting mismatch: expected 'passwordauthentication no'` on Ubuntu instances with cloud-init. Root cause: sshd uses first-match-wins, and Meridian's `99-meridian.conf` loaded after cloud-init's `50-cloud-init.conf`. Now uses `00-meridian.conf` to load first, and neutralizes conflicting settings in other drop-ins (#31)

## [3.17.1] - 2026-04-14

### Fixed
- **Credential sync and fetch for non-root deploy users** — deploying with `--user` (non-root) silently failed to sync `proxy.yml` to the server because SCP can't write to root-owned `/etc/meridian/`. This caused `relay deploy` and other commands that force-refresh credentials to fail. Now uses SSH + sudo for non-root users instead of SCP

## [3.17.0] - 2026-04-11

### Added
- **Multi-node system lab** — Docker Compose-based test harness deploys Meridian across separate exit and relay containers via real SSH, verifying Reality tunnel connectivity (direct + via relay). Catches port mismatches, SNI routing errors, and relay forwarding bugs that mocked tests cannot. Run with `make system-lab`
- **Custom SSH port** — `--ssh-port` flag for servers with non-standard SSH ports. Port is stored in the server registry and reused for all subsequent commands (#22)
- **Configurable ACME server** — `MERIDIAN_ACME_SERVER` env var overrides the certificate authority (default: letsencrypt). Enables future local CA testing with Pebble
- **Configurable connectivity test URL** — `MERIDIAN_CONNECT_TEST_URL` env var overrides the IP check endpoint (default: ifconfig.me)
- **Disable update check** — `MERIDIAN_DISABLE_UPDATE_CHECK=1` skips PyPI version check for CI/automation
- **Bootstrap TLS cert includes SAN** — self-signed bootstrap certificates now include `subjectAltName` (IP or DNS), improving client compatibility

### Fixed
- **Relay with same SNI as exit no longer crashes nginx** — when relay and exit share the same SNI, Meridian skips creating a duplicate nginx map entry (#17)
- **Relay credential sync fails loudly** — `relay remove` followed by `relay deploy` no longer gets stuck on "already attached" when SCP sync fails (#19)
- **Manual 3x-ui inbounds no longer crash client commands** — `list_inbounds()` handles empty or malformed JSON from manually-created panel inbounds (#16)
- **DebianBanner no longer blocks deploy on some OpenSSH builds** — verification is skipped when `sshd -T` doesn't recognize the directive (#20)
- **BBR no longer blocks deploy on containers** — `sysctl` failures due to missing kernel tunables (containers, old kernels) return a warning instead of failing the entire deploy. Other sysctl errors still fail
- **SSH drop-in priority** — `00-meridian.conf` ensures Meridian's sshd hardening takes precedence over cloud-init overrides
- **SCP directory copy** — fixed `-r` flag compatibility with OpenSSH ≥ 9.0 (SFTP protocol default)

## [3.16.1] - 2026-04-10

### Fixed
- **Redeploy no longer blocked by missing server credentials** — servers deployed before credential sync had no `/etc/meridian/proxy.yml`, causing redeploy to fail with "stale local cache". Now gracefully proceeds with the local copy and syncs after deploy
- **Deploy syncs credentials to server** — `proxy.yml` is pushed to `/etc/meridian/` after provisioning so `update-stats.py` cron and client mutation refresh work correctly
- **E2E acme.sh stub** — stub now returns rc=0 for `--install-cronjob`/`--install-cert`/`--info` to match new return-code checks

### Added
- **Reproducible redeploy command** — deploy success output shows the exact `meridian deploy` command to redo the same configuration without the wizard

## [3.16.0] - 2026-04-10

### Added
- **Cloudflare setup guidance** — domain-mode deploy success output now prints DNS, SSL/TLS, and WebSocket configuration steps
- **Deploy page fanout** — deploy/redeploy regenerates all saved client handoff pages so branding, domain, and SNI changes reach existing clients
- **SSH hardening via drop-in** — sshd hardening uses an authoritative `/etc/ssh/sshd_config.d/99-meridian.conf` with `sshd -T` validation, overriding cloud-init defaults
- **Nested credential round-tripping** — unknown fields under panel, server, protocols, clients, relays, and branding sections are preserved across CLI versions via `_extra`
- **Relay SSH user persistence** — `relay check` and `relay remove` reuse the stored SSH user from the registry instead of defaulting to root

### Fixed
- **XHTTP nginx path mismatch** — nginx now routes both `/<path>` and `/<path>/` to the XHTTP upstream
- **Firewall cleanup safety** — `ConfigureFirewall` no longer deletes arbitrary user-managed TCP rules; cleanup is limited to Meridian-owned ports
- **Handoff page self-containment** — generated HTML/PWA pages no longer depend on `getmeridian.org/ping`; troubleshooting is self-contained
- **Relay SNI fail-closed** — new relay deploys fail closed when no relay-local SNI is available instead of silently falling back to the default
- **Client credential mutations fail-closed** — `client add` refreshes from server before mutating and rolls back local state if sync fails
- **Release workflow pinning** — `release.yml` now checks out `workflow_run.head_sha` so untested commits can't be released
- **PWA canonical subscription URL** — frontend honors server-provided `subscription_url` instead of reconstructing from `location.pathname`

### Changed
- **No silent patch auto-upgrade** — patch releases are surfaced as a notification with a link to releases; `meridian update` + `meridian deploy` is now explicit

## [3.15.2] - 2026-04-10

### Fixed
- **False local mode detection via TUN** — users connected to a VPN server via TUN mode had their public IP match the server IP, causing `_is_on_server()` to wrongly activate local mode. This made `client add` fail with "No credentials found" and `teardown` run destructively on the local machine. Detection now uses file/directory checks (`/etc/meridian/`) instead of IP matching.

### Added
- **`--geo-block` / `--no-geo-block` flag** — geo-blocking is now optional. Use `--no-geo-block` to skip country-based firewall rules during deploy.

## [3.15.1] - 2026-04-09

### Added
- **IPv6 support** — deploy to IPv6-only or dual-stack servers with `meridian deploy 2001:db8::1`. IPv6 addresses are accepted everywhere: deploy, relay, probe, ping, server commands. Connection URLs bracket IPv6 per RFC 3986. Credential directories use sanitized paths. SCP, TCP connectivity, and public IP detection all handle IPv6 natively.

### Fixed
- **mypy type errors** — `ProvisionContext.credentials` nullable access now guarded in `CreateInbound` and `DeployConnectionPage` steps.

## [3.15.0] - 2026-04-09

### Added
- **Per-relay SNI** — each relay gets its own geographically-appropriate SNI target with a dedicated Xray Reality inbound on the exit server. Passive observation sees plausible domestic traffic; active probes get the correct certificate. Use `--sni` to specify manually or let the scanner pick one automatically.
- **`meridian test` relay support** — ping section checks relay TCP reachability, connection section tests Reality + XHTTP through each relay end-to-end.
- **PWA SNI indicator** — connection cards now show "Appears as: domain" so users understand what their traffic looks like to censors.

## [3.14.4] - 2026-04-09

### Fixed
- **`--server-name` flag drift** — deploy docs (all 4 locales) and README still referenced `--server-name` after it was renamed to `--display-name` in v3.9.0. FA/ZH flag tables also had stale `--email`, `--xhttp`, `--name` flags.
- **PyPI sdist bloat** — source distribution was 38MB because it included blog hero images, website/, tests/, and build artifacts. Now 176KB.

### Added
- **CI drift validators** — `validate_cli_docs.py` checks all 19 commands' flags are documented in cli-reference.md (replaces deploy-only check). `validate_apps_sync.py` validates `_PWA_APPS` in render.py matches apps.json.
- **Global flags in cli-reference** — `--user`, `--server`, `--sni`, `--domain` documented once in Global flags section instead of repeated per command.
- **CLAUDE.md coverage** — updated all 15 existing files, created 3 new ones (layouts, i18n, content/docs). Added missing features: WARP, post-quantum, relay routing, color palettes, clock skew detection.

## [3.14.3] - 2026-04-09

### Fixed
- **Relay not found in local mode** — `relay list`, `relay check`, and `relay remove` now find relays when running on the exit server itself (`deploy local`). Previously they only checked `~/.meridian/credentials/` and missed `/etc/meridian/` where local-mode credentials are stored. (#9)
- **False client page warnings during relay deploy** — `upload_client_files()` returns empty string on success, but the check was inverted (`if not result`), causing every successful upload to show "Could not update connection page". (#9)

## [3.14.2] - 2026-04-09

### Fixed
- **Socket leaks** — `tcp_connect()` and `_get_cert_der()` now use context managers to ensure cleanup on exception paths.
- **nginx `return 444` in HTTP block** — invalid stream directive replaced with `return 403;` for IP-mode deployments.
- **WARP deploy silent failures** — `systemctl enable`, `set-proxy-port`, and `warp-cli connect` return codes now checked; step fails instead of claiming success.
- **Firewall policy silent failures** — `ufw default deny/allow` return codes now checked in both exit and relay firewall steps.
- **Reality port conflict** — standalone mode now pre-checks port 443 availability before creating Xray inbound.
- **SSRF in icon download** — `_process_image_url()` now blocks private/loopback/reserved IP addresses.
- **IPv6 URL formatting** — protocol URL builders now wrap IPv6 addresses in brackets per RFC 3986.
- **Fragile tmp_zip cleanup** — `ensure_xray_binary()` uses reliable `None` init instead of `"in dir()"` check.
- **Broad exception handlers** — narrowed `except Exception` to specific types in QR generation, HTTP date parsing, and version checking.
- **Redundant branding condition** — removed duplicate check in deploy setup.
- **`SystemExit` catch anti-pattern** — added `try_resolve_server()` wrapper; uninstall command uses the safer pattern.
- **E2E Docker image** — added missing `fail2ban` package that was breaking CI.

### Improved
- **Type annotations** — `_deploy_client_page()` now has typed `list[ProtocolURL]` and `list[RelayURLSet]` parameters.
- **Test coverage** — added 80 new tests: branding module (37), xray_client module (35), render template content assertions (8). Total: 721 tests.

## [3.14.1] - 2026-04-08

### Improved
- **Panel URL in deploy output** — post-deploy now shows the 3x-ui panel URL and credentials for advanced users.

## [3.14.0] - 2026-04-08

### Added
- **Cloudflare WARP outbound** — new `--warp` flag routes server egress through Cloudflare's network. Destination sites see a Cloudflare IP instead of the VPS IP. Useful when sites block datacenter IP ranges. Installs `cloudflare-warp` in SOCKS5 proxy mode; incoming connections (SSH, nginx) are unaffected. Connection failure is a warning, not a hard fail — WARP endpoints are blocked in some regions.
- **Panel URL in credentials** — `proxy.yml` now includes a `panel.url` field with the full panel URL, so users don't have to assemble it from parts.
- **Camouflage vs. domain explainer** — deploy docs (EN/RU) now have a "Camouflage target vs. domain" section explaining the difference and why you can't use your own domain for both.

### Fixed
- **Web panel proxy headers** — nginx panel location block was missing WebSocket upgrade headers needed by 3x-ui web UI. Added `proxy_http_version 1.1`, `Host`, `Upgrade`, and `Connection` headers.
- **`meridian test` with WARP** — when WARP is enabled, exit IP check now expects a Cloudflare IP instead of the server IP.

### Improved
- **Wizard camouflage text** — expanded to explain it's any popular site you don't own, not a domain you control.
- **Install location clarity** — getting-started docs (EN/RU) clarify that both local and VPS work, recommend local for `meridian test` and credential safety.

## [3.13.0] - 2026-04-08

### Added
- **Post-quantum encryption** — opt-in ML-KEM-768 + X25519 hybrid encryption for the Reality protocol. Enable with `--pq` flag or interactive wizard. Generates keys via `xray vlessenc`. Only affects Reality; XHTTP and WSS use TLS and are unaffected.
- **End-to-end connection testing** — `meridian test` now verifies actual proxy traffic, not just reachability. Downloads a local xray client, connects through the proxy for each active protocol (Reality, XHTTP, WSS), and confirms traffic flows end-to-end.
- **Server name in URL fragments** — when `--display-name` is set, client app connection names show "alice @ My VPN" instead of just "alice". Applies to all protocols and relay URLs.
- **OG meta tags** — PWA connection pages now include OpenGraph meta tags for social media link previews.
- **Relay connectivity retry** — relay verification retries the service check up to 4 times with 2s delay, preventing false failures when Realm needs time to bind after start.

## [3.12.0] - 2026-04-07

### Added
- **Server-side geo-blocking** — all deployments now block Russian traffic (`geosite:category-ru` + `geoip:ru`) at the Xray routing level via blackhole outbound. No manual panel configuration needed.
- **Enhanced `meridian doctor`** — new diagnostic sections: Xray process status, nginx error log, TLS certificate expiry with warnings, geo-blocking verification, deployment context (mode, protocols, client count). Every remote section now shows the exact command that produced the output.
- **Panel recovery in `client show`** — if a client is missing from local credentials (e.g. added from a different machine), `show` now recovers the data from the live panel automatically and syncs back.

### Fixed
- **`client show` missing XHTTP URLs** — console output skipped XHTTP connection links while the HTML share page worked. The `xhttp_path` in credentials is now the sole signal for XHTTP availability.
- **`meridian doctor` PermissionError** — crashed when run as non-root on the server. Now handles permission errors gracefully.
- **Stale container on deploy** — deploying to a server with a leftover 3x-ui container (custom credentials from a previous deployment) failed with "Empty response from login endpoint". Now resets the container DB and retries.
- **First client missing from XHTTP/WSS** — the deploy-time client was only added to the Reality inbound, not XHTTP or WSS. Now added to all active protocol inbounds.
- **UFW hard-failure on relay** — relay deployment crashed if `ufw` wasn't pre-installed. Now installs it automatically before configuring firewall rules.
- **Probe false positive on relay nodes** — SNI consistency warning now clarifies that certificate variation is expected for relay nodes (TCP forwarders).

### Changed
- **CLI flag renames** — `--name` → `--client-name`, `--server-name` → `--display-name`. Branding flags grouped in `--help`. Removed `--email` and `--xhttp` (wizard handles these).
- **Teardown output** — now suggests `meridian deploy IP` as a next step.

## [3.11.2] - 2026-04-07

### Added
- **Subscription QR code** — connection page now shows a QR code for the subscription URL. Scanning it imports all protocols at once instead of requiring per-protocol QR scans.
- **Version-level update warnings** — patch updates install silently, minor versions note possible behavior changes, major versions highlight breaking changes and recommend redeploying.

### Fixed
- **Docker compose on pre-installed Docker** — servers with `docker.io` (not `docker-ce`) lacked the compose plugin. Now detected and installed automatically.

## [3.11.1] - 2026-04-07

### Fixed
- **Docker credsStore on headless servers** — `docker compose pull` failed on old Ubuntu servers where Docker defaulted to the `secretservice` credential helper (D-Bus unavailable). Now stripped automatically after installation using `jq`. (#6, #7)
- **Domain in connection page URLs** — share links and deploy output now use the configured domain instead of raw server IP. SNI target shown in deploy summary when non-default. (#2, #3)

### Changed
- **nginx auto-upgrade** — if the distro nginx is too old (< 1.16), Meridian adds the official nginx.org repo and upgrades automatically, mirroring the Docker installation pattern. Previously failed with a manual upgrade message. (#8)

## [3.11.0] - 2026-04-03

### Added
- **Deep link overhaul** — 5 → 13 proxy client apps with one-tap import. Fixed v2RayTun deep link (the #1 user complaint). Added ShadowRocket, V2Box, FoXray, Happ, Karing, sing-box, FlClash, NekoBox. Three URL encoding modes: percent-encoded, raw path, base64.
- **Platform-specific download URLs** — cross-platform apps show App Store links on iOS, Google Play on Android, instead of generic GitHub/website links. Non-matching platforms collapsed under "Other platforms."
- **Numbered choices in deploy wizard** — all Y/n prompts replaced with numbered options. Manual SNI entry without scan. Rich spinner during scan. Deploy summary shows hardening status.
- **`choose()` helper** — new console utility for numbered selection prompts, documented in commands/CLAUDE.md.

### Fixed
- **SNI scan using private VPC IP** — servers with VPC addresses (e.g. 10.129.x.x) scanned a private /24 subnet instead of the public IP network. Now excludes all RFC 1918 ranges.
- **Partial deploy recovery** — if a previous deploy failed mid-"Configure panel", re-deploy detected stale credentials, nukes the panel DB, and reconfigures from scratch automatically.
- **Panel wait before login** — both "Configure panel" and "Log in to panel" steps now wait for the panel to become responsive before attempting login. Fixes "Empty response from login endpoint" on slow servers.
- **Panel health check** — checks `/login/` endpoint instead of `/` (which returns 404 when webBasePath is set). Accepts any HTTP response, not just 200.
- **Relay error messages** — "relay cannot reach exit" now includes a 3-point checklist and manual test command.
- **sudo detection** — non-root SSH to servers without sudo gives actionable hint instead of generic SSH failure.
- **Scan junk filtering** — filters Kubernetes ingress fake certs, self-signed placeholders from scan results.

## [3.10.9] - 2026-04-03

### Fixed
- **nginx stream `map_hash_bucket_size`** — long SNI hostnames (e.g. `learn.microsoft.com`) exceeded the default 32-byte bucket, failing `nginx -t` on deploy. Set to 128.

### Changed
- **Backlog cleanup** — 171 → 78 items. Dropped theoretical threats, overengineered fixes, all P3 items, and refactoring debt. Added community feedback from vas3k.club launch.
- **README** — added Community section with launch post link.

## [3.10.8] - 2026-04-01

### Changed
- **Disable Xray logs on deploy** — new `DisableXrayLogs` step patches the Xray config template to set `access="none"` and `error="none"`. Runs on every deploy (idempotent), so existing servers get logs disabled on re-deploy.

### Fixed
- **Probe SNI consistency false positive** — the check tested 3 different unknown SNIs which get TCP-proxied to the Reality dest; the dest CDN returns different certs for different SNIs (expected behavior). Now tests the same SNI repeatedly to verify deterministic routing.

## [3.10.7] - 2026-04-01

### Changed
- **nginx stream idle timeout** — increased `proxy_timeout` from 10m (default) to 30m so idle VPN sessions aren't killed prematurely. Added `proxy_socket_keepalive` to keep relay→exit TCP connections alive through NATs/firewalls.
- **XHTTP upstream keepalive** — XHTTP sub-requests now reuse TCP connections to Xray via an upstream keepalive pool (`keepalive 32`) instead of opening a new connection per request. Reduces latency, especially in `packet-up` mode and through relay nodes.

## [3.10.6] - 2026-04-01

### Fixed
- **Relay re-bind after exit teardown** — `relay deploy` now detects a previous Realm process on the port and stops it instead of failing with "port already in use". Fixes the teardown → re-deploy → relay bind flow.
- **Teardown leaves zombie relays** — `teardown` now stops and disables relay nodes before uninstalling the exit server, preventing orphaned Realm processes forwarding to a dead exit.

## [3.10.5] - 2026-04-01

### Fixed
- **Probe false positives on Meridian-deployed servers** — suspicious port scan now verifies with an HTTPS request before flagging. Cloud middleboxes that complete TCP handshakes but serve no content are reported as informational, not issues.
- **Probe SNI consistency false positives** — certificates are now compared by subject+issuer identity (via openssl) instead of raw bytes. CDN cert rotation (same origin, different serial numbers) no longer triggers warnings.
- **XHTTP upstream timeouts** — nginx reverse proxy `proxy_read_timeout` increased from 360s to 86400s for XHTTP location blocks. XHTTP `mode=auto` lets clients negotiate streaming modes (`stream-one`/`stream-up`) with long-lived connections that previously timed out. Added `proxy_send_timeout` and `proxy_request_buffering off`.

### Changed
- **Probe verdict** — removed circular "Meridian handles these automatically: `meridian deploy`" suggestion. Replaced with neutral diagnostic messaging.

## [3.10.4] - 2026-04-01

### Fixed
- **SSH timeout crashes** — `conn.run()` now catches `TimeoutExpired` and returns a graceful failure (rc=124) instead of crashing with a raw Python traceback. Every provisioning step benefits.
- **Misleading "ufw not found"** — a timeout on `which ufw` was reported as "ufw not found" instead of showing the actual timeout error.
- **Opaque PWA upload errors** — `"Failed to upload shared PWA assets"` now includes the filename and stderr (e.g. `"Failed to upload pwa/app.js: Command timed out after 30s"`).

### Changed
- **Increased all SSH timeouts for low-end machines** — simple commands 5s→15s, file writes 15s→30s, Docker/systemctl 30s→60s, apt/acme.sh 120s→180s, Docker pull 180s→300s. Accounts for SSH handshake overhead on cheap VPS providers.

## [3.10.3] - 2026-03-31

### Fixed
- **XHTTP URL missing `sni` and `fp` parameters** — Shadowrocket (and potentially other clients) failed to connect via XHTTP because the URL lacked TLS parameters. Reality URLs had them; XHTTP URLs did not. Fixed in all 4 URL generation locations.
- **Rich markup eating `[y/N]` prompt hints** — the relay offer prompt showed no hint about how to decline because Rich parsed `[y/N]` as a markup tag. All y/n prompts now display correctly.

### Changed
- **Replaced `qrencode` binary with Python `segno` package** — QR codes are now generated in pure Python. No more broken QR codes when system `qrencode` is not installed. Removes the `qrencode` apt dependency from server provisioning too.

## [3.10.2] - 2026-03-31

### Fixed
- **`meridian probe`** — no longer aborts when port 443 is closed. Remaining checks skip gracefully, and the port scan still reports other open ports.

## [3.10.1] - 2026-03-31

### Fixed
- **`--no-harden` port 443 bug** — deploying with `--no-harden` on a server with an active firewall left port 443 blocked. New `EnsurePort443` step adds the rule when ufw is already active, without touching SSH or other firewall settings.

## [3.10.0] - 2026-03-31

### Added
- **`meridian probe`** — censor-perspective server analysis. Runs 9 checks from the client side (no SSH): port surface, HTTP response, TLS certificate, SNI consistency, proxy path probing, WebSocket upgrade, reverse DNS, HTTP/2 ALPN, and legacy TLS versions. Works on any server, not just Meridian deployments. Accepts IPs and domain names.
- **Non-LTS Ubuntu detection** — preflight warns on non-LTS Ubuntu releases (only 9 months of support). Provisioner detects EOL package repos and gives a clear error.

## [3.9.0] - 2026-03-30

### Added
- **Decoy mode** (`--decoy 403`) — unknown paths return a stock nginx 403 page instead of aborting the connection. Server header, Content-Type, and response body match real nginx. CONNECT method handled. HTTP/3 disabled in decoy mode to prevent alt-svc header leak.
- **Firewall cleanup** — deploy now removes stale UFW rules from previous setups, keeping only ports 22, 443, and optionally 80.

### Changed
- **Vision refresh** — README, website hero, docs, and CLAUDE.md updated around "deploy it right, share it easily" messaging. Emphasizes airtight deployment over protocol choice.
- **Hero kicker** — "Open-source privacy tool" → "Open-source internet freedom tool" (English and Chinese aligned with existing Russian/Farsi translations).
- **PyPI description** updated to reflect hardened-by-default messaging.

## [3.8.3] - 2026-03-25

### Added
- **One-tap subscription import** — deep link buttons on connection pages let users import auto-updating subscriptions with a single tap. Supports v2rayNG, Hiddify, and Streisand URI schemes. Replaces the hidden manual-copy subscription flow.
- **Streisand app** — added as an iOS client option (popular in censored regions, supports deep links).
- **Branding flags in CLI reference** — `--server-name`, `--icon`, `--color` now documented in cli-reference across all 4 locales.

## [3.8.2] - 2026-03-25

### Added
- **Server branding** — personalize connection pages with `--server-name`, `--icon` (emoji or image URL), and `--color` (6 curated palettes: ocean, sunset, forest, lavender, rose, slate). Interactive wizard prompts for all three. Branding stored in credentials and rendered in PWA.

### Fixed
- **Connection pages unreachable in IP-only mode** — browsers don't send SNI for bare IP addresses (RFC 6066), so HAProxy dropped every connection before reaching Caddy. Added no-SNI routing rule. Also added Caddy `abort` catch-all so censors probing unknown paths see nothing.

## [3.8.1] - 2026-03-25

### Fixed
- **Blank demo page** — `jinja2` was only a dev dependency but needed at runtime for PWA template rendering. Moved to main dependencies. Added `qrencode` to CI for QR code generation
- **PyPI README** — replaced relative image paths with absolute URLs, replaced Mermaid diagram (unsupported on PyPI) with architecture SVG image
- **CI demo validation** — fixed `find` command that incorrectly excluded all `index.html` files

### Changed
- **Command Builder elevated on homepage** — moved from section 7 to section 4 (after "How it works") for better discoverability
- **Trust bar updated** — 290+ → 480+ tests across all 4 locales
- **SECURITY.md** — documented update check transparency (reads PyPI only, no telemetry)

### Added
- **Backlog items** — `meridian client export` for offline HTML sharing, live GitHub stars in trust bar

## [3.8.0] - 2026-03-24

### Security
- **XSS in inline onclick handlers** — replaced all `onclick="shareUrl('...')"` patterns with `data-url` attributes + delegated event listener. URLs no longer interpolated into inline JS strings
- **Content-Security-Policy** — added CSP header to Caddy connection page block (`default-src 'self'; img-src 'self' data:`)
- **Remove VPN identifiers from client-facing pages** — share titles no longer say "VPN", footer "Powered by Meridian" and GitHub link removed, manifest name changed to "Setup"
- **Service worker cache anonymity** — cache key changed from `meridian-pwa-v1` to `pwa-v1`

### Added
- **RTL CSS support for Farsi** — all directional properties converted to CSS logical properties (`margin-inline-start/end`, `text-align: start/end`, `border-inline-start`)
- **i18n: 15+ previously hardcoded strings now translated** — toast "Copied", stats "Active now/ago", page title, relay "via {name}", error messages, noscript fallback (RU/FA/ZH)
- **config.json error handling** — retry button + 10-second loading timeout with translated messages
- **Keyboard accessibility** — `tabindex`, `role="button"`, Enter/Space handlers on clickable URL divs; `role="alert"` + `aria-live` on toast
- **40 new tests** — upload pipeline, Caddy XHTTP block, `handle_path` structure, config.json schema, `_PWA_APPS`↔`apps.json` sync, Unicode client names, `_render_stats_script`

### Changed
- **Landing page restructured** — added trust bar (MIT, 290+ tests, 4 languages, open source), final CTA, removed redundant Reference section; reordered: Technology before Command Builder
- **README improved** — "Why Meridian?" comparison table, architecture diagram promoted above CLI table, CLI reference trimmed to 8 essentials
- **Architecture diagram** — hand-crafted SVG replacing missing PNG (was 404)
- **Locale-aware docs links** — `updateDocsLinks()` rewrites `/docs/en/` to active locale on language switch
- **GitHub Discussions** enabled
- **GitHub topics** — removed stale `ansible`, added `anti-censorship`, `vpn`, `censorship-circumvention`, `python`, `cli`

### Fixed
- **install.sh deprecated commands** — `setup`→`deploy`, `check`→`preflight`, `ping`→`test`, `version`→`--version`
- **`prefers-reduced-motion`** — scroll-reveal animations and smooth scrolling now respect user preference
- **Stats files unreadable by Caddy** — cron script wrote files as root with `0o600`; changed to `0o644` (directory is already access-controlled)
- **Service worker `networkFirst` returned `undefined`** — now returns 503 Response on full cache+network miss
- **Service worker stale assets** — replaced `cacheFirst` with stale-while-revalidate pattern (serve cached, refresh in background)
- **Silent template failures** — `render.py` bare `except` now logs warning instead of silently returning empty string
- **`upload_client_files` could exceed ARG_MAX** — switched from `printf '%s'` to base64 transport matching `upload_pwa_assets`
- **`mkdir -p` return codes unchecked** — both upload functions now check and return False on failure
- **Caddy config duplication** — extracted shared connection-page block into `_render_connection_page_block()` helper
- **Farsi question mark** — ASCII `?` → Persian `؟` (U+061F)
- **Deep links** — Android uses `vless://` scheme instead of Hiddify-specific `intent://`; iOS no longer silently overwrites clipboard
- **Focus outline** — `summary:focus{outline:none}` → `:focus:not(:focus-visible)` to preserve keyboard focus ring

## [3.7.4] - 2026-03-24

### Fixed
- **Caddy PWA cache headers** — `handle` + `uri strip_prefix` caused path matchers to evaluate before prefix stripping, so `@pwa_assets path /pwa/*` never matched; switched to `handle_path` which strips at the handle level
- **Mypy type errors in dev.py** — fix bytes/str variable shadowing and SimpleHTTPRequestHandler init typing
- **Ruff formatting** — fix import order in dev.py, formatting in services.py

## [3.7.2] - 2026-03-24

### Added
- **`meridian deploy local`** — run directly on the server without SSH. Accepts `local` or `locally` wherever an IP is expected (`deploy`, `check`, `client add`, etc.)
- **Interactive wizard local detection** — auto-detects root on VPS and offers local deployment, skipping IP/SSH prompts
- **Service health watchdog** — 5-minute cron checks Xray, Caddy, HAProxy and restarts crashed services (logged via syslog)
- **Disk space pre-check** — provisioner fails early if <2GB free, before pulling Docker images
- **Systemd restart policies** — HAProxy and Caddy get `Restart=on-failure` drop-in overrides; relay gets `StartLimitBurst=5`
- **Realm SHA256 verification** — binary checksum verified after download (pinned digests in `config.py`)
- **Provisioner test suite** — 64+ new tests across provisioner steps, relay, and pipeline

### Fixed
- **Host key verification** — write only the verified key type to `known_hosts` (was writing all scanned types); refuse to auto-accept in non-interactive mode
- **Cookie file race window** — panel login cookie created with `umask 077` in subshell (was world-readable until separate chmod)
- **Realm config permissions** — `realm.toml` set to `chmod 600` (was 644, contained exit IP in plaintext)
- **Stats files permissions** — per-client JSON stats set to `chmod 600` (was 644, revealed traffic patterns)
- **CORS on private pages** — removed `Access-Control-Allow-Origin: *` from connection page Caddy config
- **Credential dir permissions** — explicitly enforce `chmod 0o700` after mkdir (was ignored on existing dirs)
- **Shell injection in sysctl** — BBR persistence uses `shlex.quote()` + `printf` instead of unquoted `echo`
- **Stats script auth** — panel password URL-encoded in generated cron script
- **Stats cron logging** — output piped to `logger -t meridian-stats` (was silent on failure)
- **Teardown cleanup** — removes health watchdog cron, systemd restart overrides, and runs `daemon-reload`
- **E2E test infrastructure** — pre-populate `known_hosts` for port-2222 sshd, fix misleading error message
- **Mypy** — fix `_check_ports` type annotation (`object` → `ServerConnection`), add `StepStatus` literal type

### Changed
- `detect_public_ip()` moved from `setup.py` to `resolve.py` (shared across wizard and server resolution)

## [3.7.1] - 2026-03-23

### Fixed
- **HAProxy port mismatch** — HAProxy hardcoded Reality backend port 10443 instead of using the actual IP-derived port, causing VPN to connect but produce no internet traffic

## [3.7.0] - 2026-03-22

### Added — Website (getmeridian.org)
- **New Astro website** replacing old static HTML docs — 45 pages, fully static
- **Full i18n**: 30 translated doc pages (10 each for Russian, Farsi, Chinese) + landing page UI translations (~250 keys)
- **CommandBuilder**: 5-tab interactive CLI builder (Deploy, Preflight, SNI Scan, Doctor, Teardown) with real-time command generation, localStorage persistence, IPv4 validation
- **Pagefind search**: static full-text search across all 40 doc pages in 4 languages
- **Scroll reveal animations**: IntersectionObserver fade-in-up on landing page sections
- **Mobile navigation**: hamburger menu at <640px with slide-down drawer
- **404 page**: branded with noindex meta tag
- **Sitemap** (`@astrojs/sitemap`) and `robots.txt`
- **Mermaid diagram rendering** via `rehype-mermaid` (build-time inline SVGs)
- **llms.txt endpoints**: `/llms.txt`, `/llms-full.txt`, `/md/{locale}/{slug}` for AI consumption
- **Terminal deploy SVG**: recorded from live server deploy, sanitized with RFC 5737 example IPs
- **Locale-aware docs**: sidebar filters by locale, locale switcher buttons, `<link rel="alternate" hreflang>` for SEO
- **hreflang links** on all doc pages for proper multi-language indexing
- **CSS sync script** (`sync-template-css.mjs`): extracts tokens from Astro and injects into Jinja2 template (ready for SYNC markers)
- **App links single source of truth**: `src/data/apps.json` validated by CI and pre-push hook against Jinja2 template

### Changed — Website
- **Self-hosted fonts**: Fraunces, Source Sans 3, JetBrains Mono served from `/fonts/` — zero external requests to Google (critical for users in censored regions)
- **Demo page dark mode**: all hardcoded hex colors replaced with CSS custom properties
- Protocol card colors (`--blue`, `--amber`) added to design token system with dark mode variants
- Early `<script>` in `<head>` sets `lang`/`dir` from localStorage before paint (prevents RTL layout shift)
- Docs redirect (`/docs/`) detects stored locale preference before redirecting

### Changed — CI/CD
- **Release workflow** builds Astro site instead of copying old `docs/`
- **Website Build** job added to CI pipeline
- Actions upgraded: `setup-node` v6 (Node 24, npm caching), `setup-python` v6, `setup-uv` v7, `upload-pages-artifact` v4
- Playwright Chromium installed in CI/release for Mermaid rendering
- Pre-push hook validates app links against `apps.json` (was `docs/demo.html`)
- AI docs source moved to `website/src/content/ai/` (Makefile, hooks, workflows updated)

### Changed — Repository
- `docs/` directory removed entirely — all content consolidated under `website/`
- README.md image paths updated to `website/public/img/`

### Security
- `<meta name="referrer" content="no-referrer">` prevents origin leaks for at-risk users
- Ping page XSS fix: HTML-escape all localStorage-rendered values
- Demo page uses RFC 5737 documentation IPs (was real-ish subnet)
- Terminal SVG uses example IPs and redacted paths (was live server data)
- All `target="_blank"` links have `rel="noopener"`

### Accessibility
- `aria-current="page"` on active sidebar link
- `aria-label` on search input
- `for=` attributes linking CommandBuilder labels to inputs
- Translatable timeline steps, code block labels, callouts, TOC label, "then" separator

## [3.6.0] - 2026-03-22

### Changed
- **BREAKING:** XHTTP now routed through port 443 via Caddy (no extra port exposed)
  - URLs simplified: `security=tls`, path-based routing, no Reality params
  - Old XHTTP URLs (with separate port + Reality) no longer work
  - XHTTP remains enabled by default — it's now a free fallback with zero cost
- Deploy wizard redesigned with protocol explanation, Rich Panel summary
- Inline SNI scan: wizard offers to scan for optimal camouflage target (~30s)
- "Camouflage target" terminology replaces "SNI" in user-facing text
- Flag descriptions improved for non-VPN-expert users

### Added
- `scan_for_sni()` extracted as reusable function for wizard integration
- `xhttp_path` field in credentials (random 16-char path for Caddy routing)
- Caddy reverse proxy for XHTTP in both IP and domain modes

### Fixed
- XHTTP no longer opens external firewall port (localhost-only)

## [3.5.0] - 2026-03-22

### Added
- Codified architecture patterns in CLAUDE.md for consistent development
- Light mode support for website and ping tool
- WCAG accessibility improvements (color contrast, keyboard focus, ARIA labels)
- Step counter in provisioner output ([1/14] format)
- SNI and XHTTP visibility in setup wizard summary
- qrencode missing warning
- Mermaid architecture diagrams replacing ASCII art
- CHANGELOG.md

### Fixed
- Documentation accuracy for v3.4.0 architecture (HAProxy+Caddy in all modes)
- XHTTP checkbox default in website command builder
- base64 encoding cross-platform consistency in services.py
- render.py template code duplication
- HTML escaping in minimal fallback renderer

### Changed
- Renamed CLI commands for clarity: `setup` → `deploy`, `check` → `preflight`, `ping` → `test`, `diagnostics` → `doctor` (alias: `rage`), `uninstall` → `teardown`, `self-update` → `update`
- Removed `version` subcommand (use `--version` / `-v` flag instead)
- Improved CLI help text for deploy, preflight, test, doctor commands
- Improved connection page QR code guidance for non-technical users
- Reordered client add terminal output (shareable URL first)

## [3.4.0] - 2025-03-21

### Added
- Server-hosted connection pages via Let's Encrypt IP certificates
- HAProxy + Caddy deployment in standalone mode (all modes now use this architecture)
- Per-client hosted pages with shareable URLs
- Usage stats on server-hosted connection pages
- `render_hosted_html()` for server-hosted page rendering

### Changed
- Architecture: all modes now deploy HAProxy (port 443) + Caddy (port 8443) + Xray
- Connection pages automatically deployed during `client add`

## [3.3.1] - 2025-03-20

### Fixed
- Uninstall cron grep pattern never matched actual cron entry
- i18n textContent stripped ping test link for RU/FA/ZH users
- test_panel.py body format test always passed due to `assert X or Y`

### Changed
- Completed output.py migration to urls.py/render.py/display.py
- Centralized magic values in config.py
- Normalized provisioner step names to human-readable format

## [3.3.0] - 2025-03-18

### Fixed
- `--xhttp` flag was always True, now proper `--xhttp/--no-xhttp` toggle
- `InstallHAProxy` status always "changed" (copy-paste bug)
- `ConfigureFirewall` idempotency was fake

### Removed
- Complete Ansible purge (88 files, -3,785 lines)

### Changed
- Provisioner refactored: timed decorator deduplicated, PanelClient methods public
- Setup success celebration message
- Better error messages throughout

## [3.2.0] - 2025-03-15

### Added
- Python provisioner engine (15 steps replacing Ansible)
- Protocol foundation (ProtocolURL, dict registry)
- Output split into urls.py / render.py / display.py
- Error taxonomy and sudo escalation
- PanelClient context manager

### Removed
- Ansible fully deleted (-2,825 lines)

## [3.1.0] - 2025-03-12

### Added
- Uninstall provisioner
- E2E test pipeline
