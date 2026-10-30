"""All API guides describe discovery and name only contracted operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.control.openapi import openapi_document
from scripts.validate_api_docs import LOCALES, check_api_doc

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("locale", LOCALES)
def test_api_guide_matches_the_contract(locale: str) -> None:
    source = (ROOT / "website" / "src" / "content" / "docs" / locale / "client-management.md").read_text(
        encoding="utf-8"
    )
    assert check_api_doc(source, openapi_document().to_data()) == []


def test_api_table_check_refuses_an_unknown_operation() -> None:
    errors = check_api_doc("| `PATCH /api/unknown` | An unsupported edit |", openapi_document().to_data())
    assert "unknown API table operation: PATCH /api/unknown" in errors
