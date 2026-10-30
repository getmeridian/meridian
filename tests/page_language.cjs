/* Page language order and early errors at the shared Node browser boundary. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createBrowser, response } = require('./support/pwa_browser.cjs');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const page = process.argv[2];

async function run() {
  let rejectConfig;
  const support = page !== 'connection';
  const legacy = page.startsWith('legacy-');
  const noContact = page === 'legacy-empty';
  const url = legacy ? 'https://198.51.100.1/connect/' +
    (noContact ? 'pwa/support.html#demo' : 'sub/~pwa/pwa-test/support.html#demo')
    : 'https://198.51.100.1/connect/sub/demo' + (support ? '/support.html' : '');
  const browser = createBrowser(data.config, data.catalog, {
    pageLanguage: data.page_language,
    storage: data.saved ? { 'meridian-lang': data.saved } : {},
    language: data.browser, storageUnavailable: data.storage_unavailable,
    url,
    fetch(url) {
      if (url.endsWith('config.json')) return new Promise((resolve, reject) => { rejectConfig = reject; });
      return Promise.resolve(response(data.catalog));
    },
  });
  const root = browser.document.documentElement;
  assert.equal(root.lang, data.expected, 'language must be chosen before a config request');
  assert.equal(root.dir, data.expected === 'fa' ? 'rtl' : 'ltr');
  assert.equal(browser.requests.length, 0, 'startup language must not make a request');
  if (support) {
    browser.document.body.innerHTML = '<h1 id="title"></h1><p id="status"></p>' +
      '<div id="owner-contact"' + (noContact ? '' : ' data-owner-text="Help"') + '></div><a id="open-page" hidden></a>' +
      '<div class="lang-bar"><button class="lang-btn" data-lang="en">EN</button><button class="lang-btn" data-lang="fa">FA</button></div>';
    if (!legacy) root.setAttribute('data-page', '/connect/sub/demo');
    browser.load('contact.js');
    browser.load('support.js');
    assert.equal(root.lang, data.expected);
    assert.equal(root.dir, data.expected === 'fa' ? 'rtl' : 'ltr');
    const titles = { en: 'Connection Setup', ru: 'Настройка подключения', fa: 'تنظیم اتصال', zh: '连接设置' };
    assert.equal(browser.document.getElementById('title').textContent, titles[data.expected]);
    assert.equal(browser.document.getElementById('open-page').getAttribute('href'), '/connect/sub/demo');
    assert.equal(browser.requests.length, 0, 'support must not fetch person or config data');
    if (legacy) {
      assert.equal(browser.context.location.replaced, '/connect/sub/demo' + (noContact ? '' : '/support.html'));
      return;
    }
    const next = data.expected === 'en' ? 'fa' : 'en';
    browser.document.querySelector('[data-lang="' + next + '"]').dispatchEvent({ type: 'click' });
    assert.equal(root.lang, next, 'the support page still lets the person choose');
    assert.equal(root.dir, next === 'fa' ? 'rtl' : 'ltr');
    if (!data.storage_unavailable) assert.equal(browser.storage['meridian-lang'], next);
    return;
  }
  await browser.start();
  await browser.advance(10000);
  const dict = browser.context.page.T[data.expected];
  assert.equal(browser.document.querySelector('.load-slow').textContent, dict['error.slow'] || 'Taking longer than expected…');
  rejectConfig(new Error('config unavailable'));
  await browser.flush();
  assert.ok(browser.document.querySelector('.load-error').textContent.includes(
    dict['error.msg'] || 'Could not load configuration. Please reload the page.'));
  assert.equal(root.lang, data.expected);
  browser.context.page.switchLang(data.expected === 'en' ? 'fa' : 'en');
  assert.equal(root.lang, data.expected === 'en' ? 'fa' : 'en', 'the person can still switch');
  assert.equal(root.dir, root.lang === 'fa' ? 'rtl' : 'ltr');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
