"""Select the verification for a beta tag; stdout is the mode used by release.yml."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

REPO = "getmeridian/meridian"


def successful_main_run(commit: str, payload: dict[str, Any]) -> str:
    runs = payload["workflow_runs"]
    if payload["total_count"] != 1 or len(runs) != 1:
        raise ValueError(f"Expected one CI push run on main for {commit}; found {payload['total_count']}.")
    run = runs[0]
    if (
        run["path"] != ".github/workflows/ci.yml"
        or run["event"] != "push"
        or run["head_branch"] != "main"
        or run["head_sha"] != commit
        or run["repository"]["full_name"] != REPO
        or run["head_repository"]["full_name"] != REPO
    ):
        raise ValueError(f"CI evidence does not match a {REPO} push to main at {commit}.")
    url = f"https://github.com/{REPO}/actions/runs/{run['id']}"
    if (run["status"], run["conclusion"]) != ("completed", "success"):
        raise ValueError(f"Main CI is {run['conclusion'] or run['status']}: {url}")
    return url


def verification_mode(ref: str) -> str:
    # A shallow/missing main ref is not evidence that a tag belongs to a separate beta branch.
    if subprocess.check_output(["git", "rev-parse", "--is-shallow-repository"], text=True).strip() != "false":
        raise ValueError("Full main history is required to determine the verification for this tag.")
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], text=True
    )
    commit = commit.strip()
    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"])
    if ancestry.returncode == 1:
        print(f"{commit} is off main; running the tag's unit suite.", file=sys.stderr)
        return "unit-tests"
    ancestry.check_returncode()

    query = f"repos/{REPO}/actions/workflows/ci.yml/runs?event=push&branch=main&head_sha={commit}&per_page=2"
    payload = json.loads(subprocess.check_output(["gh", "api", query], text=True))
    print(f"Verified main CI: {successful_main_run(commit, payload)}", file=sys.stderr)
    return "main-ci"


if __name__ == "__main__":
    try:
        print(verification_mode(sys.argv[1] if len(sys.argv) > 1 else "HEAD"))
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"{exc}\nNo release. Once main CI passes, rerun this tag's failed job without moving the tag.")
