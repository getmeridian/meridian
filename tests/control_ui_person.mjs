/* Person-screen journeys execute the shipped console against same-origin API doubles. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en', dataText = '{}'] = process.argv.slice(2);
const data = JSON.parse(dataText) || {};
const dom = new JSDOM('<!doctype html><body><div id="app"></div></body>', {
  url: 'https://198.51.100.1/console/#/people/sample', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const events = [], writes = [], pending = [];
const report = {};
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
  events.push({ url, method: options.method, headers: options.headers });
  if (options.method !== 'GET') {
    writes.push({ url, method: options.method, body: JSON.parse(options.body || '{}') });
    return new Promise(resolve => pending.push(resolve));
  }
  if (url === 'auth/state') return Promise.resolve(reply({ configured: true, owner_exists: true, signed_in: true }));
  if (url === 'ui/api/fleet') return Promise.resolve(reply({ profile_title: 'Test fleet', declared_users: [],
    display_names: { sample: data.displayName || 'Sample person' }, servers: [] }));
  if (url.startsWith('ui/api/people/')) return Promise.resolve(reply({ ...share, username: url.split('/')[3] }));
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
load('people.js');
load('person-connections.js');
load('person.js');
load('settings.js');
load('app.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 10));
await settle();
await settle();
const view = () => w.document.getElementById('view');
const button = key => [...view().querySelectorAll('button')].find(el => el.textContent.includes(M.t(key)));

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
  M.confirmCutOffRemoval = () => Promise.resolve({ cut_off: false });
  const action = data.action || 'toggle';
  if (action === 'save') {
    const strategy = view().querySelector('.limit-fields select:last-child');
    strategy.value = 'week';
    strategy.dispatchEvent(new w.Event('change', { bubbles: true }));
    strategy.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  } else if (action === 'metadata') {
    const note = view().querySelector('textarea');
    note.value = 'A private draft';
    note.dispatchEvent(new w.Event('input', { bubbles: true }));
    note.form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
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
}

dom.window.close();
process.stdout.write(JSON.stringify(report));
