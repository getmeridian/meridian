"""PWA connection page rendering."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from meridian.models import ProtocolURL

if TYPE_CHECKING:
    from jinja2 import Environment


# ---------------------------------------------------------------------------
# PWA rendering functions
# ---------------------------------------------------------------------------

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
    },
    {
        "name": "FlClashX",
        "platform": "Android, desktop",
        "platforms": ["Android", "Windows", "macOS", "Linux"],
        "recommended": ["Android", "Windows", "macOS", "Linux"],
        "url": "https://github.com/pluralplay/FlClashX/releases/latest",
        "deeplink": "flclashx://install-config?url={url}",
        "protocols": ["reality", "wss"],
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
    },
    {
        "name": "Throne",
        "platform": "Desktop",
        "platforms": ["Windows", "macOS", "Linux"],
        "recommended": ["Windows", "macOS", "Linux"],
        "url": "https://github.com/throneproj/Throne/releases/latest",
        "deeplink": "throne://addsub/{url_b64}",
        "protocols": ["reality", "wss"],
    },
    {
        "name": "ShadowRocket",
        "platform": "iOS",
        "platforms": ["iOS"],
        "url": "https://apps.apple.com/app/shadowrocket/id932747118",
        "deeplink": "sub://{url_b64}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "Streisand",
        "platform": "iOS",
        "platforms": ["iOS"],
        "url": "https://apps.apple.com/app/streisand/id6450534064",
        "deeplink": "streisand://import/{url_raw}#{name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "v2RayTun",
        "platform": "iOS",
        "platforms": ["iOS"],
        "url": "https://apps.apple.com/app/v2raytun/id6476628951",
        "deeplink": "v2raytun://import/{url}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "v2rayNG",
        "platform": "Android",
        "platforms": ["Android"],
        "url": "https://github.com/2dust/v2rayNG/releases/latest",
        "deeplink": "v2rayng://install-sub?url={url}#{name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "NekoBox",
        "platform": "Android",
        "platforms": ["Android"],
        "url": "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest",
        "deeplink": "sn://subscription?url={url}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "FlClash",
        "platform": "Android, desktop",
        "platforms": ["Android", "Windows", "macOS", "Linux"],
        "url": "https://github.com/chen08209/FlClash/releases/latest",
        "deeplink": "flclash://install-config?url={url}",
        "protocols": ["reality", "wss"],
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
    },
    {
        "name": "v2rayN",
        "platform": "Desktop",
        "platforms": ["Windows", "macOS", "Linux"],
        "url": "https://github.com/2dust/v2rayN/releases/latest",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "Clash Verge Rev",
        "platform": "Desktop",
        "platforms": ["Windows", "macOS", "Linux"],
        "url": "https://github.com/clash-verge-rev/clash-verge-rev/releases/latest",
        "deeplink": "clash://install-config?url={url}",
        "protocols": ["reality", "wss"],
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
    )


def render_manifest(
    *,
    client_name: str = "",
    asset_path: str = "../pwa",
    server_name: str = "",
) -> str:
    """Render the per-client PWA manifest from manifest.webmanifest.j2."""
    return _render_pwa_template(
        "manifest.webmanifest.j2",
        client_name=client_name,
        asset_path=asset_path,
        server_name=server_name,
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


# ---------------------------------------------------------------------------
# PWA template helper
# ---------------------------------------------------------------------------


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
