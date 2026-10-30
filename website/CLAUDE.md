# website — Astro site (getmeridian.org): landing, Starlight docs, blog

```bash
cd website && pnpm install && pnpm run build   # Astro + Starlight + Pagefind
```

## Design decisions

**Static Astro** — every page is a flat HTTP request with minimal shipped JS; content collections hold docs and blog.
**Docs run on Starlight** — `src/content/docs/{locale}/` renders at `/{locale}/{page}/`; `src/lib/docs-sidebar.mjs` lists the sidebar for every locale, and `astro.config.mjs` refuses a page missing from it and redirects old `/docs/{locale}/{page}/` URLs.
**Agent-friendly by default** — every docs page has a Markdown twin at `/{locale}/{page}.md` (`src/lib/docs-markdown.ts`), `/agents.md` serves the CLI's agent guide, and `scripts/ai-docs.mjs` is the only bundler for `/llms-full.txt`, `/ai/reference.md`, and the packaged `ai-reference.md`.
**Diagrams render before the build** — Mermaid blocks stay in the Markdown; `src/lib/diagrams.mjs` swaps each for a checked-in SVG that `node scripts/diagrams.mjs render` draws in Chromium and that colours itself from theme variables, so no diagram JavaScript reaches readers.
**i18n and one look** — docs are per-locale Markdown, the landing static HTML per locale (`src/i18n/CLAUDE.md`); landing, blog, and docs share tokens and components (`src/styles/CLAUDE.md`).
**Self-hosted everything** — `scripts/check-runtime-assets.mjs` fails the build on any external request in HTML, CSS, or JavaScript, rerun after the demos join `dist`.
**Demos are recorded, not hand-written** — `scripts/build_showcase.py` seeds a real control service with an invented fleet, records its responses, validates them against today's models, and writes `/demo/` and `/demo/console/` (the unchanged console plus the in-memory `website/demo/demo.js` shim). Neither ships in the package.
**pnpm with guardrails** — `pnpm-workspace.yaml` blocks exotic transitive sources, enforces a release-age delay, and allows build scripts only for trusted packages.
**App links** — `src/data/apps.json` is the source of truth, validated against the page template and Python constants.

## Pitfalls

- **Theme and language cross the docs boundary** — the landing and Starlight use different storage keys; ThemeToggle writes both on click, or switching sections flips the theme. Only a language link writes the language; reading a page is not a choice.
- **Astro builds every `.md` under `src/pages`** — the `drop-contributor-notes` integration deletes the pages folder notes would produce.
- **Links are validated** — the build fails on a broken docs link or anchor; translated headings have translated anchors.
- **Remark plugins need the unified processor** from `@astrojs/markdown-remark`, and Starlight owns Astro i18n: never add a top-level `i18n` block.
- **Prefetch on hover only** — viewport prefetch downloaded every visible sidebar page on each visit.
- **The build only checks diagrams** — CI has no Chromium; render and commit a changed block's SVG, and bump `RENDER_VERSION` when renderer output changes. Diagrams are always LTR, or RTL pages reorder label punctuation.
- **The landing video is a file, not an embed** — keep `preload="none"`, and never embed a player from another origin.
- **The demo shim mirrors service shapes exactly** — byte counts as decimal strings, statuses upper case; other shapes made the page reject its own account panel.
- **A demo link never reaches a real app** — app-import links show a notice, or a visitor's VPN app would fetch the demo's URL.
- **Demo dates shift by whole months and keep the `2099-12-31` sentinel** — pages test the sentinel literally, and periods keep their day of the month.
- **Screenshots pin UTC and `en-US`** — the browser's own zone once leaked into a screenshot.
- **Keep `sharp` an explicit dependency** for Astro image optimization.
