# Roadmap

Meridian's high-level direction. For concrete trackable tasks see
[open issues](https://github.com/getmeridian/meridian/issues). Shipped work is in
[CHANGELOG.md](CHANGELOG.md).

The roadmap is intentionally thematic — specific line items get promoted to
issues as they become well-scoped. Priorities are maintainer calls, not
commitments; contributions in any theme are welcome.

## In flight

**Meridian 4.0** (in development on the `v4` branch)
Remnawave replaces 3x-ui; 4.0 is a fresh deploy, not an in-place upgrade.
Every command now works on one reviewed topology in `cluster.yml`:
`meridian deploy IP` builds a single-server topology, `meridian setup` is a
resumable wizard for exits, Realm relay chains, routing gateways with failover
pools, and users, and `meridian plan / apply` review and apply it with
checkpoints. Deploy picks a working camouflage target and verifies real
traffic before finishing; connection pages are built from each user's real
subscription. Real-VM testing runs through the Hetzner harness.
Related roadmap issues:

- [#32](https://github.com/getmeridian/meridian/issues/32) — multi-hop chain topologies (arbitrary N ≥ 2)
- [#33](https://github.com/getmeridian/meridian/issues/33) — multi-IP nodes (ingress IP ≠ egress IP)
- [#34](https://github.com/getmeridian/meridian/issues/34) — split routing (direct for selected domains/IPs)
- [#35](https://github.com/getmeridian/meridian/issues/35) — end-to-end IPv6 (dual-stack ingress/relay/egress)
- [#36](https://github.com/getmeridian/meridian/issues/36) — expand real-VM harness for existing features
- [#37](https://github.com/getmeridian/meridian/issues/37) — real-VM harness: multi-topology + multi-provider + optional CI smoke

### 4.0 release hardening

- Safe retirement: `client remove`, `node remove`, `relay remove`, and
  shrinking the topology (today apply is additive and refuses removals)
- Per-hop relay health checks (`relay check`)
- Complete typed process-API envelopes and generated clients across commands
- Make operation state durable, cancellation real, and diagnostics redacted
- Scan built artifacts for secrets
- Studio topology authoring (Studio is experimental)
- Browser and accessibility coverage for Studio and connection pages

## Near-term themes

### Operator trust and correctness

Meridian's value proposition is correctness — a deployment that does not leak.
Every trust-adjacent gap matters.

- Drift visibility between `cluster.yml` and Remnawave UI edits in every CLI
  path (plan is good; apply should continue catching edge cases)
- Transactional destructive mutations (local + remote + handoff in one
  fail-closed boundary)
- Docs ↔ CLI ↔ command-builder agreement, validated in CI (not just flag tables)
- Recalibrate onboarding promises to the real setup cost (VPS, SSH, mode choice)

### Supply-chain hardening

- Sign and verify install.sh / scanner artifacts; currently the
  install flow trusts a mutable URL
- Pin every remote script or binary that Meridian downloads at runtime
- Move the Remnawave admin UI off the public camouflage identity (or make the
  operator access path explicit, not shared with camouflage)

### Client handoff UX

- Install-aware handoff pages — detect missing target apps, fall back to
  install instructions, keep the import path recoverable
- Trust-first link previews — sender identity in OG/title, not generic
  "Connection Setup"
- Plain-language recipient troubleshooting — remove CLI jargon from user-
  facing recovery docs
- Opinionated recommended app per platform, demote the long list under
  "advanced / other platforms"
- Usable no-JS / failed-config fallback when the handoff page's JavaScript
  or `config.json` fails

### Multi-server fleet ergonomics

- Client migration across servers (`deploy NEW_IP --from OLD_IP`)
- Proactive IP-block detection → webhook / Telegram alerts
- Relay-aware health monitoring across the fleet

### Security posture

- Residual IP-cert fingerprinting surface in IP mode — keep tightening
- Probe/check tooling must distinguish IP vs domain cleanly (domain-mode
  cert is intentional, not a leak)
- WARP health-gated and reversible (don't switch outbound until WARP is up)

## Longer horizon (no concrete timeline)

### Branded client app

Fork Hiddify (Flutter + sing-box, Apache 2.0, 28k stars) into a stripped-
down Meridian client optimised for the subscription-URL flow.

### Zero-to-VPN onboarding on the website

In-browser wizard: paste credentials / scan QR / connect. Shares code with
the CLI backend.

### Amnezia-style GUI wrapper

Download-and-deploy GUI for non-technical users. `meridian deploy` under the
hood; no terminal required.

### Key/credential rotation without reinstall

Rotate Reality keys, JWT secrets, and subscription paths in place.

## Tracked follow-ups (v4 aftermath)

Security / supply chain:
- [#38](https://github.com/getmeridian/meridian/issues/38) — `install.sh` verify `SHA256SUMS` before exec
- ~~[#39](https://github.com/getmeridian/meridian/issues/39) — pin + verify `RealiTLScanner` binary~~ — shipped: `meridian scan` runs a pinned, SHA-256-verified release

Product:
- [#42](https://github.com/getmeridian/meridian/issues/42) — `meridian deploy NEW_IP --from OLD_IP` fleet migration
- [#43](https://github.com/getmeridian/meridian/issues/43) — auto SSH key setup for password-only VPS

UX / docs:
- [#44](https://github.com/getmeridian/meridian/issues/44) — WebRTC leak warning on connection page
- ~~[#45](https://github.com/getmeridian/meridian/issues/45) — Windows / WSL setup guide~~ — shipped on `main`; carry it into 4.0 docs
