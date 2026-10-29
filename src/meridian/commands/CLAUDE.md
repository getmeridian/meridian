# commands — CLI adapters

## Design decisions
- CLI wiring lives in `cli.py`, `cli_groups.py`, and focused `cli_*` modules; imports defer to command modules so help and contract discovery do no work.
- Fleet edits change intent, review it, then apply through `_helpers.apply_reviewed_intent`. Operator-owned client status/limits/expiry and `privacy.py` change service state directly; applies leave them alone. `control_access` is the service adapter.
- Every JSON switch is `cli_options.Json`; Click resolves argv, parent options, context copies, and default group actions. Bare `privacy` is `privacy.show`, including JSON and read-only context gating.
- Context selection happens before I/O: explicit names must exist unless a command creates a fleet; superseded contexts admit `READS_ONLY`, and Remnawave contexts admit only the migration/export/removal gate.
- Shared targets/options live in `cli_options` and `resolve`; wizard presentation uses `i18n`, `wizard_prompts`, and terminal pickers while setup/compile/apply logic stays in libraries.
- Health rendering gives negative evidence exit 4, missing evidence 3, full pass 0. `check`, `test`, `probe`, and fleet upgrade inspect what the intent actually runs.
- Public commands need help panels, command output mappings, schemas for JSON, and four locale references. Hidden serve/dev stay outside the catalog. Retired spellings fail with a pointer.
- Privacy reviews disclose storage/seizure costs before confirmation, describe deletion on lowering, preserve omitted settings, and require --yes for JSON changes. Usage reads include console people; client show adds seven days only when enabled.

## What's done well
- `fleet upgrade` raises pins through the reviewed plan, then checks the fleet; it never mutates beside the plan.
- Changed or uncertain service writes trigger the backup hook; typed partial results describe remote changes a local save could not record.

## Pitfalls
- Convert `MeridianError` at entry points with `fail`; it exits (user 2, system 3, bug 1). Wrap validation errors before rendering.
- Reach Click only through `meridian.cli_types`; Typer vendors it.
- `confirm` returns bool: false must raise `typer.Exit(1)`.
- Validate typed inputs before SSH/service access. Keep numeric timeout/limit/history flags as strings when bad input needs a JSON envelope; never rebuild untrusted models with model_copy.
- Reissue deletes the old page before changing credentials, then publishes the new page; applies cannot repair a page under an abandoned UUID.
- Removal checks control-server/dependent roles before review; teardown saves profile deletion for last, and forgetting unreachable servers persists only after confirmation.
- Exact service usernames beat case-folded/display matches; ambiguous people refuse. Mutations target declared access users, while reads also show console people.
- Enabling reports the service's actual status, including still-expired or still-limited.
- Failed local persistence after a remote change exits 3 with typed partial data and a repair hint.
- Never render admin credentials. Handoff links appear only in their producing result via HANDOFF_LINK_KEYS; diagnostic text uses redact_diagnostic_text with saved secrets, never Rich markup.
- Save the identity file returned by key setup, and offer setup only with a terminal, without --yes/JSON. Unknown bare targets never dial; test/probe check saved names before DNS.
- Setup needs a terminal unless --yes; stage errors offer back/retry/save instead of looping. Pickers use os.ttyname, accept Ctrl+J, and validate through Prompter.ask.
- Re-adding declared nodes/relays resumes matching intent and refuses conflicts; an address is not a friendly name.
- Node replacement refuses the control server and already-used hosts; preserve the bare-IP certificate SNI/Host correctly and check roles before dialing.
- Remnawave fleets migrate explicitly; deploy offers it only in a terminal and refuses silent --yes/JSON moves.
- `panel --setup-link` alone requests an owner reset; bare panel never resets. Its ControlClient uses report_mutations=False because owner setup is unsnapshotted.
- Validate page icons and use core.inputs.PAGE_PALETTES; a redeploy merges saved unnamed branding flags.
- Port preflight covers all nginx listeners, including loopback ports. Own-domain SNI checks use own_hostname rather than probing a certificate before apply.
- Check agent versions against control_artifacts.expected_agent_version, which accepts every shipped build. Doctor reads netcore from systemd and the journal, never an old Docker container.
