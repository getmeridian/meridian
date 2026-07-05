# Backlog

Prioritized task list for Meridian development.
Version history is in [CHANGELOG.md](CHANGELOG.md).

---

## Manual / External

Things that require human action outside the codebase.

### Housekeeping

- [ ] **Create 3-5 "good first issue" GitHub issues** — pull from P2 items below to signal contributor-friendliness
- [ ] **Regenerate OG image** — `og.png` shows old domain `getmeridian.com`; needs `getmeridian.org`
- [ ] **Retake connection page screenshot** — `connection-page.png` shows stale domain in browser bar
- [ ] **Set GitHub social preview** — repo Settings > Social preview (depends on new OG image)
- [ ] **Write VPS onboarding guide** — ~300-word doc page: Hetzner/DigitalOcean, Debian 12, SSH keys, IP retrieval

### Launch channels (priority order)

- [x] **Вастрик.Клуб** — [launched 2026-04-02](https://vas3k.club/project/31264/), 78 upvotes, 3k+ views, 100+ GitHub stars overnight
- [ ] **r/selfhosted post** (~350k) — lead with architecture diagram, terminal SVG, demo page. Be upfront about no web UI
- [ ] **Show HN** — "Deploy an undetectable VLESS+Reality proxy in one command". Discuss uTLS, HAProxy SNI, threat model
- [ ] **r/opensource post** (~335k) — mission framing: "help people in censored regions share secure internet"
- [ ] **Censorship expert TG channel post** — author of [state-of-usa-itd](https://habr.com/ru/articles/1014038/) offered to post about Meridian
- [ ] **Niche communities** — r/iran, Telegram V2Ray/Xray groups (Persian, Chinese), r/privacy (engage, don't self-promote)

### Post-launch

- [ ] **Technical blog post** — nginx+Xray architecture deep-dive. Cross-post to dev.to
- [ ] **"Meridian vs raw 3x-ui" comparison page** on website — captures search traffic
- [ ] **Respond to every comment** within 24h of launch posts

---

## P0 — Critical

### Anti-censorship

- [x] **Relay SNI mismatch** — relay deploy now scans its own IP range and picks a locally plausible SNI, independent of exit node. Fails closed when no relay-local SNI is available
- [ ] **Residual IP-cert fingerprinting path in IP mode** — keep tightening the direct-IP / no-SNI probe surface so the server does not hand out a distinctive nginx-served IP certificate response outside the intended camouflage path
- [x] **Fix XHTTP nginx path mismatch** — nginx now routes both `/<xhttp_path>` and `/<xhttp_path>/` to the XHTTP upstream, matching URL generation and Xray config

### Product

- [ ] **Client migration for rebuilds** — `meridian deploy NEW_IP --from OLD_IP` or equivalent so SNI, domain, clients, relays, and hosted handoff state can move without manual reconstruction
- [ ] **Eliminate state split-brain between local cache and server** — deploy now force-refreshes before provisioning; remaining work is to make branding writes, scanned-SNI writes, and deploy-time publish/sync paths fail closed so stale local `proxy.yml` never silently wins
- [ ] **Redeploy must update live state before publishing new handoff state** — never save or render URLs/pages/subscriptions that the live server is not yet serving
- [ ] **Partial panel recovery must preserve existing clients and relays** — recovery in `ConfigurePanel` still rebuilds only baseline state; it needs to reconstruct known clients, relay inbounds, and hosted pages from credentials before claiming success

### Security / Supply Chain

- [x] **Pin release artifacts to the CI-passed commit** — `release.yml` triggered by `workflow_run` now checks out `github.event.workflow_run.head_sha` for Pages, tags, and PyPI publishing
- [ ] **Replace mutable install/update trust chain with pinned, verified artifacts** — finish the release-artifact + checksum path for install/update flows
- [ ] **Stop executing unsigned remote scanner binaries as root** — pin and verify scanner artifacts, or vendor the scanner locally

---

## P1 — High

### Security

- [x] **SSH password auth not hardened during provisioning** — sshd hardening now uses an authoritative drop-in with `sshd -T` validation, overriding cloud-init
- [x] **Firewall cleanup deletes user's custom rules** — cleanup is now limited to Meridian-managed ports instead of deleting arbitrary user TCP rules
- [ ] **Remove public 3x-ui management from the shared 443 identity** — move the operator surface off the public camouflage identity or require an explicit operator-only access path
- [ ] **Stop contradicting the self-hosted / no-tracking trust story on docs pages** — the website promises "Open source. No tracking. No accounts." and the repo vision says self-host everything, but docs currently load a third-party widget. For a censorship-resistance product this is not a cosmetic inconsistency; it weakens the core trust claim right when users are evaluating operational discipline. Either remove the external dependency or narrow the public promise so the product does not overstate its privacy posture

### Anti-censorship

- [ ] **Legacy relays need SNI backfill** — new relay deploys now fail closed without a relay-local SNI; remaining work is to repair legacy relay entries with blank `relay.sni` and regenerate affected hosted pages
- [ ] **Default SNI `www.cloudflare.com` monitored** — make scanning the normal path, not the exceptional one
- [ ] **Make probe/check tooling mode-aware** — domain mode intentionally serves a real domain cert; verification must distinguish that from an IP-mode stealth leak

### Product

- [ ] **VPS provider guide** — first blocker for Tier 1 "tech friends"
- [ ] **Auto SSH key setup** — when VPS only has password auth, auto-generate key, copy via `ssh-copy-id`, and proceed. Eliminates manual key setup step before deploy
- [ ] **Telegram bot for client management** — add/revoke clients, view stats without SSH. Mobile-friendly for "not at computer" use case
- [x] **Post-deploy Cloudflare setup guidance** — domain-mode deploy now prints the DNS-only → proxied and SSL/WebSocket steps in CLI success output
- [ ] **Add Happ and ShadowRocket to connection page** — popular cross-platform clients, already support VLESS subscription URLs
- [ ] **Wizard hardening before SSH key validation** — can lock out password-only users
- [ ] **Connection page plain-language intro** — 2-3 trust-building sentences before "scan QR"
- [ ] **`client list` with usage stats** — last-seen, traffic totals via 3x-ui `getClientTraffics/{email}`
- [ ] **`client disable`/`client enable`** — panel API supports it, just needs CLI exposure
- [ ] **Proactive IP block detection** — server self-checks via ping endpoint, notifies via webhook/Telegram
- [ ] **Rebuild state transfer** — once `--from` exists, make the CLI explain what is being copied, what is live, and what still needs redeploy
- [ ] **Make destructive mutations transactional** — keep remote cleanup, local credential mutation, registry writes, and hosted page updates in one fail-closed transaction boundary
- [ ] **Require explicit server identity for risky commands** — enforce unique server aliases, separate deployer aliasing from recipient-facing branding, and add clearer target confirmation for destructive/stateful commands. Current implicit auto-select/local-mode behavior is too easy to mis-target
- [x] **Regenerate all hosted client pages when shared server state changes** — deploy/redeploy now also refreshes saved client pages after branding/domain/SNI changes
- [ ] **Unify deployer-facing and recipient-facing naming** — `--display-name` and `--server` currently model different identities but docs and UX blur them together badly enough that the documented multi-server flow is wrong (`--display-name finland` is presented as if it creates a `--server finland` alias). Either unify the concepts or make the distinction explicit everywhere: deploy flags, registry model, README, CLI help, and docs examples
- [x] **Hosted connection page must stay self-hosted in recovery flows** — generated handoff pages no longer depend on `getmeridian.org/ping`; remaining work is to remove other third-party recovery/install dependencies from the critical path
- [ ] **Make docs, README, command builder, and CLI behavior agree exactly** — several first-run surfaces drift independently today: broken deploy examples, stale/deprecated flags in docs, command-builder omissions, and claims like "supports all Meridian CLI operations" that are not true. For a product selling correctness, product-surface drift is a real trust bug, not just a docs bug
- [ ] **Recalibrate onboarding promises to the real setup cost** — public copy currently leans on "one command", "three steps", and "two minutes" while the actual flow still requires VPS selection, SSH key access, terminal use, and mode choices. Keep the strong positioning, but make the promise precise: guided, hardened deployment once prerequisites are in place
- [ ] **Add an orientation layer before procedural docs** — `/docs/` currently drops straight into Getting Started, which works for one path but not for a product with standalone, domain, relay, and family-sharing modes. Add a "choose your path" / "is this for you?" page so new users understand which deployment model fits before they start copying commands
- [ ] **Clarify the product stance on standalone vs domain mode** — marketing frames `meridian deploy` as the complete secure path, while the wizard treats domain mode as strongly recommended for a more plausible HTTPS profile and CDN fallback. Decide whether domain mode is optional convenience or the preferred "done right" path, then make website, wizard, and docs tell the same story
- [ ] **Trust-first handoff link previews** — shared connection pages need sender-recognizable metadata before the recipient opens them. Replace generic OG/title/description values like "Connection Setup" with deployer/server identity and plain-language safety cues so chat previews feel intentional instead of phishing-adjacent
- [ ] **Install-aware recipient handoff flow** — the primary "Add to App" path assumes the target app is already installed and can fail as a dead deep link. Detect likely missing-app cases, provide explicit fallback to install, and keep the import path recoverable on first use. The handoff page should feel self-explanatory even when the recipient has never heard of these apps
- [ ] **Opinionated recommended app path per platform** — the current handoff page exposes too many equal-weight client choices, including GitHub-release and external-site downloads. Preserve advanced options, but for non-technical recipients default to one recommended app per platform and demote the rest under advanced/other platforms. Trust improves when the product makes fewer decisions on the user's behalf
- [ ] **Make recipient-side troubleshooting plain-language and self-contained** — handoff pages currently fall back to deployer jargon like "ask the server owner to run `meridian test`". Replace that with clearer user-facing recovery copy, a copyable problem report, or a simpler escalation path so the two-sided UX does not collapse into CLI terminology when something goes wrong

### Reliability

- [ ] **WARP must be health-gated and reversible** — only switch outbound routing after WARP is actually up, and support full rollback
- [ ] **Domain mode must support safe steady-state redeploys behind orange-cloud** — redeploy should not require temporarily breaking the documented Cloudflare setup
- [x] **Persist relay SSH user across lifecycle commands** — `relay check` and `relay remove` now reuse the stored relay user by default
- [x] **Preserve forward-compatible nested credential fields** — `_extra` now preserves unknown nested fields under all credential sections on round-trip

### Testing

- [ ] **Make E2E fail on idempotency and redeploy regressions** — stop downgrading core deploy/redeploy failures to warnings
- [ ] **Add real-host coverage for production-sensitive branches** — cert issuance, nginx bootstrap, systemd, and redeploy migrations still need real-host coverage
- [ ] **Add end-to-end coverage for domain mode, WARP, relay migration, and recovery** — current mocks are not enough for deployment-changing behavior
- [ ] **Add dedicated tests for recovery and migration paths** — especially `ConfigurePanel` partial recovery, relay nginx migration for pre-existing servers, and stale-state conflict resolution

---

## P2 — Medium

### Security

- [ ] **`innerHTML` XSS surface** — risk if translations ever loaded externally

### Product

- [ ] **Batch client add** — `meridian client add alice bob charlie`
- [ ] **Per-client traffic/IP limits** — `--limit-gb`, `--limit-ip` flags
- [ ] **Self-hosted ping endpoint** — make this the default troubleshooting path from hosted client pages so connection testing still works when `getmeridian.org` is blocked and doesn't leak server metadata externally
- [ ] **Windows WSL setup guide** — doc page
- [ ] **`meridian server status`** — multi-server overview
- [ ] **`meridian test --via RELAY_IP`** — E2E test through relay
- [ ] **`meridian client export NAME`** — standalone HTML for offline sharing
- [ ] **OpenWRT router auto-deploy** — deploy client config directly to OpenWRT routers
- [ ] **Relay on server with existing nginx** — support deploying relay alongside an existing web server on port 443
- [ ] **WebRTC leak warning on connection page** — WebRTC leaks are client-side (browser discovers local IPs via OS APIs, bypassing the proxy entirely). Server-side fixes don't help — traffic never reaches Xray. Add amber warning box to connection page with: 1) link to browserleaks.com/webrtc leak test, 2) per-app guidance (v2rayNG: Global mode, Hiddify: route all connections), 3) browser extension recommendation. Same pattern as clock-sync warning
- [ ] **Replace `qrencode` binary with Python `segno` package** — eliminates system dependency
- [ ] **Usable no-JS / failed-config fallback for handoff pages** — the hosted connection page currently depends on JavaScript and `config.json`; the noscript/error state is mostly a dead end. Add a minimal static fallback with subscription URL, manual instructions, and enough context that recipients are not stuck if scripting or fetch fails

### Reliability

- [ ] **`_wait_for_panel` SSH vs panel confusion** — polling breaks on transient SSH issues
- [ ] **Xray process accumulation on `test_connection()` timeouts** — orphan processes not cleaned up when `_wait_for_port()` times out (`xray_client.py:305-350`)

### Testing

- [ ] **12 source modules have zero tests** — remaining gaps: `ping.py`, `check.py`, `scan.py`, `docker.py`, `config.py`, `display.py`, `ai.py`, `models.py`, `server.py`, `uninstall.py`, `provision/uninstall.py`

### UX

- [ ] **Multi-protocol jargon** — "XHTTP" meaningless to non-tech. Use "Connection 1 / 2"
- [ ] **`index.html` not in SW precache** — first offline visit fails
- [ ] **`apple-touch-icon` uses SVG** — iOS needs PNG
- [ ] **Wizard `_confirm_scan()` fails silently on WSL**
- [x] **Use canonical `subscription_url` in the PWA** — frontend now honors server-provided canonical URL instead of reconstructing from `location.pathname`

### Website

- [ ] **Live GitHub stars in trust bar** — shields.io badge or API fetch
- [ ] **Dark mode toggle** — system-preference only, no manual override
- [ ] **Docs sidebar on mobile** — no nav below 860px
- [ ] **Validate executable docs examples, not just flag tables** — CI currently misses broken README/deploy-guide commands and translated-doc drift. Add validation for high-traffic command examples and behavior claims across docs surfaces
- [ ] **Move bounded trust earlier on the landing page** — the homepage currently reaches the command builder and absolute anti-detection claims before it clearly frames scope, limitations, and why the implementation should be trusted. Re-sequence the page so threat-model and trust boundaries arrive earlier than configuration widgets

---

## Icebox

- [ ] **Amnezia-style app deploy** — download app, enter VPS ip:user:password, auto-deploy without knowing SSH. Full GUI wrapper around `meridian deploy`
- [ ] **Meridian-branded cross-platform client app** — fork Hiddify (Flutter, sing-box, open source, 28k stars) into a stripped-down Meridian client. Subscription URL as primary flow: open app → paste URL / scan QR → connected. Happ is NOT open source (can't fork)
- [ ] **Key/credential rotation without reinstall**
- [ ] **Zero-to-VPN onboarding wizard on website**
- [ ] **Shell completion** — typer built-in
- [ ] **Remove v1→v2 credential migration** — dead code
- [ ] **Remove all Caddy and HAProxy code** — we use nginx, these are dead paths
- [ ] **Accessibility + i18n pass** — RTL CSS, ARIA landmarks, keyboard nav, touch targets, screen reader support, translation completeness (batched from ~20 items)

---

## Done

Collapsed — see [CHANGELOG.md](CHANGELOG.md) for details.

- **Review loop** — XHTTP exact/slash nginx routing, release workflow pinned to CI-passed SHA, deploy force-refreshes credentials, nested credential field round-tripping, PWA canonical `subscription_url`, client + relay fail-closed mutations, silent patch auto-upgrade removed, sshd hardening via authoritative drop-in, relay lifecycle reuses stored SSH user, firewall cleanup scoped to Meridian ports, handoff pages self-contained, deploy page fanout, domain deploy Cloudflare guidance, relay SNI fail-closed
- **3.14** — `client show`, WARP flag, stats script fix, `--sni` plumbing, docker pull on re-deploy
- **3.8.1** — Deploy version tracking, SECURITY.md, CODE_OF_CONDUCT, PWA sub-url toggle + clock warning, trust bar cleanup
- **3.8.0** — PWA security/a11y/i18n (40 tests), landing page, install.sh, architecture SVG, reduced-motion
- **3.7** — Local mode, security hardening (19 items), Caddy/HAProxy fixes, website, provisioner hardening
- **Code quality sprint** — socket leaks, nginx 444, IPv6 URLs, WARP/ufw return codes, port conflict check, SSRF guard, exception handlers, SystemExit refactor, 80 new tests (branding, xray_client, render templates)
