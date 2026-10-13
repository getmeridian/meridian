# content/docs — Markdown documentation source

## Design decisions

**Per-locale directories** — each locale (en, ru, fa, zh) has its own folder with identically named markdown files. English is the source; other locales are translations.

**Frontmatter schema** — Starlight's `docsSchema` plus `order` (sidebar position) and `section` (`guides` or `reference`). English values drive the sidebar for every locale.

**Machine-readable consumption** — these markdown files are read at build time by the Markdown twins (`/{locale}/{page}.md`, `/md/...`), `/llms-full.txt`, and `make ai-docs` (bundled `ai-reference.md`).

**Durable prose** — write as the current release: no beta notes, no "still works for one release", no version-dated caveats.

**Links** — link other pages as `/{locale}/{page}/` (with `#anchor` from that locale's own heading); the build validates every link.

## Pitfalls

- **Locale parity** — all four locale directories must have the same filenames. A missing file means that locale won't have that doc page, and the sidebar will show a gap.
- **AI reference is English-only** — `make ai-docs` reads only from `en/`. Translations don't affect the AI-facing docs.
- **English order + section drive the sidebar** — `astro.config.mjs` reads them from `en/`; keep translations' values identical anyway.
- **This CLAUDE.md lives inside a content collection** — `content.config.ts` uses `**/[a-z]*.md` glob to exclude it. All doc filenames must stay lowercase.
- **CLI validation is live-tree and section-scoped** — every public leaf and root option must appear in its matching section in all four locales; hidden developer commands are excluded.
