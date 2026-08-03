# pages — File-based routing

## Design decisions

**Dynamic docs routing** — `docs/[...slug].astro` catches all doc URLs. Locale extracted from slug. Content collection query renders correct locale markdown. Sidebar auto-filters to same locale.

**Machine-readable endpoints** — `/llms.txt` (index with descriptions), `/llms-full.txt` (all EN docs concatenated for LLM context windows), `/md/[slug]` (individual doc as raw markdown). These read markdown files via Node.js `fs` at build time.

**Ping page** — client-side network reachability tester. No server-side ICMP. Tests port 443 connectivity + HTTPS domain. Stores history in localStorage.

## What's done well

- **Docs index redirect** — `/docs/` uses early script to detect locale and redirect. Avoids shipping a "choose language" page.
- **`/llms-full.txt` link rewriting** — internal `/docs/` links rewritten to `/md/` for LLM consistency.

## Pitfalls

- **Ping page CORS** — fetch tests may fail for CORS reasons even if server is reachable. Results need cautious interpretation.
- **Build-time file reading** — `llms-full.txt.ts` reads fs. All markdown must exist before build.
- **JS helpers in Astro frontmatter** — add an explicit boundary type before iterating helper results; otherwise `astro check` reports implicit `any`.
