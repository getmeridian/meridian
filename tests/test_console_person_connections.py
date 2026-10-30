"""The closed connection disclosure copies canonical credentials and shows local QR codes."""

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_individual_connections_are_closed_local_and_in_the_supplied_order(locale: str) -> None:
    result = run_console(
        "person-connections",
        locale,
        {
            "share": {
                "profile_title": "Test fleet",
                "connections": [
                    {
                        "remark": "First <img src=x>",
                        "protocol": "reality",
                        "uri": "vless://test-only-first@198.51.100.1:443#First",
                        "available": True,
                        "qr_svg": "data:image/svg+xml;base64,PHN2Zy8+",
                    },
                    {
                        "remark": "Second",
                        "protocol": "wss",
                        "uri": "vless://test-only-second@198.51.100.1:443#Second",
                        "available": True,
                        "qr_svg": "data:image/svg+xml;base64,PHN2Zy8+",
                    },
                ],
                "formats": [
                    {"format": name, "url": "https://198.51.100.1/sub/test-only-link/" + name}
                    for name in ("xray-json", "mihomo", "singbox")
                ],
            }
        },
    )
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["closed"] is True
    assert result["summary"] == catalog["person.connections"]
    assert result["remarks"] == ["First <img src=x>", "Second"]
    assert result["copied"] == ["vless://test-only-first@198.51.100.1:443#First"]
    assert result["qr"] == "data:image/svg+xml;base64,PHN2Zy8+"
    assert [url.rsplit("/", 1)[1] for url in result["formats"]] == ["xray-json", "mihomo", "singbox"]
    assert result["injected"] is False and result["outside"] == []
