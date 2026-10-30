/* Behavioral scenarios for the shipped PWA, invoked by pytest without npm. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { createBrowser, response } = require('./support/pwa_browser.cjs');

const cases = {
  test_legacy_shell_without_data_paths_still_updates_its_worker: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { legacyShell: true, info: data.info });
    const registrations = [];
    browser.context.navigator.serviceWorker = {
      register: (url, options) => { registrations.push({ url, options }); return Promise.resolve(); },
    };
    await browser.start();
    assert.equal(registrations[0].url, '../pwa/sw.js');
    assert.equal(registrations[0].options.scope, '../');
    const config = browser.requests.find(request => request.url.endsWith('config.json'));
    assert.ok(config, 'old shells still need a config path');
    assert.equal(config.init.cache, 'no-store');
  },
  test_worker_only_intercepts_shared_assets: async function() {
    const handlers = {};
    const context = {
      URL, Promise,
      self: { location: new URL('https://198.51.100.1/connect/sub/~pwa/sw.js'),
        addEventListener: (name, callback) => { handlers[name] = callback; } },
      caches: { match: () => Promise.resolve({ cached: true }) },
    };
    vm.runInNewContext(fs.readFileSync('src/meridian/templates/pwa/sw.js', 'utf8'), context);
    for (const path of ['/sub/demo', '/sub/demo/config.json', '/sub/demo/manifest.webmanifest', '/sub/demo/info',
      '/sub/~pwa/pwa-v1/probe.bin', '/sub/~pwa/pwa-v1/support.html', '/private-console/']) {
      let intercepted = false;
      handlers.fetch({ request: { url: 'https://198.51.100.1/connect' + path, method: 'GET', cache: 'default' },
        respondWith: () => { intercepted = true; } });
      assert.equal(intercepted, false, path);
    }
    let shared;
    handlers.fetch({ request: { url: 'https://198.51.100.1/connect/sub/~pwa/pwa-v1/app.js', method: 'GET', cache: 'default' },
      respondWith: promise => { shared = promise; } });
    assert.ok((await shared).cached, 'versioned shared assets use the cache');
  },
  test_legacy_service_worker_releases_old_pages: async function() {
    const handlers = {}, deleted = [], navigated = [];
    let unregistered = false;
    const context = {
      URL, Promise,
      caches: {
        keys: () => Promise.resolve(['pwa-01234567', 'other-app', 'pwa-other-app']),
        delete: key => { deleted.push(key); return Promise.resolve(true); },
      },
      self: {
        location: new URL('https://198.51.100.1/connect/pwa/sw.js'),
        addEventListener: (name, callback) => { handlers[name] = callback; },
        clients: {
          claim: () => Promise.resolve(),
          matchAll: () => Promise.resolve([{ url: 'https://198.51.100.1/connect/550e8400-e29b-41d4-a716-446655440000/',
            navigate: url => { navigated.push(url); return Promise.resolve(); } }]),
        },
        registration: { unregister: () => { unregistered = true; return Promise.resolve(true); } },
      },
    };
    vm.runInNewContext(fs.readFileSync('src/meridian/templates/pwa/sw.js', 'utf8'), context);
    let completion;
    handlers.activate({ waitUntil: promise => { completion = promise; } });
    await completion;
    assert.equal(unregistered, true, 'old worker must unregister');
    assert.deepEqual(deleted, ['pwa-01234567'], 'only Meridian page caches belong to this worker');
    assert.equal(navigated.length, 1, 'old pages must reload to follow the UUID redirect');
  },
  test_preferred_store_route_names_its_destination: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { info: data.info });
    await browser.start();
    assert.equal(browser.document.querySelector('.app-row-get').textContent, 'Google Play');
    const platform = browser.document.getElementById('platform-choice');
    platform.value = 'ios'; browser.fire('change', platform);
    assert.equal(browser.document.querySelector('.app-row-get').textContent, 'App Store');
    browser.context.page.switchLang('fa');
    assert.equal(browser.document.querySelector('.app-row-get').textContent, 'اپ استور');
  },
  test_single_platform_labels_follow_persian: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { info: data.info });
    await browser.start();
    browser.context.page.switchLang('fa');
    const choice = browser.document.getElementById('app-choice');
    choice.value = 'v2rayNG'; browser.fire('change', choice);
    assert.match(browser.document.querySelector('.app-row-text').textContent, /اندروید/);
  },
  test_error_document_title_uses_the_saved_language: async function(data) {
    const browser = createBrowser(data.config, data.catalog, {
      storage: { 'meridian-lang': 'fa' }, fetch: () => Promise.reject(new Error('offline')),
    });
    browser.document.title = 'Connection Setup';
    await browser.start();
    assert.equal(browser.document.title, 'تنظیم اتصال');
  },
  test_account_formats_persian_numbers_and_calendar: async function(data) {
    data.info.user.expiresAt = '2030-01-01T12:00:00.000Z';
    const browser = createBrowser(data.config, data.catalog, { info: data.info });
    await browser.start(); browser.context.page.switchLang('fa');
    const text = browser.document.getElementById('account-panel').textContent;
    assert.ok(text.includes('۱۴۰۸'), 'expiry must use the Persian calendar');
    assert.ok(text.includes('۱٫۵'), 'traffic must use Persian digits and decimal separator');
    assert.ok(text.includes('۷ روز'), 'days left must use Intl numbers');
  },
  test_account_not_found_is_verified_and_disables_import: async function(data) {
    let missing = false;
    const browser = createBrowser(data.config, data.catalog, { fetch(url) {
      if (url.endsWith('/info')) return Promise.resolve(missing ? response({ isFound: false, statusCode: 404, message: 'Resource not found' }, 404) : response(data.info));
      return Promise.resolve(response(url.endsWith('config.json') ? data.config : data.catalog));
    } });
    await browser.start(); missing = true;
    browser.fire('click', browser.document.querySelector('[data-action="account-refresh"]'));
    await browser.flush();
    const panel = browser.document.getElementById('account-panel');
    assert.match(panel.textContent, /no longer available/);
    assert.ok(!panel.textContent.includes('unverified'));
    assert.equal(browser.document.querySelector('[data-import]').getAttribute('aria-disabled'), 'true');
  },
  test_account_on_another_origin_stays_hidden_and_unfetched: async function(data) {
    const config = { ...data.config, subscription_url: 'https://198.51.100.9/connect/sub/Other' };
    const browser = createBrowser(config, data.catalog, { info: data.info });
    await browser.start();
    assert.equal(browser.requests.filter(r => r.url.endsWith('/info')).length, 0, 'no cross-origin account fetch');
    assert.ok(browser.document.getElementById('account-panel').hidden, 'an unreadable account shows nothing');
  },
  test_account_loads_info_without_cache_and_refreshes_on_return: async function(data) {
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
  },
  test_suspended_account_explains_and_dims_imports: async function(data) {
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
  },
  test_account_failure_keeps_only_account_snapshot_and_marks_it_unverified: async function(data) {
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
  },
  test_account_unlimited_and_no_expiry_are_not_zero_or_a_sentinel_date: async function(data) {
    data.info.user.trafficLimitBytes = '0'; data.info.user.expiresAt = '2099-12-31T23:59:59.000Z';
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    const panel = browser.document.getElementById('account-panel'); assert.ok(panel, 'account panel is missing');
    assert.match(panel.textContent, /Unlimited/); assert.match(panel.textContent, /No expiry/);
    assert.ok(!panel.textContent.includes('2099'));
  },
  test_switching_from_persian_to_english_resets_direction_even_without_storage: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    browser.context.page.switchLang('fa');
    assert.equal(browser.document.documentElement.dir, 'rtl');
    browser.context.page.switchLang('en');
    assert.equal(browser.document.documentElement.lang, 'en');
    assert.equal(browser.document.documentElement.dir, 'ltr', 'English must restore LTR');
    browser.context.localStorage.setItem = () => { throw new Error('storage blocked'); };
    browser.context.page.switchLang('ru');
    assert.equal(browser.document.documentElement.lang, 'ru', 'a preference must work without localStorage');
  },
  test_load_error_uses_saved_language_before_config_arrives: async function(data) {
    const browser = createBrowser(data.config, data.catalog, {
      storage: { 'meridian-lang': 'fa' }, fetch: () => Promise.reject(new Error('offline'))
    });
    await browser.start();
    assert.equal(browser.document.documentElement.lang, 'fa');
    assert.equal(browser.document.documentElement.dir, 'rtl');
    assert.match(browser.document.getElementById('app').textContent, /بارگذاری/);
  },
  test_fixed_catalog_labels_and_accessible_actions_follow_language: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { storage: { 'meridian-lang': 'ru' }, info: data.info });
    await browser.start();
    assert.ok(!browser.document.getElementById('app').textContent.includes('Android, desktop'));
    assert.equal(browser.document.querySelector('.lang-bar').getAttribute('aria-label'), 'Язык');
    assert.equal(browser.document.querySelector('.install-dismiss').getAttribute('aria-label'), 'Закрыть');
  },
  test_support_page_honors_saved_language: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { storage: { 'meridian-lang': 'fa' }, language: 'en' });
    browser.document.body.innerHTML = '<h1 id="title"></h1><p id="status"></p>';
    browser.load('support.js');
    assert.equal(browser.document.documentElement.lang, 'fa');
    assert.equal(browser.document.documentElement.dir, 'rtl');
    assert.match(browser.document.getElementById('status').textContent, /صفحه/);
  },
  test_optional_catalog_never_blocks_config_or_resets_open_content: async function(data) {
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
    const copy = browser.document.querySelector('[data-ui-key="subscription-copy"]'); copy.focus();
    browser.document.getElementById('selfcheck-results').textContent = 'Saved self-check';
    release(response(data.catalog)); await browser.flush();
    assert.ok(browser.document.querySelector('[data-disclosure="manual-import"]').open);
    assert.equal(browser.document.getElementById('app-choice').value, 'FlClashX');
    assert.equal(browser.document.getElementById('platform-choice').value, 'android');
    assert.equal(browser.document.activeElement.getAttribute('data-ui-key'), copy.getAttribute('data-ui-key'));
    assert.equal(browser.document.getElementById('selfcheck-results').textContent, 'Saved self-check');
    assert.ok(browser.document.querySelector('.app-mark').getAttribute('src'), 'late catalog should supply icons');
  },
  test_catalog_timeout_ignores_late_answers_without_abort_support: async function(data) {
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
  },
  test_bad_optional_catalog_keeps_valid_embedded_apps: async function(data) {
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
  },
  test_unsupported_config_version_is_rejected_before_rendering: async function(data) {
    data.config.version = 2;
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    assert.ok(browser.document.querySelector('.load-error'), 'unsupported configuration must fail before rendering');
    assert.ok(!browser.document.querySelector('[data-import]'));
  },
  test_quoted_attributes_keep_user_text_literal: async function(data) {
    const browser = createBrowser(data.config, data.catalog);
    const value = data.quoted_url;
    browser.document.getElementById('app').innerHTML = browser.context.page.renderSubUrl(value);
    const button = browser.document.querySelector('[data-ui-key="subscription-copy"]');
    assert.equal(button.getAttribute('data-url'), value, 'quotes must remain part of the attribute value');
    assert.ok(!button.hasAttribute('onfocus'));
    const app = { ...data.config.apps[0], name: 'demo" data-injected="yes' };
    browser.document.getElementById('app').innerHTML = browser.context.page.renderAppRow(app, 'Android');
    assert.ok(!browser.document.querySelector('[data-injected]'));
  },
  test_legacy_catalog_platform_labels_remain_usable: async function(data) {
    for (const app of data.config.apps) { delete app.platforms; delete app.recommended; }
    const browser = createBrowser(data.config, null, { info: data.info }); await browser.start();
    assert.ok(browser.document.querySelector('[data-import]'), 'older embedded catalogs must still render');
  },
  test_copy_failure_shows_localized_selectable_text: async function(data) {
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
  },
  test_diagnostic_report_omits_device_data: async function(data) {
    const browser = createBrowser(data.config, data.catalog);
    browser.load('selfcheck.js', ['buildReport']);
    const report = browser.context.page.buildReport([]);
    assert.ok(!report.includes('Browser:'), 'diagnostics must not retain device data');
    assert.ok(!report.includes(browser.context.navigator.userAgent));
  },
  test_diagnostic_copy_failure_uses_the_same_selectable_fallback: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    browser.fire('click', browser.document.querySelector('[data-action="selfcheck-run"]')); await browser.flush();
    const copy = browser.document.querySelector('[data-action="selfcheck-copy"]');
    assert.ok(copy, 'the completed self-check must offer a report');
    browser.fire('click', copy); await browser.flush();
    const field = browser.document.getElementById('copy-fallback');
    assert.ok(field, 'report copy failure must offer selectable text');
    assert.match(field.querySelector('textarea').value, /^Meridian connection self-check/);
    assert.ok(!field.querySelector('textarea').value.includes('198.51.100.1'));
  },
  test_raw_and_percent_deep_links_never_compute_unused_base64: async function(data) {
    const browser = createBrowser(data.config, data.catalog);
    const url = 'https://198.51.100.1/sub/اشتراک';
    browser.context.btoa = () => { throw new Error('unused Base64'); };
    assert.equal(browser.context.page.buildDeepLink('happ://add/{url_raw}', url, 'demo'), 'happ://add/' + url);
    assert.equal(browser.context.page.buildDeepLink('stash://install-config?url={url}', url, 'demo'), 'stash://install-config?url=' + encodeURIComponent(url));
  },
  test_unicode_base64_deep_link_contains_an_ascii_url: async function(data) {
    const browser = createBrowser(data.config, data.catalog);
    const url = 'https://198.51.100.1/sub/连接';
    const link = browser.context.page.buildDeepLink('throne://addsub/{url_b64}', url, 'demo');
    assert.equal(Buffer.from(link.split('/').at(-1), 'base64').toString('ascii'), new URL(url).href);
  },
  test_one_invalid_import_action_cannot_replace_the_page_with_an_error: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { userAgent: 'Windows', info: data.info });
    browser.context.btoa = () => { throw new Error('handler encoding'); };
    await browser.start();
    assert.ok(!browser.document.querySelector('.load-error'), 'an invalid handler must not break the whole page');
    assert.ok(browser.document.querySelector('[data-import]'));
    const choice = browser.document.getElementById('app-choice'); choice.value = 'Throne'; browser.fire('change', choice);
    assert.ok(!browser.document.querySelector('.load-error'));
    assert.ok(!browser.document.querySelector('[data-import]'));
    assert.ok(browser.document.querySelector('[data-ui-key="subscription-copy"]'), 'the canonical manual URL must remain accessible');
  },
  test_recommended_app_has_three_localized_connect_steps: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    const steps = browser.document.querySelector('.connect-steps');
    assert.ok(steps, 'import must be followed by connection instructions');
    assert.equal(steps.querySelectorAll('li').length, 3);
    assert.match(steps.textContent, /VPN/); assert.match(steps.textContent, /server|profile/);
    for (const app of data.catalog.apps.filter(app => app.recommended)) {
      for (const lang of ['en', 'ru', 'fa', 'zh']) assert.ok(app.connect && app.connect[lang].length >= 3, app.name + ' lacks ' + lang + ' instructions');
    }
  },
  test_manual_import_has_selectable_url_and_the_chosen_apps_menu: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    const manual = browser.document.querySelector('.manual-import');
    assert.ok(manual, 'manual import must be beside the chosen app action');
    assert.equal(manual.querySelector('textarea').value, data.config.subscription_url);
    assert.ok(manual.querySelector('textarea').hasAttribute('readonly'));
    assert.ok(manual.querySelector('.manual-path').textContent.length > 5);
    assert.match(manual.textContent, /Import manually/);
  },
  test_platform_picker_drives_apps_and_resets_choice: async function(data) {
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
  },
  test_app_without_import_scheme_shows_manual_route_directly: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { userAgent: 'Windows', info: data.info }); await browser.start();
    const choice = browser.document.getElementById('app-choice'); assert.ok(choice, 'app choice is missing');
    choice.value = 'v2rayN'; browser.fire('change', choice);
    assert.ok(!browser.document.querySelector('[data-import]'));
    const manual = browser.document.querySelector('.manual-import');
    assert.ok(manual && manual.tagName !== 'DETAILS', 'v2rayN must show the manual URL directly');
    assert.equal(manual.querySelector('textarea').value, data.config.subscription_url);
    assert.match(manual.querySelector('.manual-path').textContent, /Subscription group/);
  },
  test_clash_desktop_steps_include_tun_while_mobile_steps_do_not: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { userAgent: 'Windows', info: data.info }); await browser.start();
    let choice = browser.document.getElementById('app-choice'); choice.value = 'FlClashX'; browser.fire('change', choice);
    assert.match(browser.document.querySelector('.connect-steps').textContent, /TUN/);
    assert.equal(browser.document.querySelector('.connect-steps').querySelectorAll('li').length, 4);
    const platform = browser.document.getElementById('platform-choice'); platform.value = 'android'; browser.fire('change', platform);
    choice = browser.document.getElementById('app-choice'); choice.value = 'FlClashX'; browser.fire('change', choice);
    assert.ok(!browser.document.querySelector('.connect-steps').textContent.includes('TUN'));
  },
  test_install_alternatives_are_labelled_and_platform_specific: async function(data) {
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
  },
  test_stash_import_is_percent_encoded_and_keeps_additional_status: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { userAgent: 'iPhone', info: data.info }); await browser.start();
    const choice = browser.document.getElementById('app-choice');
    const option = choice.querySelectorAll('option').find(option => option.value === 'Stash');
    assert.ok(option); assert.equal(option.parentNode.getAttribute('label'), 'More apps');
    choice.value = 'Stash'; browser.fire('change', choice);
    assert.equal(browser.document.querySelector('[data-import]').getAttribute('href'), 'stash://install-config?url=' + encodeURIComponent(data.config.subscription_url));
    assert.ok(browser.document.querySelector('.app-mark').getAttribute('src').startsWith('data:image/svg+xml;base64,'));
  },
  test_long_connection_lists_reveal_locally_in_subscription_order: async function(data) {
    data.config.protocols = Array.from({ length: 9 }, (_, i) => ({
      key: 'reality', label: 'Connection ' + i, url: 'vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443#Connection-' + i, qr_b64: '', recommended: i === 0
    }));
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    assert.equal(browser.document.querySelectorAll('.conn').length, 3, 'initial rendering should stay small');
    const button = browser.document.querySelector('[data-action="show-connections"]');
    assert.ok(button); assert.match(button.textContent, /Show all 9/);
    assert.equal(button.getAttribute('aria-expanded'), 'false');
    assert.ok(browser.document.getElementById(button.getAttribute('aria-controls')));
    const requests = browser.requests.length;
    browser.fire('click', button);
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.equal(browser.document.querySelectorAll('.conn').length, 9);
    assert.deepEqual(browser.document.querySelectorAll('.conn-name').map(el => el.textContent), data.config.protocols.map(p => p.label));
    assert.equal(browser.requests.length, requests, 'revealing connections must not fetch data');
    browser.context.page.switchLang('ru');
    assert.equal(browser.document.querySelectorAll('.conn').length, 9, 'language changes keep the expanded list');
  },
  test_app_help_stays_collapsed_and_reads_plainly_in_every_language: async function(data) {
    const browser = createBrowser(data.config, data.catalog, { info: data.info }); await browser.start();
    const keys = ['ping.red', 'sub.refresh', 'sub.launch'];
    const help = key => browser.document.querySelectorAll('li').find(item => item.getAttribute('data-t') === key);
    const panel = help('ping.red').closest('details');
    assert.ok(panel && !panel.hasAttribute('open'), 'help stays closed so import buttons keep their place');
    assert.ok(panel.parentNode.getAttribute('class').includes('panels'), 'help sits with the other secondary panels');
    assert.match(help('ping.red').textContent, /does not mean the connection is broken/);
    assert.match(help('sub.refresh').textContent, /regularly/);
    assert.match(help('sub.launch').textContent, /update subscription on launch/);
    const english = keys.map(key => help(key).textContent);
    const requests = browser.requests.length;
    for (const [lang, pattern] of [['ru', /[а-я]/i], ['fa', /[\u0600-\u06ff]/], ['zh', /[\u4e00-\u9fff]/]]) {
      browser.context.page.switchLang(lang);
      keys.forEach((key, index) => {
        const shown = help(key).textContent;
        assert.match(shown, pattern, lang + ' ' + key);
        assert.notEqual(shown, english[index], lang + ' ' + key + ' must be translated');
      });
    }
    assert.equal(browser.requests.length, requests, 'help text never fetches anything');
  },
  test_suspended_account_also_dims_individual_imports: async function(data) {
    data.info.user.userStatus = 'DISABLED'; data.info.user.isActive = false;
    data.config.protocols = [{ key: 'reality', label: 'Connection', url: 'vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443', qr_b64: '', recommended: true }];
    const browser = createBrowser(data.config, data.catalog, { userAgent: 'iPhone', info: data.info }); await browser.start();
    const link = browser.document.querySelector('.conn').querySelector('a');
    assert.equal(link.getAttribute('aria-disabled'), 'true');
    assert.ok(browser.fire('click', link).defaultPrevented);
    assert.equal(browser.context.location.protocol, 'https:', 'a suspended action cannot launch a handler');
  },
};

const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const scenario = cases[process.argv[2]];
assert.equal(typeof scenario, 'function', 'unknown PWA scenario');
scenario(data).catch(error => { console.error(error); process.exitCode = 1; });
