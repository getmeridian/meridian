# CI/CD — GitHub Actions workflows

## Design decisions

**Two-stage pipeline** — CI validates on every push/PR. Release triggers only on CI success on main via `workflow_run`. Prevents accidental releases from failed builds.

**CI jobs**: Python Tests (3.11–3.14 matrix), Lint (plus the never-failing dead-code report as PR annotations), Type Check, Validate (PWA rendering + app metadata + VERSION + CHANGELOG + public CLI docs), Shell (shellcheck), System Lab (multi-node Docker deploy), Website Build.

**Website package manager** — website CI uses pnpm with `website/pnpm-lock.yaml`; keep supply-chain guardrails in `website/pnpm-workspace.yaml`.

**VERSION-driven releases** — read VERSION file, check if git tag exists. If missing: detect semver change, extract CHANGELOG section, push tag, create Release. Idempotent — safe to re-run.

**Pre-releases from tags** — pushing `vX.Y.ZbN` (or `aN`/`rcN`) runs `release.yml` from the tagged commit: unit tests, a GitHub pre-release with `.github/BETA.md` plus the upcoming `X.Y.Z` CHANGELOG section, then the same PyPI gate. Stable releases still come only from main. Installers and `meridian update` on stable builds never pick a pre-release.

**Verifiable installer** — stable and pre-release jobs attach `install.sh` and `SHA256SUMS` to the GitHub release (a second origin beside Pages); `install.sh` pins uv's installer by version and SHA-256, bumped together.

**OIDC publishing** — Pages deploy uses trusted publisher (no token). PyPI requires `environment: pypi` approval gate + OIDC. No long-lived secrets.

## What's done well

- **Validate job** — one job checks PWA rendering, `_PWA_APPS` against `apps.json`, VERSION, CHANGELOG, generated contracts, and the live CLI tree against every localized reference.
- **System lab waits for lint and tests** — no Docker minutes spent on obvious failures.
- **PWA demo validation** — the website job builds `meridian dev preview` output and checks its files, a disabled SW, and rendered client HTML.
- **Contract drift checks** — the website build also runs `pnpm run contracts:check`.
- **Package asset check** — website CI builds a wheel after Astro and verifies the generated Studio entry point is bundled.

## Pitfalls

- **Release notes depend on CHANGELOG discipline** — if human forgets to update CHANGELOG before bumping VERSION, release notes fall back to git log (less useful).
- **The AI-docs file list lives in three places** — `make ai-docs` and the deploy-pages and publish-pypi jobs in `release.yml`; change all three together.
- **`workflow_run` needs `branches: [main]`** — without it every PR's CI completion queues a skipped Release run.
- **Cancel superseded PR runs only** — CI's concurrency group cancels in progress for pull requests; a cancelled main run would never reach Release.
- **`workflow_run` uses main's copy of `release.yml`** — a branch whose packaging differs (e.g., a beta line) must release through its tag trigger, never by waiting for main's workflow.
- **A pre-release VERSION never ships as stable** — the main release job skips `X.Y.ZbN`; only its tag publishes it.
- **Stable release type ignores beta tags** — `git describe` excludes `a`/`b`/`rc` tags, or 4.0.0 after 4.0.0b2 would be titled a patch.
- **System lab timeout 45min** — Remnawave image pulls inside nested Docker are slow. Timeout is generous to accommodate cold caches.
