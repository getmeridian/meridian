# website — Astro static site (getmeridian.org)

```bash
cd website && pnpm install && pnpm run build   # Astro + Pagefind
```

## Design decisions

**Why Astro** — static generation means every page is a flat HTTP request. Content collections integrate natively with markdown docs. File-based routing. Minimal JS shipped (islands architecture).

**i18n strategy** — docs are per-locale markdown files (`src/content/docs/{locale}/`). Landing page uses client-side `data-t` attribute swapping. English is baked into HTML; non-EN languages swap at runtime. This is asymmetric but avoids shipping EN translations in JS.

**Self-hosted everything** — fonts (woff2), no CDN, no Google. Zero external requests. Target users live in censored regions where external resources are blocked.

**Machine-readable endpoints** — `/llms.txt` (AI index), `/llms-full.txt` (all docs concatenated), `/md/[slug]` (raw markdown per doc), `/context-hub.md`. These make the project AI-native.

**pnpm with guardrails** — `pnpm-workspace.yaml` blocks exotic transitive sources, enforces a one-week release age, and allows build scripts only for explicitly trusted packages.

## Website ↔ CLI relationship

- **App links**: `src/data/apps.json` is SOT. CI validates against template + Python constants.
- **AI docs**: en/ markdown → `make ai-docs` → bundled `ai-reference.md`. CI generates automatically.
- **install.sh**: deployed to `getmeridian.org/install.sh` by CI release workflow.
- **Studio contracts**: `pnpm run contracts:generate` builds `src/studio/generated/` from checked-in `contracts/meridian/v1`. Static Studio consumes these directly; executable Studio will use the same types through a local adapter.

## Pitfalls

- **Single language picker** — one LanguagePicker in the nav header handles all pages. On docs pages it reads available locales from `#docs-locales` JSON and navigates to locale-specific URLs. On landing page it does client-side DOM swap.
- **Language switch reload asymmetry** — EN reloads page (build-time HTML), non-EN does client-side DOM swap. Consolidating would simplify but requires shipping EN translations.
- **Early `<head>` scripts** — detect lang + theme from localStorage before paint. Prevents RTL layout shift and dark mode flash.
- **SEO structured data** — JSON-LD schemas (FAQPage, Organization, BreadcrumbList, Article) are baked at build time. Hreflang links on docs pages connect locale variants for search engines.
- **Sitemap i18n** — Astro sitemap integration generates entries for all locale paths.
- **Sharp is direct** — Astro image optimization must keep `sharp` as an explicit dependency; relying on Astro's optional edge leaves clean Linux builds without a linked image service.
