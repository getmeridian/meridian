"""Render every connection-page template with strict mock data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "src" / "meridian" / "templates"
MOCK_VARS = {
    "asset_path": "../pwa",
    "client_name": "default",
    "server_name": "Demo",
}


def main() -> int:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(("html", "xml")),
        undefined=StrictUndefined,
    )
    env.filters["tojson"] = lambda value: json.dumps(str(value), ensure_ascii=False)[1:-1]
    templates = sorted(path.relative_to(TEMPLATES_DIR) for path in TEMPLATES_DIR.rglob("*.j2"))
    if not templates:
        print(f"FAIL: no templates found in {TEMPLATES_DIR.relative_to(ROOT)}")
        return 1

    failed = False
    for path in templates:
        try:
            rendered = env.get_template(path.as_posix()).render(**MOCK_VARS)
            if path.suffixes[-2:] == [".webmanifest", ".j2"]:
                json.loads(rendered)
            print(f"OK: {path} ({len(rendered)} chars)")
        except Exception as error:
            print(f"FAIL: {path} — {error}")
            failed = True

    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
