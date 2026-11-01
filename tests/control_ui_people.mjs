/* People triage through the shipped console, including hash reload and row identity. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale, dataText] = process.argv.slice(2);
const data = JSON.parse(dataText);
const dom = new JSDOM('<!doctype html><body><div id="app" class="boot"></div></body>', {
  url: 'https://198.51.100.1/console/' + (data.hash || '#/people'), runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
w.Date.now = () => Date.parse('2026-11-01T12:00:00Z');
w.document.documentElement.lang = locale;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const requests = [], report = {};
w.fetch = (url, options) => {
  requests.push(url);
  let body;
  if (url === 'auth/state') body = { configured: true, owner_exists: true, signed_in: true };
  else if (url === 'ui/api/fleet') body = { servers: [], declared_users: [], display_names: data.names || {} };
  else if (url === 'api/users' && data.failUsers) return Promise.resolve({ ok: false, status: 500, headers: { get: () => null }, text: () => Promise.resolve('{}') });
  else if (url === 'api/users') body = { users: data.people };
  else throw new Error('Unexpected request: ' + url);
  return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, text: () => Promise.resolve(JSON.stringify(body)) });
};
const load = name => w.eval(fs.readFileSync(path.join(ui, name), 'utf8'));
load('core.js'); load('drafts.js'); load('people.js');
if (fs.existsSync(path.join(ui, 'people-list.js'))) load('people-list.js');
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale, load: () => Promise.resolve() };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, key) => String(params[key] ?? ''));
M.takeSetupToken = () => '';
M.renderServers = view => M.mount(view, M.h('p', { text: 'Servers' }));
load('app.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 150));
await settle();
const view = () => w.document.getElementById('view');
const shown = () => [...view().querySelectorAll('a.row-link')].filter(el => !el.hidden).map(el => decodeURIComponent(el.getAttribute('href').split('/').at(-1)));
const rows = new Map([...view().querySelectorAll('a.row-link')].map(el => [el.getAttribute('href'), el]));
const input = view().querySelector('input[type="search"]');
input.focus();
for (const [name, value] of Object.entries(data.change || {})) {
  const control = name === 'q' ? input : view().querySelector('[name="' + name + '"]');
  if (!control) { report.missing = name; continue; }
  if (control.type === 'checkbox') control.checked = value === true;
  else control.value = String(value);
  control.dispatchEvent(new w.Event(control.localName === 'select' || control.type === 'checkbox' ? 'change' : 'input', { bubbles: true }));
}
await settle();
report.shown = shown();
report.hash = w.location.hash;
report.text = view().textContent;
report.search = input.value;
report.sameRows = [...view().querySelectorAll('a.row-link')].every(el => rows.get(el.getAttribute('href')) === el);
report.sameInput = view().querySelector('input[type="search"]') === input;
report.focusKept = w.document.activeElement === input;
report.selects = Object.fromEntries([...view().querySelectorAll('select[name]')].map(el => [el.name, [...el.options].map(option => ({ value: option.value, text: option.textContent }))]));
report.filtersOpen = !!view().querySelector('details.people-filter-card')?.open;
report.errors = [...view().querySelectorAll('.field-error')].map(el => el.textContent).filter(Boolean);
report.route = M.route();
if (mode === 'people-triage-reload') {
  M.render(true);
  await settle();
  report.afterReload = shown();
  report.afterSearch = view().querySelector('input[type="search"]').value;
}
report.requests = requests;
report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
dom.window.close();
process.stdout.write(JSON.stringify(report));
