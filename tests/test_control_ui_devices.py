"""Execute the device card: private rendering, confirmed removals, and partial cap edits."""

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
const vm = require("vm");
const options = JSON.parse(process.argv[2]);
const catalog = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const nodes = [], requests = [], notices = [];
let refreshed = 0;
function h(tag, attrs, ...children) {
  const node = { tag, attrs: attrs || {}, children: children.flat(Infinity).filter(Boolean), events: {},
    value: String((attrs && attrs.value) || ""), validity: { badInput: false },
    addEventListener: function (event, callback) { this.events[event] = callback; },
    querySelector: function (selector) {
      return nodes.find((n) => n.attrs.class === selector.slice(1));
    }
  };
  nodes.push(node);
  return node;
}
const M = {
  h,
  t: (key, values) => (catalog[key] || key).replace(/\\{(\\w+)\\}/g, (_, name) => String((values || {})[name])),
  field: (label, input, hint) => h("label", {}, label, input, hint),
  mount: (host, children) => { host.children = [children].flat(Infinity).filter(Boolean); },
  api: (method, path, body) => {
    requests.push({ method, path, body });
    return Promise.resolve(method === "GET" ? options.devices : { deleted: 1 });
  },
  confirm: () => Promise.resolve(!options.cancel),
  busy: (_form, work) => Promise.resolve().then(work),
  toast: (text) => notices.push(text),
  renderPerson: () => { refreshed += 1; },
  date: (value) => value,
  errorText: () => "error"
};
const context = { window: { M }, document: { getElementById: () => ({}) } };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context);
const card = M.devicesCard(options.user);
function text(node) {
  if (typeof node === "string") return node;
  return (node.attrs.text || node.textContent || "") + " " + node.children.map(text).join(" ");
}
async function run() {
  await new Promise((resolve) => setImmediate(resolve));
  const shown = text(card);
  if (options.action === "cap") {
    const input = nodes.find((node) => node.tag === "input");
    if (options.value !== null) input.value = options.value;
    input.validity.badInput = Boolean(options.badInput);
    nodes.find((node) => node.tag === "form").events.submit({ preventDefault: () => {} });
  } else if (options.action === "clear" || options.action === "remove") {
    const label = catalog["devices." + options.action];
    nodes.find((node) => node.tag === "button" && node.children.includes(label)).attrs.onclick();
  }
  await new Promise((resolve) => setImmediate(resolve));
  process.stdout.write(JSON.stringify({ shown, requests, notices, refreshed,
    error: nodes.find((node) => node.attrs.class === "form-error").textContent || "",
    tags: nodes.map((node) => node.tag) }));
}
run().catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });
"""


def card(
    *,
    level: str = "private",
    limit: int | None = None,
    action: str = "view",
    value: str | None = None,
    cancel: bool = False,
    bad_input: bool = False,
) -> dict[str, Any]:
    options = {
        "user": {"username": "alice", "device_limit": limit},
        "action": action,
        "value": value,
        "cancel": cancel,
        "badInput": bad_input,
        "devices": {
            "level": level,
            "count": 1,
            "limit": 3,
            "devices": [
                {
                    "device_key": "a" * 64,
                    "platform": "Example OS",
                    "os_version": "1.0",
                    "model": "<img src=x onerror=alert(1)>",
                    "user_agent": "Example app",
                    "first_seen_at": "2026-10-29T12:00:00Z",
                    "last_seen_at": "2026-10-29T13:00:00Z",
                }
            ],
        },
    }
    result = subprocess.run(
        ["node", "-e", _HARNESS, str(UI_DIR / "devices.js"), json.dumps(options), str(UI_DIR / "locales/en.json")],
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(result.stdout))


def test_private_card_never_renders_ids_metadata_or_fetch_times() -> None:
    result = card()
    assert "1 of 3 devices" in result["shown"]
    assert all(value not in result["shown"] for value in ("a" * 64, "Example OS", "Example app", "2026-10-29"))


def test_full_card_shows_system_version_and_keeps_hostile_metadata_as_text() -> None:
    result = card(level="full")
    assert "a" * 64 in result["shown"] and "Example OS 1.0" in result["shown"]
    assert "<img src=x onerror=alert(1)>" in result["shown"] and "Example app" in result["shown"]
    assert "img" not in result["tags"] and "script" not in result["tags"]


@pytest.mark.parametrize(
    ("old", "value", "body"),
    [(None, None, None), (2, "2", None), (2, "", {"device_limit": None}), (None, "3", {"device_limit": 3})],
    ids=["untouched-default", "untouched-cap", "clear-override", "set-override"],
)
def test_cap_save_names_only_the_edited_cap_and_default_is_null(
    old: int | None, value: str | None, body: dict | None
) -> None:
    result = card(limit=old, action="cap", value=value)
    patches = [request for request in result["requests"] if request["method"] == "PATCH"]
    assert patches == ([] if body is None else [{"method": "PATCH", "path": "api/users/alice", "body": body}])


@pytest.mark.parametrize("value", ["0", "101", "1.5"], ids=["zero", "over-100", "fraction"])
def test_an_invalid_cap_cannot_clear_or_change_the_saved_override(value: str) -> None:
    result = card(limit=2, action="cap", value=value)
    assert result["error"] and all(request["method"] == "GET" for request in result["requests"])
    assert card(limit=2, action="cap", value="", bad_input=True)["error"]


@pytest.mark.parametrize(
    ("action", "level", "suffix"),
    [("clear", "private", ""), ("remove", "full", "/" + "a" * 64)],
    ids=["private-clear", "full-remove"],
)
def test_a_confirmed_removal_forgets_only_the_chosen_device_slots(action: str, level: str, suffix: str) -> None:
    result = card(level=level, action=action)
    deletes = [request for request in result["requests"] if request["method"] == "DELETE"]
    assert deletes == [{"method": "DELETE", "path": "api/users/alice/devices" + suffix}]
    assert result["refreshed"] == 1
    assert all(request["method"] == "GET" for request in card(level=level, action=action, cancel=True)["requests"])
