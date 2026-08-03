# content/docs — Markdown documentation source

## Design decisions

**Per-locale directories** — each locale (en, ru, fa, zh) has its own folder with identically named markdown files. English is the source; other locales are translations.

**Frontmatter schema** — every doc has `title`, `description`, `order` (sidebar position), `section` (sidebar group label). Astro content collections validate this at build time.

**Machine-readable consumption** — these markdown files are read at build time by `/md/[slug]` (raw markdown endpoint), `/llms-full.txt` (all docs concatenated), and `make ai-docs` (bundled `ai-reference.md`).

## Pitfalls

- **Locale parity** — all four locale directories must have the same filenames. A missing file means that locale won't have that doc page, and the sidebar will show a gap.
- **AI reference is English-only** — `make ai-docs` reads only from `en/`. Translations don't affect the AI-facing docs.
- **Order + section drive sidebar** — changing `order` or `section` in frontmatter reshuffles the sidebar for that locale. Keep values consistent across locales to avoid navigation mismatches.
- **This CLAUDE.md lives inside a content collection** — `content.config.ts` uses `**/[a-z]*.md` glob to exclude it. All doc filenames must stay lowercase.
- **CLI validation is live-tree and section-scoped** — every public leaf and root option must appear in its matching section in all four locales; hidden developer commands are excluded.
