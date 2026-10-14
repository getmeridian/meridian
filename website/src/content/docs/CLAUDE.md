# content/docs — Markdown documentation source

## Design decisions

**Per-locale directories** — each locale (en, ru, fa, zh) has its own folder with identically named markdown files. English is the source; other locales are translations.

**Frontmatter schema** — Starlight's plain `docsSchema` (`title`, `description`). Sections and page order live in one list, `src/lib/docs-sidebar.mjs`, shared by every locale.

**Machine-readable consumption** — these markdown files are read at build time by the Markdown twins (`/{locale}/{page}.md`), `/llms-full.txt`, and `make ai-docs` (bundled `ai-reference.md`).

**Durable prose** — write as the current release: no beta notes, no "still works for one release", no version-dated caveats.

**Links** — link other pages as `/{locale}/{page}/` (with `#anchor` from that locale's own heading); the build validates every link.

## Pitfalls

- **Locale parity** — all four locale directories must have the same filenames. A missing file means that locale won't have that doc page, and the sidebar will show a gap.
- **AI reference is English-only** — `make ai-docs` reads only from `en/`. Translations don't affect the AI-facing docs.
- **A new page joins the sidebar list** — add its slug to `src/lib/docs-sidebar.mjs`; the build fails on a page that is missing from it or a slug without a page.
- **This CLAUDE.md lives inside a content collection** — `content.config.ts` uses `**/[a-z]*.md` glob to exclude it. All doc filenames must stay lowercase.
- **CLI validation is live-tree and section-scoped** — every public leaf and root option must appear in its matching section in all four locales; hidden developer commands are excluded.
