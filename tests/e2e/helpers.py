"""Small browser gestures shared across journeys; no product API substitutes."""

import json
import re

import httpx
from playwright.sync_api import Locator, Page, expect


def person(owner: Page, name: str = "Luma") -> None:
    owner.get_by_role("navigation").get_by_role("link", name="People", exact=True).click()
    owner.locator(f'.people-row[href="#/people/{name}"]').click()
    expect(owner.get_by_role("heading", name=name, exact=True)).to_be_visible()


def editor(owner: Page, key: str) -> Locator:
    owner.locator(f'[data-person-edit="{key}"]').click()
    form = owner.locator(f'[data-person-field="{key}"] form')
    expect(form).to_be_visible()
    return form


def add_person(owner: Page, name: str = "E2E_visitor") -> None:
    owner.get_by_role("button", name="Add person", exact=True).click()
    dialog = owner.get_by_role("dialog")
    dialog.get_by_label("Name", exact=True).fill(name)
    dialog.locator('[name="expiry_mode"]').select_option("fixed")
    dialog.locator('input[type="date"]').fill("2027-02-15")
    dialog.locator('input[type="time"]').fill("12:00:00")
    dialog.get_by_label("Traffic allowance", exact=True).select_option("limited")
    dialog.get_by_label("Traffic limit", exact=True).fill("12")
    dialog.get_by_role("button", name="Add", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(owner.get_by_role("heading", name=name, exact=True)).to_be_visible()


def share_url(owner: Page) -> str:
    owner.locator('[data-person-action="share"]').click()
    dialog = owner.get_by_role("dialog")
    link = dialog.get_by_role("link", name="Open page", exact=True)
    expect(link).to_have_attribute("href", re.compile(r"^https://127\.0\.0\.1:\d+/sub/"))
    url = link.get_attribute("href")
    assert url is not None
    dialog.get_by_role("button", name="Close", exact=True).click()
    expect(dialog).not_to_be_visible()
    return url


def connected(visitor: Page, url: str, name: str) -> None:
    visitor.goto(url)
    expect(visitor.locator('[data-t="hero.for"]')).to_contain_text(name)
    expect(visitor.locator(".account-status")).to_have_text("Active")
    expect(visitor.locator("#account-panel")).to_have_attribute("aria-busy", "false")


def document(app: httpx.Client, url: str) -> str:
    reply = app.get(url)
    assert reply.status_code == 200
    return reply.text


def connections(app: httpx.Client, url: str) -> str:
    text = document(app, url)
    configs = json.loads(text)
    assert any(outbound["protocol"] == "vless" for config in configs for outbound in config["outbounds"])
    return text


LOCALES = ["en", "ru", "fa", "zh"]
SCREENS = ["overview", "people", "people/Luma", "servers", "settings"]


def language(page: Page, code: str) -> None:
    page.locator(".account-toggle").click()
    page.locator("[data-console-language]").select_option(code)
    expect(page.locator("html")).to_have_attribute("lang", code)


def ready(page: Page, route: str) -> None:
    expect(page.locator("#view h1").first).to_be_visible()
    if route.startswith("people/"):
        expect(page.locator("[data-person-action=share]")).to_be_enabled()
    elif route in {"overview", "people", "servers"}:
        expect(page.locator("#view")).to_have_attribute("aria-busy", "false")
    expect(page.locator("#view .spinner:visible")).to_have_count(0)
