"""PR evidence has to survive a real merge and prove coverage from the same attempt."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci_reuse import REPO, SHELL, NoMatch, prove

BASE, HEAD, MERGE, TESTED, TREE = (f"{i:040x}" for i in range(1, 6))
RUNS = f"actions/workflows/ci.yml/runs?event=pull_request&head_sha={HEAD}&per_page=1"
PULLS = f"commits/{MERGE}/pulls?per_page=2"
JOBS = "actions/runs/99/attempts/2/jobs?per_page=100"


@pytest.fixture
def gh() -> dict[str, Any]:
    def git_commit(sha: str) -> dict[str, Any]:
        return {"sha": sha, "tree": {"sha": TREE}, "parents": [{"sha": BASE}, {"sha": HEAD}]}

    repo = {"full_name": REPO}
    pr = {
        "number": 42,
        "state": "closed",
        "merged_at": "2026-11-21T09:45:42Z",
        "merge_commit_sha": MERGE,
        "base": {"ref": "main", "repo": repo},
        "head": {"ref": "topic", "sha": HEAD, "repo": repo},
    }
    run = {
        "id": 99,
        "run_attempt": 2,
        "status": "completed",
        "conclusion": "success",
        "event": "pull_request",
        "path": ".github/workflows/ci.yml",
        "head_branch": "topic",
        "head_sha": HEAD,
        "repository": repo,
        "head_repository": repo,
        "referenced_workflows": [{"sha": TESTED, "path": f"{SHELL}@{TESTED}", "ref": "refs/pull/42/merge"}],
    }
    names = ["Changes", "Validate", "Python Lint", "Type Check", "Shell / Shell", "E2E", "System Lab", "Website Build"]
    names += ["Agent", *(f"Python Tests (3.{i})" for i in range(11, 16))]
    jobs = [{"name": n, "status": "completed", "conclusion": "success", "run_id": 99, "run_attempt": 2} for n in names]
    return {
        f"git/commits/{MERGE}": git_commit(MERGE),
        f"git/commits/{TESTED}": git_commit(TESTED),
        PULLS: [pr],
        RUNS: {"workflow_runs": [run]},
        JOBS: {"total_count": len(jobs), "jobs": jobs},
    }


def test_same_files_and_merge_inputs_with_complete_attempt(gh):
    assert MERGE != TESTED
    assert prove(BASE, MERGE, True, True, gh.__getitem__) == 99


def test_path_filtered_checks_need_the_same_exclusions(gh):
    for job in gh[JOBS]["jobs"]:
        if job["name"] == "E2E":
            job["conclusion"] = "skipped"
    assert prove(BASE, MERGE, True, False, gh.__getitem__) == 99
    with pytest.raises(NoMatch):
        prove(BASE, MERGE, True, True, gh.__getitem__)


def test_newer_attempt_starts_while_comparing_jobs(gh):
    first = gh[RUNS]["workflow_runs"][0]
    runs = iter([gh[RUNS], {"workflow_runs": [dict(first, run_attempt=3, status="in_progress", conclusion=None)]}])
    with pytest.raises(NoMatch, match="changed during verification"):
        prove(BASE, MERGE, True, True, lambda path: next(runs) if path == RUNS else gh[path])


@pytest.mark.parametrize(
    ("where", "path", "value"),
    [
        (f"git/commits/{MERGE}", ["parents"], [{"sha": BASE}]),  # squash or direct push
        (f"git/commits/{MERGE}", ["parents", 0, "sha"], HEAD),  # stale or multi-commit push
        (f"git/commits/{TESTED}", ["tree", "sha"], HEAD),
        (f"git/commits/{TESTED}", ["parents"], [{"sha": HEAD}, {"sha": BASE}]),  # same tree; different diff
        (PULLS, [0, "head", "repo"], {"full_name": "fork/project"}),
        (PULLS, [0, "base", "ref"], "other"),
        (PULLS, [0, "merge_commit_sha"], HEAD),
        (RUNS, ["workflow_runs", 0, "event"], "workflow_dispatch"),
        (RUNS, ["workflow_runs", 0, "path"], ".github/workflows/other.yml"),
        (RUNS, ["workflow_runs", 0, "head_repository"], {"full_name": "fork/project"}),
        (RUNS, ["workflow_runs", 0, "head_sha"], MERGE),
        (RUNS, ["workflow_runs", 0, "status"], "in_progress"),
        (RUNS, ["workflow_runs", 0, "conclusion"], "failure"),
        (RUNS, ["workflow_runs", 0, "referenced_workflows"], []),
        (RUNS, ["workflow_runs", 0, "referenced_workflows", 0, "ref"], "refs/pull/23/merge"),
        (RUNS, ["workflow_runs", 0, "referenced_workflows", 0, "path"], f"{SHELL}@main"),
        (JOBS, ["total_count"], 101),  # truncated or partial attempt
        (JOBS, ["jobs", 0, "run_attempt"], 1),
        (JOBS, ["jobs", 0, "status"], "in_progress"),
        (JOBS, ["jobs", 0, "name"], "Validate"),  # duplicate job masking missing coverage
    ],
)
def test_unproven_merge_runs_again(gh, where, path, value):
    obj = gh[where]
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value
    with pytest.raises(NoMatch):
        prove(BASE, MERGE, True, True, gh.__getitem__)


@pytest.mark.parametrize(
    ("changed", "mode", "verified", "reuse"),
    [
        (".github/ISSUE_TEMPLATE/v4_beta.yml", "", True, False),
        (".github/ISSUE_TEMPLATE/v4_beta.yml", "enabled", True, True),
        (".github/ISSUE_TEMPLATE/v4_beta.yml", "enabled", False, False),
        (".github/workflows/ci.yml", "enabled", True, False),
        (".github/workflows/ci-shell.yml", "enabled", True, False),
        ("scripts/ci_reuse.py", "enabled", True, False),
    ],
)
def test_workflow_policy_in_a_git_checkout(tmp_path, changed, mode, verified, reuse):
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    steps = yaml.safe_load(workflow.read_text())["jobs"]["changes"]["steps"]
    check = next(step["run"] for step in steps if step.get("id") == "reuse")

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ci_reuse.py").write_text(
        'from pathlib import Path\nPath("did-verify").touch()\n'
        + ("print(99)\n" if verified else "raise SystemExit(1)\n")
    )
    git("add", ".")
    commit = [
        "-c",
        "user.name=CI",
        "-c",
        "user.email=ci@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
    ]
    git(*commit, "base")
    base = git("rev-parse", "HEAD")
    path = tmp_path / changed
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write("# Changed\n")
    git("add", ".")
    git(*commit, "candidate")
    env = {
        **os.environ,
        "BASE": base,
        "GITHUB_SHA": git("rev-parse", "HEAD"),
        "LAB": "true",
        "E2E": "true",
        "MODE": mode,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", check], cwd=tmp_path, env=env, check=True)
    assert (tmp_path / "output").read_text().strip() == f"reuse={str(reuse).lower()}"
    assert (tmp_path / "did-verify").exists() == changed.startswith(".github/ISSUE_TEMPLATE/")
