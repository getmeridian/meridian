# tests
## Design decisions
- Unit tests exercise typed parsing, topology compilation and mock SSH boundaries; deployment and CLI tests use the same shared builders in `tests/support/`.
- `serve_control` runs the real loopback service for account, name resolution, lifecycle, adoption and authorization tests. Seed people in the service or setup draft, never topology.
- Use tables over near-duplicates and quirk-named scenarios; assertions cover contracts and user outcomes, not individual helper implementations.
- Keep mocks, `CaptureReporter`, fence hosts, control fixtures and topology builders in `tests/support/`; search there before adding a local double.
- PWA and console tests use Node with the shared DOM/fetch/storage boundary. Real browser E2E, Docker system lab and VM providers remain opt-in.
- Pinned Xray/Mihomo/sing-box tests exercise actual delivered documents when configured; local runs may skip missing runtimes.
## What's done well
- Adoption tests prove loading writes nothing, confirmed forgetting differs from retirement, saved cut-offs survive, and a lost creation reply resumes without rotating keys.
- Scope matrices use runnable `ROUTE_SAMPLES`; every handler's actual request exercises its cataloged permissions.
- Real service pages supply people-query and browser fixtures, so API changes reach the console scenarios.
- Privacy tests include negative evidence: lower scopes read no private values, previews write nothing, events stay content-free and snapshots preserve permitted state.
## Pitfalls
- Pin every clock the scenario reads: service, ledgers, registry, settings and receipt time. Due schedules otherwise make results depend on the real date.
- `tmp_home` patches both environment and `meridian.config` attributes; read config values at call time.
- Reset JSON, quiet and requested/current language modes between tests; leaked modes hide stderr, force confirmation or change translated expectations.
- Shared panel doubles supply bounded query pages and current display names; do not infer them from old access declarations or pin internal read counts.
- Real current writes refuse folded name collisions; pre-26 ambiguity scenarios must seed the historical rows directly.
- Older-schema fixtures rewind later fields, indices and triggers before setting schema version; name, policy, membership, capacity and row-version migrations must stay ordered.
- Drop membership/profile triggers before removing people tables; their row-version references otherwise break migration rewinds.
- Schema migration assertions compare with `SCHEMA_VERSION`, not a copied current number.
- Report freshness uses accepted receipt time; updating a hello must never renew an older runtime report.
- MockConnection matches substrings; use specific patterns because an unrelated command may contain the same fragment.
- Terminal readers use poll/selectors rather than select's fixed descriptor limit; full suites exhaust that small limit.
- Pinned Xray blocks private freedom targets by default; loopback revocation tests need an explicit allow before judging traffic.
- Console scenarios go over stdin, since Linux limits an individual argument's size.
- DOM scenarios require `needs_console_dom`, make the document visible and include head; hidden screens defer requests, and optional script parents match real pages.
- Console drivers wait until controls are enabled. Shared component builders and real fetch replies preserve row identity and guards.
- Count visible DOM markers; hidden retained badges do not represent current state.
- Strip styling before help assertions, and normalize Persian digits before comparing displayed counts.
- Wizard prompts read `/dev/tty`; use scripted IO or pexpect instead of CliRunner stdin for interactive journeys.
- Stall fixtures zero delays while preserving retry counts; inject sleep and delays when pause behavior is the subject.
- Scope fixtures must seed actual profile discovery and snapshot compatibility evidence before confirmations; permissive doubles can hide early refusal requirements.
- Demo route scans refuse unknown routes and exclude only real opaque-ID boundaries; do not add wildcard exceptions.
- Treat capability IDs as data rather than locale keys, while every literal translation call must resolve.
- Read-only previews never trigger post-mutation backups; mutating replies and uncertain writes retain backup notifications.
- Keep unchanged subscription corpus requests byte-identical, and keep test-only helpers outside production modules.
