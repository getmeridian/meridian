"""Short, fixed client-list remarks; account status codes stay English."""

from __future__ import annotations

from meridian.control.models import UserStatus
from meridian.core.languages import Language

STATUS_REMARKS: dict[Language, dict[UserStatus, tuple[str, ...]]] = {
    "en": {
        "active": (),
        "disabled": ("🚫 Subscription disabled", "Contact support"),
        "limited": ("🚧 Subscription limited", "Contact support"),
        "expired": ("⌛ Subscription expired", "Contact support"),
    },
    "ru": {
        "active": (),
        "disabled": ("🚫 Подписка отключена", "Свяжитесь с владельцем"),
        "limited": ("🚧 Лимит трафика исчерпан", "Свяжитесь с владельцем"),
        "expired": ("⌛ Срок подписки истёк", "Свяжитесь с владельцем"),
    },
    "fa": {
        "active": (),
        "disabled": ("🚫 اشتراک غیرفعال است", "با مدیر اتصال تماس بگیرید"),
        "limited": ("🚧 سهمیهٔ ترافیک تمام شده", "با مدیر اتصال تماس بگیرید"),
        "expired": ("⌛ اعتبار اشتراک تمام شده", "با مدیر اتصال تماس بگیرید"),
    },
    "zh": {
        "active": (),
        "disabled": ("🚫 订阅已停用", "请联系管理者"),
        "limited": ("🚧 流量额度已用完", "请联系管理者"),
        "expired": ("⌛ 订阅已到期", "请联系管理者"),
    },
}
