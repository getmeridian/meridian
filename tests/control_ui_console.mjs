/* Execute the shipped scripts with a DOM and local API doubles. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { deliveryReply, runDeliveryScenario } from './control_ui_delivery.mjs';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en', dataText = 'null'] = process.argv.slice(2);
const data = JSON.parse(dataText);
const dom = new JSDOM('<!doctype html><body><div id="view"></div></body>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const events = [], writes = [], delayed = {};
let fakeNow = Date.now(), timerId = 0, hidden = false, failPolls = false;
let localeReady = false;
const timers = new Map(), rounds = new Map();
const virtual = mode.startsWith('live-') || mode.startsWith('deadline-') || mode.startsWith('commands-');
if (virtual) {
  w.Date.now = () => fakeNow;
  w.setTimeout = (fn, delay = 0) => { const id = ++timerId; timers.set(id, { fn, at: fakeNow + delay }); return id; };
  w.clearTimeout = (id) => timers.delete(id);
  Object.defineProperty(w.document, 'hidden', { get: () => hidden });
}
const server = {
  node_id: 'main', role: 'exit', region: '', live: true,
  last_seen_at: new Date().toISOString(), agent_version: '0.1.0', revision: 2, applied_revision: 1,
  in_sync: false, failed: ['core-unit'], errors: ['core-unit: unit exited'], traffic_bytes: 1024,
  runtime_available: true,
  runtime: { observed_at: new Date().toISOString(),
    health: { status: 'unhealthy', uptime_seconds: 7200, load1_milli: 1250,
      memory_available_bytes: 2 * 1024 ** 3, disk_free_bytes: 5 * 1024 ** 3,
      checks: [{ name: 'stats', status: 'fail', message: 'last collected 120 s ago' },
        { name: 'xray.api', status: 'fail', message: 'core API is not ready' },
        { name: 'xray.uptime', status: 'pass', message: 'up 42s' }] },
    versions: { agent: '0.1.0', xray: '26.7.28' } },
};
function reply(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(body)) };
}
w.fetch = (url, options) => {
  options = options || {};
  events.push({ method: options.method, url, headers: options.headers, at: fakeNow });
  if (mode.startsWith('delivery-')) return Promise.resolve(reply(deliveryReply(url, options, data, writes)));
  if (mode === 'owner-settings') {
    return Promise.resolve(reply(url === 'api/settings' ? { profile_title: 'Meridian', owner_contact: data.contact } : { sessions: [] }));
  }
  if (mode === 'metadata') {
    if (options.method !== 'GET') {
      const change = JSON.parse(options.body);
      writes.push(change);
      Object.assign(data.user, change);
    }
    if (url === 'api/users/sample') return Promise.resolve(reply(data.user));
    if (url === 'ui/api/people/sample/share') return Promise.resolve(reply({ declared: false }));
  }
  if (mode.startsWith('commands-')) {
    const node = url.split('/')[2];
    const commandId = url.split('/').at(-1);
    if (node === 'node-a') return Promise.resolve(reply({ node_id: node, command: { command_id: commandId },
      status: 'succeeded', result: { status: 'succeeded', effect: data.effect || null, error: null } }));
    if (data.errorStatus) return Promise.resolve(reply({ error: { code: 'http_' + data.errorStatus,
      message: '<img src=x onerror="window.pwned=1"> cannot read the command' } }, data.errorStatus));
    return Promise.resolve(reply({ node_id: node, command: { command_id: commandId }, status: 'pending', result: null }));
  }
  if (mode.startsWith('expiry-')) {
    const user = { username: 'sample', status: 'active', created_at: '2026-10-29T00:00:00Z',
      expire_at: '2031-12-31T00:00:00Z', traffic_limit_bytes: 0, traffic_limit_strategy: 'month' };
    if (options.method !== 'GET') {
      writes.push(JSON.parse(options.body));
      return Promise.resolve(reply(user));
    }
    if (url === 'api/users') return Promise.resolve(reply({ users: [] }));
    if (url === 'api/users/sample') return Promise.resolve(reply(user));
    if (url === 'ui/api/people/sample/share') return Promise.resolve(reply({ declared: false }));
  }
  if (mode === 'phone-app') {
    if (url === 'auth/state') return Promise.resolve(reply({ configured: true, owner_exists: true, signed_in: true }));
    if (url === 'api/users') return Promise.resolve(reply({ users: [] }));
    if (url === 'ui/api/fleet') return Promise.resolve(reply({ servers: [], display_names: {}, declared_users: [] }));
  }
  if (mode.startsWith('locale-')) {
    const code = url.split('/').at(-1).split('.')[0];
    if (mode === 'locale-switch' && code === 'en' && !data.englishReady) return Promise.reject(new Error('English unavailable'));
    if (mode === 'locale-fallback' && code === 'en') return new Promise(resolve => { delayed.english = resolve; });
    if (!localeReady) return Promise.reject(new Error('catalog unavailable'));
    const content = JSON.parse(fs.readFileSync(path.join(ui, 'locales', code + '.json')));
    return Promise.resolve({ ...reply(content), json: () => Promise.resolve(content) });
  }
  if (mode.startsWith('token-')) {
    if (url === 'api/tokens' && options.method === 'GET') return Promise.resolve(reply({ tokens: [] }));
    if (url === 'api/tokens' && options.method === 'POST') {
      writes.push(JSON.parse(options.body));
      if (mode === 'token-uncertain') return Promise.reject(new Error('reply lost'));
      if (mode === 'token-missing-secret') return Promise.resolve(reply(null));
      return new Promise(resolve => { delayed.issue = resolve; });
    }
  }
  if (mode.startsWith('deadline-')) {
    delayed.signal = options.signal;
    return mode === 'deadline-fetch' ? new Promise(() => {}) : Promise.resolve({
      ...reply(null), text: () => new Promise(() => {}),
    });
  }
  if (mode.startsWith('live-')) {
    const round = (rounds.get(url) || 0) + 1;
    rounds.set(url, round);
    if (mode === 'live-race') return new Promise((resolve, reject) => { delayed[url + ':' + round] = { resolve, reject }; });
    if (failPolls) return Promise.resolve(reply({ error: { code: 'network' } }, 503));
    if (mode === 'live-fleet-failure' && url === 'ui/api/fleet') return Promise.resolve(reply({ error: { code: 'network' } }, 503));
    if (url === 'api/users') return Promise.resolve(reply({ users: [{ username: 'sample', status: 'active',
      used_traffic_bytes: round * 1024, traffic_limit_bytes: 0, expire_at: null, online_at: null }] }));
    if (url === 'ui/api/fleet') return Promise.resolve(reply({ profile_title: 'Test fleet', display_names: {},
      declared_users: [], servers: [{ ...server, traffic_bytes: round * 1024 }] }));
  }
  if (url === 'ui/api/fleet') return Promise.resolve(reply({ servers: data?.servers || [server],
    server_usage: data?.server_usage || [], server_attention: data?.server_attention || [] }));
  throw new Error('Unexpected request: ' + url);
};
for (const script of ['core.js', 'drafts.js', 'qr-export.js', 'people.js', 'people-list.js', 'expiry.js', 'person-connections.js', 'fleet.js']) {
  w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
}
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale,
  langs: ['en', 'ru', 'fa', 'zh'], names: { en: 'English', ru: 'Русский', fa: 'فارسی', zh: '中文' } };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = (text) => events.push({ toast: text });
M.fleetHistory = () => null;
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const view = w.document.getElementById('view');
const settle = () => new Promise(setImmediate);
const report = {};
if (mode.startsWith('delivery-')) await runDeliveryScenario({ w, M, ui, mode, locale, data, view, report, events, writes, settle });
async function advance(ms) {
  const target = fakeNow + ms;
  while (true) {
    const due = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    fakeNow = due[1].at;
    timers.delete(due[0]);
    due[1].fn();
    await settle();
  }
  fakeNow = target;
  await settle();
}
function visibility(value) {
  hidden = value;
  w.document.dispatchEvent(new w.Event('visibilitychange'));
}

if (mode.startsWith('commands-')) {
  w.eval(fs.readFileSync(path.join(ui, 'commands.js'), 'utf8'));
  const a = { node_id: 'node-a', command: { command_id: '00000000-0000-4000-8000-000000000001' },
    status: data.initialA || 'pending', result: data.initialA === 'succeeded'
      ? { status: 'succeeded', effect: data.effect || null, error: null } : null };
  const b = { node_id: 'node-b', command: { command_id: '00000000-0000-4000-8000-000000000002' }, status: 'pending', result: null };
  const labels = () => Object.fromEntries([...w.document.querySelectorAll('dialog section.stack-sm')]
    .map(section => [section.querySelector('bdi').textContent, section.querySelector('p').textContent]));
  const reads = () => events.filter(event => event.url?.startsWith('api/nodes/'));
  M.showCommandResults([a, b]);
  await settle();
  report.first = labels();
  report.firstReads = reads().map(event => event.url);
  await advance(data.advance || 1000);
  report.last = labels();
  report.text = w.document.querySelector('dialog').textContent;
  report.beforeStop = reads().length;
  await advance(30000);
  report.afterStop = reads().length;
  report.images = w.document.querySelectorAll('img').length;
  report.pwned = !!w.pwned;
}

if (mode === 'servers') {
  M.renderServers(view);
  await settle();
  report.text = view.textContent;
  report.badges = [...view.querySelectorAll('.server-badges .pill')].map(el => el.textContent);
  report.images = view.querySelectorAll('img').length;
  report.pwned = !!w.pwned;
  report.requests = events.filter(event => event.method);
}

if (mode === 'quota') {
  const fields = M.limitFields(data.user || null);
  const form = M.h('form', { novalidate: true }, fields.nodes);
  view.appendChild(form);
  const changes = [];
  form.addEventListener('submit', (event) => { event.preventDefault(); changes.push(fields.value()); });
  await settle();
  if (fields.mode) {
    fields.mode.value = data.mode;
    fields.mode.dispatchEvent(new w.Event('change'));
  }
  if (fields.unit) {
    fields.unit.value = data.unit;
    fields.unit.dispatchEvent(new w.Event('change'));
  }
  fields.gb.value = data.amount;
  fields.gb.dispatchEvent(new w.Event('input'));
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  report.change = changes[0] || null;
  report.error = fields.gb.validationMessage || '';
  report.invalid = fields.gb.getAttribute('aria-invalid') === 'true';
  report.disabled = fields.gb.disabled;
  report.units = fields.unit ? [...fields.unit.options].map(option => option.value) : [];
}

if (mode.startsWith('expiry-')) {
  M.personHistory = M.devicesCard = () => null;
  M.go = () => {};
  let cleanup;
  if (mode === 'expiry-add') {
    cleanup = M.renderPeople(view);
    await settle();
    view.querySelector('.btn-primary').click();
    w.document.querySelector('dialog input[type="text"]').value = 'sample';
  } else {
    w.eval(fs.readFileSync(path.join(ui, 'person.js'), 'utf8'));
    M.renderPerson(view, 'sample');
  }
  await settle();
  const expiry = w.document.querySelector('input[type="date"]');
  expiry.value = data.date;
  expiry.dispatchEvent(new w.Event('input'));
  if ('time' in data) {
    const time = w.document.querySelector('input[type="time"]');
    time.value = data.time;
    time.dispatchEvent(new w.Event('input'));
  }
  if (!data.date) {
    const never = w.document.querySelector('.expiry-fields input[type="checkbox"]');
    never.checked = true;
    never.dispatchEvent(new w.Event('change'));
  }
  expiry.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  report.writes = writes;
  report.error = expiry.validationMessage;
  report.invalid = expiry.getAttribute('aria-invalid') === 'true';
  if (typeof cleanup === 'function') cleanup();
}

if (mode === 'metadata') {
  M.personHistory = M.devicesCard = () => null;
  w.eval(fs.readFileSync(path.join(ui, 'person.js'), 'utf8'));
  M.renderPerson(view, 'sample');
  await settle();
  const note = view.querySelector('textarea[name="note"]');
  const tag = view.querySelector('input[name="tag"]');
  report.initialNote = note?.value;
  report.initialTag = tag?.value;
  report.text = view.textContent;
  report.injected = !!view.querySelector('img[src="x"]') || !!w.pwned;
  if (note && tag) {
    if ('note' in data.change) note.value = data.change.note;
    if ('tag' in data.change) tag.value = data.change.tag;
    note.dispatchEvent(new w.Event('input'));
    tag.dispatchEvent(new w.Event('input'));
    note.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    await settle();
    report.error = note.validationMessage || tag.validationMessage;
  }
  report.writes = writes;
}

if (mode === 'owner-page') {
  const { createBrowser } = require(path.resolve('tests/support/pwa_browser.cjs'));
  const browser = createBrowser(data.config, data.catalog, { language: locale });
  if (fs.existsSync('src/meridian/templates/pwa/contact.js')) browser.load('contact.js');
  await browser.start();
  const holder = browser.document.getElementById('owner-contact');
  const link = holder?.querySelector('a');
  report.text = holder?.textContent || '';
  report.contactLink = link?.getAttribute('href') || '';
  report.rel = link?.getAttribute('rel') || '';
  report.target = link?.getAttribute('target') || '';
  report.dir = browser.document.documentElement.dir;
  report.injected = !!holder?.querySelector('img') || !!browser.context.pwned;
  report.externalRequests = browser.requests.filter(request => new URL(request.url, browser.context.location.href).origin !== browser.context.location.origin);
}

if (mode === 'owner-support') {
  const pageDir = path.resolve(ui, '../../templates/pwa');
  const support = new JSDOM(fs.readFileSync(path.join(pageDir, 'support.html'), 'utf8'), {
    url: 'https://198.51.100.1/connect/sub/sampleShortUuid00/support.html', runScripts: 'outside-only',
  });
  const sw = support.window;
  sw.localStorage.setItem('meridian-lang', locale);
  sw.document.documentElement.setAttribute('data-page', '/connect/sub/sampleShortUuid00');
  sw.eval(fs.readFileSync(path.join(pageDir, 'language.js'), 'utf8'));
  const holder = sw.document.getElementById('owner-contact');
  if (holder) {
    holder.setAttribute('data-owner-text', data.contact.text);
    holder.setAttribute('data-owner-link', data.contact.link);
  }
  const requested = [];
  sw.fetch = url => { requested.push(url); return Promise.reject(new Error('No requests expected')); };
  if (fs.existsSync(path.join(pageDir, 'contact.js'))) sw.eval(fs.readFileSync(path.join(pageDir, 'contact.js'), 'utf8'));
  sw.eval(fs.readFileSync(path.join(pageDir, 'support.js'), 'utf8'));
  await settle();
  const link = holder?.querySelector('a');
  report.text = holder?.textContent || '';
  report.contactLink = link?.getAttribute('href') || '';
  report.rel = link?.getAttribute('rel') || '';
  report.target = link?.getAttribute('target') || '';
  report.pageLink = sw.document.getElementById('open-page')?.getAttribute('href') || '';
  report.dir = sw.document.documentElement.dir;
  report.injected = !!holder?.querySelector('img') || !!sw.pwned;
  report.externalRequests = requested;
  sw.close();
}

if (mode === 'owner-settings') {
  M.privacySection = M.noticeSection = M.languageSection = M.formatPreviewSection = M.cliHint = () => M.h('section');
  M.i18n = { ...M.i18n, langs: [locale], names: { [locale]: locale }, lang: locale };
  M.state.auth = { passkeys: { reason: 'no_domain' } };
  w.eval(fs.readFileSync(path.join(ui, 'settings.js'), 'utf8'));
  M.renderSettings(view);
  await settle();
  const fleet = view.querySelector('.card');
  report.text = fleet.textContent;
  report.controls = fleet.querySelectorAll('input,button,select,textarea,form').length;
  report.injected = !!fleet.querySelector('img[src="x"]') || !!w.pwned;
}

if (mode.startsWith('live-')) {
  const interval = data.screen === 'servers' ? 10000 : 30000;
  if (mode === 'live-fleet-failure') M.state.fleet = data.fleet;
  const cleanup = data.screen === 'servers' ? M.renderServers(view) : M.renderPeople(view);
  await settle();
  const fleetCalls = () => events.filter(event => event.url === 'ui/api/fleet');
  const liveData = () => (view.querySelector('.live-data') || view).textContent;
  if (mode === 'live-fleet-failure') {
    report.names = [...view.querySelectorAll('.name')].map(el => el.textContent);
    report.declared = view.querySelectorAll('.tag').length;
    report.initial = liveData();
    await advance(interval);
    report.afterPoll = liveData();
    report.fleet = M.state.fleet;
    failPolls = true;
    await advance(interval);
    report.afterUserFailure = liveData();
    report.stale = view.classList.contains('view-stale');
    report.retryVisible = !!view.querySelector('.live-retry') && !view.querySelector('.live-retry').hidden;
  }
  if (mode === 'live-cycle') {
    await advance(interval);
    report.afterPoll = fleetCalls().length;
    visibility(true);
    await advance(interval * 2);
    report.whileHidden = fleetCalls().length;
    visibility(false);
    await settle();
    report.afterReturn = fleetCalls().length;
    report.updated = view.querySelector('.live-updated')?.textContent || '';
    report.passive = fleetCalls().map(call => call.headers['X-Meridian-Passive'] === '1');
    report.cleanup = typeof cleanup === 'function';
    if (report.cleanup) cleanup();
    await advance(interval * 2);
    report.afterStop = fleetCalls().length;
  }
  if (mode === 'live-failure') {
    report.beforeFailure = liveData();
    failPolls = true;
    await advance(interval);
    report.afterFailure = liveData();
    report.stale = view.classList.contains('view-stale');
    const retry = view.querySelector('.live-retry');
    report.retryVisible = !!retry && !retry.hidden;
    report.staleLabel = view.querySelector('.live-trouble')?.textContent || '';
    report.updated = view.querySelector('.live-updated')?.textContent || '';
    failPolls = false;
    retry?.click();
    await settle();
    report.recovered = !view.classList.contains('view-stale') && liveData() !== report.beforeFailure;
    report.retriedActively = fleetCalls().at(-1).headers['X-Meridian-Passive'] !== '1';
  }
  if (mode === 'live-backoff') {
    failPolls = true;
    await advance(630000);
    const calls = fleetCalls();
    report.gaps = calls.slice(1).map((call, index) => call.at - calls[index].at);
  }
  if (mode === 'live-race') {
    visibility(true);
    visibility(false);
    await settle();
    for (const url of ['api/users', 'ui/api/fleet']) {
      const newer = delayed[url + ':2'];
      if (newer) newer.resolve(reply(url === 'api/users' ? { users: [{ username: 'new', status: 'active' }] }
        : { profile_title: 'New fleet', servers: [{ ...server, node_id: 'new' }] }));
    }
    await settle();
    report.afterNew = liveData();
    for (const url of ['api/users', 'ui/api/fleet']) delayed[url + ':1']?.reject(new Error('old reply failed'));
    await settle();
    report.afterOld = liveData();
    report.stale = view.classList.contains('view-stale');
    report.fleetTitle = M.state.fleet?.profile_title || '';
  }
  if (typeof cleanup === 'function') cleanup();
}

if (mode.startsWith('deadline-')) {
  let error;
  M.api('POST', 'api/tokens', { name: 'sample', scope: 'read' }).catch(value => { error = value; });
  await settle();
  await advance(15000);
  report.code = error?.code || null;
  report.uncertain = !!error?.uncertain;
  report.aborted = !!delayed.signal?.aborted;
}

if (mode.startsWith('token-')) {
  M.renderTokens(view);
  await settle();
  view.querySelector('.btn-primary').click();
  const form = w.document.querySelector('dialog form');
  form.querySelector('input').value = 'sample';
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  if (mode === 'token-pending') {
    const dialog = w.document.querySelector('dialog');
    report.closeDisabled = dialog.querySelector('.dialog-head button').disabled;
    dialog.dispatchEvent(new w.Event('cancel', { cancelable: true }));
    dialog.querySelector('.dialog-head button').click();
    form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    await settle();
    report.openDuringWrite = dialog.isConnected && dialog.open;
    report.issuedSecret = 'mrd_0123456789ab_test-only-secret-not-a-credential';
    delayed.issue(reply({ id: '0123456789ab', name: 'sample', scope: 'read', token: report.issuedSecret }));
    await settle();
    report.resultHasForm = !!w.document.querySelector('dialog form');
    report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
    report.secretInUrl = w.location.href.includes(report.issuedSecret);
  }
  report.secret = w.document.querySelector('.secret-token')?.textContent || '';
  report.dialog = w.document.querySelector('dialog')?.textContent || '';
  report.listReads = events.filter(event => event.url === 'api/tokens' && event.method === 'GET').length;
  report.writes = writes.length;
}

if (mode.startsWith('locale-')) {
  w.eval(fs.readFileSync(path.join(ui, 'i18n.js'), 'utf8'));
  if (mode === 'locale-switch') {
    localeReady = true;
    await M.i18n.load(data.previous);
    report.before = { label: M.t('nav.people'), lang: M.i18n.lang,
      rootLang: w.document.documentElement.lang, dir: w.document.documentElement.dir };
    localeReady = false;
  }
  let loaded = false;
  M.i18n.load(locale).then(() => { loaded = true; });
  await settle();
  if (mode === 'locale-fallback') {
    const english = JSON.parse(fs.readFileSync(path.join(ui, 'locales/en.json')));
    delayed.english({ ...reply(english), json: () => Promise.resolve(english) });
    await settle();
  } else {
    report.notice = w.document.querySelector('.i18n-notice')?.textContent || '';
    if (mode === 'locale-switch') {
      report.after = { label: M.t('nav.people'), lang: M.i18n.lang,
        rootLang: w.document.documentElement.lang, dir: w.document.documentElement.dir };
      report.switchResolved = loaded;
      data.englishReady = true;
    }
    localeReady = true;
    w.document.querySelector('.i18n-notice button')?.click();
    await settle();
    report.recovered = loaded;
    report.noticeAfter = w.document.querySelector('.i18n-notice')?.textContent || '';
  }
  report.label = M.t('nav.people');
  report.lang = M.i18n.lang;
  report.dir = w.document.documentElement.dir;
}

if (mode.startsWith('phone-')) {
  const style = w.document.createElement('style');
  // jsdom has no pointer devices; apply the coarse-pointer branch for this style-contract check.
  style.textContent = ['styles.css', 'screens.css'].map(file => fs.readFileSync(path.join(ui, file), 'utf8'))
    .join('\n').replace(/@media\s*\(pointer:\s*coarse\)/g, '@media all');
  w.document.head.appendChild(style);
  if (mode === 'phone-app') {
    const app = M.h('div', { id: 'app', class: 'boot' });
    w.document.body.appendChild(app);
    M.i18n.load = () => Promise.resolve();
    M.takeSetupToken = () => '';
    w.eval(fs.readFileSync(path.join(ui, 'app.js'), 'utf8'));
    await settle();
    report.signOutName = app.querySelector('.top button')?.getAttribute('aria-label') || '';
  } else if (mode === 'phone-touch') {
    const targets = ['btn btn-sm', 'btn-icon', 'cmd-copy', 'tab'].map(className => M.h('button', { class: className }));
    M.mount(view, targets);
    report.targets = targets.map(element => {
      const css = w.getComputedStyle(element);
      return { height: Math.max(parseFloat(css.height) || 0, parseFloat(css.minHeight) || 0),
        width: Math.max(parseFloat(css.width) || 0, parseFloat(css.minWidth) || 0) };
    });
  } else {
    let form;
    M.dialog('Parent title', () => {
      form = M.h('form', { class: 'stack', novalidate: true },
        Array.from({ length: 20 }, (_, index) => M.field('Field ' + index, M.h('input', { type: 'text' }))),
        M.h('div', { class: 'actions' }, M.h('button', { class: 'btn btn-primary', type: 'submit', text: 'Save' })));
      form.addEventListener('submit', event => {
        event.preventDefault(); report.submitted = true;
        M.busy(form, () => new Promise(() => {}));
      });
      return form;
    });
    if (mode === 'phone-identity') {
      w.eval(fs.readFileSync(path.join(ui, 'auth.js'), 'utf8'));
      M.confirmIdentity().catch(() => {});
      const dialogs = [...w.document.querySelectorAll('dialog')];
      report.titles = dialogs.map(dialog => dialog.getAttribute('aria-labelledby'));
      report.labelsMatch = dialogs.every(dialog => dialog.querySelector('h2').id === dialog.getAttribute('aria-labelledby'));
    } else {
      const dialog = w.document.querySelector('dialog');
      report.bodyOverflow = w.getComputedStyle(dialog.querySelector('.dialog-body')).overflowY;
      report.maxHeight = w.getComputedStyle(dialog).maxBlockSize;
      const actions = dialog.querySelector('.actions');
      report.footerPosition = w.getComputedStyle(actions).position;
      const focused = form.querySelectorAll('input')[5];
      focused.getBoundingClientRect = () => ({ bottom: 600, height: 44 });
      actions.getBoundingClientRect = () => ({ top: 500, height: 60 });
      focused.dispatchEvent(new w.FocusEvent('focusin', { bubbles: true }));
      await new Promise(resolve => w.requestAnimationFrame(resolve));
      report.focusedScrolled = dialog.querySelector('.dialog-body').scrollTop > 0;
      actions.querySelector('button').click();
      await settle();
      report.buttonsDisabled = actions.querySelector('button').disabled;
    }
  }
}

process.stdout.write(JSON.stringify(report));
dom.window.close();
