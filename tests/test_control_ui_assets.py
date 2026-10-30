"""The web UI's shipped files: CSP-safe markup, no HTML injection sinks, no outside requests, locale parity."""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path

import pytest

from meridian.control.models import USERNAME_PATTERN, LimitStrategy, RevisionStatus, TokenScope, UserStatus
from meridian.control.server import ALL_ROUTES, OWNER_ROUTES
from meridian.control.ui_models import PasskeyState, ServerView
from meridian.control.ui_routes import UI_DIR

SRC = Path(__file__).resolve().parents[1] / "src" / "meridian" / "control"
INDEX = (UI_DIR / "index.html").read_text(encoding="utf-8")
SCRIPTS = sorted(UI_DIR.glob("*.js"))
STYLES = sorted(UI_DIR.glob("*.css"))
LOCALES = {
    path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted((UI_DIR / "locales").glob("*.json"))
}
ENGLISH = LOCALES["en"]
JS = {path.name: path.read_text(encoding="utf-8") for path in SCRIPTS}


def test_page_loads_every_script_and_stylesheet_from_files() -> None:
    referenced = re.findall(r'<script src="ui/([a-z0-9-]+\.js)"', INDEX)
    assert sorted(referenced) == sorted(JS), "every script is loaded, and only shipped ones"
    sheets = re.findall(r'<link rel="stylesheet" href="ui/([a-z-]+\.css)"', INDEX)
    assert sorted(sheets) == [path.name for path in STYLES]
    assert re.findall(r"<script(?![^>]*\bsrc=)", INDEX) == [], "no inline script"
    assert not re.search(r"\sstyle=|\son[a-z]+=|javascript:", INDEX), "no inline style or handlers"


@pytest.mark.parametrize("name", sorted(JS), ids=str)
def test_scripts_build_the_dom_without_html_sinks(name: str) -> None:
    source = JS[name]
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in source, f"{name} uses {sink}"
    assert 'setTimeout("' not in source and 'setInterval("' not in source


@pytest.mark.parametrize("path", [*SCRIPTS, *STYLES, UI_DIR / "index.html"], ids=lambda p: p.name)
def test_ui_files_make_no_outside_requests(path: Path) -> None:
    text = path.read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"https?://", text), f"{path.name} names an outside URL"
    assert "@import" not in text
    assert not re.search(r"url\((?!data:)", text), f"{path.name} loads a file through url()"


@pytest.mark.parametrize("path", [*SCRIPTS, *STYLES], ids=lambda p: p.name)
def test_ui_files_stay_within_the_line_budget(path: Path) -> None:
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 800


def test_every_request_carries_the_csrf_header() -> None:
    assert '"X-Meridian-Request": "1"' in JS["core.js"]
    for name, source in JS.items():
        if name not in ("core.js", "i18n.js"):
            assert "fetch(" not in source, f"{name} calls fetch directly instead of M.api"


# How the scripts spell a call: a literal path, optionally `+ name` and `+ "/suffix"`,
# or person.js's `path(name, "/suffix")`.
_API_CALL = re.compile(
    r'M\.api\("([A-Z]+)", (?:"([^"]+)"( \+ [\w.]+(?:\([\w.]+\))?(?: \+ "([^"]*)")?)?'
    r'|path\(\w+(?:, "([^"]+)"( \+ encodeURIComponent\(\w+\))?)?\)|commandPath\((\w+)\))'
)
_ID_SAMPLES = ("1", "0123456789ab")  # usernames and revision numbers; token IDs


def test_every_route_the_console_calls_admits_an_owner_session() -> None:
    """`server.OWNER_ROUTES` is an allowlist, so a screen calling an unlisted route would always fail."""
    calls = [match.groups() for source in JS.values() for match in _API_CALL.finditer(source)]
    assert len(calls) > 15
    for method, literal, joined, tail, person_suffix, device_joined, node_command in calls:
        if literal is None:
            literal, joined, tail = "api/users/", True, person_suffix
        paths = [
            ("/" + literal + (sample + (tail or "") if joined else "") + (sample if device_joined else "")).split(
                "?", 1
            )[0]
            for sample in _ID_SAMPLES
        ]
        if node_command:
            paths = ["/api/nodes/main/commands/1b6f3c1e-8f6a-4d2b-9c41-7a5e2d9f0c13"]
        routes = [route for route in ALL_ROUTES for path in paths if route[0] == method and route[1].fullmatch(path)]
        assert routes, f"{method} {paths[0]} matches no route"
        scope, handler = routes[0][2], routes[0][3]
        assert scope is None or handler in OWNER_ROUTES, f"{method} {paths[0]} refuses an owner session"


def test_setup_token_is_stripped_from_the_address_bar() -> None:
    assert "history.replaceState(null" in JS["auth.js"]


def test_locales_match_english_key_for_key_and_placeholder_for_placeholder() -> None:
    assert set(LOCALES) == {"en", "ru", "fa", "zh"}
    for lang, catalog in LOCALES.items():
        assert list(catalog) == list(ENGLISH), f"{lang} keys differ from English or are out of order"
        for key, text in catalog.items():
            assert set(re.findall(r"\{\w+\}", text)) == set(re.findall(r"\{\w+\}", ENGLISH[key])), (lang, key)
            assert text.strip(), (lang, key)


def test_every_literal_key_the_scripts_use_exists() -> None:
    """Any literal in a catalog namespace counts, so a key picked by a ternary or passed on does too."""
    namespaces = {key.split(".", 1)[0] for key in ENGLISH}
    used = {
        literal
        for source in JS.values()
        for literal in re.findall(r'"([a-z_]+\.[a-z_.]*[a-z])"', source)
        if literal.split(".", 1)[0] in namespaces
    }
    assert used and sorted(used - set(ENGLISH)) == []


@pytest.mark.parametrize(
    ("prefix", "values"),
    [
        ("status.", typing.get_args(UserStatus)),
        ("reset.", typing.get_args(LimitStrategy)),
        ("scope.", typing.get_args(TokenScope)),
        ("revision.", typing.get_args(RevisionStatus)),
        ("role.", typing.get_args(ServerView.model_fields["role"].annotation)),
        ("passkeys.", typing.get_args(PasskeyState.model_fields["reason"].annotation)),
        ("person.status_", typing.get_args(UserStatus)),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_every_value_the_service_sends_has_a_label(prefix: str, values: tuple[str, ...]) -> None:
    assert values and [value for value in values if prefix + value not in ENGLISH] == []


def test_every_refusal_the_sign_in_routes_raise_has_a_message() -> None:
    codes = set()
    for module in ("owner.py", "ui_routes.py", "login_limits.py", "tokens.py", "server.py"):
        source = (SRC / module).read_text(encoding="utf-8")
        codes |= set(re.findall(r'(?:ServiceError|super\(\)\.__init__)\(\s*"([a-z_]+)"', source))
    assert codes and sorted(code for code in codes if f"error.{code}" not in ENGLISH) == []


def test_farsi_is_the_only_rtl_locale() -> None:
    assert 'code === "fa" ? "rtl" : "ltr"' in JS["i18n.js"]
    assert 'lang === "fa" ? "rtl" : "ltr"' in JS["boot.js"]


def test_the_console_checks_usernames_with_the_services_pattern() -> None:
    assert f"/^{USERNAME_PATTERN}$/" in JS["people.js"]
