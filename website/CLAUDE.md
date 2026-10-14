# website — Astro site (getmeridian.org): landing, Starlight docs, blog, Studio

```bash
cd website && pnpm install && pnpm run build   # Astro + Starlight + Pagefind
```

## Design decisions

**Static Astro** — every page is a flat HTTP request with minimal shipped JS; content collections hold docs and blog.

**Docs run on Starlight** — `src/content/docs/{locale}/` renders at `/{locale}/{page}/` with Starlight's sidebar, search, TOC, RTL, and Expressive Code. `astro.config.mjs` builds the sidebar from English `order`/`section` frontmatter and redirects every old `/docs/{locale}/{page}/` URL; `/docs/` stays the stable entry. Overrides live in `src/components/starlight/`, the route middleware in `src/lib/docs-route.ts`, the theme in `src/styles/starlight.css`.

**Agent-friendly by default** — every docs page has a Markdown twin at `/{locale}/{page}.md` (linked from `<head>` and the Copy page / View as Markdown / Open in ChatGPT or Claude actions), `/agents.md` serves the CLI's `meridian help agents` guide, and `/llms.txt`, `/llms-full.txt`, `/context-hub.md` cover LLM tooling. All of them go through `src/lib/docs-markdown.ts`.

**i18n strategy** — docs are per-locale markdown files and Starlight UI strings come from `src/content/i18n/`; non-docs pages use the client-side swap in `src/i18n/CLAUDE.md`.

**Self-hosted everything** — the root rule, enforced: `scripts/check-runtime-assets.mjs` fails the build on any external request.

**pnpm with guardrails** — `pnpm-workspace.yaml` blocks exotic transitive sources, enforces a one-week release age, and refuses trust downgrades; allows build scripts only for explicitly trusted packages.

## Website ↔ CLI relationship

- **App links**: `src/data/apps.json` is SOT. CI validates against template + Python constants.
- **AI docs**: en/ markdown → `make ai-docs` → bundled `ai-reference.md`. CI generates automatically. The agent guide is the CLI's `src/meridian/data/agents.md`.
- **install.sh**: deployed to `getmeridian.org/install.sh` by CI release workflow.
- **Studio contracts**: `pnpm run contracts:generate` builds `src/studio/generated/` from checked-in `contracts/meridian/v1`.

## Pitfalls

- **Theme and language cross the docs boundary** — landing uses `meridian-theme`/`meridian-lang`, Starlight `starlight-theme`; ThemeToggle writes both and a Starlight head script copies them, or switching sections flips the theme.
- **Astro builds every `.md` under `src/pages`** — the `drop-contributor-notes` integration deletes the `/CLAUDE/` pages that folder notes produce.
- **Links are validated** — `starlight-links-validator` fails the build on a broken docs link or anchor; translated headings have translated anchors. Custom pages (blog, demo, studio) are excluded because Starlight can't see them.
- **Remark plugins need the unified processor** — Astro 7 defaults to Sätteri; pass plugins to `unified()` from `@astrojs/markdown-remark` (Starlight needs it too).
- **Starlight owns Astro i18n** — never add a top-level `i18n` block; Starlight derives it from `locales`.
- **Sharp is direct** — Astro image optimization must keep `sharp` as an explicit dependency.
