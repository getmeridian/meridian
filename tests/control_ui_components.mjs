/* Shared console components in a real DOM: core helpers, components, and the English catalog. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, scenario, locale = 'en', raw = 'null'] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw) || {};
const dom = new JSDOM('<!doctype html><html><body><main id="app"></main></body></html>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, document = w.document, report = {};
document.documentElement.lang = locale;
document.documentElement.dir = locale === 'fa' ? 'rtl' : 'ltr';
for (const name of ['core.js', 'components.js']) w.eval(fs.readFileSync(path.join(ui, name), 'utf8'));
const M = w.M, catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json'), 'utf8'));
M.i18n = { lang: locale, locale: () => locale };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
const app = document.getElementById('app');
const state = tip => {
  const button = tip.querySelector('button'), bubble = document.getElementById(button.getAttribute('aria-controls'));
  return { expanded: button.getAttribute('aria-expanded'), hidden: bubble.hidden, text: bubble.textContent,
    role: bubble.getAttribute('role'), label: button.getAttribute('aria-label'), focused: document.activeElement === button };
};
const key = (target, name) => target.dispatchEvent(new w.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));

if (scenario === 'component-tip') {
  const first = M.tip('Traffic', 'How much data this person may use.');
  const second = M.tip('Devices', M.h('span', {}, 'Apps that fetched the link.'));
  const box = M.h('input', { type: 'checkbox' });
  const inLabel = M.tip('Never used', 'People who never connected.');
  app.append(first, second, M.h('label', {}, box, 'Never used', inLabel), M.h('p', { id: 'outside', text: 'Elsewhere' }));
  report.initial = state(first);
  first.querySelector('button').click();
  report.opened = state(first);
  second.querySelector('button').click();
  report.switched = { first: state(first), second: state(second) };
  second.querySelector('button').click();
  report.toggledClosed = state(second);
  first.querySelector('button').focus(); first.querySelector('button').click();
  key(first.querySelector('.tip-bubble'), 'Escape');
  report.escaped = state(first);
  first.querySelector('button').click();
  document.getElementById('outside').click();
  report.outside = state(first);
  inLabel.querySelector('button').click();
  report.label = { tip: state(inLabel), checked: box.checked };
  const input = M.h('input', { type: 'text' });
  app.append(input);
  first.querySelector('button').focus(); first.querySelector('button').click();
  input.focus();
  report.tabbedAway = state(first);
  const details = M.h('details', { open: true }, M.h('summary', { text: 'Filters' }));
  const inside = M.tip('Quota', 'Share of the cap.');
  details.append(inside); app.append(details);
  inside.querySelector('button').click();
  details.open = false;
  await new Promise(resolve => setTimeout(resolve, 0));
  report.disclosureClosed = state(inside);
}

if (scenario === 'component-toast') {
  let runs = 0;
  M.toast('Disabled Kavi.', null, { label: M.t('action.undo'), run: () => { runs += 1; } });
  const host = document.getElementById('toast'), button = host.querySelector('.toast-action');
  report.offered = { shown: host.classList.contains('show'), action: host.classList.contains('has-action'),
    label: button?.textContent, type: button?.type, text: host.textContent };
  button.click(); button.click();
  report.undone = { runs, shown: host.classList.contains('show'), action: host.classList.contains('has-action'),
    waits: host.onmouseleave === null };
  M.toast('Saved.');
  report.plain = { shown: host.classList.contains('show'), action: host.classList.contains('has-action'),
    buttons: host.querySelectorAll('button').length, waits: host.onmouseenter === null };
}

process.stdout.write(JSON.stringify(report));
