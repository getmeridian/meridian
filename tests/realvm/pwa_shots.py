"""Full-page screenshots of a connection page across devices, themes, and languages.

Runs wherever Playwright's Chromium can launch (a lab VM works when local
Chrome cannot): `uv run --with playwright python pwa_shots.py OUT_PREFIX URL [case ...]`
after `playwright install chromium`. Serve `meridian dev preview -o DIR` over HTTP
for an offline page, or point it at a deployed page.

A case is `name:width:scheme:ua[:open][:ru|fa|zh]` — ua is android, ios, or mac;
`open` expands every disclosure and the QR code before the shot.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

UAS = {
    "android": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Mobile Safari/537.36"
    ),
    "ios": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Mobile/15E148 Safari/604.1"
    ),
    "mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
DEFAULT_CASES = ["m-light:390:light:android", "m-dark:390:dark:ios", "d-light:1280:light:mac"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    prefix, url, *cases = argv
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for spec in cases or DEFAULT_CASES:
            name, width, scheme, ua, *flags = spec.split(":")
            context = browser.new_context(
                viewport={"width": int(width), "height": 844},
                color_scheme=scheme,  # type: ignore[arg-type]
                user_agent=UAS[ua],
                device_scale_factor=2 if int(width) < 600 else 1,
            )
            page = context.new_page()
            errors: list[str] = []
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            for flag in flags:
                if flag in ("ru", "fa", "zh"):
                    page.add_init_script(f"localStorage.setItem('meridian-lang', '{flag}')")
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(800)
            if "open" in flags:
                page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
                page.evaluate("var b=document.querySelector('[data-action=toggle-qr]'); if(b) b.click()")
                page.wait_for_timeout(300)
            page.screenshot(path=f"{prefix}-{name}.png", full_page=True)
            # A plain HTTP server lacks Service-Worker-Allowed, so the SW scope error is expected there.
            print(name, "errors:", errors[:5])
            context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
