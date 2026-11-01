# commands — One module per subcommand

## Design decisions
**Client registration** — `cli_client.py` owns the client group so lifecycle options do not grow `cli_groups.py`.
**One file per command** — `cli.py` registers top-level commands and the root callback, `cli_groups.py` the sub-apps (a group may live in its own `cli_*.py`); both parse flags and defer to the command module.
**Commands edit intent** — fleet commands load `cluster.yml` with `load_topology_cluster()`, edit `topology_intent`, and apply it through `_helpers.apply_reviewed_intent()` with the exact review hash; removals let reviewed retirement delete what vanished. Never mutate panel or server state beside the compiled plan; the exceptions are operator-owned service data (client status, limits, devices, expiry, notes, tags, language, privacy, notices, fleet language), which applies never touch.
**Reviews before writes** — every intent edit previews and confirms; JSON mode requires `--yes`. Service-owned edits (`fleet language`, `fleet notice`, `privacy set`, `client set`) confirm against a service-rendered preview where one exists and never run an apply.
**Client names resolve like `client show`** — an exact service username wins over a case-folded or display-name match, and a name folding onto two people is refused. Service-owned edits and reads cover console-added people; topology-owned removal and reissue act only on `topology_intent.access.users`.
**One target vocabulary** — `cli_options.py` owns the shared `SERVER` types; `resolve.parse_target()` reads `[user@]host[:port]`, saved names, and `local` like ssh, and `resolve.py` adds the prompts.
**One JSON switch, one context switch** — every `--json` is `cli_options.Json` and bodies only read `is_json_mode()`; the root callback resolves argv, `--context`, and help requests through Click (`cli_catalog`), never by re-parsing argv. A superseded context runs only `context.READS_ONLY` commands.
**Machine deploy mode** — `deploy --json`, `--events=jsonl`, `--request FILE`, and `--dry-run` serve agents and automation: no prompts or Rich, the final envelope on stdout, JSONL on stderr; hidden `--cancel-file` stops at the next safe point (exit 5).
**Health surface** — `check` dispatches by role, `test` runs a client's delivered Xray config end to end, `doctor` bundles a report, `logs` tails the server's units; all render through `check_render.py`: negative evidence exits 4, unavailable evidence 3, a full pass 0.
**Presentation-only wizard** — `setup_v4.py` and `setup_wizard.py` collect resumable choices (text from `meridian.i18n`, prompts from `wizard_prompts.py`, lists from `wizard_terminal.py`); compile, apply, and verify stay in `meridian.setup`.
**Agent guide** — `meridian help agents` prints `data/agents.md`; `test_help_command.py` fails if it names a missing command.
**Retired spellings** — `cli.RENAMED`/`RENAMED_OPTIONS` refuse old commands and flags with exit 2 and the new spelling; `cli.RETIRED` refuses a command removed without a successor (`studio`) and points to where the work moved.

## What's done well
- `fleet upgrade` reads each relay's running Realm (a failed upgrade leaves the saved pin ahead), applies the unchanged intent, then runs `check`.
- `client remove|reissue --cut-off` discloses every affected node and other people's reconnects before confirming, and keeps typed per-node outcomes and new links even on failure.

## Pitfalls
- **`console.fail()` always exits** (user 2, system 3, bug 1) — entry points convert library `MeridianError`s with `fail(exc)`, or the user sees a traceback.
- **Catch control-service errors inside the command's `error_context`** — it supplies JSON metadata but catches nothing; a non-retryable poll error is a failed outcome, not a timeout.
- **A local save failure after a remote change exits 3** and says remote state changed.
- **Reach Click only through `meridian.cli_types`** — Typer vendors it and production has no `click` package.
- **`confirm()` returns a bool** — callers must `raise typer.Exit(1)` on False.
- **Build and validate request models before opening SSH or panel connections**; rebuild models from untrusted data, never `model_copy(update=...)`.
- **A new public command needs a `_HELP_LAYOUT` entry, a `rich_help_panel`, and a `core.command_catalog.COMMAND_OUTPUTS` entry** (`test_cli_catalog.py`); hidden `serve` and `dev` stay out of the catalog.
- **Never save intent from a declined or dry-run review**; omitted options preserve saved values.
- **Role names are server names** — `server set` accepts the exit, gateway, and relay IDs `fleet status` prints, since labs whose saved names are IPs had no other handle.
- **Topology-owned removal and reissue never touch console-only people** — and `client reissue` clears device registrations and makes the new subscription URL the page URL at once.
- **`client enable` reports the status the service answers** (`expired`/`limited` when those still hold), never the requested one.
- **Removals refuse the control server and its dependents before review**; `node remove --force` only extends the edit to relay chains into that exit.
- **`forget_unreachable_servers()` forgets in memory only** — the confirmed apply saves it, or a declined import leaves bindings forgotten on disk.
- **Teardown refuses a server that still holds roles** (a whole single-server fleet excepted) and deletes the saved profile last.
- **`panel` asks for a setup link only with `--setup-link`** — a bare request racing an owner's setup would wipe their password; it builds its client with `report_mutations=False` because a setup link is no state a backup keeps.
- **Never render saved admin credentials** — handoff links appear only in the producing command's final output (`emit_json(..., reveal=...)`); `doctor` and `logs` pass free text through `redact_diagnostic_text()` and never through Rich markup.
- **A bare word that is neither a saved name nor an address is refused, never dialed**; bare `test` follows the intent, not the saved-server count.
- **Key setup may repin the target** — save the returned identity file as the profile's `key_path`; never offer key setup without a terminal or in JSON mode.
- **The wizard needs a terminal** — `prompt()` returns defaults without `/dev/tty`, so `setup` refuses without one unless `--yes`; a failing non-interactive stage offers back/retry/save instead of re-running into the same error.
- **Pickers open the terminal's real device** (`os.ttyname`), not `/dev/tty` (macOS kqueue refuses the alias), and accept Ctrl+J as Enter.
- **Re-running `node add`/`relay add` for a declared server resumes it** and refuses a conflicting SNI, domain, exit, or port; never add a second entry.
- **`node replace` refuses the control server** (subscription URLs would change) and a target that already holds a role; a bare-IP exit's certificate SNI follows it to the new IP.
- **`deploy` port preflight covers every port nginx binds**, loopback ones included, or deploy fails late on `nginx -t`.
- **Normalize branding before SSH** — palettes live in `core.inputs.PAGE_PALETTES`, icons go through `branding.normalize_icon()`, and a redeploy merges unnamed branding flags from saved values.
- **Agent versions accept any shipped build** (`control_artifacts.expected_agent_version()`) — a checkout that rebuilt one target carries two IDs.
