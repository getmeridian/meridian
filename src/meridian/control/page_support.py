"""Render language, local assets, and public contact into no-store support HTML."""

from __future__ import annotations

from html import escape

from meridian.control.page_contact import support_contact_html
from meridian.core.languages import FleetLanguage, language_or_english
from meridian.core.owner_contact import OwnerContact

_ROOT = b'<html lang="en" dir="ltr" data-language="auto" data-page="">'
_ASSETS = ("language.js", "styles.css", "icon.svg", "contact.js", "support.js")


def render_support_html(
    template: bytes, contact: OwnerContact, *, language: FleetLanguage, asset_path: str, page_path: str = ""
) -> bytes:
    """Personal pages carry a connection path; old fragment links carry only the fleet default."""
    if template.count(_ROOT) != 1:
        raise ValueError("The support page has no language placeholder.")
    lang = language_or_english(language)
    direction = "rtl" if lang == "fa" else "ltr"
    root = (
        f'<html lang="{lang}" dir="{direction}" data-language="{language}" data-page="{escape(page_path, quote=True)}">'
    )
    data = support_contact_html(template, contact).replace(_ROOT, root.encode("utf-8"), 1)
    for name in _ASSETS:
        source = f'"{name}"'.encode("ascii")
        destination = f'"{escape(asset_path, quote=True)}/{name}"'.encode("utf-8")
        data = data.replace(source, destination)
    return data
