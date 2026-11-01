/* Real service review shapes through the shipped People DOM flow. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale, raw] = process.argv.slice(2);
// Large scenarios arrive on stdin: Linux caps one argument at 128 KiB.
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw);
const dom = new JSDOM('<!doctype html><body><div id="app"></div></body>', {
  url: 'https://198.51.100.1/console/#/people?tag=GROUP_A', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
Object.defineProperty(w.document, 'hidden', { value: false, configurable: true });
w.Date.now = () => Date.parse('2026-11-01T12:00:00Z');
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const requests = [], report = {};
let committed = false, undone = false, commits = 0, undos = 0;
w.fetch = async (url, options = {}) => {
  requests.push(url);
  let body, status = 200;
  if (url === 'auth/state') body = { configured: true, owner_exists: true, signed_in: true };
  else if (url === 'ui/api/fleet') body = { servers: [], declared_users: [], display_names: {} };
  else if (url.startsWith('api/people?')) body = committed && !undone ? data.after : data.page;
  else if (url === 'api/users') body = { users: data.page.users };
  else if (url === 'api/people/bulk/preview') { report.previewRequest = JSON.parse(options.body); body = data.review; }
  else if (url === 'api/people/bulk/commit') {
    commits++; report.commitRequest = JSON.parse(options.body);
    if (data.stale) { status = 409; body = { error: { code: 'stale_people', message: 'p-a changed. Preview again.', subjects: ['p-a'] } }; }
    else { committed = true; body = data.receipt; }
  } else if (url === 'api/people/bulk/undo') { undos++; undone = true; body = data.undone; }
  else throw new Error('Unexpected request: ' + url);
  return { ok: status === 200, status, headers: { get: () => null }, text: async () => JSON.stringify(body) };
};
const load = name => { if (fs.existsSync(path.join(ui, name))) w.eval(fs.readFileSync(path.join(ui, name), 'utf8')); };
['core.js', 'drafts.js', 'expiry.js', 'lifecycle.js', 'people.js', 'people-bulk.js', 'people-bulk-fields.js', 'people-list.js'].forEach(load);
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale, load: async () => {} };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, key) => String(params[key] ?? ''));
M.takeSetupToken = () => '';
M.renderServers = view => M.mount(view, M.h('p', { text: 'Servers' }));
load('app.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 180));
await settle();
const rows = [...w.document.querySelectorAll('a.row-link')];
const row = w.document.querySelector('input[data-person]');
if (!row) {
  report.missing = 'row selection';
} else {
  row.focus();
  row.dispatchEvent(new w.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
  // jsdom does not implement Space's native checkbox click.
  if (!row.checked) { row.checked = true; row.dispatchEvent(new w.Event('change', { bubbles: true })); }
  report.keyboard = w.document.activeElement === row;
  w.document.querySelector('[data-select-matching]').click();
  report.selected = Number(w.document.querySelector('[data-bulk-count]').getAttribute('data-bulk-count'));
  const action = w.document.querySelector('[name="bulk-action"]');
  action.value = 'disable'; action.dispatchEvent(new w.Event('change', { bubbles: true }));
  w.document.querySelector('[data-bulk-preview]').click();
  await settle();
  const commit = w.document.querySelector('[data-bulk-commit]');
  commit.click(); commit.click();
  await settle();
  report.dialog = w.document.querySelector('dialog')?.textContent || '';
  if (!data.stale) {
    w.document.querySelector('[data-bulk-undo]').click();
    await settle();
    report.dialog = w.document.querySelector('dialog')?.textContent || '';
  }
  report.sameRows = [...w.document.querySelectorAll('a.row-link')].every((el, index) => rows[index] === el);
}
report.commits = commits; report.undos = undos; report.undone = undone;
report.outside = requests.some(url => /^https?:/.test(url));
report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
dom.window.close();
process.stdout.write(JSON.stringify(report));
