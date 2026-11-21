# CI/CD — GitHub Actions workflows

## Design decisions

- **Two-stage pipeline** — CI validates on every push/PR. Release triggers only on CI success on main via `workflow_run`. Prevents accidental releases from failed builds.
- **Python matrix** — each of Python 3.11–3.15 runs the whole unit suite with the locked dependencies. Three pytest workers share the work on each hosted runner; tag releases run the same complete suite before publishing.
- **System Lab only where it matters** — the `changes` job diffs the PR (or push) and runs System Lab only when runtime code (`src/meridian` minus Markdown and locale catalogs), the agent, `tests/systemlab` (or the corpus comparison it reuses), dependencies, or `ci.yml` change; a skipped job still satisfies required checks and Release. `workflow_dispatch` always runs it. Widen the path list when the lab starts depending on something new.
- **E2E only where it matters** — the same `changes` job gates browser journeys on package code, the demo and its build scripts, shared test support, CI, or dependencies. The ten-minute budget includes a fresh Chromium and WebKit install. Failure traces and screenshots live for seven days.
- **Tool pins** — action references are full commit SHAs with release labels. CI reads Node, Rust and uv from `.github/.tool-versions` and pnpm from `website/package.json`; frozen installs retain the workspace supply-chain guardrails.
- **VERSION-driven releases** — read VERSION file, check if git tag exists. If missing: detect semver change, extract CHANGELOG section, push tag, create Release. Idempotent — safe to re-run.
- **Pre-releases from tags** — pushing `vX.Y.ZbN` (or `aN`/`rcN`) runs `release.yml` from the tagged commit: unit tests, a GitHub pre-release with `.github/BETA.md` plus the upcoming `X.Y.Z` CHANGELOG section, then the same PyPI gate. Stable releases still come only from main. A beta bump changes `VERSION` and the `v4_beta.yml` version placeholder and re-exports contracts (the OpenAPI document carries the version), then pushes the tag. Installers and `meridian update` on stable builds never pick a pre-release.
- **Verifiable installer** — stable and pre-release jobs attach `install.sh` and `SHA256SUMS` to the GitHub release (a second origin beside Pages); `install.sh` pins uv's installer by version and SHA-256, bumped together. While main's VERSION is a pre-release, deploy-pages sets `MERIDIAN_DEFAULT_VERSION` in the site's copy so it installs the beta it documents.
- **Weekly pin bump** — `bump-pins.yml` runs `scripts/bump_pins.py` on Mondays, force-pushes `bot/bump-pins`, opens or updates its PR, and dispatches CI on the branch (a `GITHUB_TOKEN` push triggers no `pull_request` run; `workflow_dispatch` is exempt). Opening the PR needs "Allow GitHub Actions to create pull requests"; without it the run warns with a compare link. A source it could not check fails the run after the PR step.
- **Weekly dependency canary** — `dependency-canary.yml` installs the checkout's wheel with the newest allowed dependencies (stable, and with `--prerelease allow`) and runs `scripts/smoke_install.py` (CLI help plus `meridian serve` answering 401). A failed scheduled run opens or comments on one issue. Control servers are unaffected: they install uv.lock's pins (`control-constraints.txt`, written by `scripts/hatch_build.py`).
- **OIDC publishing** — Pages deploy uses trusted publisher (no token). PyPI requires `environment: pypi` approval gate + OIDC. No long-lived secrets.

## What's done well

- **Validate job** — one job checks PWA rendering, `_PWA_APPS` against `apps.json`, VERSION, CHANGELOG, generated contracts, and the live CLI tree against every localized reference.
- **System Lab waits for lint and Agent** — the lab runs alongside the full Python matrix and deploys the exact runner-architecture binary that the same run packaged and verified. Download the artifact by its immutable ID, restore executable permission, and check its hash and reported version. No CI artifact crosses into publishing.
- **Client routing runs on real Xray** — Python 3.12 downloads the checksum-verified pinned runtime before pytest; bootstrap failure fails CI instead of silently skipping the loopback handshake tests. The same leg installs the website's jsdom for the console DOM scenarios (`MERIDIAN_TEST_CONSOLE`).
- **Demo validation** — the website job runs `scripts/build_showcase.py` into `dist/demo` and checks the connection page's assets, its disabled SW, rendered pages, and the console demo's files; the release job builds the same output.

## Pitfalls
- **Playwright must know the runner image** — `ubuntu-latest` moved to 26.04 and Playwright 1.58 refused to install Chromium there; raise the `e2e` group with the image rather than pinning an old runner.
- **Real-session tests compile the agent offline** — the runtime Python leg needs a stable Rust toolchain and a fetched locked Cargo cache before pytest, even though the separate Agent job prepares its own dependencies.

- **Pre-release notes read only `## [X.Y.Z]`** — beta changes go under its `### Since <previous beta>` part, never a separate `## Unreleased` heading, which no release job extracts (b9's whole changelog once sat there).
- **Release notes depend on CHANGELOG discipline** — if human forgets to update CHANGELOG before bumping VERSION, release notes fall back to git log (less useful).
- **Never bundle docs inline in a workflow** — `website/scripts/ai-docs.mjs` owns the AI bundles; the site build serves `/ai/reference.md`, and publish-pypi runs the script for the packaged copy.
- **`workflow_run` needs `branches: [main]`** — without it every PR's CI completion queues a skipped Release run.
- **Cancel superseded PR runs only** — CI's concurrency group cancels in progress for pull requests; a cancelled main run would never reach Release.
- **`workflow_run` uses main's copy of `release.yml`** — a branch whose packaging differs (e.g., a beta line) releases through its tag trigger; a pre-release VERSION never ships as stable (the main job skips `X.Y.ZbN`, only its tag publishes it).
- **Stable release type ignores beta tags** — `git describe` excludes `a`/`b`/`rc` tags, or 4.0.0 after 4.0.0b2 would be titled a patch.
- **Expressions reach shell through `env:`** — never inline `${{ }}` in a `run:` script (code scanning flags it as injection); CI's token is read-only.
- **PyPI rebuilds after approval** — build both musl agents from the release checkout; the locked Hatchling backend builds an sdist, then a wheel from that sdist. Verify release assets, both agent hashes and versions before the OIDC publishing step.
- **System lab timeout 45min** — image builds and package installs on cold caches are slow; the timeout is generous on purpose.
