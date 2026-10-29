# commands — One module per subcommand
## Design decisions
- **Entry points** — root commands live in `cli.py`, sub-apps in `cli_groups.py`; flags defer to command modules. Public commands need help layout/panel, output metadata, supported JSON contracts, and four-locale CLI reference coverage.
- **Reviewed intent** — fleet mutations edit intent through `apply_reviewed_intent`; removals retire resources. Client status, quotas/device caps, expiry, and traffic resets are service-owned exceptions. Add sets requested limits after creation; applies preserve them.
- **Devices** — `client devices` reads through `control_access`: private human output shows counts, JSON removable fingerprints, full output details. Remove/clear are exclusive, return zero when already done, and preserve credentials. Reissue clears registrations.
- **Targets** — `cli_options`/`resolve` own saved names, IPs, user@host, ports, and local mode. Validate typed requests before connections; library errors become actionable command errors.
- **Machine output** — shared Json and `is_json_mode`; Click resolves JSON/context flags through `cli_types` only. Deploy request/events/dry-run never prompt or mix Rich into JSON stdout.
- **Contexts** — select root/leaf context before reads; superseded contexts admit listed reads, Remnawave fleets only move/remove/export. Never scan argv to infer commands/help.
- **Health and wizard** — renderers distinguish failure (4) from unavailable evidence (3). Wizards collect choices while setup/compiler/reconciler execute; non-interactive setup requires --yes or an intent file.
- **Ownership** — list/show/devices read console people; CLI mutations target declared users. Exact names win before folded/display names; ambiguous names refuse. Connection pages require saved evidence and publishing uses SSH.
- **Compatibility** — renamed commands/flags refuse with current pointers; `help agents` prints release-independent Markdown naming live commands.
## What's done well
- Upgrades review/apply running-version changes then check results; client mutations pull encrypted snapshots through the shared control client.
## Pitfalls
- `fail` exits by category (user 2, unavailable 3, bug 1); wrap validation/library exceptions, never expose tracebacks. Rebuild untrusted models rather than `model_copy(update=...)`.
- Declined confirmation exits; JSON removals/reissues require --yes. Handoff links appear only in producing envelopes via `HANDOFF_LINK_KEYS`; diagnostics redact free-form text with cluster secrets.
- Caps accept 1–100/default: default sends explicit null, omitted flags preserve overrides. Validate removal IDs/options before connecting.
- Reissue deletes the old page before rotating its UUID, then publishes the new page and persists evidence.
- Teardown refuses remaining roles; removals refuse control/dependents. Save unreachable-state forgetting only after confirmed apply.
- Enable reports resulting expired/limited status; extending expiry, lifting quota, or resetting use restores access.
- Post-mutation save failures say remote state changed, exit 3, and retain typed partial results where supported.
- Select context before file access; read `config` paths at call time, never hard-code the install home.
- Refuse unknown bare words before dialing; test targets resolve saved names before DNS. Timeout stays text for JSON validation errors.
- Local SSH follows saved machine identity; persist offered key setup's identity file, never offer passwords without terminals or in JSON mode.
- Wizards use real terminal devices, not /dev/tty; validate through prompters, accept Ctrl+J, and raise non-interactive failures rather than retry forever.
- Node/relay re-add resumes declarations and refuses conflicts; addresses reused as saved names are no exit titles.
- Replacement refuses control/already-owned targets; bare-IP certificate SNI/Host follow replacement, and reruns repair them.
- Bare tests follow intent, not saved-server counts; client tests/mutations never reach unrelated panel users.
- `panel` resets only with --setup-link, skips mutation listeners, and never renders saved admin credentials.
- Remnawave deploy offers migration interactively; silent modes refuse. Align its gate with the live command tree.
- Preflight includes loopback nginx ports; failed initial applies still own resources until retirement.
- Palettes/icons use core/branding validation; saved unnamed branding survives redeploys.
- Health accepts every shipped agent build ID; doctor reads netcore's unit/journal, not retired Docker containers.
