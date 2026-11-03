"""Real service and isolated browser contexts, shared by every journey area."""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import ssl
import sys
import threading
from collections.abc import Callable, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Route, sync_playwright

from meridian import __version__
from meridian.control.enrollment import token_sha256
from meridian.control.owner import OwnerSession
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from meridian.control.ui_routes import SESSION_COOKIE
from meridian.core.agent_desired import SystemdUnitResource
from meridian.core.owner_contact import OwnerContact
from scripts.build_showcase import export_showcase
from scripts.showcase_seed import ADMIN_TOKEN, FLEET_ID, seed_service
from tests.e2e.support import OWNER_PASSWORD, BrowserFaults, ServiceClock
from tests.support.tls_server import self_signed_cert

FAILED = pytest.StashKey[bool]()


class _StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="session")
def demo_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Build both demos once and serve their unchanged assets over loopback."""
    directory = tmp_path_factory.mktemp("e2e-showcase")
    export_showcase(directory / "demo")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_StaticHandler, directory=str(directory)))
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/demo"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        item.stash[FAILED] = True


@pytest.fixture
def clock() -> ServiceClock:
    return ServiceClock()


@pytest.fixture
def service(tmp_path: Path, clock: ServiceClock, record_property) -> Iterator[ControlService]:
    start = perf_counter()
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version=__version__, clock=clock)
    try:
        seed_service(service, clock.moment)
        # The visual showcase omits runnable units. Supply the managed core needed by real cut-off reviews.
        for node in service.list_nodes():
            spec = service.node_spec(node.node_id)
            assert spec is not None
            unit = SystemdUnitResource(
                id="unit-xray",
                depends_on=["xray"],
                name="netcore.service",
                enabled=True,
                state="started",
                content="[Service]\nExecStart=/usr/local/bin/xray run -config /etc/xray/config.json\n",
            )
            service.put_node_spec(node.node_id, spec.model_copy(update={"resources": [*spec.resources, unit]}))
        record_property("seed_seconds", round(perf_counter() - start, 3))
        yield service
    finally:
        service.close()


@pytest.fixture(scope="session")
def tls_context(tmp_path_factory: pytest.TempPathFactory) -> ssl.SSLContext:
    cert, key = self_signed_cert(tmp_path_factory.mktemp("e2e-tls"))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


class _TlsControlServer(ControlServer):
    def handle_error(self, request: object, client_address: tuple[str, int]) -> None:
        # Handshakes now run in handler threads; a client that rejects the test certificate or hangs up is gone.
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError)):
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]


@pytest.fixture
def base_url(service: ControlService, tls_context: ssl.SSLContext) -> Iterator[str]:
    server = _TlsControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN), public_path="/console")
    # Chromium opens many connections at once. A deeper backlog and handshakes in the handler threads
    # (not in the single accept loop) keep asset requests from being reset under that burst.
    server.socket.listen(128)
    server.socket = tls_context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    base = f"https://127.0.0.1:{server.server_address[1]}"
    settings = service.settings().model_copy(
        update={"public_url": base, "owner_contact": OwnerContact(text="Ask the owner.")}
    )
    service.put_settings(settings)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.fail("Chromium is missing. Run: uv run --group e2e playwright install chromium")
        args = (
            ["--single-process", "--in-process-gpu", "--ignore-certificate-errors"]
            if os.environ.get("E2E_SINGLE_PROCESS") == "1"
            else []
        )
        browser = playwright.chromium.launch(channel="chromium", args=args)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def browser_faults() -> BrowserFaults:
    return BrowserFaults()


def loopback(url: str) -> bool:
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


@pytest.fixture
def context_factory(
    browser: Browser,
    browser_faults: BrowserFaults,
    clock: ServiceClock,
    request: pytest.FixtureRequest,
    tmp_path: Path,
    base_url: str,
) -> Iterator[Callable[..., BrowserContext]]:
    contexts: list[BrowserContext] = []
    local_browsers: list[Browser] = []
    traces: list[Path] = []

    def create(**options) -> BrowserContext:
        target = browser
        if os.environ.get("E2E_SINGLE_PROCESS") == "1":
            target = browser.browser_type.launch(
                channel="chromium", args=["--single-process", "--in-process-gpu", "--ignore-certificate-errors"]
            )
            local_browsers.append(target)
        context = target.new_context(
            **{
                "viewport": {"width": 1440, "height": 900},
                "timezone_id": "UTC",
                "locale": "en-US",
                "color_scheme": "light",
                "service_workers": "block",
                "ignore_https_errors": True,
                **options,
            }
        )
        context.set_default_timeout(10_000)
        contexts.append(context)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)

        def route(route: Route) -> None:
            if loopback(route.request.url):
                route.continue_()
            else:
                browser_faults.unexpected.append(f"external request: {route.request.url}")
                route.abort("blockedbyclient")

        def page_opened(page: Page) -> None:
            page.clock.set_fixed_time(clock.moment)
            clock.pages.append(page)
            page.on("console", browser_faults.console)
            page.on("pageerror", lambda error: browser_faults.record(f"pageerror: {error}"))

        context.route("**/*", route)
        context.on("page", page_opened)
        return context

    yield create
    faults = browser_faults.unexpected + [f"expected error did not occur: {p.pattern}" for p in browser_faults.expected]
    failed = request.node.stash.get(FAILED, False) or bool(faults)
    directory = Path(os.environ.get("E2E_ARTIFACTS", "test-results/e2e")) / re.sub(r"[^\w.-]", "_", request.node.nodeid)
    for index, context in enumerate(contexts):
        try:
            if failed:
                directory.mkdir(parents=True, exist_ok=True)
                for page_index, page in enumerate(context.pages):
                    if not page.is_closed():
                        page.screenshot(path=str(directory / f"context-{index}-page-{page_index}.png"), full_page=True)
            trace = tmp_path / f"context-{index}-trace.zip"
            context.tracing.stop(path=str(trace))
            traces.append(trace)
        finally:
            context.close()
    for local_browser in local_browsers:
        local_browser.close()
    faults = browser_faults.unexpected + [f"expected error did not occur: {p.pattern}" for p in browser_faults.expected]
    if failed or faults:
        directory.mkdir(parents=True, exist_ok=True)
        for trace in traces:
            shutil.copy2(trace, directory / trace.name)
    if faults:
        pytest.fail("\n".join(faults))


@pytest.fixture
def owner_session(service: ControlService) -> OwnerSession:
    return service.owner.complete_setup(service.owner.issue_setup_link(reset=False).token, OWNER_PASSWORD)


@pytest.fixture
def owner(context_factory, owner_session: OwnerSession, base_url: str) -> Page:
    context = context_factory()
    # The test server stands behind nginx's stripped secret path, as the probe does.
    context.add_cookies([{"name": SESSION_COOKIE, "value": owner_session.token, "url": base_url}])
    context.add_init_script("""
        if (localStorage.getItem('mrd.mode') === null) localStorage.setItem('mrd.mode', 'detailed');
        if (localStorage.getItem('mrd.lang') === null) localStorage.setItem('mrd.lang', 'en');
    """)
    page = context.new_page()
    page.goto(base_url + "/#/overview")
    return page


@pytest.fixture
def visitor(context_factory) -> Page:
    return context_factory().new_page()


@pytest.fixture
def app(base_url: str) -> Iterator[httpx.Client]:
    def request_guard(request: httpx.Request) -> None:
        assert loopback(str(request.url)), f"external app request: {request.url}"

    with httpx.Client(
        base_url=base_url,
        headers={"User-Agent": "Happ/1.0", "Accept": "*/*"},
        timeout=10,
        verify=False,
        trust_env=False,
        event_hooks={"request": [request_guard]},
    ) as client:
        yield client
