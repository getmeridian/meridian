/* Run the shipped console scripts in a DOM with mocked, same-origin API replies. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en'] = process.argv.slice(2);
const dom = new JSDOM('<!doctype html><body><div id="view"></div></body>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only',
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const events = [], writes = [], delayed = {};
const defaults = { device_limits: 'off', default_device_limit: 3, usage_history: 'off', history_days: 90 };
let state = { settings: { ...defaults }, changes: [] };
let putAttempts = 0;

function reply(body, status = 200) {
  return { ok: status === 200, status, headers: { get: () => null }, text: () => Promise.resolve(JSON.stringify(body)) };
}
function usage(days, retention = 730) {
  return { username: 'alice', days, history_days: retention,
    daily: Array.from({ length: days }, (_, i) => ({
      day: new Date(Date.UTC(2026, 9, 29 - days + 1 + i)).toISOString().slice(0, 10),
      uplink_bytes: i, downlink_bytes: 2 * i,
    })) };
}
w.fetch = (url, options) => {
  events.push(options.method + ':' + url);
  if (url === 'api/privacy' && options.method === 'GET') return Promise.resolve(reply(state));
  if (url === 'api/privacy' && options.method === 'PUT') {
    writes.push({ body: JSON.parse(options.body), csrf: options.headers['X-Meridian-Request'] });
    if (++putAttempts === 1) return Promise.resolve(reply({ error: { code: 'reauth_required' } }, 403));
    state = { settings: writes.at(-1).body, changes: [{ author: 'owner', changed_at: '2026-10-29T12:00:00Z',
      before: state.settings, after: writes.at(-1).body }], changed: true,
      deleted: { devices: 0, traffic_daily: 0, node_traffic_daily: 0 }, rekeyed_devices: 0 };
    return Promise.resolve(reply(state));
  }
  if (mode === 'history-off') return Promise.resolve(reply({ error: { code: 'history_off' } }, 409));
  const days = Number(url.split('days=')[1]);
  if (mode === 'race' && days !== 7) return new Promise((resolve) => { delayed[days] = resolve; });
  if (url.startsWith('api/traffic/daily')) {
    const daily = [{ day: '2026-10-29', bytes: 42 }];
    return Promise.resolve(reply({ days: 1, history_days: 90, daily,
      nodes: [{ node_id: 'main', daily }, { node_id: 'edge', daily: [{ day: '2026-10-29', bytes: 7 }] }] }));
  }
  return Promise.resolve(reply(usage(days)));
};
for (const script of ['core.js', 'fleet.js', 'privacy.js', 'usage.js']) {
  w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
}
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = (text) => events.push('toast:' + text);
M.confirmIdentity = () => { events.push('step-up'); return Promise.resolve(); };
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const view = w.document.getElementById('view');
const settle = () => new Promise(setImmediate);
const report = {};

if (['privacy', 'cancel', 'lower-invalid', 'malicious', 'bad-number', 'unchanged'].includes(mode)) {
  if (mode === 'lower-invalid') state.settings = { ...defaults, device_limits: 'full', usage_history: 'daily' };
  if (mode === 'malicious') state.changes = [{ author: '<img src=x onerror="window.pwned=1">',
    changed_at: '2026-10-29T12:00:00Z', before: defaults, after: defaults }];
  view.appendChild(M.privacySection());
  await settle();
  if (mode === 'malicious') {
    report.images = view.querySelectorAll('img').length;
    report.text = view.textContent;
    report.pwned = !!w.pwned;
  } else {
    const form = view.querySelector('form');
    const cap = form.querySelector('input[max="100"]');
    const days = form.querySelector('input[max="730"]');
    if (mode === 'lower-invalid') {
      cap.value = days.value = '';
      form.querySelector('input[name="device_limits"][value="off"]').click();
      form.querySelector('input[name="usage_history"][value="off"]').click();
    } else if (mode !== 'unchanged') {
      form.querySelector('input[value="private"]').click();
      form.querySelector('input[value="daily"]').click();
      days.value = mode === 'bad-number' ? '0' : '7';
      days.dispatchEvent(new w.Event('input'));
    }
    form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    await settle();
    report.writesBeforeConfirm = writes.length;
    report.dialog = w.document.querySelector('dialog')?.textContent || '';
    if (mode !== 'bad-number' && mode !== 'unchanged') {
      const actions = w.document.querySelector('dialog .dialog-body .actions');
      actions.children[mode === 'cancel' ? 0 : 1].click();
      await settle();
    }
    report.error = form.querySelector('.form-error').textContent;
    report.settings = state.settings;
  }
} else {
  const card = mode === 'fleet' ? M.fleetHistory() : M.personHistory('alice');
  view.appendChild(card);
  await settle();
  if (mode === 'race') {
    const picker = card.querySelector('select');
    for (const n of [30, 90]) {
      picker.value = String(n);
      picker.dispatchEvent(new w.Event('change'));
      await settle();
    }
    delayed[90](reply(usage(14, 14)));
    await settle();
    report.afterNewest = card.textContent;
    delayed[30](reply({ error: { code: 'internal' } }, 500));
    await settle();
    report.afterOlder = card.textContent;
    report.range = picker.value;
  }
  if (mode === 'fleet') {
    const picker = card.querySelectorAll('select')[1];
    picker.value = 'edge';
    picker.dispatchEvent(new w.Event('change'));
  }
  const elements = [...card.querySelectorAll('svg, svg *')];
  report.svgNamespaces = elements.map((element) => element.namespaceURI);
  report.bars = [...card.querySelectorAll('svg rect')].map((bar) => ({
    height: Number(bar.getAttribute('height')), label: bar.getAttribute('aria-label'),
  }));
  report.text = card.textContent;
  report.links = [...card.querySelectorAll('a')].map((a) => a.getAttribute('href'));
}
report.events = events;
report.writes = writes;
dom.window.close();
process.stdout.write(JSON.stringify(report));
