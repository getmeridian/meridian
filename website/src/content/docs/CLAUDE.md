# content/docs — Markdown documentation source

## Design decisions
- API-token guides explain scoped issuance, authenticated OpenAPI discovery, credential/scope markers, and cut-off permissions in every locale; operation tables are checked against the committed admin contract by `scripts/validate_api_docs.py`.
- The person-screen guide describes effective enable results and preserves parity across all four locales.
- Client and connecting guides explain reviewed public owner contact, user-initiated support links, explicit clearing, and bounded inactive remarks in every locale.
- Client guides document private notes/tags, explicit clearing, tag filters, people/admin scope, snapshots, and migration bounds in all four locales.
- **Server allowance guide** — the fleet/server CLI reference documents reviewed metadata, binary GB/TB aliases, UTC receipt periods and clamped reset days, approximate coverage, unmeasured relays, and computed payment reminders in every locale.
**Client routing policy** — split-routing.md documents reviewed `ru-split | full`, private/LAN and best-effort Xray BitTorrent bypasses, format limits, subscription refresh, read-only console settings, and separate exit geo-blocking in every locale.

**People API access** — the API-token section in client-management documents the separate people capability, credential access, and transactional declared-user refusals in all four locales.

**Per-locale directories** — each locale (en, ru, fa, zh) has its own folder with identically named markdown files. English is the source; other locales are translations.

**Frontmatter schema** — Starlight's plain `docsSchema` (`title`, `description`). Sections and page order live in one list, `src/lib/docs-sidebar.mjs`, shared by every locale.

**Machine-readable consumption** — these markdown files are read at build time by the Markdown twins (`/{locale}/{page}.md`), and by `scripts/ai-docs.mjs` (`/llms-full.txt`, `/ai/reference.md`, and the packaged `ai-reference.md`).

**Durable prose** — write as the current release: no beta notes, no "still works for one release", no version-dated caveats.

**Device privacy** — the client guide documents default off, private/full disclosures, restore registration loss, supported-HWID counting, and registration removal versus credential reissue in all four locales.

**Russian glossary** (docs, landing, CLI) — relay is «промежуточный сервер», firewall «файрвол», no «флот» (say «ваши серверы»); the product is a «VPN» in prose, «прокси» only where technically exact (protocols, Xray, app proxy modes).

**App list from the catalog** — a line holding only `<!-- apps -->` becomes the featured apps of `src/data/apps.json` (`src/lib/apps-list.mjs`) in the page and its Markdown twin; never hand-write an app list.

**Links** — link other pages as `/{locale}/{page}/` (with `#anchor` from that locale's own heading); the build validates every link.

## Pitfalls

- **Privacy disclosures are source text** — privacy.md explains defaults, seizure costs, downgrade deletion, and private device re-registration after restore in every locale; totals-only claims must say they are the default.
- **Locale parity** — all four locale directories must have the same filenames. A missing file means that locale won't have that doc page, and the sidebar will show a gap.
- **AI bundles are English-only** — `scripts/ai-docs.mjs` reads only from `en/`. Translations don't affect the AI-facing docs.
- **A new page joins the sidebar list** — add its slug to `src/lib/docs-sidebar.mjs`; the build fails on a page that is missing from it or a slug without a page.
- **This CLAUDE.md lives inside a content collection** — `content.config.ts` uses `**/[a-z]*.md` glob to exclude it. All doc filenames must stay lowercase.
- **CLI validation is live-tree and section-scoped** — every public leaf and root option must appear in its matching section in all four locales; hidden developer commands are excluded.
