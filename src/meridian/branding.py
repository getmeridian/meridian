"""Server branding — palettes, icon processing, validation.

Handles emoji detection, image download/resize, and color palette
definitions. UX philosophy: never reject input when we can fix it.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import logging
import socket
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color palettes — curated presets with dark + light mode variants
# ---------------------------------------------------------------------------

PALETTES: dict[str, dict[str, str]] = {
    "ocean": {"dark": "#5b9cf5", "light": "#2b7de9"},
    "sunset": {"dark": "#e57c4e", "light": "#c4602a"},
    "forest": {"dark": "#4CD68A", "light": "#1E8C52"},
    "lavender": {"dark": "#9b8cf5", "light": "#6b5de9"},
    "rose": {"dark": "#f56b8a", "light": "#d94468"},
    "slate": {"dark": "#8B8FA2", "light": "#646880"},
}

PALETTE_LABELS: dict[str, str] = {
    "ocean": "Ocean (blue)",
    "sunset": "Sunset (coral)",
    "forest": "Forest (green)",
    "lavender": "Lavender (purple)",
    "rose": "Rose (pink)",
    "slate": "Slate (gray)",
}

# Suggested emoji for the wizard picker
ICON_SUGGESTIONS = ["🛡️", "🌐", "🔐", "🚀", "⚡", "🏔️", "🌊", "🔒"]

# Max image download size (10 MB raw, will be resized)
_MAX_DOWNLOAD = 10 * 1024 * 1024
# Target icon size after resize
_ICON_SIZE = 128
# Max data URI size to store (after base64 encoding)
_MAX_DATA_URI = 300 * 1024


def validate_color(color: str) -> str:
    """Validate and normalize a palette name. Returns name or empty string."""
    normalized = color.strip().lower()
    if normalized in PALETTES:
        return normalized
    return ""


def process_icon(raw_input: str) -> str:
    """Process icon input — emoji or image URL — into a storable string.

    Returns:
        Emoji string, data URI, or empty string. Never raises.
    """
    text = raw_input.strip()
    if not text:
        return ""

    # URL path — download and process image
    if text.startswith(("http://", "https://")):
        return _process_image_url(text)

    # Emoji path — extract first emoji from input
    return _extract_emoji(text)


def _extract_emoji(text: str) -> str:
    """Extract the first emoji (or emoji sequence) from text.

    Handles multi-codepoint emoji like flags, skin tones, ZWJ sequences.
    """
    # Walk through characters, collect contiguous emoji codepoints
    result: list[str] = []
    in_emoji = False

    for char in text:
        cat = unicodedata.category(char)
        # Emoji-like categories: Symbol-Other, Symbol-Math (some emoji),
        # plus variation selectors and ZWJ
        is_emoji_char = (
            cat == "So"  # Symbol, other (most emoji)
            or cat == "Sk"  # Symbol, modifier (skin tones)
            or char in ("\u200d", "\ufe0f", "\ufe0e")  # ZWJ, variation selectors
            or _is_emoji_codepoint(ord(char))
        )

        if is_emoji_char:
            result.append(char)
            in_emoji = True
        elif in_emoji:
            # We've left the emoji sequence, stop
            break

    if result:
        return "".join(result)

    # No emoji found — if it's short enough, maybe they typed a single char
    # that's a symbol or number. Just return it if it's 1-2 chars.
    if len(text) <= 2 and not text.isascii():
        return text

    return ""


def _is_emoji_codepoint(cp: int) -> bool:
    """Check if a codepoint is in common emoji ranges."""
    return (
        0x1F600 <= cp <= 0x1F64F  # Emoticons
        or 0x1F300 <= cp <= 0x1F5FF  # Misc Symbols & Pictographs
        or 0x1F680 <= cp <= 0x1F6FF  # Transport & Map
        or 0x1F1E0 <= cp <= 0x1F1FF  # Flags
        or 0x2600 <= cp <= 0x26FF  # Misc symbols
        or 0x2700 <= cp <= 0x27BF  # Dingbats
        or 0x1F900 <= cp <= 0x1F9FF  # Supplemental Symbols
        or 0x1FA00 <= cp <= 0x1FA6F  # Chess, extended-A
        or 0x1FA70 <= cp <= 0x1FAFF  # Symbols extended-A
        or 0xFE00 <= cp <= 0xFE0F  # Variation selectors
        or cp == 0x200D  # ZWJ
        or 0x1F000 <= cp <= 0x1F02F  # Mahjong, dominos
        or 0xE0020 <= cp <= 0xE007F  # Tags (flag sequences)
    )


def _is_private_ip(hostname: str) -> bool:
    """Check if hostname resolves to a private/loopback IP."""
    try:
        addr = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addr:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError):
        return False
    return False


def _process_image_url(url: str) -> str:
    """Download an image URL, resize, and convert to data URI.

    Never raises — returns empty string on failure with a logged warning.
    """
    parsed = urlparse(url)
    if parsed.hostname and _is_private_ip(parsed.hostname):
        logger.warning("Icon URL points to private/internal address, skipping")
        return ""

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Meridian/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
            data = resp.read(_MAX_DOWNLOAD + 1)
            if len(data) > _MAX_DOWNLOAD:
                logger.warning("Image too large (>10MB), skipping: %s", url)
                data = data[:_MAX_DOWNLOAD]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("Failed to download icon: %s", exc)
        return ""

    # Try Pillow for smart resize
    result = _resize_with_pillow(data, content_type)
    if result:
        return result

    # Fallback: use raw bytes if small enough
    return _fallback_raw(data, content_type)


def _resize_with_pillow(data: bytes, content_type: str) -> str:
    """Resize image using Pillow. Returns data URI or empty string."""
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        img: Image.Image = Image.open(io.BytesIO(data))

        # Animated GIF: take first frame
        if getattr(img, "is_animated", False):
            img.seek(0)

        # Convert to RGBA for consistent processing
        img = img.convert("RGBA")

        # Center-crop to square
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

        # Resize to target
        img = img.resize((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)  # type: ignore[attr-defined]

        # Save as PNG
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()

        b64 = base64.b64encode(png_bytes).decode()
        return f"data:image/png;base64,{b64}"
    except (OSError, ValueError) as exc:
        logger.warning("Pillow processing failed: %s", exc)
        return ""


def _fallback_raw(data: bytes, content_type: str) -> str:
    """Fallback: convert raw bytes to data URI if small enough."""
    # Detect format from magic bytes
    mime = _detect_mime(data)
    if not mime:
        # Try the declared content type
        if content_type in ("image/png", "image/jpeg", "image/svg+xml", "image/webp", "image/gif"):
            mime = content_type
        else:
            logger.warning("Unknown image format, skipping")
            return ""

    b64 = base64.b64encode(data).decode()
    data_uri = f"data:{mime};base64,{b64}"

    if len(data_uri) > _MAX_DATA_URI:
        logger.warning("Image too large for data URI without Pillow (install Pillow for auto-resize)")
        return ""

    return data_uri


def _detect_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:4] == b"<svg" or b"<svg" in data[:256]:
        return "image/svg+xml"
    return ""
