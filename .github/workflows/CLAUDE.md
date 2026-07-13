# CI/CD — GitHub Actions workflows

## Design decisions

**Two-stage pipeline** — CI validates on every push/PR. Release triggers only on CI success on main via `workflow_run`. Prevents accidental releases from failed builds.

**CI jobs**: Python Tests (3.11–3.13 matrix), Lint, Type Check, Validate (PWA rendering + app metadata + VERSION + CHANGELOG + deploy CLI flags), Shell (shellcheck), System Lab (multi-node Docker deploy), Website Build.

**Website package manager** — website CI uses pnpm with `website/pnpm-lock.yaml`; keep supply-chain guardrails in `website/pnpm-workspace.yaml`.

**VERSION-driven releases** — read VERSION file, check if git tag exists. If missing: detect semver change, extract CHANGELOG section, push tag, create Release. Idempotent — safe to re-run.

**OIDC publishing** — Pages deploy uses trusted publisher (no token). PyPI requires `environment: pypi` approval gate + OIDC. No long-lived secrets.

## What's done well

- **Validate job** — single job checks the PWA renders, app metadata matches across surfaces, VERSION is valid semver, CHANGELOG has an entry, and deploy CLI flags are documented in cli-reference.md. Catches drift between docs and code.
- **System lab depends on lint+test** — syntax must be clean before spinning up Docker. Saves CI minutes on obvious failures.
- **PWA demo validation** — CI generates a demo PWA page and verifies all required files exist, SW is disabled for static hosting, and client HTML renders correctly.
- **Contract drift checks** — Python validate runs `scripts/export_contracts.py --check`; website build runs `pnpm run contracts:check`.
- **Package asset check** — website CI builds a wheel after Astro and verifies the generated Studio entry point is bundled.

## Pitfalls

- **Release notes depend on CHANGELOG discipline** — if human forgets to update CHANGELOG before bumping VERSION, release notes fall back to git log (less useful).
- **AI docs generated in 3 places** — CI website build, deploy-pages, publish-pypi all regenerate. Single source would reduce duplication.
- **System lab timeout 30min** — Remnawave image pulls inside nested Docker are slow. Timeout is generous to accommodate cold caches.
