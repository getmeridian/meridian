# scripts — Repository automation

## Design decisions
- Admin OpenAPI joins `export_contracts.py` and its drift check; `validate_api_docs.py` checks all four localized API operation tables against the committed document, and `make validate` runs both gates.

**Contract export** — `export_contracts.py` writes `contracts/meridian/`; its rules live in `contracts/CLAUDE.md`.

**Deterministic output** — scripts that write tracked artifacts must have a `--check` mode for CI drift detection.

**Dead-code report** — `find_dead_code.py` (stdlib `ast`) builds a reference graph over `src/meridian`: imports resolve precisely through re-exports, attributes and production strings match members by name. Liveness spreads from production roots (src top level, `scripts/`, entry points, registration decorators), then from `tests/`; it reports `dead`, `test-only`, and `unused-param` as sorted warnings in `make ci` and CI, failing only under `--strict`.

**Package hook** — `hatch_build.py` is the wheel and sdist build hook; `verify_package_assets.py` checks a wheel carries the agent guide, and `--release` (publish-pypi) also requires the generated AI reference. The sdist is an allowlist (`only-include` in `pyproject.toml`): the package, this hook, `VERSION`, and `CHANGELOG.md`.

**Bundled node agent** — the same hook packages each `agent/dist/netagent-<target>` build `make agent` wrote, with its `.sha256` and `.version`, as `meridian/agent_dist/` (the sdist carries them under `src/meridian/agent_dist/`, so a wheel built from it keeps them and needs no Rust); `verify_package_assets.py --release` and `--agent-only` require both builds and check their digests. Wheels stay `py3-none-any`: the binaries run on remote hosts.

## What's done well

- `build_showcase.py` and `showcase_seed.py` generate the website demos from a temporary real control service, and `showcase_screenshots.py` photographs them (Playwright, local only); none of the three ships in the sdist allowlist or the wheel (tested).

- `subset_fonts.py` rebuilds the website's WOFF2 subsets from the upstream font releases (instanced axes, per-script `unicode-range` files; network, never CI); keep its ranges equal to `website/src/styles/fonts.css`.
- `bump_pins.py` raises Realm, RealiTLScanner, and acme.sh (hashed here; it publishes no assets) to the newest release in each tested major and rewrites `config.py` by exact substring, refusing an ambiguous match; the weekly `bump-pins` workflow runs it and opens a PR.
- `export_geodata_lists.py` writes `core/geodata_lists.py` from the geodata in the pinned Xray archive (network, never CI), the same files node agents install; a test fails until it is rerun after `XRAY_VERSION` moves.
- `cli_map.py` renders the concept map, traffic and plan/apply diagrams, and every command's live help (walked from `cli_root()`) into one self-contained `build/cli-map.html` (`--hidden` adds the control-service commands, `--json` dumps the commands); draft visuals for the docs, never shipped.

## Pitfalls

- Allowlist only real false positives in `dead_code_allowlist.txt`, each with a `# reason`; delete genuine dead code (and the tests keeping it alive) instead. Teach the analyzer a recurring reference pattern rather than allowlisting every name it hides.
- Hatch drops gitignored files, so a generated file the package needs goes in `[tool.hatch.build] artifacts`; `ai-reference.md` was built in every release and shipped in none.
