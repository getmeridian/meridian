# pages — File-based routing for everything except docs

## Design decisions

**Docs are Starlight routes** — Starlight injects `/{locale}/{page}/`; this folder only holds `/docs/` (the stable entry that opens Getting Started in the reader's language) and the Markdown twins.

**Machine-readable endpoints** — `[...slug].md.ts` (`/{locale}/{page}.md`), `llms.txt.ts`, `llms-full.txt.ts`, `ai/reference.md.ts`, and `agents.md.ts` (the CLI's agent guide). The bundles come from `scripts/ai-docs.mjs`, the rest from `src/lib/docs-markdown.ts`.

## What's done well

- **Markdown links stay Markdown** — `markdownBody()` rewrites links between docs pages to their `.md` twins, so an agent can follow them without HTML; the bundles make every link absolute.

## Pitfalls

- **No browser reachability testers** — a page cannot tell reachable from refused or reset, and its TLS checks warn on healthy servers; point readers to `meridian test --probe` or their connection page.
- **Static endpoints keep only the body** — response headers are discarded at build time and the host derives the content type from the extension, so name endpoints `.md`/`.txt`.
- **Build-time file reading** — the Markdown endpoints read files with `fs`; `agents.md.ts` reads `../src/meridian/data/agents.md`, so the site builds only inside the full repo. Resolve paths from the website folder, never `import.meta.url` (bundling moves it).
- **JS helpers in Astro frontmatter** — add an explicit boundary type before iterating helper results; otherwise `astro check` reports implicit `any`.
