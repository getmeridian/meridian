"""A release can reuse only a successful main CI run for the actual tag commit."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.prerelease_ci import REPO, successful_main_run, verification_mode

COMMIT = "a" * 40


def run(**overrides: Any) -> dict[str, Any]:
    return {
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": COMMIT,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        "id": 42,
        "status": "completed",
        "conclusion": "success",
        **overrides,
    }


def test_reuse_only_the_exact_successful_main_push() -> None:
    url = successful_main_run(COMMIT, {"total_count": 1, "workflow_runs": [run()]})
    assert url == f"https://github.com/{REPO}/actions/runs/42"


@pytest.mark.parametrize(
    "evidence",
    [
        [],
        [run(), run()],
        [run(status="in_progress", conclusion=None)],
        [run(conclusion="failure")],
        [run(conclusion="cancelled")],
        [run(head_sha="b" * 40)],
        [run(event="pull_request")],
        [run(event="workflow_dispatch")],
        [run(head_branch="feature")],
        [run(path=".github/workflows/another.yml")],
        [run(repository={"full_name": "fork/meridian"})],
        [run(head_repository={"full_name": "fork/meridian"})],
    ],
)
def test_missing_ambiguous_untrusted_or_unsuccessful_main_cannot_release(evidence: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError):
        successful_main_run(COMMIT, {"total_count": len(evidence), "workflow_runs": evidence})


def test_off_main_annotated_tag_runs_its_own_suite_but_missing_main_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=CI", "-c", "user.email=ci@example.org", *args], check=True)

    git("init", "-b", "main")
    git("commit", "--allow-empty", "-m", "main")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("switch", "-c", "separate-beta")
    git("commit", "--allow-empty", "-m", "independent beta")
    git("tag", "-a", "beta", "-m", "annotated beta")
    assert verification_mode("refs/tags/beta") == "unit-tests"
    git("update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises(subprocess.CalledProcessError):
        verification_mode("refs/tags/beta")
