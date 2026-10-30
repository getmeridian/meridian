"""Check localized API operation tables against the committed admin contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("en", "ru", "fa", "zh")
CONTRACT = ROOT / "contracts" / "meridian" / "v1" / "admin" / "openapi.json"


def check_api_doc(source: str, document: dict[str, Any]) -> list[str]:
    errors = []
    for term in (
        "OpenAPI 3.1",
        "/api/openapi.json",
        "contracts/meridian/v1/admin/openapi.json",
        "x-meridian-credentials: true",
        "x-meridian-scopes",
        "Authorization: Bearer <token>",
    ):
        if term not in source:
            errors.append(f"missing API contract guidance: {term}")
    operations = [
        (match[1], match[2])
        for line in source.splitlines()
        if line.startswith("|")
        for match in re.finditer(r"`(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (/api/[^`]+)`", line)
    ]
    if not operations:
        errors.append("missing API operation table")
    for method, path in operations:
        if method.lower() not in document["paths"].get(path, {}):
            errors.append(f"unknown API table operation: {method} {path}")
    return errors


def main() -> int:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    errors = []
    for locale in LOCALES:
        path = ROOT / "website" / "src" / "content" / "docs" / locale / "client-management.md"
        errors.extend(
            f"{locale}/client-management.md: {error}"
            for error in check_api_doc(path.read_text(encoding="utf-8"), document)
        )
    if errors:
        print("\n".join(errors))
        return 1
    print("OK: localized API tables match the admin OpenAPI contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
