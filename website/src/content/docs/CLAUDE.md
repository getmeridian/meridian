# content/docs — Markdown documentation source

## Design decisions

**Per-locale directories** — each locale (en, ru, fa, zh) has its own folder with identically named markdown files. English is the source; other locales are translations.

**Frontmatter schema** — Starlight's plain `docsSchema` (`title`, `description`). Sections and page order live in one list, `src/lib/docs-sidebar.mjs`, shared by every locale.

**Machine-readable consumption** — these markdown files are read at build time by the Markdown twins (`/{locale}/{page}.md`), and by `scripts/ai-docs.mjs` (`/llms-full.txt`, `/ai/reference.md`, and the packaged `ai-reference.md`).

**Durable prose** — write as the current release: no beta notes, no "still works for one release", no version-dated caveats.

**Device privacy** — the client guide documents default off, private/full disclosures, restore registration loss, supported-HWID counting, and registration removal versus credential reissue in all four locales.

**Russian glossary** (docs, landing, CLI) — relay is «промежуточный сервер», firewall «файрвол», no «флот» (say «ваши серверы»); the product is a «VPN» in prose, «прокси» only where technically exact (protocols, Xray, app proxy modes).

**App list from the catalog** — a line holding only `<!-- apps -->` becomes the featured apps of `src/data/apps.json` (`src/lib/apps-list.mjs`) in the page and its Markdown twin; never hand-write an app list.

**Links** — link other pages as `/{locale}/{page}/` (with `#anchor` from that locale's own heading); the build validates every link.

## Pitfalls

- **Privacy disclosures are source text** — privacy.md explains defaults, seizure costs, downgrade deletion, and private device re-registration after restore in every locale; totals-only claims must say they are the default.
- **Locale parity** — all four locale directories must have the same filenames. A missing file means that locale won't have that doc page, and the sidebar will show a gap.
- **AI bundles are English-only** — `scripts/ai-docs.mjs` reads only from `en/`. Translations don't affect the AI-facing docs.
- **A new page joins the sidebar list** — add its slug to `src/lib/docs-sidebar.mjs`; the build fails on a page that is missing from it or a slug without a page.
- **This CLAUDE.md lives inside a content collection** — `content.config.ts` uses `**/[a-z]*.md` glob to exclude it. All doc filenames must stay lowercase.
- **CLI validation is live-tree and section-scoped** — every public leaf and root option must appear in its matching section in all four locales; hidden developer commands are excluded.
