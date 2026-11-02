"""Every console request needs a registered demo handler or an explicit local refusal."""

from __future__ import annotations

import re
from pathlib import Path

from meridian.control.ui_routes import UI_DIR

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r"\bM\.(?:rawApi|api)\s*\(")
STRING = re.compile(r"^([\"'])(.*?)\1$", re.DOTALL)
REGISTRATION = re.compile(r'D\.(?:route|recorded|unavailable)\("([A-Z]+)", "([^"]+)"')


def expression(source: str, start: int, delimiters: str = ",;") -> tuple[str, int]:
    """Read an argument without treating a comma inside a helper call as its end."""
    depth, quote, escaped = 0, "", False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif depth == 0 and char in delimiters:
            return source[start:index].strip(), index
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return source[start:index].strip(), index
            depth -= 1
    assert not quote and not depth, "Unfinished JavaScript API argument"
    return source[start:].strip(), len(source)


def request_paths(value: str, source: str) -> set[str]:
    if re.fullmatch(r"[\w$]+", value):
        assignment = re.search(r"\b" + re.escape(value) + r"\s*=\s*", source)
        assert assignment, f"Resolve the console route variable {value!r} in the demo guard"
        assigned, _ = expression(source, assignment.end())
        if "?" in assigned and not assigned.startswith(('"', "'")):
            choices = re.findall(r'["\']([^"\']+)["\']', assigned)
            assert choices and all("/" in choice for choice in choices), assigned
            return set(choices)
        return request_paths(assigned, source)
    if value.startswith("commandPath("):
        return {"api/nodes/{id}/commands/{id}"}
    if value.startswith("path("):
        arguments, end = expression(value, value.index("(") + 1)
        if value[end] == ",":
            suffix_value, _ = expression(value, end + 1)
            return request_paths('"api/users/{id}" + ' + suffix_value, source)
        return {"api/users/{id}"}
    assert value.startswith(('"', "'")), f"Resolve the console route expression {value!r} in the demo guard"
    parts, start = [], 0
    while start < len(value):
        part, end = expression(value, start, "+")
        literal = STRING.fullmatch(part)
        parts.append(literal[2] if literal else "{id}")
        start = end + 1
    return {"".join(parts).split("?", 1)[0]}


def console_requests(scripts: dict[str, str]) -> set[tuple[str, str]]:
    requests = set()
    for name, source in scripts.items():
        for call in CALL.finditer(source):
            method, end = expression(source, call.end())
            literal = STRING.fullmatch(method)
            assert literal, f"Resolve the console request method in {name}: {method}"
            path, _ = expression(source, end + 1)
            requests.update((literal[2], path) for path in request_paths(path, source))
    return requests


def missing_requests(scripts: dict[str, str]) -> set[tuple[str, str]]:
    registered = {
        (method, path)
        for file in (ROOT / "website/demo").glob("*.js")
        for method, path in REGISTRATION.findall(file.read_text())
    }
    # Locale catalogs are copied from the console and fetched as same-origin static files.
    assert all((UI_DIR / "locales" / f"{locale}.json").is_file() for locale in ("en", "ru", "fa", "zh"))
    registered.add(("GET", "ui/locales/{id}.json"))
    return console_requests(scripts) - registered


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
        {"person.js": 'M.api("POST", path(username, "/future-action"), {});'}
    )
