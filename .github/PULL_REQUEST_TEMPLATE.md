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

### If modifying connection pages:
- [ ] PWA templates, assets, and app metadata are in sync
- [ ] Tested light and dark mode

### If adding a new CLI command:
- [ ] Added help smoke test in `tests/test_cli.py`
- [ ] Updated README.md commands table
- [ ] Updated CLAUDE.md subcommands list

### If modifying cluster state or secrets:
- [ ] Tested loading and saving an existing v4 `cluster.yml`
- [ ] Updated the relevant typed cluster model

### If modifying provisioner steps:
- [ ] Step returns proper StepResult (ok/changed/skipped/failed)
- [ ] ProvisionContext fields typed if used by other steps
- [ ] Shell values use shlex.quote()
