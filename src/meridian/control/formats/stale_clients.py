"""One inert entry for an app too old, or no longer maintained, to use what Meridian delivers.

Decided per request from the User-Agent alone: nothing is stored or logged, and no device
row, event, limit, privacy level, or format choice depends on it. Each rule below says why
its app is in the table; an app without a documented threshold is left out, never guessed.

- v2rayNG before 1.9.17: its release notes (github.com/2dust/v2rayNG/releases) first list
  XHTTP, with the packet-up mode Meridian delivers, in 1.9.17 (2024-11-19). Earlier builds
  carry only the SplitHTTP predecessor (1.8.26 to 1.9.16) or no such transport, so they
  drop Meridian's XHTTP connections. Reality itself needs only 1.8.0, the floor in
  `xray_workload.REALITY_MIN_CLIENT_VERSION`.
- NekoRay (MatsuriDayo/nekoray, desktop): archived by its author in December 2024 as "no
  longer maintained", so no version of it will gain a fix; Throne (throneproj/Throne,
  listed in the connection page's apps) is its maintained successor. NekoRay's source sends
  `NekoBox/PC/<version> (Prefer ClashMeta Format)`; the Android app says `NekoBox/Android/`,
  which this rule never matches (the connection page still recommends NekoBox there).

Left out: Happ's GitHub builds send the same `Happ/<version>` as its store builds, so a
User-Agent cannot tell them apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from meridian.core.languages import Language
from meridian.core.subscription_preview import StaleClient


@dataclass(frozen=True)
class StaleRule:
    pattern: re.Pattern[str]
    kind: StaleClient
    below: tuple[int, int, int] | None = None  # versions under this are stale; None: every version


RULES: tuple[StaleRule, ...] = (
    StaleRule(re.compile(r"^v2rayNG/(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE), "outdated", below=(1, 9, 17)),
    StaleRule(re.compile(r"^NekoBox/PC/", re.IGNORECASE), "abandoned"),
)

# Each remark stays within `ENDPOINT_REMARK_MAX_LENGTH` (tested); the leading symbols match the
# status placeholders' style. "abandoned" names its successor in every language.
STALE_REMARKS: dict[Language, dict[StaleClient, str]] = {
    "en": {
        "outdated": "🆕 Please update this app: this version is too old",
        "abandoned": "🔄 NekoRay is no longer maintained: switch to Throne",
    },
    "ru": {
        "outdated": "🆕 Обновите приложение: эта версия устарела",
        "abandoned": "🔄 NekoRay больше не поддерживается: перейдите на Throne",
    },
    "fa": {
        "outdated": "🆕 لطفاً برنامه را به‌روز کنید: این نسخه قدیمی است",
        "abandoned": "🔄 NekoRay دیگر نگهداری نمی‌شود: به Throne بروید",
    },
    "zh": {
        "outdated": "🆕 请更新此应用：当前版本过旧",
        "abandoned": "🔄 NekoRay 已停止维护：请改用 Throne",
    },
}


def stale_client(user_agent: str | None) -> StaleClient | None:
    """Which nudge a User-Agent earns, if any; the first matching rule decides."""
    agent = (user_agent or "").strip()
    for rule in RULES:
        match = rule.pattern.search(agent)
        if match is None:
            continue
        if rule.below is None:
            return rule.kind
        version = tuple(int(part or 0) for part in match.groups())
        return rule.kind if version < rule.below else None
    return None


def stale_remarks(kind: StaleClient | None, language: Language) -> tuple[str, ...]:
    """The one inert entry for *kind* in the person's language (none for no nudge)."""
    return (STALE_REMARKS[language][kind],) if kind is not None else ()
