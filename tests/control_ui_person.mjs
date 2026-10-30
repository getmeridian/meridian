/* Person-screen journeys execute the shipped console against same-origin API doubles. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en', dataText = '{}'] = process.argv.slice(2);
const data = JSON.parse(dataText) || {};
const dom = new JSDOM('<!doctype html><body><div id="app"></div></body>', {
  url: 'https://198.51.100.1/console/' + (mode === 'person-first-navigation' ? '' : '#/people/sample'),
  runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const events = [], writes = [], pending = [];
const report = {};
let fakeNow = Date.now(), timerId = 0, hidden = false, failInitial = mode === 'person-initial-failure';
let signedIn = mode !== 'person-first-navigation';
const timers = new Map(), rounds = new Map(), delayed = {};
if (mode.startsWith('person-live')) {
  w.Date.now = () => fakeNow;
  w.setTimeout = (fn, delay = 0) => { const id = ++timerId; timers.set(id, { fn, at: fakeNow + delay }); return id; };
  w.clearTimeout = id => timers.delete(id);
  Object.defineProperty(w.document, 'hidden', { get: () => hidden });
}
let user = {
  username: 'sample', status: data.initialStatus || 'disabled', created_at: '2026-10-29T00:00:00Z',
  expire_at: '2031-12-31T10:20:30Z', traffic_limit_bytes: 1024 ** 3,
  used_traffic_bytes: 0, lifetime_traffic_bytes: 0, traffic_limit_strategy: 'month', note: '', tag: '',
  ...data.user,
};
const share = {
  username: 'sample', declared: false, subscription_url: 'https://198.51.100.1/sub/test-only-link',
  page_url: 'https://198.51.100.1/sub/test-only-link',
  qr_svg: 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="white"/></svg>').toString('base64'),
  ...data.share,
};
if (data.unsafeSvg) share.qr_svg = 'data:image/svg+xml;base64,' + Buffer.from(data.unsafeSvg).toString('base64');
function reply(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(body)) };
}
w.fetch = (url, options = {}) => {
  const event = { url, method: options.method, headers: options.headers };
  if (options.body !== undefined) event.body = JSON.parse(options.body);
  events.push(event);
  if (options.method !== 'GET') {
    writes.push({ url, method: options.method, body: JSON.parse(options.body || '{}') });
    return new Promise(resolve => pending.push(resolve));
  }
  if (url === 'auth/state') return Promise.resolve(reply({ configured: true, owner_exists: true, signed_in: signedIn }));
  if (url === 'ui/api/fleet') return Promise.resolve(reply({ profile_title: 'Test fleet', declared_users: [],
    display_names: { sample: data.displayName || 'Sample person' }, servers: [] }));
  if (url.startsWith('ui/api/people/sample/expiry')) return Promise.resolve(reply({ expire_at: data.preview, months: data.months }));
  if (url.startsWith('ui/api/people/')) return Promise.resolve(reply({ ...share, username: url.split('/')[3] }));
  if (url.endsWith('/devices')) return Promise.resolve(reply(data.devices || { level: 'private', count: 1, limit: user.device_limit || 3, devices: [] }));
  if (url === 'api/users/sample') {
    if (failInitial) return Promise.resolve(reply({ error: { code: 'network' } }, 503));
    const round = (rounds.get(url) || 0) + 1;
    rounds.set(url, round);
    if (mode === 'person-live-race' && round > 1) return new Promise(resolve => { delayed.read = resolve; });
  }
  if (url.startsWith('api/users/')) return Promise.resolve(reply({ ...user, username: decodeURIComponent(url.split('/')[2]) }));
  if (url === 'api/users') return Promise.resolve(reply({ users: [user] }));
  if (url === 'api/settings') return Promise.resolve(reply({ profile_title: 'Test fleet' }));
  if (url === 'auth/sessions') return Promise.resolve(reply({ sessions: [] }));
  throw new Error('Unexpected request: ' + url);
};
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const load = script => w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
load('core.js');
load('drafts.js');
load('qr-export.js');
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale, load: () => Promise.resolve(),
  langs: ['en', 'ru', 'fa', 'zh'], names: { en: 'English', ru: 'Русский', fa: 'فارسی', zh: '中文' }, lang: locale };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = text => events.push({ toast: text });
M.takeSetupToken = () => '';
M.personHistory = M.devicesCard = M.privacySection = M.cliHint = () => null;
M.renderServers = view => M.mount(view, M.h('h1', { text: M.t('nav.servers') }));
M.renderRevisions = M.renderTokens = () => null;
load('auth.js');
load('people.js');
load('expiry.js');
load('person-connections.js');
if (mode === 'person-device-race' || mode === 'person-devices') load('devices.js');
load('person.js');
load('settings.js');
load('app.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 10));
await settle();
await settle();
const view = () => w.document.getElementById('view');
const button = key => [...view().querySelectorAll('button')].find(el => el.textContent.includes(M.t(key)));

if (mode === 'person-devices') {
  events.splice(0);
  writes.splice(0);
  let refreshed = 0;
  M.renderPerson = () => { refreshed++; };
  M.confirm = () => Promise.resolve(!data.cancel);
  const card = M.devicesCard(data.user);
  M.mount(view(), card);
  await settle();
  report.shown = card.textContent;
  if (data.action === 'cap') {
    const input = card.querySelector('input[type="number"]');
    if (data.value !== null) input.value = data.value;
    if (data.badInput) Object.defineProperty(input, 'validity', { value: { badInput: true } });
    input.dispatchEvent(new w.Event('input', { bubbles: true }));
    input.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  } else if (data.action === 'clear' || data.action === 'remove') {
    [...card.querySelectorAll('button')].find(el => el.textContent === M.t('devices.' + data.action)).click();
  }
  await settle();
  for (const resolve of pending.splice(0)) {
    const change = writes[0];
    if (change?.method === 'PATCH') Object.assign(user, change.body);
    resolve(reply(change?.method === 'PATCH' ? user : { deleted: 1 }));
  }
  await settle();
  report.requests = events.filter(event => event.method).map(event => {
    const request = { method: event.method, path: event.url };
    if ('body' in event) request.body = event.body;
    return request;
  });
  report.notices = events.filter(event => event.toast).map(event => event.toast);
  report.refreshed = refreshed;
  report.error = card.querySelector('.form-error')?.textContent || '';
  report.tags = [...card.querySelectorAll('*')].map(el => el.localName);
}
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
const guard = () => w.document.querySelector('[data-draft-choice="stay"]')?.closest('dialog');
function choose(value) { w.document.querySelector('[data-draft-choice="' + value + '"]')?.click(); }
function fieldFor(screen) {
  return screen === 'password' ? view().querySelector('input[type="password"]')
    : screen === 'access' ? view().querySelector('input[type="time"]') : view().querySelector('textarea');
}

if (mode === 'person-first-navigation') {
  M.state.auth.signed_in = true;
  M.go('people', 'sample'); await settle();
  const note = view().querySelector('textarea');
  note.value = 'Private draft'; note.dispatchEvent(new w.Event('input', { bubbles: true }));
  w.history.back(); await settle(); await settle();
  report.prompt = !!guard();
  report.restored = w.location.hash === '#/people/sample';
}

if (mode === 'person-guard') {
  if (data.navigation === 'back') {
    M.go('servers'); await settle();
    M.go('people', 'sample'); await settle();
  }
  if (data.screen === 'password') { M.go('settings'); await settle(); }
  const field = fieldFor(data.screen), original = field.value;
  if (data.cycleUnlimited) {
    const never = view().querySelector('.expiry-fields input[type="checkbox"]');
    never.checked = false; never.dispatchEvent(new w.Event('change', { bubbles: true }));
    const date = view().querySelector('input[type="date"]');
    date.value = '2032-01-15'; date.dispatchEvent(new w.Event('input', { bubbles: true }));
    never.checked = true; never.dispatchEvent(new w.Event('change', { bubbles: true }));
    const mode = view().querySelector('.limit-fields select'), amount = view().querySelector('.limit-fields input');
    mode.value = 'limited'; mode.dispatchEvent(new w.Event('change', { bubbles: true }));
    amount.value = '1'; amount.dispatchEvent(new w.Event('input', { bubbles: true }));
    mode.value = 'unlimited'; mode.dispatchEvent(new w.Event('change', { bubbles: true }));
  } else if (!data.clean || data.revert) {
    field.value = data.screen === 'access' ? '12:34:56' : 'Private draft';
    field.dispatchEvent(new w.Event('input', { bubbles: true }));
    if (data.revert) { field.value = original; field.dispatchEvent(new w.Event('input', { bubbles: true })); }
  }
  const origin = w.location.hash;
  if (data.saveCycle) {
    field.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    await settle();
    report.writes = writes.map(write => write.body);
  }
  async function navigate() {
    if (data.navigation === 'signout') w.document.querySelector('button[aria-label="' + M.t('auth.sign_out') + '"]').click();
    else if (data.navigation === 'back') w.history.back();
    else if (data.navigation === 'hash') w.location.hash = '#/servers';
    else w.document.querySelector('a[href="#/servers"]').click();
    await settle(); await settle();
  }
  await navigate();
  report.prompt = !!guard();
  report.promptText = guard()?.textContent || '';
  if (report.prompt) {
    choose('stay'); await settle();
    report.stayPreserved = field.isConnected && field.value !== original && w.location.hash === origin;
    await navigate();
    report.secondPrompt = !!guard();
    choose('leave'); await settle(); await settle();
    if (data.navigation === 'signout') {
      signedIn = false;
      for (const resolve of pending.splice(0)) resolve(reply({}));
      await settle(); await settle();
    }
  }
  report.left = !field.isConnected && (data.navigation === 'signout' || w.location.hash !== origin);
  report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
}

if (mode === 'person-initial-failure') {
  report.spinner = !!view().querySelector('.spinner');
  report.retry = !!view().querySelector('.live-retry') && !view().querySelector('.live-retry').hidden;
  failInitial = false;
  view().querySelector('.live-retry')?.click(); await settle();
  report.recovered = !!view().querySelector('textarea') && !view().classList.contains('view-stale');
}

if (mode === 'person-guard-dialog') {
  if (data.kind === 'password') M.confirmIdentity().catch(() => {});
  else { M.go('people'); await settle(); button('people.add').click(); }
  await settle();
  const dialog = w.document.querySelector('dialog'), input = dialog.querySelector('input');
  input.value = 'Private draft';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  function dismiss() {
    if (data.dismissal === 'escape') dialog.dispatchEvent(new w.Event('cancel', { cancelable: true }));
    else if (data.dismissal === 'cancel') [...dialog.querySelectorAll('button')].find(el => el.textContent === M.t('action.cancel')).click();
    else dialog.querySelector('.dialog-head button').click();
  }
  dismiss(); await settle();
  report.prompt = !!guard();
  choose('stay'); await settle();
  report.stayPreserved = dialog.isConnected && dialog.open && input.value === 'Private draft';
  dismiss(); await settle();
  choose('leave'); await settle();
  report.left = !dialog.isConnected;
  report.writes = writes;
}

if (mode === 'person-guard-save') {
  const note = view().querySelector('textarea'), time = view().querySelector('input[type="time"]');
  if (data.twoForms) {
    time.value = '12:34:56'; time.dispatchEvent(new w.Event('input', { bubbles: true }));
    note.value = 'Private draft'; note.dispatchEvent(new w.Event('input', { bubbles: true }));
  } else { note.value = 'Private draft'; note.dispatchEvent(new w.Event('input', { bubbles: true })); }
  const unloading = () => { const event = new w.Event('beforeunload', { cancelable: true }); w.dispatchEvent(event); return event.defaultPrevented; };
  report.beforeUnload = unloading();
  const form = data.twoForms ? time.form : note.form;
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  if (!data.failed) user = { ...user, ...writes[0]?.body };
  for (const resolve of pending.splice(0)) resolve(data.failed ? reply({ error: { code: 'network' } }, 503) : reply(user));
  await settle();
  report.otherDraft = view().querySelector('textarea')?.value;
  report.afterUnload = unloading();
  w.document.querySelector('a[href="#/servers"]').click(); await settle();
  report.prompt = !!guard();
}

if (mode.startsWith('person-live')) {
  const note = view().querySelector('textarea'), time = view().querySelector('input[type="time"]');
  if (mode === 'person-live-race') {
    const old = { ...user };
    await advance(30000);
    button('person.enable').click(); await settle();
    user = { ...user, status: 'active' };
    for (const resolve of pending.splice(0)) resolve(reply(user));
    await settle();
    delayed.read?.(reply(old)); await settle();
    report.status = view().querySelector('.page-head .pill')?.textContent || '';
  } else {
    note.value = 'Private draft'; note.dispatchEvent(new w.Event('input', { bubbles: true }));
    time.value = '12:34:56'; time.dispatchEvent(new w.Event('input', { bubbles: true }));
    note.focus(); note.setSelectionRange(3, 6);
    user = { ...user, status: 'limited', used_traffic_bytes: 1024 ** 3 };
    await advance(30000);
    report.sameNote = view().querySelector('textarea') === note;
    report.sameTime = view().querySelector('input[type="time"]') === time;
    report.note = view().querySelector('textarea')?.value;
    report.time = view().querySelector('input[type="time"]')?.value;
    report.focusKept = w.document.activeElement === note && note.selectionStart === 3 && note.selectionEnd === 6;
    report.status = view().querySelector('.page-head .pill')?.textContent || '';
    report.usage = view().querySelector('.usage')?.textContent || '';
    const reads = () => events.filter(event => event.url === 'api/users/sample');
    report.passive = reads().map(event => event.headers['X-Meridian-Passive'] === '1');
    hidden = true; w.document.dispatchEvent(new w.Event('visibilitychange'));
    const beforeHidden = reads().length;
    await advance(60000);
    report.readsWhileHidden = reads().length - beforeHidden;
    M.askToLeave = () => Promise.resolve(true);
    M.go('servers'); await settle();
    const beforeStop = reads().length;
    await advance(60000);
    report.readsAfterStop = reads().length - beforeStop;
    report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
  }
}

if (mode === 'person-device-race') {
  M.confirm = () => Promise.resolve(true);
  M.askToLeave = () => Promise.resolve(true);
  if (data.action === 'clear') button('devices.clear').click();
  else {
    const cap = view().querySelector('input[type="number"]');
    cap.value = '5'; cap.dispatchEvent(new w.Event('input', { bubbles: true }));
    cap.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  }
  await settle(); M.go('servers'); await settle();
  report.before = view().textContent;
  for (const resolve of pending.splice(0)) resolve(reply(data.action === 'clear' ? { deleted: 1 } : { ...user, device_limit: 5 }));
  await settle();
  report.after = view().textContent;
  report.toasts = events.filter(event => event.toast).map(event => event.toast);
}

if (mode === 'person-expiry') {
  const date = view().querySelector('input[type="date"]');
  const time = view().querySelector('input[type="time"]');
  const never = view().querySelector('.expiry-fields input[type="checkbox"]');
  report.hasTime = !!time;
  if (data.months) {
    view().querySelector('[data-months="' + data.months + '"]')?.click();
    await settle();
  } else if (data.clear) {
    if (never) { never.checked = true; never.dispatchEvent(new w.Event('change', { bubbles: true })); }
  } else {
    if ('date' in data && date) { date.value = data.date; date.dispatchEvent(new w.Event('input', { bubbles: true })); }
    if ('time' in data && time) { time.value = data.time; time.dispatchEvent(new w.Event('input', { bubbles: true })); }
    if (data.strategy) {
      const strategy = [...view().querySelectorAll('.limit-fields select')].at(-1);
      strategy.value = data.strategy;
      strategy.dispatchEvent(new w.Event('change', { bubbles: true }));
    }
  }
  report.zone = view().querySelector('.expiry-zone')?.textContent || '';
  report.utc = view().querySelector('.expiry-utc')?.textContent || '';
  report.beforeSave = writes.map(write => write.body);
  date?.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  report.writes = writes.map(write => write.body);
  report.error = date?.validationMessage || time?.validationMessage || '';
  report.previewReads = events.filter(event => event.url?.includes('/expiry?')).map(event => event.url);
}

if (mode === 'person-connections') {
  const disclosure = view().querySelector('.individual-connections');
  report.closed = !!disclosure && !disclosure.open;
  report.summary = disclosure?.querySelector('summary')?.textContent || '';
  report.remarks = [...(disclosure?.querySelectorAll('.connection-remark') || [])].map(el => el.textContent);
  report.formats = [...(disclosure?.querySelectorAll('a') || [])].map(el => el.getAttribute('href'));
  const copied = [];
  M.copy = text => copied.push(text);
  disclosure?.querySelector('.connection-entry button')?.click();
  report.copied = copied;
  disclosure?.querySelector('.connection-entry button:last-child')?.click();
  report.qr = w.document.querySelector('dialog img')?.getAttribute('src') || '';
  report.injected = !!view().querySelector('img[src="x"]') || !!w.pwned;
  report.outside = events.filter(event => event.url?.includes('://'));
}

if (mode === 'person-qr') {
  const generated = [], clipboard = [], downloads = [];
  let source = '', draws = 0, canvasSize = [];
  Object.defineProperty(w, 'isSecureContext', { value: true });
  w.ClipboardItem = class {
    constructor(items) { this.items = items; }
    static supports() { return data.clipboard !== 'unsupported'; }
  };
  Object.defineProperty(w.navigator, 'clipboard', { value: data.clipboard === 'absent' ? undefined : {
    write(items) {
      if (data.clipboard === 'throws') throw new Error('Clipboard refused');
      if (data.clipboard === 'rejects') return Promise.reject(new Error('Clipboard refused'));
      clipboard.push(...items);
      return Promise.resolve();
    },
  } });
  w.URL.createObjectURL = blob => { generated.push(blob); return 'blob:https://198.51.100.1/generated-' + generated.length; };
  w.URL.revokeObjectURL = () => {};
  w.HTMLAnchorElement.prototype.click = function () { downloads.push({ href: this.href, filename: this.download }); };
  w.HTMLImageElement.prototype.decode = function () { source = this.src; return Promise.resolve(); };
  w.HTMLCanvasElement.prototype.getContext = function () {
    const canvas = this;
    return { font: '24px sans-serif', fillStyle: '', textAlign: '',
      measureText: text => ({ width: [...text].length * 12 }),
      fillRect() {}, fillText() {},
      drawImage(image) { source = image.src; draws++; canvasSize = [canvas.width, canvas.height]; },
    };
  };
  w.HTMLCanvasElement.prototype.toBlob = function (done, type) { done(new w.Blob(['test-only-pixels'], { type })); };
  M.render();
  await settle();
  const allActions = [...view().querySelectorAll('[data-qr-action]')];
  report.actions = allActions.map(el => el.dataset.qrAction);
  const action = data.action === 'copy' && !allActions.some(el => el.dataset.qrAction === 'copy') ? 'png' : data.action;
  allActions.find(el => el.dataset.qrAction === action)?.click();
  await settle();
  const readBlob = blob => new Promise(resolve => {
    const reader = new w.FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsText(blob);
  });
  const svgBlob = generated.find(blob => blob.type === 'image/svg+xml');
  report.svg = svgBlob ? await readBlob(svgBlob) : source.startsWith('data:image/svg+xml,') ? decodeURIComponent(source.split(',').slice(1).join(',')) : '';
  report.downloads = downloads;
  report.downloadTypes = generated.map(blob => blob.type);
  report.copiedTypes = clipboard.flatMap(item => Object.keys(item.items));
  report.canvasSize = canvasSize;
  report.draws = draws;
  report.outside = events.filter(event => event.url?.includes('://'));
  report.toasts = events.filter(event => event.toast).map(event => event.toast);
  report.generalUrlsRejected = !M.h('a', { href: 'blob:https://198.51.100.1/arbitrary' }).hasAttribute('href')
    && !M.h('a', { href: 'data:image/png;base64,AAAA' }).hasAttribute('href')
    && !M.h('img', { src: 'data:image/png;base64,AAAA' }).hasAttribute('src');
}

if (mode === 'person-mutation') {
  M.confirm = () => Promise.resolve(true);
  M.askToLeave = () => { events.push({ asked: true }); return Promise.resolve(true); };
  M.confirmCutOffRemoval = () => Promise.resolve({ cut_off: false });
  const action = data.action || 'toggle';
  if (data.editBeforeRemove) {
    const note = view().querySelector('textarea');
    note.value = 'Private draft'; note.dispatchEvent(new w.Event('input', { bubbles: true }));
  }
  if (action === 'save') {
    const strategy = [...view().querySelectorAll('.limit-fields select')].at(-1);
    strategy.value = 'week';
    strategy.dispatchEvent(new w.Event('change', { bubbles: true }));
    strategy.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  } else if (action === 'metadata') {
    const note = view().querySelector('textarea');
    note.value = 'A private draft';
    note.dispatchEvent(new w.Event('input', { bubbles: true }));
    note.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  } else if (action === 'clear') {
    const never = view().querySelector('.expiry-fields input[type="checkbox"]');
    never.checked = true;
    never.dispatchEvent(new w.Event('change', { bubbles: true }));
    never.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  } else {
    const key = { toggle: user.status === 'disabled' ? 'person.enable' : 'person.disable',
      reset: 'person.reset_traffic', clear: 'person.clear_expiry', reissue: 'person.reissue', remove: 'person.remove' }[action];
    button(key).click();
  }
  await settle();
  report.started = writes;
  const readsBefore = events.filter(event => event.method === 'GET' && event.url.startsWith('api/users/')).length;
  if (data.navigate) {
    M.go(data.navigate === 'servers' || data.navigate === 'return' ? 'servers' : 'people', data.navigate === 'person' ? 'other' : undefined);
    await settle();
    if (data.navigate === 'return') {
      M.go('people', 'sample');
      await settle();
    }
  }
  report.before = view().textContent;
  user = { ...user, ...writes[0]?.body, status: data.status || 'active' };
  for (const resolve of pending.splice(0)) resolve(reply(action === 'remove' ? { deleted: 'sample' } : user));
  await settle();
  report.after = view().textContent;
  report.status = view().querySelector('.page-head .pill')?.textContent || '';
  report.toasts = events.filter(event => event.toast).map(event => event.toast);
  report.readsAfterResult = events.filter(event => event.method === 'GET' && event.url.startsWith('api/users/')).length - readsBefore;
  report.hash = w.location.hash;
  report.asks = events.filter(event => event.asked).length;
}

dom.window.close();
process.stdout.write(JSON.stringify(report));
