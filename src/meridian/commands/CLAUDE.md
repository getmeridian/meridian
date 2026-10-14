# commands — One module per subcommand

## Design decisions

**One file per command** — `cli.py` registers top-level commands and the root callback, `cli_groups.py` the sub-apps; both parse flags and defer to the command module.
**Commands edit intent** — fleet commands load `cluster.yml` through `load_topology_cluster()`. `client`, `node`, `relay`, and `route` add/remove edit `topology_intent` and apply it through `_helpers.apply_reviewed_intent()` (progress from `apply_progress()`); removals let reviewed retirement delete what vanished (`topology_removal.py`, `node_replace.py`). Never mutate panel or server state beside the compiled plan.
**One target vocabulary** — `cli_options.py` owns the shared `SERVER` argument and option types; `meridian.resolve.parse_target()`/`merge_target()` read `[user@]host[:port]`, saved names, and `local` like ssh. `resolve.py` adds prompts around the cascade: target, local mode, single saved server, else fail with candidates.
**One JSON switch** — every `--json` is `cli_options.Json` and every JSON-implying flag uses `enable_json_output`, so bodies only read `is_json_mode()`. The root callback resolves argv through Click (`cli_catalog`); global `--json` works exactly where the command declares it. Never re-parse argv by hand.
**Machine deploy mode** — `deploy --json`, `--events=jsonl`, `--request FILE`, and `--dry-run` serve UI clients: no prompts or Rich, the final `meridian.output/v1` envelope on stdout, JSONL on stderr. Hidden `--cancel-file` stops at the next safe point with a `cancelled` envelope (exit 5).
**Health surface** — `check` (`health.py`) dispatches by role to preflight, server, or relay (`relay_check.py`) scopes; `test` runs one active client's delivered Xray JSON end to end, once per public entry when bare (`--probe`, or `probe`, for the censor view; `--basic` refuses `--client`); `doctor` bundles a report. All render through `check_render.py`: negative evidence exits 4, unavailable evidence 3, full pass 0. `logs` prints plain tails of what the server's roles run (`diagnostics/logs.py`) and exits 3 when one is unreadable.
**Presentation-only wizard** — `setup_v4.py` and `setup_wizard.py` collect resumable choices with text from `meridian.i18n` and prompts from `wizard_prompts.py`; smart defaults replace questions, and compile/apply/verify stay in `meridian.setup`. `wizard_terminal.py` adds prompt_toolkit lists, checklists, and validated text fields (the `Pickers` protocol); `wizard_io()` falls back to numbered prompts without a capable terminal or with `MERIDIAN_PLAIN_PROMPTS`.
**Agent guide** — `meridian help agents` prints `data/agents.md` as plain Markdown; `test_help_command.py` fails if it names a missing command. Keep it free of release talk.
**Retired spellings** — `cli.RENAMED`/`RENAMED_OPTIONS` refuse old commands and flags with exit 2 and a pointer to the new spelling.

## What's done well

- **`fleet upgrade` never mutates beside the plan** — it reads images (`diagnostics/images.py`), applies the unchanged intent (new pins ride the render contract), then runs the fleet `check`.

## Pitfalls
- `console.fail()` always exits (user=2, system=3, bug=1); entry points convert library `MeridianError`s with `fail(exc)`, or the user sees a traceback.
- Reach Click only through `meridian.cli_types` — Typer ≥ 0.26 vendors it and production has no `click` package (architecture-tested).
- `confirm()` returns a bool; callers must `raise typer.Exit(1)` on False.
- Build core request models and render wrapped validation errors before opening SSH or panel connections; rebuild a model from untrusted data, never `model_copy(update=...)`.
- Teardown refuses a server that still holds intent roles (only a whole single-server fleet goes in one step) and deletes the saved profile last.
- Removals refuse the control server and its dependents before review; `node remove --force` only extends the edit to relay chains into that exit.
- Post-mutation local save failures exit 3, say remote state changed, and keep typed partial data where the contract allows.
- Client reads and tests expose only `topology_intent.access.users`, never panel-wide service users.
- Never render saved admin credentials except `panel --show-password`; handoff links (`HANDOFF_LINK_KEYS`) appear only in the producing command's final output (`emit_json(..., reveal=...)`); doctor and `logs` pass free-form text through `redact_diagnostic_text()` with `ClusterConfig.secret_values()` (bare request paths match no pattern) and never through Rich markup.
- A bare word that is neither a saved name nor an address is refused, never dialed; keep `--timeout` a string so bad values still get the JSON error envelope.
- `ensure_server_connection(offer_key_setup=True)` (deploy without `--yes`, setup) may return a target pinned to Meridian's new key; save `resolved.conn.identity_file` as the profile's `key_path`. Never offer it without a terminal or in JSON mode.
- The wizard needs a terminal (`prompt()` returns defaults without `/dev/tty`); `setup` refuses without one unless `--yes`.
- An error in a setup stage that asks nothing (review, apply, verify) offers back/retry/save, and `--yes` raises it; re-running the stage looped forever on the same error.
- Pickers open the terminal's real device (`os.ttyname`), never the `/dev/tty` alias (macOS kqueue refuses it with EOFError), and accept Ctrl+J as Enter (pexpect and some terminals send a bare line feed); validate through `Prompter.ask(validate=)` so both modes re-ask.
- Re-running `node add` or `relay add` for a declared server resumes it and refuses a conflicting SNI, domain, exit, or port; never add a second entry.
- `node replace` refuses the control server (subscription URLs would change) and a new server that already holds a role; check roles before dialing.
- Bare `test` follows the intent, not the saved-server count: removed servers stay saved.
- A new public command needs a `_HELP_LAYOUT` entry, a `rich_help_panel`, and a `core.command_catalog.COMMAND_OUTPUTS` entry (`None` unless it declares `--json`); `test_cli_catalog.py` enforces it.
- `deploy` port preflight (`port_check.py`) covers every port nginx binds, including loopback 8443/8444; a missed one failed mid-deploy on `nginx -t`.
- Page palettes live in `core.inputs.PAGE_PALETTES` (tested against `app.js`); normalize `--icon` with `branding.normalize_icon()` before SSH, and a redeploy merges unnamed branding flags from saved values.
