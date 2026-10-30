"""Client User-Agents the subscription golden corpus records, with the rule each should hit.

Meridian pushes no Response Rules of its own (``SubscriptionSettingsPayload`` keeps the
panel's unmanaged rules and sets ``serveJsonAtBaseSubscription``), so the routing is
Remnawave 3.4.5's seeded default (``prisma/seed/default/response-rules.ts``), evaluated in
order, followed by the JSON upgrade of the base64 fallback for the User-Agents in
``src/modules/subscription-template/constants/extended-clients.ts``. The capture stage
also stores the live rules, and Remnawave's request history records the rule that matched.

Every app in the connection page catalog (``render._PWA_APPS``) has an entry, every rule and
JSON-fallback pattern has at least one, plus a browser, curl, Meridian's own httpx fetch, and
an empty or absent User-Agent.
"""

from __future__ import annotations

from dataclasses import dataclass

BROWSER = "Browser Subscription"
MIHOMO = "Mihomo Clients"
STASH = "Stash (iOS, macOS)"
SINGBOX = "Sing-box clients"
CLASH = "Clash Core Clients"
FALLBACK = "Fallback Base64"
# The rules middleware answers 403 before any rule runs when the User-Agent is missing or empty.
REFUSED = ""

CLIENT_ACCEPT = "*/*"
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@dataclass(frozen=True)
class ClientAgent:
    """One request shape: who asks and what the default rules should answer."""

    id: str
    user_agent: str | None  # None sends no User-Agent header at all
    expected_rule: str
    expected_type: str  # Remnawave response type after the JSON upgrade
    app: str = ""  # connection page catalog name, when the app is listed there
    accept: str = CLIENT_ACCEPT


def _agent(
    id: str, user_agent: str | None, rule: str, response_type: str, app: str = "", accept: str = CLIENT_ACCEPT
) -> ClientAgent:
    return ClientAgent(id, user_agent, rule, response_type, app, accept)


AGENTS: tuple[ClientAgent, ...] = (
    # Connection page catalog, in its order.
    _agent("happ", "Happ/3.2.1", FALLBACK, "XRAY_JSON", "Happ"),
    _agent("flclashx", "FlClash X/v0.3.1 Platform/android", MIHOMO, "MIHOMO", "FlClashX"),
    _agent("clash-mi", "ClashMeta/1.19.32; mihomo/1.19.32", MIHOMO, "MIHOMO", "Clash Mi"),
    _agent("throne", "Throne/1.0.13", FALLBACK, "XRAY_BASE64", "Throne"),
    _agent(
        "shadowrocket",
        "Shadowrocket/2678 CFNetwork/3826.500.131 Darwin/24.5.0",
        FALLBACK,
        "XRAY_BASE64",
        "ShadowRocket",
    ),
    _agent("streisand", "Streisand/1.6.53", FALLBACK, "XRAY_JSON", "Streisand"),
    _agent("v2raytun", "v2raytun/ios", FALLBACK, "XRAY_BASE64", "v2RayTun"),
    _agent("v2rayng", "v2rayNG/1.10.16", FALLBACK, "XRAY_JSON", "v2rayNG"),
    _agent("nekobox", "NekoBox/Android/1.3.9 (Prefer ClashMeta Format)", FALLBACK, "XRAY_BASE64", "NekoBox"),
    _agent("flclash", "FlClash/v0.8.87 clash-verge Platform/android", MIHOMO, "MIHOMO", "FlClash"),
    _agent("sing-box-android", "SFA/1.12.9 (588; sing-box 1.12.9; language en_US)", SINGBOX, "SINGBOX", "sing-box"),
    _agent("sing-box-ios", "SFI/1.12.9 (588; sing-box 1.12.9; language en_US)", SINGBOX, "SINGBOX", "sing-box"),
    _agent("sing-box-macos", "SFM/1.12.9 (588; sing-box 1.12.9; language en_US)", SINGBOX, "SINGBOX", "sing-box"),
    _agent("hiddify", "HiddifyNext/2.5.7 (android) like ClashMeta v2ray sing-box", FALLBACK, "XRAY_BASE64", "Hiddify"),
    _agent("karing", "karing/1.2.5.888 android", SINGBOX, "SINGBOX", "Karing"),
    _agent("v2box", "V2Box/9.3.1 (iOS)", FALLBACK, "XRAY_JSON", "V2Box"),
    _agent("v2rayn", "v2rayN/7.14.4", FALLBACK, "XRAY_JSON", "v2rayN"),
    _agent("clash-verge-rev", "clash-verge/v2.4.2", MIHOMO, "MIHOMO", "Clash Verge Rev"),
    # Remaining Mihomo alternatives, the other YAML rules, and the JSON-fallback patterns.
    _agent("mihomo", "mihomo/1.19.32", MIHOMO, "MIHOMO"),
    _agent("clash-meta-android", "ClashMetaForAndroid/2.11.18.Meta", MIHOMO, "MIHOMO"),
    _agent("flowvy", "Flowvy/1.0.4", MIHOMO, "MIHOMO"),
    _agent("prizrak-box", "prizrak-box/1.2.0", MIHOMO, "MIHOMO"),
    _agent("koala-clash", "koala-clash/0.2.7", MIHOMO, "MIHOMO"),
    _agent("stash", "Stash/3.2.0 Clash/1.11.0", STASH, "STASH"),
    _agent("clash-for-android", "ClashForAndroid/2.5.12", CLASH, "CLASH"),
    _agent("incy", "INCY/1.4.0", FALLBACK, "XRAY_JSON"),
    _agent("ktor-client", "ktor-client", FALLBACK, "XRAY_JSON"),
    _agent("saeeddev94-xray", "io.github.saeeddev94.xray/6.2.0", FALLBACK, "XRAY_JSON"),
    _agent("v2plus", "v2plus/1.2.3", FALLBACK, "XRAY_JSON"),
    # Not apps: a browser, plain HTTP tools, Meridian's own handoff fetch, nothing at all.
    _agent(
        "browser",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36",
        BROWSER,
        "BROWSER",
        accept=BROWSER_ACCEPT,
    ),
    _agent("curl", "curl/8.5.0", FALLBACK, "XRAY_BASE64"),
    _agent("python-httpx", "python-httpx/0.28.1", FALLBACK, "XRAY_BASE64"),
    _agent("empty", "", REFUSED, "FORBIDDEN"),
    _agent("absent", None, REFUSED, "FORBIDDEN"),
)

AGENTS_BY_ID = {agent.id: agent for agent in AGENTS}

# A suspended client and an unknown subscription: one request per delivered format.
STATUS_AGENT_IDS = (
    "happ",
    "flclashx",
    "sing-box-android",
    "shadowrocket",
    "stash",
    "clash-for-android",
    "browser",
    "curl",
)
UNKNOWN_AGENT_IDS = ("happ", "browser", "curl")

# Explicit format selectors (``/sub/<short>/<selector>``); Meridian's own checks use ``json``.
SELECTORS = ("json", "mihomo", "singbox")
SELECTOR_AGENT_ID = "curl"
