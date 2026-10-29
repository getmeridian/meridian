"""Exercise shipped PWA JavaScript at its DOM, storage, and fetch boundaries."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from datetime import UTC, datetime, timedelta

import pytest

from meridian.control.formats.browser import subscription_info
from meridian.control.formats.entries import SubscriptionUser
from meridian.render import render_apps_catalog, render_config_json

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="PWA runtime checks need Node, without npm packages")


@pytest.fixture
def page_data() -> dict[str, object]:
    now = datetime.now(UTC)
    user = SubscriptionUser(
        short_id="demo",
        username="client",
        uuid="550e8400-e29b-41d4-a716-446655440000",
        status="active",
        used_bytes=1536,
        limit_bytes=4096,
        lifetime_used_bytes=8192,
        expires_at=now + timedelta(days=7),
    )
    url = "https://198.51.100.1/connect/sub/demo"
    return {
        "config": json.loads(render_config_json([], "198.51.100.1", subscription_url=url)),
        "catalog": json.loads(render_apps_catalog()),
        "info": subscription_info(user, [], url, now),
    }


def run_page(script: str, data: dict[str, object]) -> None:
    source = (
        "const assert = require('node:assert/strict');\n"
        "const { createBrowser, response } = require('./tests/support/pwa_browser.cjs');\n"
        "const data = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));\n"
        "(async function() {\n"
        + textwrap.dedent(script)
        + "\n})().catch(error => { console.error(error); process.exit(1); });"
    )
    result = subprocess.run(
        [NODE or "node", "-e", source],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_account_loads_info_without_cache_and_refreshes_on_return(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { info: data.info });
        await browser.start();
        let requests = browser.requests.filter(r => r.url.endsWith('/info'));
        assert.equal(requests.length, 1, 'opening the page must fetch account information');
        assert.equal(requests[0].init.cache, 'no-store');
        const panel = browser.document.getElementById('account-panel');
        assert.match(panel.textContent, /Active/);
        assert.match(panel.textContent, /1.5.*KiB/);
        assert.match(panel.textContent, /4.*KiB/);
        assert.match(panel.textContent, /8.*KiB/);
        assert.match(panel.textContent, /7 days left/);
        assert.match(panel.textContent, /Checked/);
        browser.document.visibilityState = 'hidden'; browser.fire('visibilitychange');
        browser.document.visibilityState = 'visible'; browser.fire('visibilitychange');
        await browser.flush();
        browser.fire('click', browser.document.querySelector('[data-action="account-refresh"]'));
        await browser.flush();
        assert.equal(browser.requests.filter(r => r.url.endsWith('/info')).length, 3);
        """,
        page_data,
    )


@pytest.mark.parametrize("status", ["DISABLED", "EXPIRED", "LIMITED"])
def test_suspended_account_explains_and_dims_imports(page_data: dict[str, object], status: str) -> None:
    page_data["status"] = status
    run_page(
        """
        data.info.user.userStatus = data.status; data.info.user.isActive = false;
        const browser = createBrowser(data.config, data.catalog, { info: data.info });
        await browser.start();
        const panel = browser.document.getElementById('account-panel');
        assert.ok(panel, 'account panel is missing');
        assert.match(panel.textContent.toLowerCase(), new RegExp(data.status.toLowerCase()));
        const link = browser.document.querySelector('[data-import]');
        assert.equal(link.getAttribute('aria-disabled'), 'true');
        assert.ok(browser.fire('click', link).defaultPrevented, 'suspended import must not navigate');
        assert.ok(browser.document.getElementById('import-status').textContent.length > 30);
        """,
        page_data,
    )


def test_account_failure_keeps_only_account_snapshot_and_marks_it_unverified(page_data: dict[str, object]) -> None:
    run_page(
        """
        let fail = false;
        const options = { fetch(url) {
          if (url.endsWith('/info')) return fail ? Promise.reject(new Error('offline')) : Promise.resolve(response(data.info));
          return Promise.resolve(response(url.endsWith('config.json') ? data.config : data.catalog));
        } };
        const browser = createBrowser(data.config, data.catalog, options); await browser.start();
        const saved = JSON.stringify(browser.storage);
        assert.ok(Object.keys(browser.storage).some(k => k.startsWith('meridian-account:')), 'account answer was not saved');
        for (const secret of ['198.51.100.1', 'subscriptionUrl', 'username', 'links', 'userAgent', 'shortUuid']) {
          assert.ok(!saved.includes(secret), 'account storage must omit ' + secret);
        }
        const checks = browser.document.getElementById('selfcheck-results'); checks.textContent = 'Reachability result';
        fail = true;
        browser.fire('click', browser.document.querySelector('[data-action="account-refresh"]')); await browser.flush();
        assert.match(browser.document.getElementById('account-panel').textContent, /unverified/i);
        assert.match(browser.document.getElementById('account-panel').textContent, /1.5.*KiB/);
        assert.equal(checks.textContent, 'Reachability result');
        const reloaded = createBrowser(data.config, data.catalog, { ...options, storage: browser.storage }); await reloaded.start();
        assert.match(reloaded.document.getElementById('account-panel').textContent, /unverified/i);
        assert.match(reloaded.document.getElementById('account-panel').textContent, /1.5.*KiB/);
        """,
        page_data,
    )


def test_account_unlimited_and_no_expiry_are_not_zero_or_a_sentinel_date(page_data: dict[str, object]) -> None:
    run_page(
        """
        data.info.user.trafficLimitBytes = '0'; data.info.user.expiresAt = '2099-12-31T23:59:59.000Z';
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        const panel = browser.document.getElementById('account-panel'); assert.ok(panel, 'account panel is missing');
        assert.match(panel.textContent, /Unlimited/); assert.match(panel.textContent, /No expiry/);
        assert.ok(!panel.textContent.includes('2099'));
        """,
        page_data,
    )


def test_switching_from_persian_to_english_resets_direction_even_without_storage(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        browser.context.page.switchLang('fa');
        assert.equal(browser.document.documentElement.dir, 'rtl');
        browser.context.page.switchLang('en');
        assert.equal(browser.document.documentElement.lang, 'en');
        assert.equal(browser.document.documentElement.dir, 'ltr', 'English must restore LTR');
        browser.context.localStorage.setItem = () => { throw new Error('storage blocked'); };
        browser.context.page.switchLang('ru');
        assert.equal(browser.document.documentElement.lang, 'ru', 'a preference must work without localStorage');
        """,
        page_data,
    )


def test_load_error_uses_saved_language_before_config_arrives(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, {
          storage: { 'meridian-lang': 'fa' }, fetch: () => Promise.reject(new Error('offline'))
        });
        await browser.start();
        assert.equal(browser.document.documentElement.lang, 'fa');
        assert.equal(browser.document.documentElement.dir, 'rtl');
        assert.match(browser.document.getElementById('app').textContent, /بارگذاری/);
        """,
        page_data,
    )


def test_fixed_catalog_labels_and_accessible_actions_follow_language(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { storage: { 'meridian-lang': 'ru' }, info: data.info });
        await browser.start();
        assert.ok(!browser.document.getElementById('app').textContent.includes('Android, desktop'));
        assert.equal(browser.document.querySelector('.lang-bar').getAttribute('aria-label'), 'Язык');
        assert.equal(browser.document.querySelector('.install-dismiss').getAttribute('aria-label'), 'Закрыть');
        """,
        page_data,
    )


def test_support_page_honors_saved_language(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { storage: { 'meridian-lang': 'fa' }, language: 'en' });
        browser.document.body.innerHTML = '<h1 id="title"></h1><p id="status"></p>';
        browser.load('support.js');
        assert.equal(browser.document.documentElement.lang, 'fa');
        assert.equal(browser.document.documentElement.dir, 'rtl');
        assert.match(browser.document.getElementById('status').textContent, /صفحه/);
        """,
        page_data,
    )


def test_optional_catalog_never_blocks_config_or_resets_open_content(page_data: dict[str, object]) -> None:
    run_page(
        """
        let release;
        const browser = createBrowser(data.config, data.catalog, { fetch(url) {
          if (url.endsWith('apps.json')) return new Promise(resolve => { release = resolve; });
          return Promise.resolve(response(url.endsWith('config.json') ? data.config : data.info));
        } });
        await browser.start();
        assert.ok(browser.document.querySelector('[data-import]'), 'a stalled optional catalog must not block setup');
        const choice = browser.document.getElementById('app-choice');
        choice.value = 'FlClashX'; browser.fire('change', choice);
        const disclosure = browser.document.querySelector('[data-disclosure="manual-import"]');
        disclosure.open = true;
        const copy = browser.document.querySelector('.sub-url'); copy.focus();
        browser.document.getElementById('selfcheck-results').textContent = 'Saved self-check';
        release(response(data.catalog)); await browser.flush();
        assert.ok(browser.document.querySelector('[data-disclosure="manual-import"]').open);
        assert.equal(browser.document.getElementById('app-choice').value, 'FlClashX');
        assert.equal(browser.document.getElementById('platform-choice').value, 'android');
        assert.equal(browser.document.activeElement.getAttribute('data-ui-key'), copy.getAttribute('data-ui-key'));
        assert.equal(browser.document.getElementById('selfcheck-results').textContent, 'Saved self-check');
        assert.ok(browser.document.querySelector('.app-mark').getAttribute('src'), 'late catalog should supply icons');
        """,
        page_data,
    )


def test_catalog_timeout_ignores_late_answers_without_abort_support(page_data: dict[str, object]) -> None:
    run_page(
        """
        let release;
        const browser = createBrowser(data.config, data.catalog, { fetch(url) {
          if (url.endsWith('apps.json')) return new Promise(resolve => { release = resolve; });
          return Promise.resolve(response(url.endsWith('config.json') ? data.config : data.info));
        } });
        browser.context.AbortController = undefined;
        await browser.start();
        assert.ok(browser.document.querySelector('[data-import]'), 'fallback configuration should render immediately');
        await browser.advance(5001);
        data.catalog.apps[0].name = 'Late catalog'; release(response(data.catalog)); await browser.flush();
        assert.ok(!browser.document.getElementById('app').textContent.includes('Late catalog'));
        """,
        page_data,
    )


@pytest.mark.parametrize("bad", ["version", "app", "installer", "icon", "scheme", "guide"])
def test_bad_optional_catalog_keeps_valid_embedded_apps(page_data: dict[str, object], bad: str) -> None:
    page_data["bad"] = bad
    run_page(
        """
        if (data.bad === 'version') data.catalog.version = 2;
        if (data.bad === 'app') data.catalog.apps = [null];
        if (data.bad === 'installer') data.catalog.apps[0].urls.Android = 'javascript:alert(1)';
        if (data.bad === 'icon') data.catalog.apps[0].icon = 'https://198.51.100.2/icon.svg';
        if (data.bad === 'scheme') data.catalog.apps[0].deeplink = 'javascript:alert("{url}")';
        if (data.bad === 'guide') data.catalog.apps[0].manual = { en: 'incomplete' };
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        assert.ok(browser.document.querySelector('[data-import]'), 'malformed optional data must not break setup');
        assert.equal(JSON.stringify(browser.context._meridianConfig.apps) === JSON.stringify(data.config.apps), true, 'embedded catalog must survive invalid optional data');
        assert.ok(!browser.document.querySelector('.load-error'));
        """,
        page_data,
    )


def test_unsupported_config_version_is_rejected_before_rendering(page_data: dict[str, object]) -> None:
    run_page(
        """
        data.config.version = 2;
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        assert.ok(browser.document.querySelector('.load-error'), 'unsupported configuration must fail before rendering');
        assert.ok(!browser.document.querySelector('[data-import]'));
        """,
        page_data,
    )


def test_quoted_attributes_keep_user_text_literal(page_data: dict[str, object]) -> None:
    page_data["quoted_url"] = "https://198.51.100.1/sub/demo?q=\" onfocus=\"alert(1)&other='test'"
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog);
        const value = data.quoted_url;
        browser.document.getElementById('app').innerHTML = browser.context.page.renderSubUrl(value);
        const button = browser.document.querySelector('.sub-url');
        assert.equal(button.getAttribute('data-url'), value, 'quotes must remain part of the attribute value');
        assert.ok(!button.hasAttribute('onfocus'));
        const app = { ...data.config.apps[0], name: 'demo" data-injected="yes' };
        browser.document.getElementById('app').innerHTML = browser.context.page.renderAppRow(app, 'Android');
        assert.ok(!browser.document.querySelector('[data-injected]'));
        """,
        page_data,
    )


def test_legacy_catalog_platform_labels_remain_usable(page_data: dict[str, object]) -> None:
    run_page(
        """
        for (const app of data.config.apps) { delete app.platforms; delete app.recommended; }
        const browser = createBrowser(data.config, null, { info: data.info }); await browser.start();
        assert.ok(browser.document.querySelector('[data-import]'), 'older embedded catalogs must still render');
        """,
        page_data,
    )


@pytest.mark.parametrize("mode", ["missing", "rejected", "throws", "no_method"])
def test_copy_failure_shows_localized_selectable_text(page_data: dict[str, object], mode: str) -> None:
    page_data["mode"] = mode
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { storage: { 'meridian-lang': 'ru' }, info: data.info });
        if (data.mode === 'rejected') browser.context.navigator.clipboard = { writeText: () => Promise.reject(new Error('denied')) };
        if (data.mode === 'throws') browser.context.navigator.clipboard = { writeText() { throw new Error('denied'); } };
        if (data.mode === 'no_method') browser.context.navigator.clipboard = {};
        await browser.start();
        browser.context.copyToClipboard(data.config.subscription_url); await browser.flush();
        const fallback = browser.document.getElementById('copy-fallback');
        assert.ok(fallback, 'copy failure must offer selectable text');
        assert.match(fallback.textContent, /Не удалось скопировать/);
        const field = fallback.querySelector('textarea');
        assert.equal(field.value, data.config.subscription_url);
        assert.ok(field.hasAttribute('readonly')); assert.ok(field.selected);
        assert.ok(!browser.document.getElementById('toast').classList.contains('show'));
        """,
        page_data,
    )


def test_diagnostic_report_omits_device_data(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog);
        browser.load('selfcheck.js', ['buildReport']);
        const report = browser.context.page.buildReport([]);
        assert.ok(!report.includes('Browser:'), 'diagnostics must not retain device data');
        assert.ok(!report.includes(browser.context.navigator.userAgent));
        """,
        page_data,
    )


def test_diagnostic_copy_failure_uses_the_same_selectable_fallback(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        browser.fire('click', browser.document.querySelector('[data-action="selfcheck-run"]')); await browser.flush();
        const copy = browser.document.querySelector('[data-action="selfcheck-copy"]');
        assert.ok(copy, 'the completed self-check must offer a report');
        browser.fire('click', copy); await browser.flush();
        const field = browser.document.getElementById('copy-fallback');
        assert.ok(field, 'report copy failure must offer selectable text');
        assert.match(field.querySelector('textarea').value, /^Meridian connection self-check/);
        assert.ok(!field.querySelector('textarea').value.includes('198.51.100.1'));
        """,
        page_data,
    )


def test_raw_and_percent_deep_links_never_compute_unused_base64(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog);
        const url = 'https://198.51.100.1/sub/اشتراک';
        browser.context.btoa = () => { throw new Error('unused Base64'); };
        assert.equal(browser.context.page.buildDeepLink('happ://add/{url_raw}', url, 'demo'), 'happ://add/' + url);
        assert.equal(browser.context.page.buildDeepLink('stash://install-config?url={url}', url, 'demo'), 'stash://install-config?url=' + encodeURIComponent(url));
        """,
        page_data,
    )


def test_unicode_base64_deep_link_contains_an_ascii_url(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog);
        const url = 'https://198.51.100.1/sub/连接';
        const link = browser.context.page.buildDeepLink('throne://addsub/{url_b64}', url, 'demo');
        assert.equal(Buffer.from(link.split('/').at(-1), 'base64').toString('ascii'), new URL(url).href);
        """,
        page_data,
    )


def test_one_invalid_import_action_cannot_replace_the_page_with_an_error(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { userAgent: 'Windows', info: data.info });
        browser.context.btoa = () => { throw new Error('handler encoding'); };
        await browser.start();
        assert.ok(!browser.document.querySelector('.load-error'), 'an invalid handler must not break the whole page');
        assert.ok(browser.document.querySelector('[data-import]'));
        const choice = browser.document.getElementById('app-choice'); choice.value = 'Throne'; browser.fire('change', choice);
        assert.ok(!browser.document.querySelector('.load-error'));
        assert.ok(!browser.document.querySelector('[data-import]'));
        assert.ok(browser.document.querySelector('.sub-url'), 'the canonical manual URL must remain accessible');
        """,
        page_data,
    )


def test_recommended_app_has_three_localized_connect_steps(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        const steps = browser.document.querySelector('.connect-steps');
        assert.ok(steps, 'import must be followed by connection instructions');
        assert.equal(steps.querySelectorAll('li').length, 3);
        assert.match(steps.textContent, /VPN/); assert.match(steps.textContent, /server|profile/);
        for (const app of data.catalog.apps.filter(app => app.recommended)) {
          for (const lang of ['en', 'ru', 'fa', 'zh']) assert.ok(app.connect && app.connect[lang].length >= 3, app.name + ' lacks ' + lang + ' instructions');
        }
        """,
        page_data,
    )


def test_manual_import_has_selectable_url_and_the_chosen_apps_menu(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        const manual = browser.document.querySelector('.manual-import');
        assert.ok(manual, 'manual import must be beside the chosen app action');
        assert.equal(manual.querySelector('textarea').value, data.config.subscription_url);
        assert.ok(manual.querySelector('textarea').hasAttribute('readonly'));
        assert.ok(manual.querySelector('.manual-path').textContent.length > 5);
        assert.match(manual.textContent, /Import manually/);
        """,
        page_data,
    )


def test_platform_picker_drives_apps_and_resets_choice(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        let platform = browser.document.getElementById('platform-choice');
        assert.ok(platform, 'a detected platform needs a manual override'); assert.equal(platform.value, 'android');
        let app = browser.document.getElementById('app-choice'); app.value = 'FlClashX'; browser.fire('change', app);
        assert.match(browser.document.querySelector('[data-import]').textContent, /FlClashX/);
        platform = browser.document.getElementById('platform-choice'); platform.value = 'ios'; browser.fire('change', platform);
        app = browser.document.getElementById('app-choice'); assert.equal(app.value, 'Happ');
        const choices = app.querySelectorAll('option').map(option => option.value);
        assert.ok(choices.includes('ShadowRocket')); assert.ok(!choices.includes('FlClashX'));
        assert.ok(browser.document.querySelector('.app-row').getAttribute('href').includes('apps.apple.com'));
        browser.context.page.switchLang('fa');
        assert.equal(browser.document.getElementById('platform-choice').value, 'ios');
        """,
        page_data,
    )


def test_app_without_import_scheme_shows_manual_route_directly(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { userAgent: 'Windows', info: data.info }); await browser.start();
        const choice = browser.document.getElementById('app-choice'); assert.ok(choice, 'app choice is missing');
        choice.value = 'v2rayN'; browser.fire('change', choice);
        assert.ok(!browser.document.querySelector('[data-import]'));
        const manual = browser.document.querySelector('.manual-import');
        assert.ok(manual && manual.tagName !== 'DETAILS', 'v2rayN must show the manual URL directly');
        assert.equal(manual.querySelector('textarea').value, data.config.subscription_url);
        assert.match(manual.querySelector('.manual-path').textContent, /Subscription group/);
        """,
        page_data,
    )


def test_clash_desktop_steps_include_tun_while_mobile_steps_do_not(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { userAgent: 'Windows', info: data.info }); await browser.start();
        let choice = browser.document.getElementById('app-choice'); choice.value = 'FlClashX'; browser.fire('change', choice);
        assert.match(browser.document.querySelector('.connect-steps').textContent, /TUN/);
        assert.equal(browser.document.querySelector('.connect-steps').querySelectorAll('li').length, 4);
        const platform = browser.document.getElementById('platform-choice'); platform.value = 'android'; browser.fire('change', platform);
        choice = browser.document.getElementById('app-choice'); choice.value = 'FlClashX'; browser.fire('change', choice);
        assert.ok(!browser.document.querySelector('.connect-steps').textContent.includes('TUN'));
        """,
        page_data,
    )


def test_install_alternatives_are_labelled_and_platform_specific(page_data: dict[str, object]) -> None:
    run_page(
        """
        const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
        let routes = browser.document.querySelector('.install-routes');
        assert.ok(routes, 'a store must have its pinned direct-install alternative');
        assert.match(routes.textContent, /Official APK/);
        assert.ok(routes.querySelector('a').getAttribute('href').endsWith('/Happ.apk'));
        let platform = browser.document.getElementById('platform-choice'); platform.value = 'windows'; browser.fire('change', platform);
        const choice = browser.document.getElementById('app-choice'); choice.value = 'FlClashX'; browser.fire('change', choice);
        routes = browser.document.querySelector('.install-routes');
        assert.equal(routes.querySelectorAll('a').length, 2);
        assert.match(routes.textContent, /x64/); assert.match(routes.textContent, /ARM64/);
        assert.ok(routes.querySelectorAll('a').every(link => link.getAttribute('href').endsWith('.exe')));
        platform = browser.document.getElementById('platform-choice'); platform.value = 'ios'; browser.fire('change', platform);
        routes = browser.document.querySelector('.install-routes');
        assert.match(routes.textContent, /App Store.*Russia/);
        assert.ok(routes.querySelectorAll('a').every(link => link.getAttribute('href').includes('apps.apple.com')));
        """,
        page_data,
    )
