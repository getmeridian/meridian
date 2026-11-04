# control — Meridian's control service (`ctld`)
Owner console assets: `ui/CLAUDE.md`. Formats: `formats/CLAUDE.md`.
## Design decisions
- The service owns every person. Account creation, names, credentials, deletion, limits, expiry, metadata and memberships live in SQLite; topology only owns node specs, endpoints and reviewed settings.
- Schema 26 seeds display names once from the latest applied revision. Empty names show usernames; creation can assign Latin usernames, and writes refuse folded name/username clashes atomically.
- Every person can be removed, reissued and rotated through the same API. Bulk deletion freezes all selected people and deletes them in one transaction, without local topology proposals.
- `people_intent.py` reads the applied topology for fleet views only; revisions never determine ownership.
- The stdlib loopback HTTP server sits behind nginx's secret ingress. Cataloged routes drive dispatch, capabilities, schemas and OpenAPI.
- Authenticated API replies carry the installed version header for narrow compatibility checks; anonymous and subscription responses never expose it.
- Store migrations are self-contained forward steps under the initialization transaction; newer schemas are refused, never downgraded.
- Tokens freeze cataloged capabilities at issuance. Credentials and private metadata have separate grants; owner writes also hold session and step-up liveness.
- Revisions record secret-free reviewed intent and hashes; the service stores apply-fence CAS state without making deployer lease decisions.
- Profile declarations and compiled grants save with reviewed settings; memberships and private delivery overrides use the shared policy resolver.
- People query cursors share SQL predicates and stable row versions. Bulk previews stay bounded in memory; receipts are durable and undo checks person versions, enforcement and node/grant identity.
- Node documents compose from current accounts and specs, sign once per changed content, and use negotiated protocol capabilities and generation-bound flow-block receipts.
- Cut-offs are atomic with account changes and release only after the revoking revision applies. VLESS streams require a core restart, with disclosed fleet reconnects.
- Traffic grows once per accepted counter epoch/sequence. Node totals survive node deletion; person totals do not. Account schedules share one injected UTC clock and directional period baselines.
- Privacy defaults off. Lowering a level deletes or rekeys retained data with the audit row; no client address is stored. Snapshots exclude operator authentication secrets, sessions and owner keys.
- Subscriptions and connection pages use the same eligibility and delivery rules; person responses are no-store/noindex, and shared package assets are token-free.
- App response rules are reviewed transient matching settings; capacity history and metrics remain bounded node aggregates without person labels.
## What's done well
- Previews and derived reads write nothing; lifecycle facts are typed and contain no notes, tags, requests or credentials.
- A missing endpoint or late report cannot recreate a removed person; unknown traffic identities are discarded.
- Bulk deletion passes every retired identity to one document composition so revocation receipts match individual deletion.
- Restored snapshots preserve account state while rotating service access and the fleet CA.
## Pitfalls
- Re-check session and token liveness inside each write transaction; pre-body authorization alone permits a revoked tab to write.
- Declare every API operation and add runnable `ROUTE_SAMPLES`; include conditional credentials and private membership grants.
- Attach version headers only after authentication; broader fleet health is not a prerequisite for people writes.
- Legacy receipts can contain a proposal field: consume it when reading stored receipts, but omit it from current contracts.
- The ensure route fills empty names inside its transaction so a concurrent rename wins; preserve live settings during adoption; schema backfills validate historical names and skip conflicts instead of blocking startup.
- Only reviewed profile IDs exempt secret-shaped keys in delivery defaults; all other keys and all values undergo secret validation.
- `executescript()` commits on its own; use `Store.create()` outside write transactions for migration setup.
- Decode percent-encoded IDs before routing, bound bodies before authentication, and answer misses like nginx without logging secret paths.
- Treat uncertain 502/503/504 writes as uncertain outcomes; never retry before observing. Emit Retry-After for named throttling errors.
- Use the injected service clock for reads, schedules, history and report freshness; accepted receipt time, not a replayed hello, renews evidence.
- Validate requested lifecycle fields before enforcement; renewal anchors, counts and expiry must agree and remain before 2099.
- Period directions sum to use; carried history is a subset of download, and first use means accepted traffic rather than subscription reads.
- Keep unresolved revocation identities through later rotations; queue cut-offs against current revision and clear only matching restored keys.
- Re-check profile defaults, memberships and person overrides before preview, commit and undo; omit only the profile predicate from contextual facet counts. Redaction preserves supplied fields, and lower grants blank private names-independent metadata and credentials on every result. Missing HWID never blocks subscriptions; browser reads never register a device, and zero device cap remains unlimited. Background errors log fixed codes only; shared PWA helpers must be registered, hyphenated basenames must route, and page language is resolved under the settings lock.
