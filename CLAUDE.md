# CLAUDE.md

Project-wide context for any AI coding assistant working on Meridian.
Also see [AGENTS.md](AGENTS.md) for cross-tool discovery rules.

## Vision

**Deploy it right. Share it easily.** A strong protocol means nothing if the deployment leaks — an open port, a TLS mismatch, a fingerprint that gives the server away. Meridian ships the strongest available protocol (today: VLESS+Reality) AND configures every layer correctly: firewall, certificates, SNI routing, fingerprinting. One command. Done right.

**Audience:** deployers (self-hosters who want correctness out of the box) + their users (people who scan a QR code and connect).

**Design principles:**
- **Strongest protocol, always** — chase depth, not breadth. No open ports, no TLS leaks, no fingerprinting mistakes.
- **It just works** — every deployment hardened by default. Deployer doesn't need to understand firewall rules, TLS, or SNI.
- **Two-sided UX** — deployer (CLI wizard, smart defaults) AND end-user (PWA connection pages, QR codes, subscription auto-update).
- **Aesthetics are trust** — every surface is UI. CLI output, pages, error messages. In censored regions, polished = safe.

## Architecture (summary)

Python CLI on PyPI with a growing meridian-core contract layer underneath. nginx (stream SNI routing + http TLS + reverse proxy) + acme.sh + Xray (VLESS+Reality, XHTTP, Hysteria2 UDP fallback) + Remnawave (panel + node + PostgreSQL + Valkey + subscription-page, all pinned). Domain mode adds WSS through Cloudflare CDN. Relay nodes are L4 TCP forwarders (Realm); topology intent gives each server one or more roles, so a server can be both relay and exit. Every fleet is a reviewed `topology_intent` in `cluster.yml`; `meridian plan / apply` compile it and reconcile resource drivers. Website/Studio live in Astro, with docs on Starlight (every page also served as Markdown for agents); static Studio consumes generated contracts, and executable Studio starts through a localhost-only Engine for server setup and deploy planning. Optional real-VM harness lives at `tests/realvm/`. **Full detail in [website/src/content/docs/en/architecture.md](website/src/content/docs/en/architecture.md).**

## Per-folder CLAUDE.md — the knowledge system

Every folder with distinct architectural concerns has a `CLAUDE.md`. AI assistants load the nearest one before writing code. A pitfall documented here is a bug that never recurs.

**Format (applies to every file, root excepted):**
- Section order: **Design decisions → What's done well → Pitfalls → (optional) Links**
- ≤ 40 lines (leaf files). Root is the spine; longer is acceptable but resist bloat.
- Pitfalls are one-line, concrete, actionable.
- No duplication across files. Root has the big picture; folders have the details. Cross-reference by path.
- No line numbers or function signatures (they drift with code).

**Manifest** — every CLAUDE.md in the tree:

```
CLAUDE.md                               — this file (vision, manifest, conventions)
AGENTS.md                               — cross-tool agent discovery pointer
contracts/CLAUDE.md                     — generated meridian-core contract artifacts
README.md                               — public project landing
ROADMAP.md                              — thematic direction + follow-up issue links
SECURITY.md / CONTRIBUTING.md           — public policies
scripts/CLAUDE.md                       — repository automation and contract export

src/meridian/CLAUDE.md                  — Python CLI package overview
├── panel_bootstrap.py                  — SSH provisioner run + control-plane credential bootstrap
├── node_deploy.py                      — node container rollout, panel readiness, API tokens
├── reality_keys.py                     — Reality x25519 keypair generation
├── resolve.py                          — ResolvedServer, ensure_server_connection
├── cluster_persistence.py              — cluster.yml serialization/deserialization
├── ssh_keys.py                         — host key utilities (shared by ssh.py + engine)
├── registry_country.py                 — best-effort exit country from RIR RDAP records
├── cli_groups.py                       — Typer sub-apps (client, server, node, relay, route, fleet, api, dev)
├── cli_catalog.py                      — command catalog read from the live Typer tree
├── core/CLAUDE.md                      — transport-neutral API contracts
│   ├── defaults.py                     — core-owned constants (DEFAULT_SNI)
│   └── errors.py                       — shared error base types
├── commands/CLAUDE.md                  — per-subcommand pattern
├── setup/CLAUDE.md                     — resumable setup draft + server shelf
├── compiler/CLAUDE.md                  — pure finite topology compiler
├── diagnostics/CLAUDE.md               — reusable server health checks (CheckResult)
├── i18n/CLAUDE.md                      — CLI message catalog, language detection
├── engine/CLAUDE.md                    — local Engine use-case boundary
│   └── routes.py                       — FastAPI route handlers
├── remnawave/CLAUDE.md                 — typed control-plane adapter and version tuple
├── provision/CLAUDE.md                 — step pipeline + idempotency
├── reconciler/CLAUDE.md                — checkpointed compiled-resource drivers
└── templates/pwa/CLAUDE.md             — PWA security model, vanilla JS rationale

tests/CLAUDE.md                         — testing philosophy, MockConnection
├── provision/CLAUDE.md                 — mock boundary, idempotency dual-path
├── systemlab/CLAUDE.md                 — Docker lab topology, CI boundary
└── realvm/CLAUDE.md                    — real-VM harness, tier α/β/γ, never-in-CI
    └── providers/CLAUDE.md             — CloudProvider abstract + Hetzner impl

website/CLAUDE.md                       — Astro + Starlight docs, agent endpoints, i18n
├── src/components/CLAUDE.md            — composition pattern
├── src/content/docs/CLAUDE.md          — locale parity, frontmatter schema
├── src/i18n/CLAUDE.md                  — asymmetric i18n, detection cascade
├── src/layouts/CLAUDE.md               — Base/BlogPost shells, early theme + locale
├── src/pages/CLAUDE.md                 — docs entry, Markdown twins, machine-readable endpoints
├── src/pages/blog/CLAUDE.md            — blog index, post route
├── src/studio/CLAUDE.md                — generated Studio contracts, future adapters
└── src/styles/CLAUDE.md                — token system, warm light-first palette

.github/workflows/CLAUDE.md             — two-stage pipeline, VERSION-driven releases
```

## How to update CLAUDE.md

Updating is part of the change, not a separate task. In the SAME commit:

- **Bug fix** → add a one-line pitfall under the relevant folder's `Pitfalls` section.
- **Design change** → update `Design decisions` of the closest folder. If it changes the big picture, update root Architecture summary + website `architecture.md`.
- **New invariant worth preserving** → add under `What's done well`.
- **Reverted / deprecated** → remove the stale entry. Don't leave historical notes.
- **New folder with distinct concerns** → create a new CLAUDE.md, add it to the Manifest above.

When in doubt: shorter is better. A 30-line CLAUDE.md that's current beats a 100-line one that's half-stale.

## Conventions

- **Shell injection**: `shlex.quote()` on ALL `conn.run()` interpolated values
- **Demo data**: RFC 5737 IPs (`198.51.100.x`), never real server IPs
- **Privacy**: never reference real people's names, server IPs, or domains in commits, code, or docs unless asked
- **Self-hosted everything**: zero external requests (fonts, JS, CSS). Target regions block CDNs
- **Commit per change**: each logical change gets its own commit; include `Refs: getmeridian/meridian#NN` footer when resolving an issue
- **Translations**: use Sonnet model agents (`model: "sonnet"`) for i18n and ru/fa/zh docs
- **ctx7 CLI**: check library docs before writing code that depends on external packages

## Module discipline

Rules enforced by `tests/test_architecture.py` — violations fail CI.

- **800-line budget** — files above 800 lines require an entry in `FILE_SIZE_ALLOWLIST` in `test_architecture.py` with a justification. When a change pushes a file past the budget, split first.
- **No private cross-module imports** — if `_foo()` is imported outside its own file, it's public. Drop the underscore and move it to the right module, or add it to the allowlist with a justification.
- **Layer boundaries** — `core/` never imports console, config, commands, or Rich. `engine/` never imports commands, provision, ssh, or remnawave. `adapters/` never imports engine. Enforced by both ruff (`TID251`) and pytest.
- **One concept, one place** — before adding a new type, helper, or constant, search for an existing one. Duplicate port computations, result types, error hierarchies, and field sets were the top source of drift.
- **Typed contracts at boundaries** — new result types must reference or compose existing core types. Don't reinvent `returncode, stdout, stderr`; use `CommandResult`. Don't hand-build dicts that match a Pydantic model; construct the model.

## Community & public communication

**Always ask before posting.** Show the exact text to the user and get approval before any `gh issue`, `gh pr create`, or GitHub comment. This covers issue bodies, PR descriptions, discussion replies, and any `gh` command that creates or modifies public content.

## Development

```bash
make install     # uv sync --extra dev --reinstall-package meridian-vpn
uv run meridian  # always use uv run (not system-wide meridian)
make ci          # lint + format + test + templates + dead-code report
make dead-code   # dead / test-only code and unused params (warnings only)
make system-lab  # multi-node Docker lab (~10-15 min)
make real-lab    # optional real-VM on Hetzner (local-only, ~€0.01)
```

## Where things live

- Concrete tracked work → **[GitHub issues](https://github.com/getmeridian/meridian/issues)**
- High-level direction → **[ROADMAP.md](ROADMAP.md)**
- Shipped history → **[CHANGELOG.md](CHANGELOG.md)**
- Cross-tool agent rules → **[AGENTS.md](AGENTS.md)**

## Agent-context imports

The `@path` directives below are a Claude Code convention for pulling in extra files during indexing. Agents that don't support them still find the same content by walking the Manifest above.

@src/meridian/CLAUDE.md
@src/meridian/commands/CLAUDE.md
@src/meridian/provision/CLAUDE.md
@src/meridian/reconciler/CLAUDE.md
@tests/CLAUDE.md
@tests/realvm/CLAUDE.md
@tests/realvm/providers/CLAUDE.md
