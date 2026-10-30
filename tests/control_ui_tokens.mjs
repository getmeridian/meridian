/* Exercise the shipped token dialog and expiry display with same-origin API replies. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode = 'default', locale = 'en'] = process.argv.slice(2);
const dom = new JSDOM('<!doctype html><body><div id="view"></div></body>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only',
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const now = Date.parse('2026-10-29T12:00:00Z');
w.Date.now = () => now;
const tokens = [
  { id: '000000000001', name: 'Expired desk', scope: 'people', created_at: '2026-10-01T12:00:00Z',
    expires_at: '2026-10-28T12:00:00Z', last_used_at: null, revoked_at: null },
  { id: '000000000002', name: 'Future desk', scope: 'read', created_at: '2026-10-01T12:00:00Z',
    expires_at: '2026-12-01T12:00:00Z', last_used_at: null, revoked_at: null },
  { id: '000000000003', name: 'Permanent desk', scope: 'approve', created_at: '2026-10-01T12:00:00Z',
    expires_at: null, last_used_at: null, revoked_at: null },
];
const events = [], writes = [];
function reply(body, status = 200) {
  return { ok: status === 200, status, headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(body)) };
}
w.fetch = (url, options) => {
  events.push(options.method + ':' + url);
  if (url !== 'api/tokens') throw new Error('Unexpected request: ' + url);
  if (options.method === 'GET') return Promise.resolve(reply({ tokens }));
  writes.push({ body: JSON.parse(options.body), csrf: options.headers['X-Meridian-Request'] });
  if (writes.length === 1) return Promise.resolve(reply({ error: { code: 'reauth_required' } }, 403));
  const body = writes.at(-1).body;
  const issued = { id: '000000000004', name: body.name, scope: body.scope,
    created_at: '2026-10-29T12:00:00Z', last_used_at: null, revoked_at: null,
    expires_at: body.expires_in_days ? new Date(now + body.expires_in_days * 86400000).toISOString() : null,
    token: 'token-shown-once-for-tests-only' };
  tokens.push(issued);
  return Promise.resolve(reply(issued));
};
for (const script of ['core.js', 'fleet.js']) w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = (text) => events.push('toast:' + text);
M.confirmIdentity = () => { events.push('step-up'); return Promise.resolve(); };
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const view = w.document.getElementById('view');
const settle = () => new Promise(setImmediate);
M.renderTokens(view);
await settle();
const rows = [...view.querySelectorAll('.row')].map((row) => ({ text: row.textContent, dim: row.classList.contains('row-dim') }));
view.querySelector('.page-head .btn-primary').click();
const form = w.document.querySelector('dialog form');
const scope = form.querySelector('select');
const expiry = form.querySelectorAll('select')[1];
const report = { rows, defaultExpiry: expiry?.value || null,
  scopes: [...scope.options].map((option) => option.value),
  expiries: expiry ? [...expiry.options].map((option) => option.value) : [] };
form.querySelector('input').value = 'People desk';
scope.value = 'people';
if (expiry && mode !== 'default' && mode !== 'cancel') expiry.value = mode;
if (mode === 'cancel') form.querySelector('.actions').children[0].click();
else form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
await settle();
report.events = events;
report.writes = writes;
report.text = view.textContent;
dom.window.close();
process.stdout.write(JSON.stringify(report));
