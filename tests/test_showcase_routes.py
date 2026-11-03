"""Every console request needs a registered demo handler or an explicit local refusal."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = re.compile(r'D\.(?:route|recorded|unavailable)\("([A-Z]+)", "([^"]+)"')
pytestmark = needs_console_dom

# These unencoded IDs come from typed service records or locale choices. Pin their source so
# changing a binding or adding another call cannot silently inherit an opaque-ID exception.
REVIEWED = {
    "fleet.js": {
        "sha256": "50f419d3ab11455c6a76868b0dbacaf86d642aad447ab51c815df15e7ad97bc9",
        "paths": {
            'GET "api/revisions/" + number': ["api/revisions/{id}"],
            'GET "api/revisions/" + chosen': ["api/revisions/{id}"],
        },
    },
    "people-bulk.js": {
        "sha256": "fdecab79e5bae6f1d0b139da126e8da308b12401eca2d8cd8280098cff0e3b6f",
        "paths": {
            'GET "api/people/bulk/receipts/" + receipt.id': ["api/people/bulk/receipts/{id}"],
            'GET "api/people/bulk/receipts/" + review.id': ["api/people/bulk/receipts/{id}"],
            'GET "api/people/bulk/receipts/" + M.lastBulkReceipt.id': ["api/people/bulk/receipts/{id}"],
        },
    },
    "tokens.js": {
        "sha256": "97324c33d557202cd94f0b8fb88bf233856157b44ebc053c25d13cc87cb99c09",
        "paths": {'DELETE "api/tokens/" + token.id': ["api/tokens/{id}"]},
    },
    "i18n.js": {
        "sha256": "2e2276da38f1961d6264b307263a699bb3082e8fd9d37f7063f96b7428751319",
        "paths": {'GET "ui/locales/" + code + ".json"': ["ui/locales/{id}.json"]},
    },
}
PERSON_PATH = (
    'function path(username, suffix) { return "api/users/" + encodeURIComponent(username) + (suffix || ""); }\n'
)


def console_requests(scripts: dict[str, str]) -> set[tuple[str, str]]:
    process = subprocess.run(
        ["node", str(ROOT / "tests/showcase_route_parser.mjs")],
        cwd=ROOT,
        input=json.dumps({"scripts": scripts, "reviewed": REVIEWED}),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    result = json.loads(process.stdout)
    assert not result["unresolved"], "Resolve console route expressions or explicitly review their source: " + str(
        result["unresolved"]
    )
    return {tuple(request) for request in result["requests"]}


def missing_requests(scripts: dict[str, str]) -> set[tuple[str, str]]:
    registered = {
        (method, path)
        for file in (ROOT / "website/demo").glob("*.js")
        for method, path in REGISTRATION.findall(file.read_text())
    }
    # Locale catalogs are copied from the console and fetched as same-origin static files.
    assert all((UI_DIR / "locales" / f"{locale}.json").is_file() for locale in ("en", "ru", "fa", "zh"))
    registered.add(("GET", "ui/locales/{id}.json"))
    return {
        (method, path)
        for method, path in console_requests(scripts)
        if not any(
            method == registered_method and re.fullmatch(re.escape(registered_path).replace(r"\{id\}", "[^/]+"), path)
            for registered_method, registered_path in registered
        )
    }


def test_every_console_route_is_emulated_or_deliberately_unavailable_in_the_demo() -> None:
    scripts = {path.name: path.read_text() for path in UI_DIR.glob("*.js")}
    requests = console_requests(scripts)
    assert ("GET", "api/people") in requests
    assert ("PUT", "api/users/{id}/delivery-policy") in requests
    assert ("POST", "auth/passkeys/login/begin") in requests
    assert not missing_requests(scripts), "Demo misses console requests: " + str(sorted(missing_requests(scripts)))


def test_demo_guard_catches_a_request_added_by_a_later_console_sweep() -> None:
    assert ("GET", "api/future-overview") in missing_requests({"overview.js": 'M.api("GET", "api/future-overview");'})
    assert ("POST", "api/users/{id}/future-action") in missing_requests(
        {"person.js": PERSON_PATH + 'M.api("POST", path(username, "/future-action"), {});'}
    )


@pytest.mark.parametrize("prefix", ["path(username)", 'path(username, "/access")'])
def test_demo_guard_keeps_suffixes_outside_person_path_helpers(prefix: str) -> None:
    expected = "api/users/{id}" + ("/access" if "/access" in prefix else "") + "/unimplemented"
    assert ("GET", expected) in missing_requests(
        {"person.js": PERSON_PATH + f'M.api("GET", {prefix} + "/unimplemented");'}
    )


def test_demo_guard_resolves_route_variables_in_each_function_scope() -> None:
    source = """
      function settings() { var endpoint = "api/settings"; M.api("GET", endpoint); }
      function future() { var endpoint = "api/unimplemented"; M.api("GET", endpoint); }
    """
    assert ("GET", "api/unimplemented") in missing_requests({"screen.js": source})


def test_demo_guard_does_not_read_comparisons_as_route_assignments() -> None:
    source = """
      function future() {
        if (endpoint === "api/settings") return;
        var endpoint = "api/unimplemented";
        M.api("GET", endpoint);
      }
    """
    assert ("GET", "api/unimplemented") in missing_requests({"screen.js": source})


@pytest.mark.parametrize(
    "source",
    [
        'function future(endpoint) { M.api("GET", endpoint); }',
        'var endpoint = "api/settings"; endpoint += "/unimplemented"; M.api("GET", endpoint);',
        'var endpoint = "api/settings"; ({ endpoint } = config); M.api("GET", endpoint);',
        PERSON_PATH + 'function future(path) { M.api("GET", path(username)); }',
        'M.api("GET", "api/users/" + unknownSuffix);',
        'M.api("GET", unknownRoute(username));',
    ],
)
def test_demo_guard_refuses_route_expressions_it_cannot_prove(source: str) -> None:
    with pytest.raises(AssertionError, match="Resolve console route expressions"):
        console_requests({"screen.js": source})


def test_demo_guard_reads_the_helper_body_and_keeps_queries_after_the_complete_path() -> None:
    source = PERSON_PATH + 'M.api("GET", path(username, "/usage") + "?days=" + chosen);'
    assert console_requests({"screen.js": source}) == {("GET", "api/users/{id}/usage")}
    changed = source.replace('"api/users/"', '"api/unimplemented/"')
    assert ("GET", "api/unimplemented/{id}/usage") in missing_requests({"screen.js": changed})


@pytest.mark.parametrize("name", list(REVIEWED))
def test_demo_guard_needs_an_explicit_review_when_an_opaque_id_source_changes(name: str) -> None:
    source = (UI_DIR / name).read_text() + "\n// Changed caller.\n"
    with pytest.raises(AssertionError, match="Resolve console route expressions"):
        console_requests({name: source})


def test_demo_guard_refuses_a_route_binding_initialized_after_the_call() -> None:
    with pytest.raises(AssertionError, match="initialized before use"):
        console_requests({"screen.js": 'M.api("GET", endpoint); var endpoint = "api/settings";'})
