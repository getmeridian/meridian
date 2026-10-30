# CI/CD — GitHub Actions workflows

## Design decisions

**Two-stage pipeline** — CI validates on every push/PR. Release triggers only on CI success on main via `workflow_run`. Prevents accidental releases from failed builds.

**CI jobs**: Python Tests (3.11–3.14 matrix), Lint (plus the never-failing dead-code report as PR annotations), Type Check, Validate (PWA rendering + app metadata + VERSION + CHANGELOG + public CLI docs), Shell (shellcheck), Agent (cargo fmt, clippy, and tests, with uv for the real-service round trip; then `make agent` and a wheel built with both musl builds, checked by `verify_package_assets.py --agent-only`), System Lab (multi-node Docker deploy of this checkout's wheel and the runner-arch agent `make system-lab-agent` builds), Website Build.

**System Lab only where it matters** — the `changes` job diffs the PR (or push) and runs System Lab only when runtime code (`src/meridian` minus Markdown and locale catalogs), the agent, `tests/systemlab` (or the corpus comparison it reuses), dependencies, or `ci.yml` change; a skipped job still satisfies required checks and Release. `workflow_dispatch` always runs it. Widen the path list when the lab starts depending on something new.

**Website package manager** — website CI uses pnpm with `website/pnpm-lock.yaml`; keep supply-chain guardrails in `website/pnpm-workspace.yaml`.

**VERSION-driven releases** — read VERSION file, check if git tag exists. If missing: detect semver change, extract CHANGELOG section, push tag, create Release. Idempotent — safe to re-run.

**Pre-releases from tags** — pushing `vX.Y.ZbN` (or `aN`/`rcN`) runs `release.yml` from the tagged commit: unit tests, a GitHub pre-release with `.github/BETA.md` plus the upcoming `X.Y.Z` CHANGELOG section, then the same PyPI gate. Stable releases still come only from main. A beta bump changes `VERSION` and the `v4_beta.yml` version placeholder, then pushes the tag. Installers and `meridian update` on stable builds never pick a pre-release.

**Verifiable installer** — stable and pre-release jobs attach `install.sh` and `SHA256SUMS` to the GitHub release (a second origin beside Pages); `install.sh` pins uv's installer by version and SHA-256, bumped together. While main's VERSION is a pre-release, deploy-pages sets `MERIDIAN_DEFAULT_VERSION` in the site's copy so it installs the beta it documents.

**Weekly pin bump** — `bump-pins.yml` runs `scripts/bump_pins.py` on Mondays, force-pushes `bot/bump-pins`, opens or updates its PR, and dispatches CI on the branch (a `GITHUB_TOKEN` push triggers no `pull_request` run; `workflow_dispatch` is exempt). Opening the PR needs "Allow GitHub Actions to create pull requests"; without it the run warns with a compare link. A source it could not check fails the run after the PR step.

**OIDC publishing** — Pages deploy uses trusted publisher (no token). PyPI requires `environment: pypi` approval gate + OIDC. No long-lived secrets.

## What's done well

- **Validate job** — one job checks PWA rendering, `_PWA_APPS` against `apps.json`, VERSION, CHANGELOG, generated contracts, and the live CLI tree against every localized reference.
- **System lab waits for lint and tests** — no Docker minutes spent on obvious failures.
- **Client routing runs on real Xray** — Python 3.12 downloads the checksum-verified pinned runtime before pytest; bootstrap failure fails CI instead of silently skipping the loopback handshake tests. The same leg installs the website's jsdom for the console DOM scenarios (`MERIDIAN_TEST_CONSOLE`).
- **Demo validation** — the website job runs `scripts/build_showcase.py` into `dist/demo` and checks the connection page's assets, its disabled SW, rendered pages, and the console demo's files; the release job builds the same output.
- **Contract drift checks** — the website build also runs `pnpm run contracts:check`.
- **Package asset check** — website CI builds a wheel after Astro and verifies the Studio page is bundled and the rest of the website is not.

## Pitfalls

- **Release notes depend on CHANGELOG discipline** — if human forgets to update CHANGELOG before bumping VERSION, release notes fall back to git log (less useful).
- **Never bundle docs inline in a workflow** — `website/scripts/ai-docs.mjs` owns the AI bundles; the site build serves `/ai/reference.md`, and publish-pypi runs the script for the packaged copy.
- **`workflow_run` needs `branches: [main]`** — without it every PR's CI completion queues a skipped Release run.
- **Cancel superseded PR runs only** — CI's concurrency group cancels in progress for pull requests; a cancelled main run would never reach Release.
- **`workflow_run` uses main's copy of `release.yml`** — a branch whose packaging differs (e.g., a beta line) releases through its tag trigger; a pre-release VERSION never ships as stable (the main job skips `X.Y.ZbN`, only its tag publishes it).
- **Stable release type ignores beta tags** — `git describe` excludes `a`/`b`/`rc` tags, or 4.0.0 after 4.0.0b2 would be titled a patch.
- **Expressions reach shell through `env:`** — never inline `${{ }}` in a `run:` script (code scanning flags it as injection); CI's token is read-only.
- **publish-pypi builds the agent before the wheel** — `make agent` fills `agent/dist`, which the build hook packages; `--release` refuses a wheel missing either musl build or whose digest file disagrees (a wheel without it cannot deploy a control-service fleet).
- **System lab timeout 45min** — image builds and package installs on cold caches are slow; the timeout is generous on purpose.
