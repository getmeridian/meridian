"""Console logic run under node: the revision diff (`M.intentDiff` in `ui/fleet.js`) and the limit form.

The asset tests read the scripts as text; this executes the pieces whose mistakes
read as "no change" between two revisions, or save a limit nobody touched.
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


_PEOPLE_HARNESS = """
const fs = require("fs");
global.window = {};
const h = (tag, attrs) => ({ tag: tag, value: (attrs && attrs.value) || "" });
window.M = { h: h, has: (o, k) => Object.prototype.hasOwnProperty.call(o, k), field: () => ({}), t: (k) => k };
eval(fs.readFileSync(process.argv[1], "utf8"));
const [user, gb, strategy] = JSON.parse(process.argv[2]);
const fields = window.M.limitFields(user);
const shown = fields.gb.value;
fields.strategy.value = user ? user.traffic_limit_strategy : "month";
if (gb !== null) fields.gb.value = gb;
if (strategy !== null) fields.strategy.value = strategy;
process.stdout.write(JSON.stringify([shown, fields.value()]));
"""


def limit_form(user: dict[str, Any] | None, gb: str | None = None, strategy: str | None = None) -> list[Any]:
    """What the limit field shows for `user`, then what saving sends after typing `gb` or picking `strategy`."""
    result = subprocess.run(
        ["node", "-e", _PEOPLE_HARNESS, str(UI_DIR / "people.js"), json.dumps([user, gb, strategy])],
        capture_output=True,
        text=True,
        check=True,
    )
    return list(json.loads(result.stdout))


def test_an_untouched_small_limit_is_shown_exactly_and_never_sent() -> None:
    """A CLI quota of 4 MB rounded to "0" GB, and saving the expiry then sent 0: unlimited."""
    user = {"traffic_limit_bytes": 4194304, "traffic_limit_strategy": "month"}
    shown, change = limit_form(user)
    assert shown == "0.004194304" and change == {}
    assert limit_form(user, gb="1")[1] == {"traffic_limit_bytes": 10**9}
    assert limit_form(user, strategy="day")[1] == {"traffic_limit_strategy": "day"}


def test_a_new_person_sends_both_limit_fields() -> None:
    assert limit_form(None) == ["", {"traffic_limit_bytes": 0, "traffic_limit_strategy": "month"}]


_LATEST_HARNESS = """
const fs = require("fs");
global.window = {};
window.M = { h: () => ({}), has: (o, k) => Object.prototype.hasOwnProperty.call(o, k) };
eval(fs.readFileSync(process.argv[1], "utf8"));
const latest = window.M.latestOnly();
const settle = {};
const seen = [];
["two", "one"].forEach((name) => {
  const request = new Promise((resolve, reject) => { settle[name] = [resolve, reject]; });
  latest(request).then((value) => seen.push("ok:" + value), (error) => seen.push("err:" + error));
});
settle.one[0]("one");
settle.two[1]("two");
setTimeout(() => process.stdout.write(JSON.stringify(seen)), 10);
"""


def test_a_superseded_comparison_never_lands_under_the_newer_label() -> None:
    """Picking #2 then #1: #2's late reply once replaced #1's diff while the label still said #1."""
    result = subprocess.run(
        ["node", "-e", _LATEST_HARNESS, str(UI_DIR / "fleet.js")], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == ["ok:one"]
