# website — Astro site (getmeridian.org): landing, Starlight docs, blog, Studio

```bash
cd website && pnpm install && pnpm run build   # Astro + Starlight + Pagefind
```

## Design decisions

**Static Astro** — every page is a flat HTTP request with minimal shipped JS; content collections hold docs and blog.

**Docs run on Starlight** — `src/content/docs/{locale}/` renders at `/{locale}/{page}/` with Starlight's sidebar, search, TOC, RTL, and Expressive Code. `src/lib/docs-sidebar.mjs` lists the sidebar (sections and page order) for every locale; `astro.config.mjs` refuses a page missing from it and redirects every old `/docs/{locale}/{page}/` URL; `/docs/` stays the stable entry. Overrides live in `src/components/starlight/`, the route middleware in `src/lib/docs-route.ts`, the theme in `src/styles/starlight.css`.

**Agent-friendly by default** — every docs page has a Markdown twin at `/{locale}/{page}.md` (linked from `<head>` and the Copy page / View as Markdown / Open in ChatGPT or Claude actions), `/agents.md` serves the CLI's `meridian help agents` guide, and `/llms.txt`, `/llms-full.txt`, and `/ai/reference.md` cover LLM tooling. Twins go through `src/lib/docs-markdown.ts`; bundles through `scripts/ai-docs.mjs`.

**Diagrams render before the build** — ```mermaid blocks stay in the Markdown as source; `src/lib/diagrams.mjs` swaps each for a checked-in `src/assets/diagrams/<hash>.svg` that `node scripts/diagrams.mjs render` draws with pinned Mermaid in Chromium (`figures/render_diagrams.py`). The SVGs color themselves with `--sl-color-*` (docs) or `tokens.css` (blog) variables, so one file fits both themes; no diagram JavaScript reaches readers.

**i18n strategy** — docs are per-locale markdown files and Starlight UI strings come from `src/content/i18n/`; the landing is static HTML per locale and the blog and 404 swap only the shell (`src/i18n/CLAUDE.md`).

**One look** — landing, blog, and docs share tokens, type, header, and buttons; `src/styles/CLAUDE.md` owns the system and `starlight.css` carries it into the docs.

**Self-hosted everything** — the root rule, enforced: `scripts/check-runtime-assets.mjs` fails the build on any external request in HTML, CSS, or JavaScript (literal absolute-URL loads), and both workflows rerun it after the demos join `dist`.

**Demos are recorded, not hand-written** — `scripts/build_showcase.py` seeds a real control service with an invented fleet (`scripts/showcase_seed.py`), records the console's responses, validates each against today's models, and writes `/demo/` (one person's connection page) and `/demo/console/` (the unchanged console plus `website/demo/demo.js`, which answers its API in memory). Neither the shim nor the data ships in the package; the landing's `Showcase` screenshots come from `scripts/showcase_screenshots.py` (local, never CI).

**pnpm with guardrails** — `pnpm-workspace.yaml` blocks exotic transitive sources, enforces a one-week release age, and refuses trust downgrades; allows build scripts only for explicitly trusted packages.

## Website ↔ CLI relationship

- **App links**: `src/data/apps.json` is SOT. CI validates against template + Python constants.
- **AI docs**: `scripts/ai-docs.mjs` is the only bundler — `/llms-full.txt` and `/ai/reference.md` at build time, and `make ai-docs` / the PyPI job write the same reference into the package as `ai-reference.md` for `doctor --ai`. The agent guide is the CLI's `src/meridian/data/agents.md`.
- **install.sh**: deployed to `getmeridian.org/install.sh` by CI release workflow.
- **Studio contracts**: `pnpm run contracts:generate` builds `src/studio/generated/` from checked-in `contracts/meridian/v1`.

## Pitfalls

- **Theme and language cross the docs boundary** — landing uses `meridian-theme`/`meridian-lang`, Starlight `starlight-theme`; ThemeToggle writes both (on click only) and a Starlight head script copies the theme, or switching sections flips it. Only a language link writes `meridian-lang`; reading a docs page is not a choice.
- **Astro builds every `.md` under `src/pages`** — the `drop-contributor-notes` integration deletes the `/CLAUDE/` pages that folder notes produce.
- **Links are validated** — `starlight-links-validator` fails the build on a broken docs link or anchor; translated headings have translated anchors. Custom pages (blog, demo, studio) are excluded because Starlight can't see them.
- **Remark plugins need the unified processor** — Astro 7 defaults to Sätteri; pass plugins to `unified()` from `@astrojs/markdown-remark` (Starlight needs it too).
- **Starlight owns Astro i18n** — never add a top-level `i18n` block; Starlight derives it from `locales`.
- **Prefetch on hover only** — `defaultStrategy: 'viewport'` with `prefetchAll` downloaded every visible sidebar page (~20) on each visit.
- **The build only checks diagrams** — CI has no Chromium or network for Mermaid; a changed block fails `node scripts/diagrams.mjs` until its SVG is rendered and committed. Bump `RENDER_VERSION` when the renderer's output changes.
- **Diagram figures are always LTR** — an RTL page reorders the punctuation in labels like `(Realm)`.
- **The landing video is a file, not an embed** — `public/video/` comes from `video/scripts/export.mjs`; keep `preload="none"` so only the poster loads, and never embed a player from another origin.
- **The demo shim mirrors service shapes exactly** — `/info` byte counts are decimal strings and statuses upper case; numbers there made the page reject its own account panel.
- **Month-anchored demo dates move by whole months** — the shim shifts recorded timestamps by the time since generation, but `period_*`, `counting_since`, and `traffic_reset_at` keep their day of the month.
- **Screenshots pin UTC and `en-US`** — the browser's own zone once showed in the person screen ("Your browser time zone") and moved the period start by an hour.
- **Sharp is direct** — Astro image optimization must keep `sharp` as an explicit dependency.
