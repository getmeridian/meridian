/* Synchronous bulk validation, including submit before the deferred form guard attaches. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale, raw] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw);
process.env.TZ = 'UTC';
const dom = new JSDOM('<!doctype html><body></body>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
Object.defineProperty(w.document, 'hidden', { value: false, configurable: true });
w.Date.now = () => Date.parse('2026-11-01T12:00:00Z');
const requests = [];
w.fetch = url => { requests.push(url); throw new Error('Unexpected request: ' + url); };
for (const name of ['core.js', 'expiry.js', 'people-bulk-fields.js']) w.eval(fs.readFileSync(path.join(ui, name), 'utf8'));
const M = w.M;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, key) => String(params[key] ?? ''));
function fill(fields) {
  fields.nodes.querySelector('input[type="checkbox"]').checked = data.never;
  fields.nodes.querySelector('input[type="date"]').value = data.date;
  fields.nodes.querySelector('input[type="time"]').value = data.time;
}
const direct = M.bulkActionFields('expiry');
fill(direct);
const value = direct.value();
const fields = M.bulkActionFields('expiry');
fill(fields);
let submitted;
const form = M.h('form', { novalidate: true, onsubmit: event => {
  event.preventDefault(); submitted = fields.value();
} }, fields.nodes);
w.document.body.appendChild(form);
// No await: expiry.js has not attached its Promise-based capture listener yet.
form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
const report = {
  direct: value, submitted,
  directError: direct.nodes.querySelector('.field-error').textContent,
  formError: fields.nodes.querySelector('.field-error').textContent,
  invalidFocused: w.document.activeElement === form.querySelector('[aria-invalid="true"]'),
  stored: [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)], requests,
};
await Promise.resolve();
dom.window.close();
process.stdout.write(JSON.stringify(report));
