"""Plain API validation errors and nginx-compatible HTTP error pages."""

from __future__ import annotations

from http import HTTPStatus

from pydantic import ValidationError


def error_page(code: int) -> bytes:
    """The same generic page nginx returns for malformed requests and unknown methods."""
    try:
        title = f"{code} {HTTPStatus(code).phrase}"
    except ValueError:
        title = str(code)
    page = f"<html>\r\n<head><title>{title}</title></head>\r\n<body>\r\n<center><h1>{title}</h1></center>\r\n"
    page += "<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n"
    return page.encode("ascii")


def validation_message(exc: ValidationError) -> str:
    """The first invalid field, bounded for the public API error body."""
    errors = exc.errors()
    if not errors:
        return "Invalid request."
    location = ".".join(str(part) for part in errors[0]["loc"])
    return f"Invalid request: {location} {errors[0]['msg']}".strip()[:500]
