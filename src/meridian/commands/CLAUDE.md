# commands — One module per subcommand
## Design decisions
- CLI registration parses flags and delegates to command modules; client registration lives in `cli_client.py`.
- Fleet commands edit topology and use `_helpers.apply_reviewed_intent()` with the reviewed hash. Operator-owned service edits never apply topology.
- Every person is service-owned. `client add` POSTs validated `UserCreate` requests; remove DELETEs one person or uses the existing bulk preview/commit for several.
- Names resolve from the service: exact username wins, otherwise a case-folded username or display-name match must be unique. Every confirmation uses the live display name.
- `client add` checks every name before the first write, refuses existing people, and reports per-name requests and outcomes without SSH or local intent saves.
- Reissue replaces links; credential rotation uses the read generation and preserves subscriptions and registrations for every person.
- Single-person `--cut-off` discloses affected nodes and other people's reconnects. Bulk removal excludes it; removing the last person is valid.
- `--proposal` is retired by the CLI's option refusal mechanism. Other retired commands and spellings stay in `cli.RETIRED` and `RENAMED`.
- Bulk edits freeze names and versions; commit only the service's returned ID/hash, and read the receipt after an uncertain reply.
- Profile and delivery reviews live in setup; every imperative apply keeps their fingerprints and service membership fence.
- Setup is presentation over the resumable draft/runtime. Deploy applies topology, ensures its draft person through the service, verifies, and hands off.
- JSON uses one `meridian.output/v1` envelope; shared flags and command metadata are registered in `cli_options`, `cli_catalog` and core.
- Health has four-state evidence: failed checks exit 4, unavailable evidence 3, success 0. Machine deploy has no prompts and supports cooperative cancellation.
## What's done well
- CLI and console account paths share service contracts, generation checks and frozen bulk receipts.
- Secrets appear only in the producing handoff or explicit detail views; confirmations remain readable and content-free events stay typed.
- `meridian help agents` prints the packaged agent guide, which tests check against the public CLI.
## Pitfalls
- An account lookup must read current service names; topology names and full `list_users()` reads miss panel renames or widen the read needlessly.
- People tokens cannot read fleet health; account writes check the authenticated API version header without requiring broader rights.
- `error_context` supplies metadata but catches nothing: convert library errors with `fail(exc)` inside it.
- Validate typed requests before opening service or SSH access, and reconstruct untrusted models instead of unchecked `model_copy` updates.
- JSON mutations require `--yes`; `confirm()` returns a bool, and declining must exit before any write.
- Never save intent or forgotten bindings from a declined or dry-run review.
- A local save failure after a remote topology mutation exits 3 and says remote state may have changed; immediate add has no local-save outcome.
- Read an uncertain bulk receipt rather than repeating the commit; an undo must still be eligible and its inverse access losses reviewed.
- Escape once at the console boundary; `ok` and `warn` already escape markup, including display names.
- Cut-off consent names each node's whole-core or inbound boundary and its failure fallback; keep typed outcomes even on failure.
- Profile assignment and edits require the Meridian backend before service access; unavailable profile evidence stays uncertain.
- Profile conflicts use localized delivery refusals, and removing the last profile still reviews service memberships and effective access.
- Restore validates snapshot identity and profile compatibility before registering targets; destination saves retain their own file guard.
- JSON fleet exports reveal reviewed delivery containers verbatim while still masking credential fields.
- Tokens issue the reviewed permissions and absolute expiry; only the producing result reveals the new token.
- Reach Click only through `meridian.cli_types`; newly public commands need help layout and output-catalog entries.
- Re-running node or relay add resumes the declared server, while conflicting SNI, domain, target or port is refused.
- Omitted options preserve saved choices; role names resolve through the same server vocabulary fleet status prints.
