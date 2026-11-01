# content/docs — Markdown documentation source

## Design decisions
**Reviewed bulk people** — client and CLI guides describe server paging, shared filters, frozen targets, atomic commit, receipts, undo, declared deletion commands, and privacy in all four locales.
**Live revocation** — client guides explain identity-only blocks, preserved DNS/IP routing, restart and re-enable cleanup, and the bounded-rule recovery request in every locale.
**Account schedules** — client and CLI guides document UTC anniversaries, first-traffic durations, capped renewal catch-up, previews, pending extension, and preservation; connecting guides explain first use and the page's dates in every locale.
**Connection-key rotation** — client guides distinguish refresh with the same URL from reissue after a leaked link, document generation preconditions, device preservation, declared ownership, and optional hard cut-off in every locale.
**Session revocation guides** — client management and CLI reference describe per-node new-flow blocking, old-agent and outage limits, surviving TCP/UDP/XUDP associations, and the explicit shared-inbound reset in every locale.
**Person guides** — explain Leave/Stay, memory-only drafts, preserved focus, visible 30-second refresh, and owner idle expiry; browser-local expiry, exact UTC, calendar-month clamping, explicit null, and changed-field saves; captioned PNG/SVG QR export, clipboard fallback, and local-only rendering; and effective enable results. With the CLI guides they document individual credentials, explicit format URLs, their people/admin access, and the producing-output boundary.
**Client, connecting, and CLI guides** — distinguish automatic browser defaults, the explicit subscription-language default and page/app languages, nullable person overrides, saved page choices, console edits, stable English status codes, the connection page's own picker, and JSON `--yes`; and document operator notices, safe substitutions, expiry, rendered confirmation, encrypted snapshots, and content-free events. Client and connecting guides explain reviewed public owner contact, user-initiated support links, explicit clearing, and bounded inactive remarks.
**Client guides** — document private notes/tags (explicit clearing, tag filters, people/admin scope, snapshots, migration bounds) and device privacy (default off, private/full disclosures, restore registration loss, supported-HWID counting, and registration removal versus credential reissue).
**Period usage** — client-management.md explains shared directional counters, preserved lifetime totals, and the explicit fallback for imported or unrecoverable historical directions.
**Browser reissue** — client-management.md explains the shared cut-off review, separate per-node outcomes, and new credentials surviving a failed cut-off.
**Subscriber device slots** — privacy, connecting, and client-management guides disclose count-only `/info`, full-slot refresh behaviour, uncounted apps, and memory-only page display.
**Activity attention** — CLI and client guides distinguish `unused` from `stopped`, disclose their independent day thresholds, and keep unknown imported activity unclassified.
**People triage** — client-management.md describes fragment persistence, contextual filter counts, integer bounds, any-age never-used filtering, and missing dates sorting last.
**API tokens** — the API-token section in client-management explains scoped issuance, authenticated OpenAPI discovery, credential/scope markers, cut-off permissions, and the separate people capability with its credential access and transactional declared-user refusals; operation tables are checked against the committed admin contract by `scripts/validate_api_docs.py`.
**Subscription guides** — explain Automatic-first Xray profiles and single-primary routing without mixing fingerprint alternatives, plus transient format preview, shared live rules, omissions, redacted headers, API-managed endpoint disabling, and reviewed apply restoration.
**Client routing policy** — split-routing.md documents reviewed `ru-split | full`, private/LAN and best-effort Xray BitTorrent bypasses, format limits, subscription refresh, read-only console settings, and separate exit geo-blocking.
**Server allowance guide** — the fleet/server CLI reference documents reviewed metadata, binary GB/TB aliases, UTC receipt periods and clamped reset days, approximate coverage, unmeasured relays, and computed payment reminders.
**Hosting, relay, and security guides** — keep dated hosting reports, mobile whitelist limits, and audited outbound requests in all four locales; distinguish page requests from installation, DNS, diagnostics, and VPN-app traffic, and state each request's data and controls.
**Per-locale directories** — each locale (en, ru, fa, zh) has its own folder with identically named markdown files. English is the source; other locales are translations.
**Frontmatter schema** — Starlight's plain `docsSchema` (`title`, `description`). Sections and page order live in one list, `src/lib/docs-sidebar.mjs`, shared by every locale.
**Machine-readable consumption** — these markdown files are read at build time by the Markdown twins (`/{locale}/{page}.md`), and by `scripts/ai-docs.mjs` (`/llms-full.txt`, `/ai/reference.md`, and the packaged `ai-reference.md`).
**Durable prose** — write as the current release: no beta notes, no "still works for one release", no version-dated caveats.
**Russian glossary** (docs, landing, CLI) — relay is «промежуточный сервер», firewall «файрвол», no «флот» (say «ваши серверы»); the product is a «VPN» in prose, «прокси» only where technically exact (protocols, Xray, app proxy modes).
**App list from the catalog** — a line holding only `<!-- apps -->` becomes the featured apps of `src/data/apps.json` (`src/lib/apps-list.mjs`) in the page and its Markdown twin; never hand-write an app list.
**Links** — link other pages as `/{locale}/{page}/` (with `#anchor` from that locale's own heading); the build validates every link.

## Pitfalls
- **Privacy disclosures are source text** — privacy.md explains defaults, seizure costs, downgrade deletion, and private device re-registration after restore in every locale; totals-only claims must say they are the default.
- **Locale parity** — every guide entry above holds in all four locales, and all four locale directories must have the same filenames. A missing file means that locale won't have that doc page, and the sidebar will show a gap.
- **AI bundles are English-only** — `scripts/ai-docs.mjs` reads only from `en/`. Translations don't affect the AI-facing docs.
- **A new page joins the sidebar list** — add its slug to `src/lib/docs-sidebar.mjs`; the build fails on a page that is missing from it or a slug without a page.
- **This CLAUDE.md lives inside a content collection** — `content.config.ts` uses `**/[a-z]*.md` glob to exclude it. All doc filenames must stay lowercase.
- **CLI validation is live-tree and section-scoped** — every public leaf and root option must appear in its matching section in all four locales; hidden developer commands are excluded.
