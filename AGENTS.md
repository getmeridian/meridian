# AGENTS.md

Cross-tool discovery pointer for AI coding agents (Codex CLI, Cursor,
Aider, OpenHands, others). Meridian's actual instructions live in
per-folder `CLAUDE.md` files throughout the tree; this file is a map, not
a duplicate source of truth.

## Start here

1. Read [`CLAUDE.md`](CLAUDE.md) — project vision, conventions, manifest of every folder-level CLAUDE.md, and how-to-update rules.
2. Read the CLAUDE.md file closest to whatever you're about to touch (list below).
3. Keep those conventions when you write code.

## CLAUDE.md manifest

```
CLAUDE.md                               — project spine: vision, manifest, conventions
contracts/CLAUDE.md                     — generated meridian-core contracts
scripts/CLAUDE.md                       — repository automation

src/meridian/CLAUDE.md                  — Python CLI architecture
  panel_bootstrap.py                    — panel setup, node deploy, xray config
  relay_ops.py                          — relay infrastructure operations
  ssh_keys.py                           — host key utilities (shared by ssh.py + engine)
  core/defaults.py                      — core-owned constants (DEFAULT_SNI)
  core/errors.py                        — shared error base types
  commands/CLAUDE.md                    — per-subcommand pattern
    wizard.py                           — interactive deploy wizard
  engine/CLAUDE.md                      — local Engine use-case boundary
    routes.py                           — FastAPI route handlers
  provision/CLAUDE.md                   — step pipeline + idempotency
  infra/CLAUDE.md                       — CloudProvider abstract + per-cloud impls
  reconciler/CLAUDE.md                  — plan / apply purity + executor
    prepare.py                          — shared plan computation entry point
  templates/pwa/CLAUDE.md               — PWA security model

tests/CLAUDE.md                         — testing philosophy
  provision/CLAUDE.md                   — mock boundary
  systemlab/CLAUDE.md                   — Docker lab
  realvm/CLAUDE.md                      — real-VM harness (local-only)

website/CLAUDE.md                       — Astro rationale, i18n
  src/components/ src/content/docs/ src/i18n/ src/layouts/ src/styles/CLAUDE.md
  src/pages/CLAUDE.md + src/pages/blog/CLAUDE.md + src/studio/CLAUDE.md

.github/workflows/CLAUDE.md             — CI / release pipeline
```

## Hard rules reproduced here (so you don't miss them)

If you ignore everything else, keep these. The rest is in the per-folder
CLAUDE.md files. All of these are enforced in code review.

- **Shell injection**: every `conn.run()` interpolated value goes through `shlex.quote()`.
- **Demo data**: use RFC 5737 IPs (`198.51.100.x`) in tests, examples, and docs. Never real IPs.
- **Privacy**: no real names, server IPs, or domains in commits, code, or public docs unless the user explicitly asks.
- **Self-hosted**: zero external HTTP requests at runtime from connection pages, CLI update checks aside. Target users are in regions that block CDNs.
- **Commit per change**: one logical change per commit. Footer `Refs: uburuntu/meridian#NN` when resolving an issue.
- **Ask before posting** anything public — GitHub issues, PR descriptions, comments, discussions. Always show the text first.

## Project management

- High-level direction: [ROADMAP.md](ROADMAP.md)
- Concrete trackable work: [GitHub issues](https://github.com/uburuntu/meridian/issues)
- Shipped history: [CHANGELOG.md](CHANGELOG.md)
- Security policy: [SECURITY.md](SECURITY.md)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)

## Adding support for a new agent tool

If your tool expects a different filename (`GEMINI.md`, `.cursorrules`,
`.github/copilot-instructions.md`), **do not duplicate content** — create
the file as a one-line pointer back to `CLAUDE.md` / `AGENTS.md`.
Single source of truth reduces drift.
