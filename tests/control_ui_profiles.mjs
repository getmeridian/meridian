/* Real service replies drive the shipped person and bulk profile forms. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en', raw = '{}'] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw);
const dom = new JSDOM('<!doctype html><body><div id="app"></div></body>', { url: 'https://198.51.100.1/console/', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.document.documentElement.lang = locale;
w.document.documentElement.dir = locale === 'fa' ? 'rtl' : 'ltr';
Object.defineProperty(w.document, 'hidden', { value: false });
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const report = {}, requests = [];
let committed = false, commits = 0;
w.fetch = async (url, options = {}) => {
  requests.push(url);
  let body;
  if (url === 'api/access-profiles') body = data.catalog;
  else if (url === 'api/users/person/access') body = committed ? data.afterAccess : data.access;
  else if (url === 'api/users/person') body = committed ? data.afterUser : data.user;
  else if (url === 'api/people/bulk/preview') { report.previewRequest = JSON.parse(options.body); body = data.review; }
  else if (url === 'api/people/bulk/commit') { commits++; committed = true; report.commitRequest = JSON.parse(options.body); body = data.receipt; }
  else throw new Error('Unexpected request: ' + url);
  return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
};
const load = name => { if (fs.existsSync(path.join(ui, name))) w.eval(fs.readFileSync(path.join(ui, name), 'utf8')); };
['core.js', 'drafts.js', 'components.js', 'person-profiles.js', 'people-bulk-fields.js', 'people-bulk.js'].forEach(load);
const M = w.M, catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale, lang: locale };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(resolve => setImmediate(resolve)); };
const app = w.document.getElementById('app');
let card, fields;
if (!M.profileChoices || !M.reviewPeople || (mode === 'profiles-person' && !M.personProfiles)) report.missing = 'profile form';
else {
  if (mode === 'profiles-person') {
    card = M.personProfiles(data.user, () => { report.saved = true; });
    app.appendChild(card.node);
  } else {
    fields = M.bulkActionFields('set-profiles');
    app.appendChild(fields.nodes);
  }
  await settle();
  const selected = app.querySelector('input[value="limited"]');
  const full = app.querySelector('input[value="default"]');
  selected.checked = true; full.checked = false; selected.focus();
  if (mode === 'profiles-person') {
    report.dirty = M.hasUnsavedEdits(app);
    card.refresh(data.user);
    await settle();
    report.refreshKeptChoice = selected.checked && !full.checked;
    report.refreshKeptFocus = w.document.activeElement === selected;
    app.querySelector('form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  } else M.reviewPeople({ usernames: ['person'], action: { action: 'set-profiles', ...fields.value() } });
  await settle();
  report.reviewText = w.document.querySelector('dialog')?.textContent || '';
  w.document.querySelector('[data-bulk-commit]')?.click();
  await settle();
  if (mode === 'profiles-person') {
    report.dirtyAfterCommit = M.hasUnsavedEdits(app);
    report.effective = app.querySelector('[data-effective-access]')?.textContent || '';
  }
}
report.commits = commits;
report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
report.outside = requests.some(url => /^https?:/.test(url));
dom.window.close();
process.stdout.write(JSON.stringify(report));
