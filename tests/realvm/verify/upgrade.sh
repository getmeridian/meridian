#!/usr/bin/env bash
# Published-release upgrade verifier. Local-only; Python owns commands and private captures.
# Exit 0: passed; 1: a claim failed; 2: refused; 75: PyPI or ACME made the run inconclusive.
set -euo pipefail

# VERIFY: published_release_deployed
# VERIFY: limited_client_created
# VERIFY: subscriptions_captured
# VERIFY: checkout_applied
# VERIFY: control_schema_current
# VERIFY: plan_converged
# VERIFY: subscriptions_unchanged
# VERIFY: client_metadata_preserved
# VERIFY: agent_version_current
# VERIFY: connection_test_passes
exec uv run python -m tests.realvm.upgrade_verifier
