# scripts — Repository automation

## Design decisions

**Contract export** — `export_contracts.py` writes `contracts/meridian/`; its rules live in `contracts/CLAUDE.md`.

**Deterministic output** — scripts that write tracked artifacts must have a `--check` mode for CI drift detection.

**Dead-code report** — `find_dead_code.py` (stdlib `ast`) builds a reference graph over `src/meridian`: imports resolve precisely through re-exports, attributes and production strings match members by name. Liveness spreads from production roots (src top level, `scripts/`, entry points, registration decorators), then from `tests/`; it reports `dead`, `test-only`, and `unused-param` as sorted warnings in `make ci` and CI, failing only under `--strict`.

**Conditional Studio packaging** — `hatch_build.py` bundles, when `website/dist` exists (so clean source installs work), only the Studio page's closure: `studio/` plus every file its HTML, CSS `url()`, and JS imports reach, and the font licenses whenever a font rides along. `verify_package_assets.py` checks the page is there and nothing outside the Engine's served roots rides along; `--release` (publish-pypi) also requires the generated AI reference. The sdist is an allowlist (`only-include` in `pyproject.toml`): the package, this hook, `VERSION`, and `CHANGELOG.md`.

## What's done well

- `subset_fonts.py` rebuilds the website's WOFF2 subsets from the upstream font releases (instanced axes, per-script `unicode-range` files; network, never CI); keep its ranges equal to `website/src/styles/fonts.css`.
- `bump_pins.py` raises Realm, RealiTLScanner, acme.sh (hashed here; it publishes no assets), and the Remnawave images to the newest release in each tested major and rewrites `config.py` by exact substring, refusing an ambiguous match; the weekly `bump-pins` workflow runs it and opens a PR.
- `export_geodata_lists.py` writes `core/geodata_lists.py` from the geodata inside the pinned node image (Docker, never CI); a test fails until it is rerun after the image moves.
- `pin_image_digests.py` resolves every `*_IMAGE` constant in `meridian.config` to its multi-arch index digest, refuses indexes without linux/amd64 and linux/arm64, and prints paste-ready lines (network, never CI).

## Pitfalls

- Pin the index (manifest-list) digest, never a per-platform manifest digest: arm64 servers would fail to pull an amd64-only pin.
- Studio links to other site pages must be absolute `https://` URLs; a site-relative link fails the build, because the wheel would not contain the page.
- Allowlist only real false positives in `dead_code_allowlist.txt`, each with a `# reason`; delete genuine dead code (and the tests keeping it alive) instead. Teach the analyzer a recurring reference pattern rather than allowlisting every name it hides.
- Hatch drops gitignored files, so a generated file the package needs goes in `[tool.hatch.build] artifacts`; `ai-reference.md` was built in every release and shipped in none.
