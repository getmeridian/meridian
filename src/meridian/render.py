"""PWA connection page rendering."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from meridian.models import ProtocolURL, RelayURLSet, derive_client_name

if TYPE_CHECKING:
    from jinja2 import Environment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PWA rendering functions
# ---------------------------------------------------------------------------

# App download links — matches website/src/data/apps.json (single source of truth)
_PWA_APPS: list[dict[str, Any]] = [
    {
        "name": "ShadowRocket",
        "platform": "iOS",
        "url": "https://apps.apple.com/app/shadowrocket/id932747118",
        "deeplink": "sub://{url_b64}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "Streisand",
        "platform": "iOS",
        "url": "https://apps.apple.com/app/streisand/id6450534064",
        "deeplink": "streisand://import/{url}#{name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "v2RayTun",
        "platform": "iOS",
        "url": "https://apps.apple.com/app/v2raytun/id6476628951",
        "deeplink": "v2raytun://import/{url}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "v2rayNG",
        "platform": "Android",
        "url": "https://github.com/2dust/v2rayNG/releases/latest",
        "deeplink": "v2rayng://install-sub?url={url}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "NekoBox",
        "platform": "Android",
        "url": "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest",
        "deeplink": "sn://subscription?url={url}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "FlClash",
        "platform": "All platforms",
        "url": "https://github.com/chen08209/FlClash/releases/latest",
        "deeplink": "flclash://install-config?url={url}",
        "protocols": ["reality", "wss"],
    },
    {
        "name": "sing-box",
        "platform": "All platforms",
        "url": "https://github.com/SagerNet/sing-box/releases/latest",
        "urls": {
            "iOS": "https://apps.apple.com/app/sing-box-vt/id6673731168",
            "Android": "https://play.google.com/store/apps/details?id=io.nekohasekai.sfa",
        },
        "deeplink": "sing-box://import-remote-profile?url={url}#{name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "Hiddify",
        "platform": "All platforms",
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
        "platform": "All platforms",
        "url": "https://apps.apple.com/app/v2box-v2ray-client/id6446814690",
        "urls": {
            "iOS": "https://apps.apple.com/app/v2box-v2ray-client/id6446814690",
            "Android": "https://play.google.com/store/apps/details?id=dev.hexasoftware.v2box",
        },
        "deeplink": "v2box://install-sub?url={url_b64}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "Happ",
        "platform": "All platforms",
        "url": "https://happ.su/",
        "urls": {
            "iOS": "https://apps.apple.com/app/happ-proxy-utility/id6504287215",
            "Android": "https://play.google.com/store/apps/details?id=com.happproxy",
        },
        "deeplink": "happ://add/{url_raw}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "v2rayN",
        "platform": "Windows",
        "url": "https://github.com/2dust/v2rayN/releases/latest",
        "deeplink": "v2rayng://install-sub?url={url}&name={name}",
        "protocols": ["reality", "xhttp", "wss"],
    },
    {
        "name": "Clash Verge Rev",
        "platform": "Windows",
        "url": "https://github.com/clash-verge-rev/clash-verge-rev/releases/latest",
        "deeplink": "clash://install-config?url={url}",
        "protocols": ["reality", "wss"],
    },
]

# App icon name mapping: app name → icon filename (without extension).
# Icons stored as optimized WebP in src/meridian/icons/.
_APP_ICON_NAMES = {
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
    "Happ": "happ",
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
        except (FileNotFoundError, OSError):
            pass
    _app_icons_cache = result
    return result


def render_config_json(
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str = "",
    *,
    client_name: str = "",
    relay_entries: list[RelayURLSet] | None = None,
    server_name: str = "",
    server_icon: str = "",
    color: str = "",
    subscription_url: str = "",
) -> str:
    """Render per-client config.json for the PWA shell.

    Returns a JSON string containing all connection data that the
    PWA's app.js needs to populate the page at runtime.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = client_name or derive_client_name(protocol_urls)

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

    relays = []
    if relay_entries:
        for relay_set in relay_entries:
            relay_urls = []
            for purl in relay_set.urls:
                if purl.url:
                    relay_urls.append(
                        {
                            "key": purl.key,
                            "label": purl.label,
                            "url": purl.url,
                            "qr_b64": purl.qr_b64,
                        }
                    )
            if relay_urls:
                relays.append(
                    {
                        "ip": relay_set.relay_ip,
                        "name": relay_set.relay_name,
                        "urls": relay_urls,
                    }
                )

    icons = _load_app_icons()
    apps = [{**app, "icon": icons[app["name"]]} if app["name"] in icons else app for app in _PWA_APPS]

    config = {
        "version": 1,
        "client_name": name,
        "server_ip": server_ip,
        "domain": domain,
        "protocols": protocols,
        "relays": relays,
        "apps": apps,
        "generated_at": now,
    }
    if server_name:
        config["server_name"] = server_name
    if server_icon:
        config["server_icon"] = server_icon
    if color:
        config["color"] = color
    if subscription_url:
        from meridian.urls import generate_qr_base64

        config["subscription_url"] = subscription_url
        config["subscription_qr_b64"] = generate_qr_base64(subscription_url)
    return json.dumps(config, indent=2, ensure_ascii=False)


def render_subscription(
    protocol_urls: list[ProtocolURL],
    *,
    relay_entries: list[RelayURLSet] | None = None,
) -> str:
    """Render a V2Ray subscription file (base64-encoded URL list).

    Standard format: base64(url1\\nurl2\\n...). Compatible with v2rayNG,
    Hiddify, and other V2Ray clients that support subscription import.
    """
    urls: list[str] = []
    # Relay URLs first (recommended)
    if relay_entries:
        for relay_set in relay_entries:
            for purl in relay_set.urls:
                if purl.url:
                    urls.append(purl.url)
    # Direct URLs
    for p in protocol_urls:
        if p.url:
            urls.append(p.url)
    if not urls:
        return ""
    return base64.b64encode("\n".join(urls).encode()).decode()


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
    try:
        from importlib.resources import files

        template_text = (files("meridian") / "templates" / "pwa" / filename).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Failed to load PWA template %s: %s", filename, exc)
        return ""

    from jinja2 import TemplateError

    # HTML templates get autoescape; JSON manifests do not
    use_autoescape = filename.endswith(".html.j2")
    env = _create_jinja_env(autoescape=use_autoescape)
    env.filters["capitalize"] = lambda v: str(v).capitalize()
    env.filters["tojson"] = lambda v: json.dumps(str(v), ensure_ascii=False)[1:-1]

    try:
        tmpl = env.from_string(template_text)
        return tmpl.render(**variables)
    except TemplateError as exc:
        logger.warning("Failed to render PWA template %s: %s", filename, exc)
        return ""


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
