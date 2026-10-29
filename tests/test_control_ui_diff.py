"""The console's revision diff (`M.intentDiff` in `ui/fleet.js`), run under node.

The asset tests read the scripts as text; this executes the one piece of logic whose
mistakes read as "no change" between two revisions.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from meridian.control.ui_routes import UI_DIR

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

_HARNESS = """
const fs = require("fs");
global.window = {};
window.M = { h: () => ({}), has: (o, k) => Object.prototype.hasOwnProperty.call(o, k) };
eval(fs.readFileSync(process.argv[1], "utf8"));
const [older, newer] = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(window.M.intentDiff(older, newer)));
"""


def diff(older: Any, newer: Any) -> list[dict[str, str]]:
    result = subprocess.run(
        ["node", "-e", _HARNESS, str(UI_DIR / "fleet.js"), json.dumps([older, newer])],
        capture_output=True,
        text=True,
        check=True,
    )
    return list(json.loads(result.stdout))


def test_a_dotted_key_and_a_nested_path_are_different_changes() -> None:
    changes = diff({"a.b": 1}, {"a": {"b": 1}})
    assert [(change["path"], "before" in change, "after" in change) for change in changes] == [
        ('["a.b"]', True, False),
        ("a.b", False, True),
    ]


def test_a_key_named_like_a_builtin_is_an_addition() -> None:
    assert diff({"a": 1}, {"a": 1, "toString": 2, "__proto__": {"b": 1}}) == [
        {"path": "__proto__.b", "after": "1"},
        {"path": "toString", "after": "2"},
    ]


def test_list_items_and_unchanged_intents() -> None:
    older = {"access": {"users": ["alice"]}}
    assert diff(older, {"access": {"users": ["alice", "bob"]}}) == [{"path": "access.users[1]", "after": '"bob"'}]
    assert diff(older, older) == []
