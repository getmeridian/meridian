"""Personal subscriptions stay useful on a phone, even without JS or a live connection."""

import re
from urllib.parse import unquote

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Browser, Page, Playwright, Route, expect

from meridian.control.models import UserUpdate
from meridian.control.service import ControlService
from tests.e2e.helpers import connected


@pytest.fixture
def subscription_url(service: ControlService, base_url: str) -> str:
    user = service.get_user("Luma")
    assert user is not None
    return f"{base_url}/sub/{user.sub_token}"


def audit(page: Page) -> None:
    result = Axe().run(
        page, options={"runOnly": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"], "resultTypes": ["violations"]}
    )
    assert not result.response["violations"], result.generate_report()


def test_phone_installs_the_recommended_app_and_checks_its_connection(
    context_factory, webkit_browser: Browser, playwright_driver: Playwright, subscription_url: str
) -> None:
    phone = dict(playwright_driver.devices["iPhone 13"])
    phone.pop("default_browser_type", None)
    page = context_factory(target_browser=webkit_browser, **phone).new_page()
    seen = []

    def probe(route: Route) -> None:
        seen.append(route.request.method)
        route.fulfill(
            content_type="application/octet-stream",
            headers={"Date": "Fri, 15 Jan 2027 12:00:00 GMT"},
            body=b"" if route.request.method == "HEAD" else bytes(96 * 1024),
        )

    page.route("**/probe.bin*", probe)
    connected(page, subscription_url, "Luma")
    add = page.get_by_role("link", name="Add to Happ")
    # A first-time visitor sees both install and import before scrolling.
    expect(add).to_be_in_viewport(ratio=1)
    account = page.locator("#account-panel")
    page.get_by_role("link", name="Your account").click()
    expect(account).to_be_focused()
    expect(account.get_by_text("Lifetime traffic")).to_be_visible()
    refresh = account.get_by_role("button", name="Refresh")
    refresh.focus()
    with page.expect_response(lambda response: response.url == subscription_url + "/info"):
        page.keyboard.press("Enter")
    expect(account).to_have_attribute("aria-busy", "false")
    expect(refresh).to_be_focused()
    choices = page.get_by_text("Change app or device", exact=True)
    choices.click()
    expect(page.get_by_role("combobox", name="Platform", exact=True)).to_have_value("ios")
    chooser = page.get_by_role("combobox", name="App", exact=True)
    expect(chooser).to_have_value("Happ")
    page.get_by_role("combobox", name="Platform", exact=True).focus()
    page.keyboard.press("Tab")
    expect(chooser).to_be_focused()
    expect(page.get_by_role("link", name="Add to Happ")).to_have_attribute("href", "happ://add/" + subscription_url)
    qr = page.locator('details[data-disclosure="subscription-qr"]')
    expect(qr.get_by_role("img")).to_have_count(0)
    qr.locator("summary").click()
    expect(qr.get_by_role("img", name="QR code")).to_be_visible()
    qr.locator("summary").click()
    audit(page)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # A desktop app must receive the same subscription in its own supported import format.
    page.get_by_role("combobox", name="Platform", exact=True).select_option("windows")
    chooser.select_option("FlClashX")
    attr = page.get_by_role("link", name="Add to FlClashX").get_attribute("href")
    assert attr and unquote(attr) == "flclashx://install-config?url=" + subscription_url
    assert not seen
    page.locator("details > summary").filter(has_text="Not connecting?").click()
    page.get_by_role("button", name="Run check").click()
    expect(page.get_by_role("button", name="Copy report")).to_be_visible()
    report = page.locator("#selfcheck-report").text_content()
    assert seen == ["HEAD", "GET"]
    assert report and "Data flow: OK - 96.0 KB" in report and subscription_url.split("/")[-1] not in report
    assert "127.0.0.1" not in report


@pytest.mark.parametrize(
    ("agent", "platform", "installer"),
    [
        ("Android", "android", r"^https://play[.]google[.]com/"),
        ("Macintosh; Intel Mac OS X", "macos", r"^https://github[.]com/Happ-proxy/happ-desktop/"),
        ("Windows NT 10.0", "windows", r"^https://github[.]com/Happ-proxy/happ-desktop/"),
        ("X11; Linux x86_64", "linux", r"^https://github[.]com/Happ-proxy/happ-desktop/"),
    ],
)
def test_a_new_visitor_gets_an_install_link_for_their_platform(
    context_factory,
    subscription_url: str,
    agent: str,
    platform: str,
    installer: str,
) -> None:
    page = context_factory(user_agent=f"Mozilla/5.0 ({agent}) AppleWebKit/537.36").new_page()
    page.goto(subscription_url)
    expect(page.get_by_role("link", name="Add to Happ")).to_be_visible()
    if platform != "android":
        transfer = page.locator('details[data-disclosure="desktop-qr"]')
        expect(transfer.get_by_role("img")).to_have_count(0)
        transfer.locator("summary").click()
        expect(transfer.get_by_role("img", name="QR code")).to_be_visible()
    page.get_by_text("Change app or device", exact=True).click()
    expect(page.get_by_role("combobox", name="Platform", exact=True)).to_have_value(platform)
    expect(page.locator("a.app-row")).to_have_attribute("href", re.compile(installer))
    expect(page.get_by_role("link", name="Add to Happ")).to_have_attribute("href", "happ://add/" + subscription_url)


def test_persian_phone_page_has_accessible_names_contrast_and_reading_direction(
    context_factory, webkit_browser: Browser, playwright_driver: Playwright, subscription_url: str
) -> None:
    phone = dict(playwright_driver.devices["iPhone 13"])
    phone.pop("default_browser_type", None)
    page = context_factory(target_browser=webkit_browser, locale="fa-IR", color_scheme="dark", **phone).new_page()
    page.goto(subscription_url)
    expect(page.locator("#account-panel")).to_have_attribute("aria-busy", "false")
    expect(page.locator("html")).to_have_attribute("lang", "fa")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    audit(page)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_suspended_person_sees_the_reason_before_setup_and_can_change_language_with_a_keyboard(
    context_factory, subscription_url: str, service: ControlService
) -> None:
    service.update_user("Luma", UserUpdate(status="disabled"))
    page = context_factory(viewport={"width": 390, "height": 664}, locale="ru-RU").new_page()
    page.goto(subscription_url)
    expect(page.locator("#account-warning")).to_contain_text("приостановил")
    expect(page.locator("#account-warning")).to_be_in_viewport(ratio=1)
    expect(page.locator("[data-import]")).to_have_attribute("aria-disabled", "true")
    chinese = page.locator('[data-lang="zh"]')
    chinese.focus()
    page.keyboard.press("Space")
    expect(page.locator("html")).to_have_attribute("lang", "zh")
    expect(chinese).to_be_focused()
    expect(page.locator("#account-warning")).to_contain_text("已停用")
    expect(page.locator("[data-import]")).to_have_attribute("aria-disabled", "true")


def test_no_javascript_subscription_can_be_imported_by_an_app(context_factory, subscription_url: str, app) -> None:
    page = context_factory(java_script_enabled=False).new_page()
    response = page.goto(subscription_url + "/index.html")
    assert response is not None and response.headers["cache-control"] == "no-store"
    link = page.get_by_role("textbox", name="Your subscription link")
    expect(link).to_be_visible()
    expect(link).to_have_value(subscription_url)
    reply = app.get(link.input_value())
    assert reply.is_success and any(entry.get("outbounds") for entry in reply.json())


def test_offline_reconnect_page_never_remembers_a_person(
    context_factory, worker_browser: Browser, subscription_url: str, browser_faults, service: ControlService
) -> None:
    context = context_factory(target_browser=worker_browser, service_workers="allow")
    page = context.new_page()
    # A previous app release could leave more than one private path in storage.
    page.add_init_script("""if (!sessionStorage.getItem('seeded')) {
      localStorage.setItem('meridian-account:/sub/former-subscriber/info', '{"status":"active"}');
      localStorage.setItem('meridian-account:/sub/second-subscriber/info', '{}');
      localStorage.setItem('separate-app-setting', 'keep');
      sessionStorage.setItem('seeded','yes');
    }""")
    # Seed the cache state of a previous worker before the personal page registers the new one.
    page.goto(subscription_url.rsplit("/", 1)[0] + "/~pwa/sw.js")
    page.evaluate("""async () => {
      await (await caches.open('pwa-0123456789abcdef')).put('/sub/~pwa/old/app.js?private', new Response('old'));
      await caches.open('other-site-cache');
    }""")
    connected(page, subscription_url, "Luma")
    assert page.evaluate("""() => new Promise(resolve => {
      if (navigator.serviceWorker.controller) { resolve(true); return; }
      const timeout = setTimeout(() => resolve(false), 10000);
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        clearTimeout(timeout); resolve(!!navigator.serviceWorker.controller);
      }, {once: true});
    })"""), "The service worker never took control of the subscription"
    assert page.evaluate("localStorage.getItem('separate-app-setting')") == "keep"
    storage = page.evaluate("JSON.stringify(localStorage)")
    assert "meridian-account:" not in storage and subscription_url.split("/")[-1] not in storage
    names = page.evaluate("caches.keys()")
    assert "pwa-0123456789abcdef" not in names and "other-site-cache" in names
    cached = page.evaluate("""async () => (await Promise.all((await caches.keys()).map(async key =>
      (await (await caches.open(key)).keys()).map(req=>req.url)))).flat()""")
    assert cached and all("/sub/~pwa/" in url and "?" not in url for url in cached)
    context.set_offline(True)
    browser_faults.expect("503")
    reply = page.reload()
    assert reply and reply.from_service_worker and reply.status == 503 and reply.headers["cache-control"] == "no-store"
    expect(page.get_by_role("heading", name="Connection page unavailable")).to_be_visible()
    expect(page.locator("html")).to_have_css("font-family", "system-ui, sans-serif")
    assert "Luma" not in page.content() and subscription_url.split("/")[-1] not in page.content()
    assert not page.locator("a, input, img").count()
    context.set_offline(False)
    connected(page, subscription_url, "Luma")
    service.reissue_user("Luma")
    browser_faults.expect("404")
    refused = page.goto(subscription_url)
    assert refused and refused.status == 404
    expect(page.get_by_role("heading", name="Connection page unavailable")).not_to_be_visible()
