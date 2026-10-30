/* Load the public demo HTML, its adapter, and every real console asset together. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [, , , mode, locale, input] = process.argv;
const data = JSON.parse(input);
const directory = path.resolve(data.directory);
const html = fs.readFileSync(path.join(directory, 'console/index.html'), 'utf8');
const dom = new JSDOM(html, { url: `https://198.51.100.1/demo/console/?lang=${locale}`, runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const report = { outside: [], nativeApi: [], errors: [], responses: {} };
w.Response = Response;
w.URL.createObjectURL = () => 'blob:demo';
w.URL.revokeObjectURL = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
w.addEventListener('error', event => report.errors.push(event.message));
w.fetch = async input => {
  const url = new URL(String(input), w.location.href);
  if (url.origin !== w.location.origin) { report.outside.push(url.href); throw new Error('outside request'); }
  if (/\/(api|auth)\//.test(url.pathname)) report.nativeApi.push(url.pathname);
  const filename = path.join(directory, url.pathname.replace(/^\/demo\//, ''));
  if (!fs.existsSync(filename)) return new Response('{}', { status: 404 });
  return new Response(fs.readFileSync(filename), { headers: { 'Content-Type': 'application/json' } });
};
for (const script of dom.window.document.querySelectorAll('script[src]')) {
  // document.currentScript is available only to a browser's real loader.
  Object.defineProperty(w.document, 'currentScript', { configurable: true, value: script });
  w.eval(fs.readFileSync(path.join(directory, 'console', script.getAttribute('src')), 'utf8'));
}
Object.defineProperty(w.document, 'currentScript', { configurable: true, value: null });
const intercepted = w.fetch;
w.fetch = async (input, options) => {
  const response = await intercepted(input, options);
  const request = new URL(String(input), w.location.href);
  const relative = request.pathname.replace(/^\/demo\/console\//, '') + request.search;
  if (response.ok && /^(api|ui\/api|auth\/state|auth\/sessions)/.test(relative)) {
    report.responses[(options?.method || 'GET') + ' ' + relative] = await response.clone().json();
  }
  return response;
};
async function until(check, label) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  throw new Error('Timed out: ' + label + '\n' + w.document.body.textContent);
}
const M = w.M;
await until(() => w.document.querySelector('#view .list'), 'people');
report.lang = w.document.documentElement.lang;
report.dir = w.document.documentElement.dir;
report.people = (await M.api('GET', 'api/users')).users.length;

if (mode === 'showcase-navigation') {
  await M.go('people', 'Luma');
  await until(() => w.document.querySelector('.individual-connections'), 'person');
  report.person = w.document.querySelector('#view h1').textContent;
  const disclosure = w.document.querySelector('.individual-connections');
  disclosure.open = true;
  report.connections = disclosure.querySelectorAll('.connection-entry').length;
  const qrButton = [...disclosure.querySelectorAll('button')].find(button => button.textContent === M.t('person.show_qr'));
  qrButton.click();
  await until(() => w.document.querySelector('.connection-qr'), 'qr');
  report.qr = w.document.querySelector('.connection-qr').src.startsWith('data:image/svg+xml');
  w.document.querySelector('dialog .btn-icon').click();
  await M.go('people');
  await until(() => w.document.querySelector('#view .list'), 'back');
  await M.go('servers');
  await until(() => w.document.querySelector('#view').textContent.includes('Example Hosting'), 'servers');
  report.servers = (await M.api('GET', 'ui/api/fleet')).servers.length;
  await M.go('settings');
  await until(() => w.document.querySelector('[data-notice="text"]')?.value, 'settings');
  report.settings = !!w.document.querySelector('.privacy-choice') && !!w.document.querySelector('[data-language]');
  await M.go('tokens');
  await until(() => w.document.querySelector('#view').textContent.includes('People helper'), 'tokens');
  report.tokens = (await M.api('GET', 'api/tokens')).tokens.length;
  report.banner = w.document.querySelector('#demo-banner a').getAttribute('href');
} else if (mode === 'showcase-mutations') {
  await M.api('PATCH', 'api/users/Luma', { note: 'Literal <b>demo note</b>', tag: 'TRIAL' });
  report.edited = (await M.api('GET', 'api/users/Luma')).note;
  await M.api('GET', 'ui/api/people/Luma/expiry?months=3');
  await M.api('DELETE', 'api/users/Luma/devices');
  report.devices = (await M.api('GET', 'api/users/Luma/devices')).count;
  const before = (await M.api('GET', 'api/privacy')).settings;
  await M.api('PUT', 'api/privacy', { ...before, device_limits: 'private' });
  await M.api('PUT', 'api/notice', { notice: { text: 'Hello {name}', until: null }, for_user: 'Luma', confirm: false });
  await M.api('PUT', 'api/notice', { notice: { text: 'Hello {name}', until: null }, for_user: 'Luma', confirm: true });
  report.notice = (await M.api('GET', 'api/notice?for=Luma')).preview;
  await M.api('PUT', 'api/language', { language: 'zh' });
  report.language = (await M.api('GET', 'api/language')).language;
  const original = (await M.api('GET', 'api/users/Luma')).sub_token;
  report.reissued = (await M.api('POST', 'api/users/Luma/reissue', {})).sub_token !== original;
  const token = await M.api('POST', 'api/tokens', { name: 'Demo reader', scope: 'read', expires_in_days: 7 });
  report.token_revoked = !!(await M.api('DELETE', 'api/tokens/' + token.id)).revoked_at;
  await M.api('POST', 'api/users', { username: 'DemoGuest', expire_at: null, traffic_limit_bytes: 0, traffic_limit_strategy: 'no_reset' });
  await M.api('GET', 'api/users/DemoGuest/usage?days=7');
  await M.api('GET', 'api/users/DemoGuest/cut-off');
  await M.api('POST', 'api/users/DemoGuest/reissue', {});
  report.guestReissued = (await M.api('POST', 'api/users/DemoGuest/reissue', {})).username === 'DemoGuest';
  await M.api('PATCH', 'api/users/Vela', { status: 'active' });
  report.velaLinks = (await M.api('GET', 'ui/api/people/Vela/share')).connections.filter(entry => entry.available).length;
  report.week = (await M.api('GET', 'api/users/Luma/usage?days=7')).daily.length;
  const privacy = (await M.api('GET', 'api/privacy')).settings;
  await M.api('PUT', 'api/privacy', { ...privacy, usage_history: 'off' });
  await M.api('PUT', 'api/privacy', { ...privacy, usage_history: 'daily' });
  const restarted = await M.api('GET', 'api/users/Luma/usage?days=7');
  report.historyRestarted = restarted.daily.every(row => !row.uplink_bytes && !row.downlink_bytes);
  report.serverDays = (await M.api('GET', 'api/traffic/daily?days=7')).days;
  await M.api('GET', 'api/users/Luma/connections');
  report.events = (await M.api('GET', 'api/events?after=0&limit=2000')).events.map(event => event.kind);
  await M.api('DELETE', 'api/users/DemoGuest', { cut_off: false });
  report.removed = !(await M.api('GET', 'api/users')).users.some(user => user.username === 'DemoGuest');
} else if (mode === 'showcase-reset') {
  report.note = (await M.api('GET', 'api/users/Luma')).note;
  report.language = (await M.api('GET', 'api/language')).language;
  report.devices = (await M.api('GET', 'api/users/Luma/devices')).count;
} else assert.fail(mode);
w.close();
console.log(JSON.stringify(report));
