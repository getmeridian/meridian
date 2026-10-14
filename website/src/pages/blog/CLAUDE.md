# blog pages — blog index + post route

## Design decisions

**No sidebar or TOC** — blog posts are linear narratives, not reference docs. Single-column layout keeps focus on reading.

**Prose styles** — `src/styles/prose.css` (`.prose`, used by `BlogPost.astro`) matches the docs' typography; code blocks render through Expressive Code like the docs.

**One listing, one card** — `components/blog/BlogListing.astro` renders the index and each category page; `BlogCard.astro` (hero thumbnail, category, summary) is shared with the "More from the blog" block. Helpers (sorting, category slugs, dates, heroes) live in `components/blog/posts.ts`.

**Categories, no tag pages** — tags stay in frontmatter for RSS categories only (six posts don't need 27 tag pages). `rss.xml.ts` serves the feed, linked from every `<head>` and the blog index.

**English-only content** — posts aren't translated; only the nav and footer follow the reader's language.

**High signal, low noise** — each post earns every paragraph with a fact, mechanism, or decision; claims follow the current docs and code, and claims about other tools are verified or cut. Write durably: no beta notes or year-dated prose. Never use a camouflage target `src/meridian/CLAUDE.md` rules out as a Reality example.

**Figures are HTML** — infographics live as `figures/<post>--<name>.html` (shared look in `figures/shared.css`, self-hosted fonts) and `uv run website/figures/render.py` renders each with Chromium to `src/content/blog/<post>/<name>.webp`. Edit the HTML and re-render; never hand-edit the image. Alt text carries the figure's facts for readers and agents.

**Slug from folder name** — Astro content collections derive slug from folder path. No `slug:` in frontmatter.

## Pitfalls

- **`Astro.redirect()` writes nothing in a static build** — yet the sitemap still lists the route; redirect through `astro.config.mjs` `redirects` instead.

- **Hero images are co-located** — images live alongside `index.md` in each post folder. Glob loader resolves relative paths from `src/content/blog/`.
- **Mermaid blocks need a rendered SVG** — after editing one, run `node scripts/diagrams.mjs render` (see `website/CLAUDE.md`); the build refuses a block without its SVG.
- **A figure repeats claims** — when a post's facts change, update its figure HTML too, or the image contradicts the text.
- **Date coercion** — `z.coerce.date()` in schema handles both `2026-03-25` and `'2026-03-25'` YAML formats.
