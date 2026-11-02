import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from '../website/node_modules/jsdom/lib/api.js';

const [ui, mode, locale, input] = process.argv.slice(2);
const data = JSON.parse(input === '-' ? fs.readFileSync(0, 'utf8') : input);
const dom = new JSDOM('<!doctype html><html><body><main id="app"></main></body></html>', {
  url: 'https://console.example.test/control/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, report = {}, calls = [];
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
Object.defineProperty(w.document, 'visibilityState', { value: 'visible', configurable: true });
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
let writes = 0;
w.fetch = async (url, options = {}) => {
  calls.push(String(url));
  let body;
  if (url === 'api/subscription/preview') { report.previewBody = JSON.parse(options.body); body = data.response; }
  else if (url === 'api/delivery-policies') body = data.catalog;
  else if (url === 'api/users/person/delivery-policy' && (options.method || 'GET') === 'GET') body = data.state;
  else if (url === 'api/users/person/delivery-policy') {
    const request = JSON.parse(options.body);
    if (request.confirm) { writes++; report.writeBody = request; body = data.changed; }
    else { report.previewBody = request; body = data.preview; }
  } else if (url === 'api/users/person') body = data.afterUser;
  else throw new Error('Unexpected request: ' + url);
  return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
};
for (const name of ['core.js', 'drafts.js', 'person-delivery-policy.js', 'format-preview.js', 'policy-preview.js']) {
  if (fs.existsSync(path.join(ui, name))) w.eval(fs.readFileSync(path.join(ui, name), 'utf8'));
}
const M = w.M, catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.i18n = { lang: locale, locale: () => locale === 'zh' ? 'zh-CN' : locale };
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(resolve => setImmediate(resolve)); };
if (mode === 'policy-preview') {
  const app = w.document.getElementById('app'); app.appendChild(M.formatPreviewSection());
  app.querySelector('[data-preview="person"]').value = 'person';
  app.querySelector('[data-preview="suffix"]').value = 'singbox';
  app.querySelector('[data-preview="destination"]').value = 'chat.example.test:443';
  app.querySelector('form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  report.text = app.querySelector('[data-preview="result"]').textContent;
  report.explanation = app.querySelector('[data-policy-explanation]')?.textContent;
  report.formatCount = app.querySelectorAll('[data-policy-preview] > details').length;
} else if (!M.personDeliveryPolicy) report.missing = 'person policy editor';
else {
  const card = M.personDeliveryPolicy(data.user, () => { report.saved = true; });
  const app = w.document.getElementById('app'); app.appendChild(card.node);
  await settle();
  const select = app.querySelector('[data-policy="override"]');
  select.value = 'full'; select.dispatchEvent(new w.Event('change', { bubbles: true })); select.focus();
  report.dirty = M.hasUnsavedEdits(app);
  card.refresh(data.user); await settle();
  report.choiceKept = select.value === 'full'; report.focusKept = w.document.activeElement === select;
  const form = app.querySelector('form');
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  report.reviewText = w.document.querySelector('dialog')?.textContent || '';
  w.document.querySelector('dialog .btn-primary')?.click();
  await settle();
  report.dirtyAfter = M.hasUnsavedEdits(app);
}
report.writes = writes;
report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
report.outside = calls.some(url => /^https?:/.test(url));
dom.window.close();
process.stdout.write(JSON.stringify(report));
