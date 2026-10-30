"""Export both public demos from a temporary real control service, without network access.

Run `uv run python scripts/build_showcase.py website/public/demo` (CI and releases write `website/dist/demo`).
The recordings and shim belong only to the website; no runtime service imports them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from meridian import __version__  # noqa: E402
from meridian.control.enrollment import token_sha256  # noqa: E402
from meridian.control.models import TokenCreate, UserCreate, UserUpdate  # noqa: E402
from meridian.control.route_catalog import API_ROUTES  # noqa: E402
from meridian.control.route_types import path_pattern  # noqa: E402
from meridian.control.server import ControlServer  # noqa: E402
from meridian.control.service import ControlService  # noqa: E402
from meridian.control.ui_models import AuthState, FleetView, SessionList, ShareView  # noqa: E402
from meridian.control.ui_routes import SESSION_COOKIE, UI_DIR  # noqa: E402
from meridian.core.client_limits import CalendarExpiryPreview  # noqa: E402
from meridian.pwa import load_pwa_assets  # noqa: E402
from scripts.showcase_seed import (  # noqa: E402
    ADMIN_TOKEN,
    FLEET_ID,
    PAGE_PERSON,
    PEOPLE,
    PUBLIC_URL,
    seed_service,
    stamp,
)

NOOP_SW = "/* SW disabled */\nself.addEventListener('install',()=>self.skipWaiting());\n"


def validate_recordings(responses: dict[str, Any]) -> None:
    """Refuse model drift at generation time and in tests, using today's route catalog."""
    for key, body in responses.items():
        method, path = key.split(" ", 1)
        path = unquote(urlsplit("/" + path).path)
        route = next(
            (route for route in API_ROUTES if route.method == method and path_pattern(route.path).fullmatch(path)), None
        )
        if route is not None:
            route.response_model.model_validate(body)
        elif method == "GET" and path == "/auth/state":
            AuthState.model_validate(body)
        elif method == "GET" and path == "/auth/sessions":
            SessionList.model_validate(body)
        elif path == "/ui/api/fleet":
            FleetView.model_validate(body)
        elif path.startswith("/ui/api/people/") and path.endswith("/share"):
            ShareView.model_validate(body)
        elif path.startswith("/ui/api/people/") and path.endswith("/expiry"):
            CalendarExpiryPreview.model_validate(body)
        elif not path.startswith("/sub/"):
            raise ValueError("No current response model for " + key)


def export_showcase(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # A rerun (pnpm dev) must not keep an older asset version or person beside the new export.
    for stale in ("console", "sub"):
        shutil.rmtree(directory / stale, ignore_errors=True)
    (directory / "index.html").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="meridian-showcase-state-") as state:
        now = datetime.now(UTC).replace(microsecond=0)
        service = ControlService(Path(state) / "control.db", fleet_id=FLEET_ID, version=__version__)
        try:
            seed_service(service, now)
            grant = service.owner.issue_setup_link(reset=False)
            session = service.owner.complete_setup(grant.token, "demo-only-password-not-an-account")
            server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN), public_path="/demo/console")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{server.server_address[1]}",
                    headers={
                        "Authorization": "Bearer " + ADMIN_TOKEN,
                        "Cookie": f"{SESSION_COOKIE}={session.token}",
                        "X-Meridian-Request": "1",
                    },
                    trust_env=False,
                    timeout=30,
                ) as client:
                    write_recordings(directory, client, service, now)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        finally:
            service.close()


def write_recordings(directory: Path, client: httpx.Client, service: ControlService, now: datetime) -> None:
    responses: dict[str, Any] = {}

    def record(path: str, method: str = "GET", body: Any = None) -> Any:
        response = client.request(method, "/" + path, json=body)
        response.raise_for_status()
        result = response.json()
        responses[method + " " + path] = result
        return result

    for path in (
        "auth/state",
        "auth/sessions",
        "ui/api/fleet",
        "api/settings",
        "api/users",
        "api/privacy",
        "api/language",
        "api/notice",
        "api/tokens",
        "api/revisions",
        "api/events",
        "api/traffic/daily?days=30",
        "api/attention",
    ):
        record(path)
    for summary in responses["GET api/revisions"]["revisions"]:
        record(f"api/revisions/{summary['number']}")
    for name in PEOPLE:
        user = record("api/users/" + name)
        record(f"ui/api/people/{name}/share")
        record(f"ui/api/people/{name}/expiry?months=1")
        record(f"ui/api/people/{name}/expiry?months=3")
        record(f"api/users/{name}/connections")
        record(f"api/users/{name}/usage?days=30")
        record(f"api/users/{name}/cut-off")
        if user["status"] == "active":
            for index, platform in enumerate(("Android", "Windows")):
                response = client.get(
                    f"/sub/{user['sub_token']}",
                    headers={
                        "User-Agent": "Demo app",
                        "x-hwid": f"demo-device-{name}-{index}",
                        "x-device-os": platform,
                        "x-ver-os": "14" if index == 0 else "11",
                        "x-device-model": "Example phone" if index == 0 else "Example laptop",
                    },
                )
                response.raise_for_status()
        record(f"api/users/{name}/devices")
        token = user["sub_token"]
        info = record(f"sub/{token}/info")
        write_person_page(directory, client, token, info)
    # Templates are real mutation replies too, so browser edits do not invent boundary shapes.
    record(
        "api/tokens",
        "POST",
        TokenCreate(name="Demo token template", scope="read", expires_in_days=90).model_dump(mode="json"),
    )
    record("api/users", "POST", UserCreate(username="DemoTemplate").model_dump(mode="json"))
    record("ui/api/people/DemoTemplate/share")
    record("api/users/DemoTemplate/devices")
    record("api/users/DemoTemplate/usage?days=30")
    record("api/users/DemoTemplate/cut-off")
    # Disabled or over-limit people get placeholder links; record their working ones too, so
    # enabling them in the demo shows real entries. A second credential set carries correctly
    # generated QR codes for reissue demonstrations.
    active_shares: dict[str, Any] = {}
    rotations: dict[str, Any] = {}
    for name in (*PEOPLE, "DemoTemplate"):
        initial_share = responses[f"GET ui/api/people/{name}/share"]
        before = service.get_user(name)
        assert before is not None
        if before.status != "active":
            service.update_user(name, UserUpdate(status="active", traffic_limit_bytes=0))
            active_shares[name] = record(f"ui/api/people/{name}/share")
        service.reissue_user(name)
        user = service.get_user(name)
        assert user is not None
        rotations[name] = {"user": user.model_dump(mode="json"), "share": record(f"ui/api/people/{name}/share")}
        if before.status != "active":
            status = "disabled" if before.status == "disabled" else "active"
            service.update_user(name, UserUpdate(status=status, traffic_limit_bytes=before.traffic_limit_bytes))
            service.enforce()
        responses[f"GET ui/api/people/{name}/share"] = initial_share
    validate_recordings(responses)
    console = directory / "console"
    console.mkdir(exist_ok=True)
    shutil.copytree(UI_DIR, console / "ui", dirs_exist_ok=True, ignore=shutil.ignore_patterns("CLAUDE.md"))
    for name in ("demo.js", "demo.css"):
        shutil.copy2(ROOT / "website/demo" / name, console / name)
    shell = (UI_DIR / "index.html").read_text()
    shell = shell.replace(
        '  <script src="ui/boot.js">',
        '  <link rel="stylesheet" href="demo.css">\n  <script src="demo.js"></script>\n  <script src="ui/boot.js">',
    )
    (console / "index.html").write_text(shell)
    (console / "demo-data.json").write_text(
        json.dumps(
            {
                "generated_at": stamp(now),
                "public_url": PUBLIC_URL,
                "page_person": PAGE_PERSON,
                "responses": responses,
                "rotations": rotations,
                "active_shares": active_shares,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    assets = load_pwa_assets()
    shared = directory / "sub/~pwa" / assets.version
    shared.mkdir(parents=True, exist_ok=True)
    for name, content in assets.files.items():
        (shared / name).write_bytes(NOOP_SW.encode() if name == "sw.js" else content)
    (shared.parent / "sw.js").write_text(NOOP_SW)
    token = responses["GET api/users/" + PAGE_PERSON]["sub_token"]
    (directory / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex, nofollow">'
        f'<meta http-equiv="refresh" content="0;url=./sub/{token}/"><title>Meridian demo</title>'
        f'<script>location.replace("./sub/{token}/"+location.search+location.hash)</script></head>'
        f'<body><a href="./sub/{token}/">Open the connection page demo</a></body></html>'
    )


def write_person_page(directory: Path, client: httpx.Client, token: str, info: dict[str, Any]) -> None:
    person = directory / "sub" / token
    person.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "config.json", "manifest.webmanifest", "support.html"):
        response = client.get(f"/sub/{token}/" + name, headers={"Accept": "text/html"})
        response.raise_for_status()
        text = response.text
        if name.endswith(".html"):
            adapter = (
                '<link rel="stylesheet" href="../../console/demo.css"><script src="../../console/demo.js"></script>'
            )
            text = text.replace("<head>", "<head>" + adapter, 1)
        (person / name).write_text(text)
    (person / "info").write_text(json.dumps(info, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    export_showcase(parser.parse_args().directory.resolve())


if __name__ == "__main__":
    main()
