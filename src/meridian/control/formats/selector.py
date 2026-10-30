"""Which document a request gets: by explicit suffix, else by Accept and User-Agent.

The rules follow what Remnawave 3.4.5 answered each app in the golden corpus
(`tests/systemlab/golden/`), first match wins, with Meridian's fixes:

- a browser (Accept names `text/html`) gets the connection page from the service;
  the explicit `info` suffix keeps selecting status JSON;
- Mihomo apps get Mihomo YAML, and so do plain-Clash apps (Remnawave sent its default
  template with no proxies at all);
- Stash gets Mihomo YAML in its own dialect (Remnawave sent its default template);
- sing-box apps get sing-box JSON, and so does Hiddify (sing-box inside; Remnawave sent
  Base64, which has no failover);
- apps known to import a list of Xray configs get Xray JSON (failover through a balancer);
- everything else, an empty or absent User-Agent included, gets Base64 (Remnawave
  refused those with 403).

NekoBox, Throne, v2RayTun, and Shadowrocket keep Base64: none documents a subscription
format that carries a failover group (NekoBox and Throne import Clash proxies but not
groups; v2RayTun documents only headers; Shadowrocket's groups live in its own config).
"""

from __future__ import annotations

import re
from typing import Literal

DocumentFormat = Literal["browser", "mihomo", "stash", "singbox", "xray-json", "base64"]

# `/sub/<token>/<suffix>`: Meridian's own checks read `json`; the connection page reads `info`.
SUFFIX_FORMATS: dict[str, DocumentFormat] = {
    "info": "browser",
    "json": "xray-json",
    "v2ray-json": "xray-json",
    "mihomo": "mihomo",
    "clash": "mihomo",
    "stash": "stash",
    "singbox": "singbox",
}

_MIHOMO = re.compile(
    r"^(?:flclash|rabbit|flowvy|murge|mihomo|prizrak-box|koala-clash|clash(?:-verge|-nyanpasu|x meta|[-.]?meta))",
    re.IGNORECASE,
)
_STASH = re.compile(r"^stash", re.IGNORECASE)
# Remnawave's rule left all but `sfa` unanchored (`^sfa|sfi|...`); anchored here, so an
# unrelated User-Agent that merely contains "sfi" is not sent sing-box.
_SINGBOX = re.compile(r"^(?:sfa|sfi|sfm|sft|karing|singbox|hiddify)", re.IGNORECASE)
_CLASH = re.compile(r"^clash", re.IGNORECASE)
_XRAY_JSON = re.compile(
    r"^(?:[Ss]treisand|Happ/|INCY/|ktor-client|V2Box|io\.github\.saeeddev94\.xray/"
    r"|v2rayNG/\d+\.\d+\.\d+|v2rayN/\d+\.\d+\.\d+|v2plus/\d+\.\d+\.\d+)"
)
_RULES: tuple[tuple[re.Pattern[str], DocumentFormat], ...] = (
    (_MIHOMO, "mihomo"),
    (_STASH, "stash"),
    (_SINGBOX, "singbox"),
    (_CLASH, "mihomo"),
    (_XRAY_JSON, "xray-json"),
)


def select_format(user_agent: str | None, accept: str | None) -> DocumentFormat:
    """The document a request without a suffix gets."""
    if "text/html" in (accept or ""):
        return "browser"
    agent = (user_agent or "").strip()
    for pattern, document in _RULES:
        if pattern.search(agent):
            return document
    return "base64"
