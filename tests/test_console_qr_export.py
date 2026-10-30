"""Local QR export captions both formats and keeps generated URLs out of general DOM APIs."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("action", ["png", "svg", "copy"])
def test_qr_exports_keep_the_person_and_fleet_caption_and_never_fetch(locale: str, action: str) -> None:
    result = run_console("person-qr", locale, {"action": action, "displayName": "Sample <person> & مثال"})
    assert result["actions"] == ["png", "svg", "copy"]
    assert result["outside"] == []
    if action == "copy":
        assert result["copiedTypes"] == ["image/png"] and result["downloads"] == []
    else:
        assert len(result["downloads"]) == 1
        assert result["downloadTypes"] == ["image/" + ("png" if action == "png" else "svg+xml")]
        assert result["downloads"][0]["filename"].endswith("." + action)
    svg = result["svg"]
    assert "Sample &lt;person&gt; &amp; مثال" in svg and "Test fleet" in svg
    assert "<script" not in svg and result["generalUrlsRejected"] is True
    if action != "svg":
        assert result["canvasSize"][0] >= 400 and result["draws"] == 1


@pytest.mark.parametrize("clipboard", ["absent", "unsupported", "throws", "rejects"])
def test_qr_clipboard_failures_download_a_png_without_losing_the_image(clipboard: str) -> None:
    result = run_console("person-qr", data={"action": "copy", "clipboard": clipboard})
    assert result["downloads"] and result["downloadTypes"] == ["image/png"]
    assert result["outside"] == []


@pytest.mark.parametrize(
    "svg",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><script>window.pwned=1</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://198.51.100.2/outside"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(https://198.51.100.2/outside)"/></svg>',
    ],
)
def test_qr_export_refuses_active_or_external_svg_content(svg: str) -> None:
    result = run_console("person-qr", data={"action": "svg", "unsafeSvg": svg})
    assert result["downloads"] == [] and result["copiedTypes"] == []
    assert result["toasts"] and result["outside"] == []
