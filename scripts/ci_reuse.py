"""Identify a successful PR test of exactly what a main push merged. No cache or artifacts."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

REPO = "getmeridian/meridian"
SHELL = f"{REPO}/.github/workflows/ci-shell.yml"
SHA = re.compile(r"[0-9a-f]{40}")
API = Callable[[str], Any]


class NoMatch(Exception):
    """The main checks must run."""


def require(condition: object, reason: str) -> None:
    if not condition:
        raise NoMatch(reason)


def github(path: str) -> Any:
    return json.loads(
        subprocess.check_output(["gh", "api", f"repos/{REPO}/{path}"], timeout=10, stderr=subprocess.DEVNULL)
    )


def commit(api: API, sha: str) -> tuple[str, list[str]]:
    obj = api(f"git/commits/{sha}")
    tree = obj["tree"]["sha"]
    parents = [p["sha"] for p in obj["parents"]]
    require(obj["sha"] == sha and SHA.fullmatch(tree) and all(SHA.fullmatch(p) for p in parents), "unknown commit")
    return tree, parents


def prove(before: str, after: str, lab: bool, e2e: bool, api: API = github) -> int:
    require(SHA.fullmatch(before) and SHA.fullmatch(after), "invalid push commits")
    tree, parents = commit(api, after)
    require(len(parents) == 2 and parents[0] == before, "not a single ordinary merge onto the previous main tip")
    head = parents[1]
    prs = api(f"commits/{after}/pulls?per_page=2")
    require(len(prs) == 1, "no single associated PR")
    pr = prs[0]
    require(
        pr["state"] == "closed"
        and pr["merged_at"]
        and pr["merge_commit_sha"] == after
        and pr["base"]["ref"] == "main"
        and pr["base"]["repo"]["full_name"] == pr["head"]["repo"]["full_name"] == REPO
        and pr["head"]["sha"] == head,
        "not a merged same-repository PR into main",
    )
    pr_number = int(pr["number"])
    # Never search backwards for an older success when the newest run didn't pass.
    latest = f"actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=1"
    runs = api(latest)["workflow_runs"]
    require(len(runs) == 1, "no PR workflow run")
    run = runs[0]
    require(
        run["status"] == "completed"
        and run["conclusion"] == "success"
        and run["event"] == "pull_request"
        and run["path"] == ".github/workflows/ci.yml"
        and run["repository"]["full_name"] == run["head_repository"]["full_name"] == REPO
        and run["head_branch"] == pr["head"]["ref"]
        and run["head_sha"] == head,
        "latest run is not a successful CI run for this PR head",
    )
    # GitHub resolves a relative workflow_call at the *caller's* commit. A
    # run's head_sha is the feature tip; display_title may be supplied by the PR.
    # Neither identifies what actions/checkout actually received.
    refs = [
        ref
        for ref in run["referenced_workflows"]
        if ref.get("ref") == f"refs/pull/{pr_number}/merge" and ref.get("path") == f"{SHELL}@{ref['sha']}"
    ]
    require(len(refs) == 1 and SHA.fullmatch(refs[0]["sha"]), "no GitHub-resolved PR merge checkout")
    require(commit(api, refs[0]["sha"]) == (tree, parents), "PR tested different files or merge parents")

    run_id, attempt = int(run["id"]), int(run["run_attempt"])
    page = api(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    jobs = page["jobs"]
    # A failed-jobs-only rerun may lack earlier passing jobs. If coverage
    # changes or we cannot prove it from this exact attempt, run main normally.
    expected = dict.fromkeys(
        ["Changes", "Python Lint", "Type Check", "Validate", "Agent", "Shell / Shell", "Website Build"], "success"
    )
    expected.update({f"Python Tests (3.{minor})": "success" for minor in range(11, 16)})
    expected.update({"System Lab": "success" if lab else "skipped", "E2E": "success" if e2e else "skipped"})
    require(
        len(jobs) == page["total_count"] == len(expected)
        and all(j["run_id"] == run_id and j["run_attempt"] == attempt and j["status"] == "completed" for j in jobs)
        and {j["name"]: j["conclusion"] for j in jobs} == expected,
        "attempt does not cover the expected checks",
    )
    latest_runs = api(latest)["workflow_runs"]
    require(
        len(latest_runs) == 1
        and latest_runs[0]["id"] == run_id
        and latest_runs[0]["run_attempt"] == attempt
        and latest_runs[0]["status"] == "completed"
        and latest_runs[0]["conclusion"] == "success",
        "PR checks changed during verification",
    )
    return run_id


def main(before: str, after: str, lab: str, e2e: str) -> int:
    try:
        require(lab in {"true", "false"} and e2e in {"true", "false"}, "unknown path filter results")
        run = prove(before, after, lab == "true", e2e == "true")
    except NoMatch as error:
        print(f"Running main checks: {error}.", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        print("Could not verify GitHub's PR evidence; running main checks.", file=sys.stderr)
        return 1
    print(f"https://github.com/{REPO}/actions/runs/{run}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
