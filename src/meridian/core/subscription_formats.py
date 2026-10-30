"""Shared document vocabulary and explicit suffixes for subscriptions and previews."""

from __future__ import annotations

from typing import Literal

DocumentFormat = Literal["browser", "mihomo", "stash", "singbox", "xray-json", "base64"]
SUFFIX_FORMATS: dict[str, DocumentFormat] = {
    "info": "browser",
    "json": "xray-json",
    "v2ray-json": "xray-json",
    "mihomo": "mihomo",
    "clash": "mihomo",
    "stash": "stash",
    "singbox": "singbox",
}
