"""PWA connection page rendering."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from meridian.models import ProtocolURL

if TYPE_CHECKING:
    from jinja2 import Environment


# App catalog — matches website/src/data/apps.json (single source of truth).
# ``platforms`` lists where the app runs; ``recommended`` lists where the page
# features it, in file order (Happ first, then the Mihomo apps, then Throne).
_PWA_APPS: list[dict[str, Any]] = [
    {
        "name": "Happ",
        "platform": "All platforms",
        "platforms": ["iOS", "Android", "Windows", "macOS", "Linux"],
        "recommended": ["iOS", "Android", "Windows", "macOS", "Linux"],
        "url": "https://happ.su/",
        "urls": {
            "iOS": "https://apps.apple.com/app/happ-proxy-utility/id6504287215",
            "Android": "https://play.google.com/store/apps/details?id=com.happproxy",
            "Windows": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
            "macOS": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
            "Linux": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
        },
        "deeplink": "happ://add/{url_raw}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "＋ → Import from clipboard",
            "ru": "＋ → Добавить из буфера обмена",
            "fa": "+، سپس «افزودن از کلیپ\u200cبورد»",
            "zh": "＋ → 从剪贴板导入",
        },
        "connect": {
            "en": [
                "Allow VPN permission if your device asks.",
                "Choose a server from the imported subscription.",
                "Press the power button to connect.",
            ],
            "ru": [
                "Если устройство запросит разрешение на VPN, разрешите.",
                "Выберите сервер из добавленной подписки.",
                "Нажмите кнопку включения, чтобы подключиться.",
            ],
            "fa": [
                "اگر دستگاه اجازهٔ VPN خواست، آن را تأیید کنید.",
                "یک سرور از اشتراک اضافه\u200cشده انتخاب کنید.",
                "برای اتصال، دکمهٔ روشن\u200cکردن را بزنید.",
            ],
            "zh": ["如果设备请求 VPN 权限，请允许。", "从导入的订阅中选择一个服务器。", "点击电源按钮连接。"],
        },
        "installers": [
            {
                "platform": "Android",
                "kind": "apk",
                "arch": "universal",
                "url": "https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk",
            },
            {
                "platform": "iOS",
                "kind": "appstore",
                "region": "ru",
                "url": "https://apps.apple.com/ru/app/happ-proxy-utility/id6783623643",
            },
            {
                "platform": "Windows",
                "kind": "exe",
                "arch": "x64",
                "url": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
            },
        ],
    },
    {
        "name": "FlClashX",
        "platform": "Android, desktop",
        "platforms": ["Android", "Windows", "macOS", "Linux"],
        "recommended": ["Android", "Windows", "macOS", "Linux"],
        "url": "https://github.com/pluralplay/FlClashX/releases/latest",
        "deeplink": "flclashx://install-config?url={url}",
        "protocols": ["reality", "wss"],
        "manual": {
            "en": "Profiles → ＋ → URL → Send",
            "ru": "Профили → ＋ → URL → Отправить",
            "fa": "پروفایل\u200cها، +، نشانی و سپس «ارسال»",
            "zh": "配置文件 → ＋ → URL → 发送",
        },
        "connect": {
            "en": [
                "Allow VPN permission if your device asks.",
                "Select the imported profile.",
                "Press the power button to connect.",
            ],
            "ru": [
                "Если устройство запросит разрешение на VPN, разрешите.",
                "Выберите добавленный профиль.",
                "Нажмите кнопку включения, чтобы подключиться.",
            ],
            "fa": [
                "اگر دستگاه اجازهٔ VPN خواست، آن را تأیید کنید.",
                "پروفایل اضافه\u200cشده را انتخاب کنید.",
                "برای اتصال، دکمهٔ روشن\u200cکردن را بزنید.",
            ],
            "zh": ["如果设备请求 VPN 权限，请允许。", "选择刚导入的配置。", "点击电源按钮连接。"],
        },
        "tun": {
            "en": "Turn on TUN mode so all apps use this connection.",
            "ru": "Включите режим TUN, чтобы все приложения использовали это подключение.",
            "fa": "حالت TUN را روشن کنید تا همهٔ برنامه\u200cها از این اتصال استفاده کنند.",
            "zh": "开启 TUN 模式，让所有应用都通过此连接上网。",
        },
        "installers": [
            {
                "platform": "Android",
                "kind": "apk",
                "arch": "universal",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-android-universal.apk",
            },
            {
                "platform": "Windows",
                "kind": "exe",
                "arch": "x64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-windows-amd64-setup.exe",
            },
            {
                "platform": "Windows",
                "kind": "exe",
                "arch": "arm64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-windows-arm64-setup.exe",
            },
            {
                "platform": "macOS",
                "kind": "dmg",
                "arch": "arm64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-macos-arm64.dmg",
            },
            {
                "platform": "macOS",
                "kind": "dmg",
                "arch": "x64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-macos-amd64.dmg",
            },
            {
                "platform": "Linux",
                "kind": "deb",
                "arch": "x64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-linux-amd64.deb",
            },
            {
                "platform": "Linux",
                "kind": "appimage",
                "arch": "x64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-linux-amd64.AppImage",
            },
            {
                "platform": "Linux",
                "kind": "rpm",
                "arch": "x64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-linux-amd64.rpm",
            },
            {
                "platform": "Linux",
                "kind": "deb",
                "arch": "arm64",
                "url": "https://github.com/pluralplay/FlClashX/releases/latest/download/FlClashX-linux-arm64.deb",
            },
        ],
    },
    {
        "name": "Clash Mi",
        "platform": "All platforms",
        "platforms": ["iOS", "Android", "Windows", "macOS", "Linux"],
        "recommended": ["iOS", "Android", "Windows", "macOS", "Linux"],
        "url": "https://clashmi.app/download",
        "urls": {
            "iOS": "https://apps.apple.com/app/clash-mi/id6744321968",
            "Android": "https://github.com/KaringX/clashmi/releases/latest",
            "Windows": "https://github.com/KaringX/clashmi/releases/latest",
        },
        "deeplink": "clash://install-config?overwrite=no&name={name}&url={url}",
        "protocols": ["reality", "wss"],
        "manual": {
            "en": "Profiles → ＋ → URL",
            "ru": "Профили → ＋ → URL",
            "fa": "پروفایل\u200cها، سپس + و نشانی",
            "zh": "配置 → ＋ → URL",
        },
        "connect": {
            "en": [
                "Allow VPN permission if your device asks.",
                "Select the imported profile.",
                "Press the power button to connect.",
            ],
            "ru": [
                "Если устройство запросит разрешение на VPN, разрешите.",
                "Выберите добавленный профиль.",
                "Нажмите кнопку включения, чтобы подключиться.",
            ],
            "fa": [
                "اگر دستگاه اجازهٔ VPN خواست، آن را تأیید کنید.",
                "پروفایل اضافه\u200cشده را انتخاب کنید.",
                "برای اتصال، دکمهٔ روشن\u200cکردن را بزنید.",
            ],
            "zh": ["如果设备请求 VPN 权限，请允许。", "选择刚导入的配置。", "点击电源按钮连接。"],
        },
        "tun": {
            "en": "Turn on TUN mode so all apps use this connection.",
            "ru": "Включите режим TUN, чтобы все приложения использовали это подключение.",
            "fa": "حالت TUN را روشن کنید تا همهٔ برنامه\u200cها از این اتصال استفاده کنند.",
            "zh": "开启 TUN 模式，让所有应用都通过此连接上网。",
        },
    },
    {
        "name": "Throne",
        "platform": "Desktop",
        "platforms": ["Windows", "macOS", "Linux"],
        "recommended": ["Windows", "macOS", "Linux"],
        "url": "https://github.com/throneproj/Throne/releases/latest",
        "deeplink": "throne://addsub/{url_b64}",
        "protocols": ["reality", "wss"],
        "manual": {
            "en": "Preferences → Groups → Add → URL",
            "ru": "Настройки → Группы → Добавить → URL",
            "fa": "تنظیمات، گروه\u200cها، افزودن و سپس نشانی",
            "zh": "首选项 → 分组 → 添加 → URL",
        },
        "connect": {
            "en": [
                "Allow VPN permission if your device asks.",
                "Select the imported profile.",
                "Press the power button to connect.",
            ],
            "ru": [
                "Если устройство запросит разрешение на VPN, разрешите.",
                "Выберите добавленный профиль.",
                "Нажмите кнопку включения, чтобы подключиться.",
            ],
            "fa": [
                "اگر دستگاه اجازهٔ VPN خواست، آن را تأیید کنید.",
                "پروفایل اضافه\u200cشده را انتخاب کنید.",
                "برای اتصال، دکمهٔ روشن\u200cکردن را بزنید.",
            ],
            "zh": ["如果设备请求 VPN 权限，请允许。", "选择刚导入的配置。", "点击电源按钮连接。"],
        },
    },
    {
        "name": "ShadowRocket",
        "platform": "iOS",
        "platforms": ["iOS"],
        "url": "https://apps.apple.com/app/shadowrocket/id932747118",
        "deeplink": "sub://{url_b64}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "＋ → Type: Subscribe → URL → Done",
            "ru": "＋ → Тип: Subscribe → URL → Готово",
            "fa": "+، نوع «Subscribe»، نشانی و سپس «انجام شد»",
            "zh": "＋ → 类型：Subscribe → URL → 完成",
        },
    },
    {
        "name": "Streisand",
        "platform": "iOS",
        "platforms": ["iOS"],
        "url": "https://apps.apple.com/app/streisand/id6450534064",
        "deeplink": "streisand://import/{url_raw}#{name}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "＋ → Import from clipboard",
            "ru": "＋ → Импортировать из буфера обмена",
            "fa": "+، سپس «افزودن از کلیپ\u200cبورد»",
            "zh": "＋ → 从剪贴板导入",
        },
    },
    {
        "name": "v2RayTun",
        "platform": "iOS",
        "platforms": ["iOS"],
        "url": "https://apps.apple.com/app/v2raytun/id6476628951",
        "deeplink": "v2raytun://import/{url}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "＋ → Import from clipboard",
            "ru": "＋ → Импортировать из буфера обмена",
            "fa": "+، سپس «افزودن از کلیپ\u200cبورد»",
            "zh": "＋ → 从剪贴板导入",
        },
    },
    {
        "name": "Stash",
        "platform": "iOS",
        "platforms": ["iOS"],
        "url": "https://apps.apple.com/us/app/stash-rule-based-proxy/id1596063349",
        "deeplink": "stash://install-config?url={url}",
        "protocols": ["reality", "wss"],
        "manual": {
            "en": "Settings → Config → ＋ → Download from URL",
            "ru": "Настройки → Конфигурация → ＋ → Загрузить по URL",
            "fa": "تنظیمات، پیکربندی، + و سپس «دانلود از نشانی»",
            "zh": "设置 → 配置 → ＋ → 从 URL 下载",
        },
        "connect": {
            "en": [
                "Allow adding a VPN configuration when asked.",
                "Select the imported configuration, then choose a server in Policy.",
                "Tap Start on the main screen.",
            ],
            "ru": [
                "Разрешите добавление конфигурации VPN, когда появится запрос.",
                "Выберите добавленную конфигурацию и сервер в разделе «Политика».",
                "Нажмите «Старт» на главном экране.",
            ],
            "fa": [
                "وقتی درخواست شد، اجازهٔ افزودن پیکربندی VPN را بدهید.",
                "پیکربندی اضافه\u200cشده و سپس یک سرور را در بخش «Policy» انتخاب کنید.",
                "در صفحهٔ اصلی، «Start» را بزنید.",
            ],
            "zh": [
                "出现提示时，请允许添加 VPN 配置。",
                "选择导入的配置，再在「策略」中选择服务器。",
                "在主界面点击「Start」。",
            ],
        },
    },
    {
        "name": "v2rayNG",
        "platform": "Android",
        "platforms": ["Android"],
        "url": "https://github.com/2dust/v2rayNG/releases/latest",
        "deeplink": "v2rayng://install-sub?url={url}#{name}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "Menu → Subscription group settings → ＋ → URL",
            "ru": "Меню → Настройки групп подписки → ＋ → URL",
            "fa": "منو، تنظیمات گروه اشتراک، + و سپس نشانی",
            "zh": "菜单 → 订阅分组设置 → ＋ → URL",
        },
        "installers": [
            {
                "platform": "Android",
                "kind": "apk",
                "arch": "universal",
                "url": "https://github.com/2dust/v2rayNG/releases/download/1.10.31/v2rayNG_1.10.31_universal.apk",
            }
        ],
    },
    {
        "name": "NekoBox",
        "platform": "Android",
        "platforms": ["Android"],
        "url": "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest",
        "deeplink": "sn://subscription?url={url}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "Groups → ＋ → Subscription → URL",
            "ru": "Группы → ＋ → Подписка → URL",
            "fa": "گروه\u200cها، +، اشتراک و سپس نشانی",
            "zh": "分组 → ＋ → 订阅 → URL",
        },
    },
    {
        "name": "FlClash",
        "platform": "Android, desktop",
        "platforms": ["Android", "Windows", "macOS", "Linux"],
        "url": "https://github.com/chen08209/FlClash/releases/latest",
        "deeplink": "flclash://install-config?url={url}",
        "protocols": ["reality", "wss"],
        "manual": {
            "en": "Profiles → ＋ → URL → Send",
            "ru": "Профили → ＋ → URL → Отправить",
            "fa": "پروفایل\u200cها، +، نشانی و سپس «ارسال»",
            "zh": "配置文件 → ＋ → URL → 发送",
        },
        "tun": {
            "en": "Turn on TUN mode so all apps use this connection.",
            "ru": "Включите режим TUN, чтобы все приложения использовали это подключение.",
            "fa": "حالت TUN را روشن کنید تا همهٔ برنامه\u200cها از این اتصال استفاده کنند.",
            "zh": "开启 TUN 模式，让所有应用都通过此连接上网。",
        },
    },
    {
        "name": "sing-box",
        "platform": "iOS, Android, macOS",
        "platforms": ["iOS", "Android", "macOS"],
        "url": "https://github.com/SagerNet/sing-box/releases/latest",
        "urls": {
            "iOS": "https://apps.apple.com/app/sing-box-vt/id6673731168",
            "Android": "https://play.google.com/store/apps/details?id=io.nekohasekai.sfa",
            "macOS": "https://apps.apple.com/app/sing-box-vt/id6673731168",
        },
        "deeplink": "sing-box://import-remote-profile?url={url}#{name}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "Profiles → New profile → Remote → URL",
            "ru": "Профили → Новый профиль → Удалённый → URL",
            "fa": "پروفایل\u200cها، پروفایل جدید، راه دور و سپس نشانی",
            "zh": "配置 → 新建配置 → 远程 → URL",
        },
    },
    {
        "name": "Hiddify",
        "platform": "All platforms",
        "platforms": ["iOS", "Android", "Windows", "macOS", "Linux"],
        "url": "https://github.com/hiddify/hiddify-app/releases/latest",
        "urls": {
            "iOS": "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532",
            "Android": "https://play.google.com/store/apps/details?id=app.hiddify.com",
        },
        "deeplink": "hiddify://install-config/?url={url}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "＋ → Add from clipboard",
            "ru": "＋ → Добавить из буфера обмена",
            "fa": "+، سپس «افزودن از کلیپ\u200cبورد»",
            "zh": "＋ → 从剪贴板添加",
        },
    },
    {
        "name": "Karing",
        "platform": "All platforms",
        "platforms": ["iOS", "Android", "Windows", "macOS", "Linux"],
        "url": "https://github.com/KaringX/karing/releases/latest",
        "urls": {
            "iOS": "https://apps.apple.com/app/karing/id6472431552",
            "Android": "https://play.google.com/store/apps/details?id=com.nebula.karing",
        },
        "deeplink": "karing://install-config?url={url}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "＋ → Add profile from URL",
            "ru": "＋ → Добавить профиль по URL",
            "fa": "+، سپس «افزودن پروفایل از نشانی»",
            "zh": "＋ → 从 URL 添加配置",
        },
    },
    {
        "name": "V2Box",
        "platform": "iOS, Android",
        "platforms": ["iOS", "Android"],
        "url": "https://apps.apple.com/app/v2box-v2ray-client/id6446814690",
        "urls": {
            "iOS": "https://apps.apple.com/app/v2box-v2ray-client/id6446814690",
            "Android": "https://play.google.com/store/apps/details?id=dev.hexasoftware.v2box",
        },
        "deeplink": "v2box://install-sub?url={url_b64}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "Configs → ＋ → Add subscription",
            "ru": "Конфигурации → ＋ → Добавить подписку",
            "fa": "پیکربندی\u200cها، + و سپس «افزودن اشتراک»",
            "zh": "配置 → ＋ → 添加订阅",
        },
    },
    {
        "name": "v2rayN",
        "platform": "Desktop",
        "platforms": ["Windows", "macOS", "Linux"],
        "url": "https://github.com/2dust/v2rayN/releases/latest",
        "protocols": ["reality", "xhttp", "wss"],
        "manual": {
            "en": "Subscription groups → Subscription group settings → Add",
            "ru": "Группы подписок → Настройки групп подписок → Добавить",
            "fa": "گروه\u200cهای اشتراک، تنظیمات گروه اشتراک و سپس «افزودن»",
            "zh": "订阅分组 → 订阅分组设置 → 添加",
        },
    },
    {
        "name": "Clash Verge Rev",
        "platform": "Desktop",
        "platforms": ["Windows", "macOS", "Linux"],
        "url": "https://github.com/clash-verge-rev/clash-verge-rev/releases/latest",
        "deeplink": "clash://install-config?url={url}",
        "protocols": ["reality", "wss"],
        "manual": {
            "en": "Profiles → New → Remote → URL → Save",
            "ru": "Профили → Создать → Удалённый → URL → Сохранить",
            "fa": "پروفایل\u200cها، جدید، راه دور، نشانی و سپس «ذخیره»",
            "zh": "配置 → 新建 → 远程 → URL → 保存",
        },
        "tun": {
            "en": "Turn on TUN mode so all apps use this connection.",
            "ru": "Включите режим TUN, чтобы все приложения использовали это подключение.",
            "fa": "حالت TUN را روشن کنید تا همهٔ برنامه\u200cها از این اتصال استفاده کنند.",
            "zh": "开启 TUN 模式，让所有应用都通过此连接上网。",
        },
    },
]

# App icon name mapping: app name → icon filename (without extension).
# Icons stored as optimized WebP in src/meridian/icons/.
_APP_ICON_NAMES = {
    "Happ": "happ",
    "FlClashX": "flclashx",
    "Clash Mi": "clash-mi",
    "Throne": "throne",
    "ShadowRocket": "shadowrocket",
    "Streisand": "streisand",
    "v2RayTun": "v2raytun",
    "v2rayNG": "v2rayng",
    "NekoBox": "nekobox",
    "FlClash": "flclash",
    "sing-box": "sing-box",
    "Hiddify": "hiddify",
    "Karing": "karing",
    "V2Box": "v2box",
    "v2rayN": "v2rayn",
    "Clash Verge Rev": "clash-verge-rev",
}

_app_icons_cache: dict[str, str] | None = None


def _load_app_icons() -> dict[str, str]:
    """Load app icons from package data and return {app_name: data_uri} dict."""
    global _app_icons_cache
    if _app_icons_cache is not None:
        return _app_icons_cache

    from importlib.resources import files

    icons_dir = files("meridian") / "icons"
    result: dict[str, str] = {}
    for app_name, filename in _APP_ICON_NAMES.items():
        resource = icons_dir / f"{filename}.webp"
        try:
            data = resource.read_bytes()
            b64 = base64.b64encode(data).decode()
            result[app_name] = f"data:image/webp;base64,{b64}"
        except OSError:
            pass
    # Stash's offline monogram is a packaged SVG; other app icons are WebP.
    monogram = files("meridian") / "templates" / "pwa" / "stash.svg"
    result["Stash"] = "data:image/svg+xml;base64," + base64.b64encode(monogram.read_bytes()).decode()
    _app_icons_cache = result
    return result


def render_config_json(
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str = "",
    *,
    client_name: str = "",
    server_name: str = "",
    server_icon: str = "",
    color: str = "",
    subscription_url: str = "",
    exit_countries: list[str] | None = None,
) -> str:
    """Render per-client config.json for the PWA shell.

    Returns a JSON string containing all connection data that the
    PWA's app.js needs to populate the page at runtime.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    protocols = []
    for i, p in enumerate(protocol_urls):
        if not p.url:
            continue
        protocols.append(
            {
                "key": p.key,
                "label": p.label,
                "url": p.url,
                "qr_b64": p.qr_b64,
                "recommended": i == 0,
            }
        )

    config = {
        "version": 1,
        "client_name": client_name,
        "server_ip": server_ip,
        "domain": domain,
        "protocols": protocols,
        # Fallback only: the page prefers the shared, icon-bearing pwa/apps.json.
        "apps": _PWA_APPS,
        "generated_at": now,
    }
    if server_name:
        config["server_name"] = server_name
    if server_icon:
        config["server_icon"] = server_icon
    if color:
        config["color"] = color
    if exit_countries:
        config["exit_countries"] = exit_countries
    if subscription_url:
        from meridian.urls import generate_qr_base64

        config["subscription_url"] = subscription_url
        config["subscription_qr_b64"] = generate_qr_base64(subscription_url)
    return json.dumps(config, indent=2, ensure_ascii=False)


def render_apps_catalog() -> str:
    """Render the shared ``pwa/apps.json`` app catalog with embedded icons.

    One cached copy serves every client page, so catalog updates reach
    existing pages with the next asset upload and per-client ``config.json``
    stays small.
    """
    icons = _load_app_icons()
    apps = [{**app, "icon": icons[app["name"]]} if app["name"] in icons else app for app in _PWA_APPS]
    return json.dumps({"version": 1, "apps": apps}, indent=2, ensure_ascii=False)


def render_pwa_shell(
    *,
    client_name: str = "",
    asset_path: str = "../pwa",
    server_name: str = "",
    config_path: str = "config.json",
    manifest_path: str = "manifest.webmanifest",
    worker_path: str = "../pwa/sw.js",
    worker_scope: str = "../",
) -> str:
    """Render the PWA HTML shell from the index.html.j2 template.

    The shell is lightweight — it loads config.json and shared assets
    at runtime.  Only the client_name, asset_path, and server_name
    are baked in.
    """
    return _render_pwa_template(
        "index.html.j2",
        client_name=client_name,
        asset_path=asset_path,
        server_name=server_name,
        config_path=config_path,
        manifest_path=manifest_path,
        worker_path=worker_path,
        worker_scope=worker_scope,
    )


def render_manifest(
    *,
    client_name: str = "",
    asset_path: str = "../pwa",
    server_name: str = "",
    start_url: str = ".",
) -> str:
    """Render the per-client PWA manifest from manifest.webmanifest.j2."""
    return _render_pwa_template(
        "manifest.webmanifest.j2",
        client_name=client_name,
        asset_path=asset_path,
        server_name=server_name,
        start_url=start_url,
    )


def _render_pwa_template(
    filename: str,
    **variables: object,
) -> str:
    """Load and render a Jinja2 template from templates/pwa/."""
    from importlib.resources import files

    # A missing packaged template is a broken install; never upload a blank page.
    template_text = (files("meridian") / "templates" / "pwa" / filename).read_text(encoding="utf-8")
    # HTML templates get autoescape; JSON manifests do not
    use_autoescape = filename.endswith(".html.j2")
    env = _create_jinja_env(autoescape=use_autoescape)
    env.filters["capitalize"] = lambda v: str(v).capitalize()
    env.filters["tojson"] = lambda v: json.dumps(str(v), ensure_ascii=False)[1:-1]

    return env.from_string(template_text).render(**variables)


def _create_jinja_env(*, autoescape: bool = True) -> Environment:
    """Create the Jinja2 environment used by PWA templates."""
    from jinja2 import BaseLoader, Environment

    env = Environment(loader=BaseLoader(), autoescape=autoescape)

    def default_filter(value: object, default_value: object = "") -> object:
        if value is None or value == "":
            return default_value
        return value

    env.filters["default"] = default_filter
    return env
