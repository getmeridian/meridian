## Summary
<!-- Brief description of changes -->

## Type of change
- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring
- [ ] Documentation

## Checklist
- [ ] I have read `CONTRIBUTING.md`
- [ ] `make ci` passes locally
- [ ] Updated relevant documentation surfaces (see below)
- [ ] Updated the nearest folder `CLAUDE.md` (pitfall for a bug fix, design note for a design change)

### If modifying connection pages:
- [ ] PWA templates, assets, and app metadata are in sync
- [ ] Tested light and dark mode

### If adding a new CLI command:
- [ ] Added a `_HELP_LAYOUT` entry, a `rich_help_panel`, and a `COMMAND_OUTPUTS` entry
- [ ] Documented it in `cli-reference.md` for every locale (`tests/validate_cli_docs.py`)
- [ ] Updated the README.md commands table

### If modifying cluster state or secrets:
- [ ] Tested loading and saving an existing v4 `cluster.yml`
- [ ] Updated the relevant typed cluster model

### If modifying provisioner steps:
- [ ] Step returns proper StepResult (ok/changed/skipped/failed)
- [ ] ProvisionContext fields typed if used by other steps
- [ ] Shell values use shlex.quote()
