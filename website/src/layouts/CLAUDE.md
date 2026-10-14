# layouts — Astro page shells for non-docs pages

## Design decisions

**Two layouts, one base** — `Base.astro` is the HTML shell (head metadata, nav, footer) for the landing, blog, and 404; `BlogPost.astro` extends it. Docs use Starlight's shell (`website/CLAUDE.md`).

**Localized or swapped** — a page that exists in every locale passes `locale` and `alternates` (hreflang, `og:locale:alternate`) and ships no i18n JavaScript. Any other page keeps English content and loads `src/i18n/swap.ts`, which translates only the shell; `titleKey` names the page for the tab title.

**Theme before paint** — the inline head script sets `data-theme` from the saved choice, else the OS (Expressive Code reads the attribute, as on Starlight pages); it never writes storage.

**Social metadata is complete** — og/twitter image with size and alt (one 1200×630 JPEG per locale, `og.jpg`/`og-<locale>.jpg`, rendered from `figures/og.html`; keep each well under WhatsApp's ~300 KB), `theme-color` per scheme, SVG + ICO favicons, the blog's RSS link, and a preload of the locale's body font.

## What's done well

- **JSON-LD from props** — `BlogPost` builds an Article schema; `Landing.astro` builds Organization and a per-locale FAQPage from the same strings it renders.
- **Version at build time** — the footer reads the repo's `VERSION`; no request per page view.

## Pitfalls

- **Wrap every storage access in try/catch** — Tor Browser and strict privacy modes throw; an uncaught throw in the head script blanks the theme.
- **Read repo files with `path.resolve()` from `website/`** — `import.meta.url` points at the built chunk, not the source file.
- **`/` redirects only on a saved or browser language** — the English landing's `early` script skips the redirect when the reader chose English or storage is unavailable, so the English link always sticks.
