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

Python CLI on PyPI with a growing meridian-core contract layer underneath. nginx (stream SNI routing + http TLS + reverse proxy) + acme.sh + Xray (VLESS+Reality) + Remnawave (panel + node + PostgreSQL + Valkey + subscription-page, all pinned). Domain mode adds WSS through Cloudflare CDN. Relay nodes are L4 TCP forwarders (Realm); emerging topology contracts model server capabilities plus routing policy so a server can be both relay and exit. Declarative `cluster.yml` + `meridian plan / apply` reconcile desired state. Website/Studio live in Astro; static Studio consumes generated contracts, and executable Studio starts through a localhost-only Engine for server setup and deploy planning. Optional real-VM harness lives at `tests/realvm/`. **Full detail in [website/src/content/docs/en/architecture.md](website/src/content/docs/en/architecture.md).**

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
├── panel_bootstrap.py                  — panel setup, node deploy, xray config
├── relay_ops.py                        — relay infrastructure operations
├── ssh_keys.py                         — host key utilities (shared by ssh.py + engine)
├── core/defaults.py                    — core-owned constants (DEFAULT_SNI)
├── core/errors.py                      — shared error base types
├── commands/CLAUDE.md                  — per-subcommand pattern
│   └── wizard.py                       — interactive deploy wizard
├── engine/CLAUDE.md                    — local Engine use-case boundary
│   └── routes.py                       — FastAPI route handlers
├── provision/CLAUDE.md                 — step pipeline + idempotency
├── infra/CLAUDE.md                     — CloudProvider abstract + per-cloud impls
├── reconciler/CLAUDE.md                — compute_plan purity + executor ordering
│   └── prepare.py                      — shared plan computation entry point
└── templates/pwa/CLAUDE.md             — PWA security model, vanilla JS rationale

tests/CLAUDE.md                         — testing philosophy, MockConnection
├── provision/CLAUDE.md                 — mock boundary, idempotency dual-path
├── systemlab/CLAUDE.md                 — Docker lab topology, CI boundary
└── realvm/CLAUDE.md                    — real-VM harness, tier α/β/γ, never-in-CI

website/CLAUDE.md                       — Astro rationale, i18n strategy
├── src/components/CLAUDE.md            — composition pattern
├── src/content/docs/CLAUDE.md          — locale parity, frontmatter schema
├── src/i18n/CLAUDE.md                  — asymmetric i18n, detection cascade
├── src/layouts/CLAUDE.md               — Base/Docs/BlogPost shells, RTL
├── src/pages/CLAUDE.md                 — dynamic routing, machine-readable endpoints
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
- **Commit per change**: each logical change gets its own commit; include `Refs: uburuntu/meridian#NN` footer when resolving an issue
- **Translations**: use Haiku model agents (`model: "haiku"`) for fast i18n
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
make ci          # lint + format + test + templates
make system-lab  # multi-node Docker lab (~10-15 min)
make real-lab    # optional real-VM on Hetzner (local-only, ~€0.01)
```

## Where things live

- Concrete tracked work → **[GitHub issues](https://github.com/uburuntu/meridian/issues)**
- High-level direction → **[ROADMAP.md](ROADMAP.md)**
- Shipped history → **[CHANGELOG.md](CHANGELOG.md)**
- Cross-tool agent rules → **[AGENTS.md](AGENTS.md)**

## Agent-context imports

The `@path` directives below are a Claude Code convention for pulling in extra files during indexing. Agents that don't support them still find the same content by walking the Manifest above.

@src/meridian/CLAUDE.md
@src/meridian/commands/CLAUDE.md
@src/meridian/provision/CLAUDE.md
@src/meridian/infra/CLAUDE.md
@src/meridian/reconciler/CLAUDE.md
@tests/CLAUDE.md
@tests/realvm/CLAUDE.md
