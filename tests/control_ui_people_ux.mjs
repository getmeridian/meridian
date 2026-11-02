/* People presentation against bounded service pages; no browser layout simulation. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { queryKey } from './console_people_page.mjs';
import { loadStyles } from './support/console_styles.mjs';
const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale, raw] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw);
const dom = new JSDOM('<!doctype html><body><div id="app"></div></body>', {
  url: 'https://198.51.100.1/console/' + (data.hash || '#/people'), runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, report = {}, requests = [];
Object.defineProperty(w.document, 'hidden', { value: false, configurable: true });
w.document.documentElement.lang = locale;
w.document.documentElement.dir = locale === 'fa' ? 'rtl' : 'ltr';
w.Date.now = () => Date.parse('2026-11-01T12:00:00Z');
if (data.displayMode) w.localStorage.setItem('mrd.mode', data.displayMode);
w.fetch = async (url, options = {}) => {
  requests.push({ url, passive: options.headers?.['X-Meridian-Passive'] === '1' });
  let body;
  if (url === 'auth/state') body = { configured: true, owner_exists: true, signed_in: true };
  else if (url === 'ui/api/fleet') body = { servers: [], declared_users: [], display_names: {} };
  else if (url.startsWith('api/people?')) body = data.replies[queryKey(url.split('?')[1])];
  else throw new Error('Unexpected request: ' + url);
  if (!body) throw new Error('Missing service page: ' + url);
  return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
};
const load = name => w.eval(fs.readFileSync(path.join(ui, name), 'utf8'));
['core.js', 'drafts.js', 'components.js', 'shell.js', 'expiry.js', 'lifecycle.js', 'people.js',
  'people-bulk-fields.js', 'people-bulk.js', 'people-list.js'].forEach(load);
const M = w.M;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale, load: async () => {}, lang: locale,
  langs: ['en', 'ru', 'fa', 'zh'], names: { en: 'English', ru: 'Русский', fa: 'فارسی', zh: '中文' } };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, key) => String(params[key] ?? ''));
M.takeSetupToken = () => '';
let additions = 0;
M.addPerson = () => { additions++; };
M.renderPerson = (view, username) => { report.opened = username; M.mount(view, M.h('p', { text: 'Person' })); return () => {}; };
load('app.js');
const settle = () => new Promise(resolve => setTimeout(resolve, 180));
await settle();
const view = () => w.document.getElementById('view');
const visible = node => !!node && !node.closest('[hidden]');
const rows = () => [...view().querySelectorAll('a.row-link')];
const selected = () => Number(view().querySelector('[data-bulk-count]')?.getAttribute('data-bulk-count'));
const snapshot = () => ({
  barHidden: !visible(view().querySelector('.people-bulk-bar')),
  matchingHidden: !visible(view().querySelector('[data-select-matching]')),
  selected: selected(),
  checkboxes: [...view().querySelectorAll('.people-select input')].filter(visible).length,
  pageSelectVisible: visible(view().querySelector('[name="select-page"]')),
  toggleVisible: visible(view().querySelector('[data-people-select]')),
  togglePressed: view().querySelector('[data-people-select]')?.getAttribute('aria-pressed'),
  reserved: !!view().querySelector('.people-page.has-selection'),
});
report.initial = snapshot();
report.addVisible = [...view().querySelectorAll('button')].some(button => visible(button) && button.textContent.includes(M.t('people.add')));
report.filterChips = [...view().querySelectorAll('.people-filter[data-filter]')].map(chip => chip.getAttribute('data-filter'));
const active = () => [...view().querySelectorAll('[data-remove-filter]')].map(chip => ({
  key: chip.getAttribute('data-remove-filter'), text: chip.textContent, label: chip.getAttribute('aria-label'),
}));
report.active = active();
report.dir = w.document.documentElement.dir;
report.text = view().textContent;
if (data.scenario === 'chips' && data.width) {
  loadStyles(w, ui, ['tokens.css', 'styles.css', 'screens.css', 'people.css'], data.width);
  const filters = view().querySelector('.people-filter-chips'), style = w.getComputedStyle(filters);
  report.filterLayout = { wrap: style.flexWrap, overflow: style.overflowX,
    shrink: [...filters.children].map(chip => w.getComputedStyle(chip).flexShrink),
    underSearch: !!(view().querySelector('input[type="search"]').compareDocumentPosition(filters) & w.Node.DOCUMENT_POSITION_FOLLOWING),
    activeOutside: !filters.contains(view().querySelector('.people-active-filters')),
    panelPosition: w.getComputedStyle(filters.querySelector('.people-filter-panel')).position };
}
if (data.scenario === 'selection') {
  const original = rows();
  view().querySelector('[data-people-select]')?.click();
  report.selecting = snapshot();
  const checkbox = view().querySelector('[data-person]');
  checkbox.checked = true; checkbox.dispatchEvent(new w.Event('change', { bubbles: true }));
  report.one = snapshot();
  report.direct = [...view().querySelectorAll('.people-bulk-bar > .people-bulk-actions > [data-bulk-action]')].map(button => button.getAttribute('data-bulk-action'));
  report.more = [...view().querySelectorAll('.people-bulk-more [data-bulk-action]')].map(button => button.getAttribute('data-bulk-action'));
  const more = view().querySelector('.people-bulk-more');
  more?.querySelector('summary').click(); report.moreOpened = !!more?.open;
  more?.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  report.moreEscaped = !more?.open && w.document.activeElement === more?.querySelector('summary');
  const page = view().querySelector('[name="select-page"]');
  page.checked = true; page.dispatchEvent(new w.Event('change', { bubbles: true }));
  report.page = snapshot();
  await M.chooseMode('detailed');
  report.detailed = snapshot();
  await M.chooseMode('simple');
  report.simpleAgain = snapshot();
  view().meridianLiveStop.refresh(true); await settle();
  report.refreshed = snapshot();
  report.sameRows = rows().every((row, index) => row === original[index]);
  view().querySelector('[data-select-matching]')?.click();
  report.matching = snapshot();
  report.matchingText = view().querySelector('[data-select-matching]')?.textContent;
  report.countText = view().querySelector('[data-bulk-count]')?.textContent;
  view().querySelector('[data-bulk-clear]')?.click();
  report.cleared = snapshot();
  view().querySelector('[data-people-select]')?.click();
  report.finished = snapshot();
} else if (data.scenario === 'bar-layout') {
  const page = view().querySelector('[name="select-page"]');
  page.checked = true; page.dispatchEvent(new w.Event('change', { bubbles: true }));
  loadStyles(w, ui, ['tokens.css', 'styles.css', 'screens.css', 'people.css'], data.width);
  const bar = view().querySelector('.people-bulk-bar'), style = w.getComputedStyle(bar);
  report.layout = {
    visible: visible(bar), position: style.position, direction: style.flexDirection, wrap: style.flexWrap,
    display: style.display, actionsDisplay: w.getComputedStyle(bar.querySelector('.people-bulk-actions')).display,
    countGrows: w.getComputedStyle(bar.querySelector('[role="status"]')).flexGrow,
    matchingBasis: w.getComputedStyle(bar.querySelector('[data-select-matching]')).flexBasis,
    matchingMargin: w.getComputedStyle(bar.querySelector('[data-select-matching]')).marginInlineStart,
    matchingSize: w.getComputedStyle(bar.querySelector('[data-select-matching]')).inlineSize,
    matchingVisible: visible(bar.querySelector('[data-select-matching]')),
  };
} else if (data.scenario === 'chips') {
  view().querySelector('[data-people-select]')?.click();
  const checkbox = view().querySelector('[data-person]');
  if (checkbox) { checkbox.checked = true; checkbox.dispatchEvent(new w.Event('change', { bubbles: true })); }
  if (data.remove !== 'q') {
    const panel = view().querySelector('[data-filter="' + data.remove + '"]');
    panel?.querySelector('summary').click(); report.panelOpened = !!panel?.open;
    const input = view().querySelector('[name="' + data.remove + '"]');
    input?.focus(); view().meridianLiveStop.refresh(true); await settle();
    report.panelKept = !!panel?.open && w.document.activeElement === input;
    panel?.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    report.panelEscaped = !panel?.open && w.document.activeElement === panel?.querySelector('summary');
  }
  const remove = view().querySelector('[data-remove-filter="' + data.remove + '"]');
  report.removeVisible = visible(remove);
  remove?.click(); await settle();
  report.afterRemove = active(); report.afterHash = w.location.hash;
  report.afterSelected = selected();
  report.focusAfterRemove = w.document.activeElement?.closest('[data-filter]')?.getAttribute('data-filter');
  report.shown = rows().map(row => decodeURIComponent(row.getAttribute('href').split('/').at(-1)));
  const values = ['status', 'expires', 'quota', 'tag', 'profile', 'never', 'attention', 'lifecycle', 'sort', 'order'];
  report.controls = Object.fromEntries(values.map(name => {
    const input = view().querySelector('[name="' + name + '"]');
    return [name, input?.type === 'checkbox' ? input.checked : input?.value];
  }));
  M.render(true); await settle();
  report.reloaded = active();
} else if (data.scenario === 'row') {
  const row = rows().find(row => row.getAttribute('href').endsWith('/p-a'));
  report.row = { tag: row.querySelector('.person-tag')?.textContent, caption: row.querySelector('.usage-text')?.textContent,
    meter: row.querySelector('meter')?.value, expiry: row.querySelector('.people-expiry')?.textContent,
    exactExpiry: row.querySelector('.people-expiry')?.title, secondary: row.querySelector('.people-secondary')?.textContent,
    notes: visible(row.querySelector('[data-people-note]')), declared: visible(row.querySelector('[data-people-declared]')),
    reasons: row.querySelector('.people-reasons')?.textContent || '',
    nestedCheckbox: !!row.querySelector('input'), href: row.getAttribute('href') };
  row.querySelector('.person-tag').click(); await settle();
  report.clicked = M.route();
  M.go('people'); await settle();
  const returned = rows().find(row => row.getAttribute('href').endsWith('/p-a'));
  returned.focus();
  const enter = new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  returned.dispatchEvent(enter);
  // jsdom has no native Enter activation for links; use the browser's default click.
  if (!enter.defaultPrevented) returned.click();
  await settle(); report.keyboard = M.route();
} else if (data.scenario === 'empty') {
  const empty = view().querySelector('.empty-state');
  report.empty = { visible: visible(empty), role: empty?.getAttribute('role'), text: empty?.textContent,
    add: visible(view().querySelector('[data-empty-add]')), clear: visible(view().querySelector('[data-empty-clear]')) };
  view().querySelector('[data-empty-add]')?.click();
  view().querySelector('[data-empty-clear]')?.click(); await settle();
  report.afterEmpty = rows().map(row => decodeURIComponent(row.getAttribute('href').split('/').at(-1)));
  report.afterHash = w.location.hash;
}
report.additions = additions;
report.requests = requests;
report.stored = [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)];
dom.window.close();
process.stdout.write(JSON.stringify(report));
