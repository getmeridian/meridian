# blog pages — blog index + post route

## Design decisions

**No sidebar or TOC** — blog posts are linear narratives, not reference docs. Single-column layout keeps focus on reading.

**Prose styles** — markdown styling lives in `src/styles/prose.css` (`.prose`), used by `BlogPost.astro`. Docs use Starlight's own styles.

**English-only** — no locale infrastructure. Blog is content marketing, not essential UI for censored users.

**High signal, low noise** — each post earns every paragraph with a fact, mechanism, or decision; claims follow the current docs and code, and claims about other tools are verified or cut. Write durably: no beta notes or year-dated prose. Never use `www.microsoft.com` or `apple.com` as a Reality example.

**Slug from folder name** — Astro content collections derive slug from folder path. No `slug:` in frontmatter.

## Pitfalls

- **Hero images are co-located** — images live alongside `index.md` in each post folder. Glob loader resolves relative paths from `src/content/blog/`.
- **Mermaid rendering** — the remark plugin in `astro.config.mjs` turns ```mermaid blocks into `<pre class="mermaid">`; `Mermaid.astro` lazy-loads mermaid.js only on pages with diagrams. `scripts/validate-mermaid.mjs` checks every diagram at build time.
- **Date coercion** — `z.coerce.date()` in schema handles both `2026-03-25` and `'2026-03-25'` YAML formats.
