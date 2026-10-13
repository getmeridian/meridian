# AGENTS.md

Pointer for AI coding agents (Codex CLI, Cursor, Aider, OpenHands, and
others). Meridian's instructions live in `CLAUDE.md` files; this file does
not repeat them.

## Working on this repository

1. Read [`CLAUDE.md`](CLAUDE.md): vision, conventions, module rules, and the
   manifest of every folder-level `CLAUDE.md`.
2. Read the `CLAUDE.md` closest to the files you are about to change.
3. Ignore the `@path` lines at the end of `CLAUDE.md` if your tool does not
   support them; the manifest lists the same files.

## Using the Meridian CLI

Run `meridian help agents` (also served at <https://getmeridian.org/agents.md>)
for how to drive the CLI safely: non-interactive flags, JSON envelopes, plan
before apply, exit codes, and which outputs are secrets.

## Tools that expect another filename

If a tool wants `GEMINI.md`, `.cursorrules`, or
`.github/copilot-instructions.md`, make that file a one-line pointer to
`AGENTS.md`. Never copy instructions into it.
