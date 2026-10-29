/* Execute the shipped scripts with a DOM and local API doubles. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en', dataText = 'null'] = process.argv.slice(2);
const data = JSON.parse(dataText);
const dom = new JSDOM('<!doctype html><body><div id="view"></div></body>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const events = [], writes = [], delayed = {};
const server = {
  node_id: 'main', role: 'exit', region: '', live: true,
  last_seen_at: new Date().toISOString(), agent_version: '0.1.0', revision: 2, applied_revision: 1,
  in_sync: false, failed: ['core-unit'], errors: ['core-unit: unit exited'], traffic_bytes: 1024,
  runtime_available: true,
  runtime: { observed_at: new Date().toISOString(),
    health: { status: 'unhealthy', uptime_seconds: 7200, load1_milli: 1250,
      memory_available_bytes: 2 * 1024 ** 3, disk_free_bytes: 5 * 1024 ** 3,
      checks: [{ name: 'stats', status: 'fail', message: 'last collected 120 s ago' },
        { name: 'xray.api', status: 'fail', message: 'core API is not ready' },
        { name: 'xray.uptime', status: 'pass', message: 'up 42s' }] },
    versions: { agent: '0.1.0', xray: '26.7.28' } },
};
function reply(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(body)) };
}
w.fetch = (url, options) => {
  events.push({ method: options.method, url, headers: options.headers });
  if (url === 'ui/api/fleet') return Promise.resolve(reply({ servers: [server] }));
  throw new Error('Unexpected request: ' + url);
};
for (const script of ['core.js', 'people.js', 'fleet.js']) {
  w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
}
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = (text) => events.push({ toast: text });
M.fleetHistory = () => null;
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const view = w.document.getElementById('view');
const settle = () => new Promise(setImmediate);
const report = {};

if (mode === 'servers') {
  M.renderServers(view);
  await settle();
  report.text = view.textContent;
  report.badges = [...view.querySelectorAll('.server-badges .pill')].map(el => el.textContent);
}

process.stdout.write(JSON.stringify(report));
dom.window.close();
