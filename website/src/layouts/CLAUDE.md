# layouts — Astro page shells for non-docs pages

## Design decisions

**Two layouts, one base** — `Base.astro` is the HTML shell (head, nav, footer, i18n bootstrap) for the landing, blog, demo, ping, and Studio pages; `BlogPost.astro` extends it. Docs pages use Starlight's own shell (see `website/CLAUDE.md`).

**Early locale + theme detection** — inline `<script>` in `<head>` reads localStorage before first paint. Sets `lang`, `dir="rtl"` for Farsi, and `data-theme` for dark mode (Starlight's `starlight-theme` first, then `meridian-theme`). Prevents layout shift and color flash.

## What's done well

- **JSON-LD schemas** — BlogPost has an Article schema, built at render time from props.
- **Docs links follow the reader's language** — English pages link docs at `/en/...`; Base rewrites them to the chosen locale on the client.

## Pitfalls

- **Base.astro i18n must stay in sync with `src/i18n/index.ts`** — the early `<head>` script hardcodes the locale map (`{ru,fa,zh}`). If locales change, both files need updating.
