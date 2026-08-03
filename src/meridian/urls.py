"""QR code generation for connection URLs."""

from __future__ import annotations

import base64
import io

import segno


def generate_qr_terminal(url: str) -> str:
    """Generate a QR code for terminal display."""
    try:
        qr = segno.make(url)
        buf = io.StringIO()
        qr.terminal(out=buf, compact=True)
        return buf.getvalue()
    except (ValueError, OSError):
        return ""


def generate_qr_base64(url: str) -> str:
    """Generate a QR code as base64-encoded PNG for HTML embedding."""
    try:
        qr = segno.make(url)
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=12)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except (ValueError, OSError):
        return ""
