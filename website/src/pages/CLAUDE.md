# pages — File-based routing for everything except docs

## Design decisions

**Docs are Starlight routes** — Starlight injects `/{locale}/{page}/`; this folder only holds `/docs/` (the stable entry that opens Getting Started in the reader's language) and the Markdown twins.

**Machine-readable endpoints** — `[...slug].md.ts` (`/{locale}/{page}.md`), `md/[...slug].ts` (the same at its original `/md/...` address), `llms.txt.ts`, `llms-full.txt.ts`, `context-hub.md.ts`, and `agents.md.ts` (the CLI's agent guide). All but the last render through `src/lib/docs-markdown.ts`.

**Ping page** — client-side network reachability tester. No server-side ICMP. Tests port 443 connectivity + HTTPS domain. Stores history in localStorage.

## What's done well

- **Markdown links stay Markdown** — `docBody()` rewrites links between docs pages to their `.md` twins, so an agent can follow them without HTML.

## Pitfalls

- **Ping page CORS** — fetch tests may fail for CORS reasons even if server is reachable. Results need cautious interpretation.
- **Build-time file reading** — the Markdown endpoints read files with `fs`; `agents.md.ts` reads `../src/meridian/data/agents.md`, so the site builds only inside the full repo.
- **JS helpers in Astro frontmatter** — add an explicit boundary type before iterating helper results; otherwise `astro check` reports implicit `any`.
