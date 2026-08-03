"""Validate that _PWA_APPS in render.py matches apps.json.

apps.json is the single source of truth for app download links.
_PWA_APPS is a Python copy used for PWA config generation.
This script ensures they stay in sync.

Usage:
    uv run python tests/validate_apps_sync.py
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APPS_JSON = ROOT / "website" / "src" / "data" / "apps.json"
RENDER_PY = ROOT / "src" / "meridian" / "render.py"


def _load_python_apps() -> list[dict[str, object]]:
    tree = ast.parse(RENDER_PY.read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_PWA_APPS"
            and node.value is not None
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, list):
                break
            return value
    raise ValueError(f"_PWA_APPS list not found in {RENDER_PY.relative_to(ROOT)}")


def main() -> int:
    with open(APPS_JSON) as f:
        json_apps = json.load(f)
    python_apps = _load_python_apps()

    # Build lookup by name
    json_by_name = {app["name"]: app for app in json_apps}
    py_by_name = {app["name"]: app for app in python_apps}

    errors: list[str] = []

    # Check for apps in one source but not the other
    json_only = set(json_by_name) - set(py_by_name)
    py_only = set(py_by_name) - set(json_by_name)

    for name in sorted(json_only):
        errors.append(f"  {name}: in apps.json but missing from _PWA_APPS (render.py)")
    for name in sorted(py_only):
        errors.append(f"  {name}: in _PWA_APPS (render.py) but missing from apps.json")

    # Check matching apps for field differences
    compare_fields = ("platform", "url", "deeplink", "urls", "protocols")
    for name in sorted(set(json_by_name) & set(py_by_name)):
        j = json_by_name[name]
        p = py_by_name[name]
        for field in compare_fields:
            jval = j.get(field)
            pval = p.get(field)
            if jval != pval:
                errors.append(f"  {name}.{field}: apps.json={jval!r} vs render.py={pval!r}")

    if errors:
        print("ERROR: _PWA_APPS and apps.json are out of sync:\n")
        print("\n".join(errors))
        print(f"\nSOT is {APPS_JSON.relative_to(ROOT)} — update _PWA_APPS in render.py to match.")
        return 1

    print(f"OK: _PWA_APPS matches apps.json ({len(json_apps)} apps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
