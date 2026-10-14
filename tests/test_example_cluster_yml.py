"""Verify the repo-root cluster.example.yml is a valid, parseable cluster config.

If this test ever fails, the example reference users see is wrong. We
ship the example as the canonical schema documentation, so it must
round-trip through ClusterConfig.load() and pass validate() with no
errors.
"""

from __future__ import annotations

from pathlib import Path

from meridian.cluster import ClusterConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "cluster.example.yml"


def test_cluster_example_yml_exists() -> None:
    assert EXAMPLE.exists(), f"{EXAMPLE} missing — annotated schema reference"


def test_cluster_example_yml_loads_without_error() -> None:
    cfg = ClusterConfig.load(EXAMPLE)
    # The file is a documentation example; it must populate the major
    # sections so users see what each looks like.
    assert cfg.panel.url
    assert cfg.topology_intent is not None
    assert len(cfg.topology_intent.exits) >= 1
    assert len(cfg.topology_intent.transparent_relays) >= 1
    assert cfg.topology_intent.access.users
    assert cfg.subscription_page is not None
    assert cfg._extra == {}


def test_cluster_example_yml_validates() -> None:
    """All IPs are RFC 5737, all UUIDs are well-formed — must validate clean."""
    cfg = ClusterConfig.load(EXAMPLE)
    errors = cfg.validate()
    assert errors == [], f"cluster.example.yml has validation errors: {errors}"


def test_cluster_example_yml_uses_a_working_camouflage_target() -> None:
    """www.microsoft.com's certificate record exceeds REALITY's 8192-byte limit."""
    from meridian.core.defaults import REALITY_SNI_CANDIDATES

    exits = ClusterConfig.load(EXAMPLE).topology_intent.exits
    targets = {path.reality_sni for exit_intent in exits for path in exit_intent.paths if path.reality_sni}
    assert targets
    assert targets <= set(REALITY_SNI_CANDIDATES)
