"""Render current fleet contact into the shared support page, without a bearer request."""

from __future__ import annotations

from html import escape

from meridian.core.owner_contact import OwnerContact

_MARKER = b'<div id="owner-contact"></div>'


def support_contact_html(template: bytes, contact: OwnerContact) -> bytes:
    """Escaped data attributes feed the local contact script; HTML is never operator-authored."""
    if template.count(_MARKER) != 1:
        raise ValueError("The support page has no owner contact placeholder.")
    markup = (
        '<div id="owner-contact" data-owner-text="'
        + escape(contact.text, quote=True)
        + '" data-owner-link="'
        + escape(contact.link, quote=True)
        + '"></div>'
    )
    return template.replace(_MARKER, markup.encode("utf-8"), 1)
