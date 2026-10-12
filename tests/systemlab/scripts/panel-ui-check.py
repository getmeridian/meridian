"""Load the Remnawave admin UI through Meridian's secret path in a real browser and log in.

Optional, local only (never in CI): re-run after every Remnawave backend bump,
because nginx sub_filter rewrites must match the shipped frontend bundle.
Run it from the Playwright image on the lab network while a deployed lab is up:

    docker run --rm --network systemlab_labnet -v "$PWD/tests/systemlab/scripts:/work" \
      mcr.microsoft.com/playwright/python:v1.63.0-noble bash -c \
      "pip install -q --break-system-packages playwright==1.63.0 && \
       python3 /work/panel-ui-check.py https://172.30.0.10 SECRET_PATH ADMIN_USER ADMIN_PASS /tmp/shots"

Usage: panel-ui-check.py BASE_URL SECRET_PATH ADMIN_USER ADMIN_PASS SCREENSHOT_DIR
Fails (exit 1) on any failed request, HTTP >= 400 for same-origin assets/API,
console errors, or a login that does not reach the dashboard.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    base, secret, user, password, shots = sys.argv[1:6]
    out = Path(shots)
    out.mkdir(parents=True, exist_ok=True)
    url = f"{base.rstrip('/')}/{secret.strip('/')}/"
    problems: list[str] = []
    requests: list[str] = []
    with sync_playwright() as pw:
        channel = os.environ.get("BROWSER_CHANNEL") or None
        browser = pw.chromium.launch(channel=channel, headless=True)
        page = browser.new_context(ignore_https_errors=True).new_page()
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("requestfailed", lambda r: problems.append(f"request failed: {r.url} {r.failure}"))

        def on_response(response) -> None:
            requests.append(f"{response.status} {response.request.method} {response.url}")
            if response.url.startswith(base) and response.status >= 400 and "/auth/" not in response.url:
                problems.append(f"HTTP {response.status}: {response.url}")

        page.on("response", on_response)
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.screenshot(path=str(out / "1-login.png"), full_page=True)
        password_box = page.locator("input[type=password]")
        if password_box.count() == 0:
            problems.append("login form did not render")
        else:
            page.locator("input:not([type=password]):not([type=hidden])").first.fill(user)
            password_box.first.fill(password)
            password_box.first.press("Enter")
            page.wait_for_load_state("networkidle", timeout=60_000)
            page.wait_for_timeout(3000)
            page.screenshot(path=str(out / "2-after-login.png"), full_page=True)
            if page.locator("input[type=password]").count() and "/auth" in page.url:
                problems.append(f"still on the login page after submitting: {page.url}")
        print("final url:", page.url)
        browser.close()
    Path(out / "requests.log").write_text("\n".join(requests) + "\n")
    leaked = [line for line in requests if base in line and f"/{secret.strip('/')}" not in line.split(" ", 2)[2]]
    for line in leaked:
        problems.append(f"same-origin request outside the secret path: {line}")
    for problem in problems:
        print("PROBLEM", problem)
    print(f"{len(requests)} requests, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
