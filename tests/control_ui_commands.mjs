/* Exercise shipped console removal and command-result rendering against same-origin replies. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, requestedMode, locale = 'en'] = process.argv.slice(2);
const reissuing = requestedMode.startsWith('reissue-');
const mode = reissuing ? requestedMode.slice('reissue-'.length) : requestedMode;
const dom = new JSDOM('<!doctype html><body><div id="view"></div></body>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const requests = [], writes = [];
const id = '1b6f3c1e-8f6a-4d2b-9c41-7a5e2d9f0c13';
const user = { username: 'person-a', status: 'active', created_at: '2026-10-30T12:00:00Z',
  traffic_limit_strategy: 'no_reset', used_traffic_bytes: 0, lifetime_traffic_bytes: 0,
  traffic_limit_bytes: 0, expire_at: null, traffic_reset_at: null, online_at: null };
const record = { node_id: 'main', status: 'waiting', wait_for: null, result: null,
  command: { command_id: id, kind: 'reset_inbound', inbound_tag: 'vless-reality' } };
function reply(body) {
  return { ok: true, status: 200, headers: { get: () => null }, text: () => Promise.resolve(JSON.stringify(body)) };
}
w.fetch = (url, options) => {
  if (!/^(api|ui\/api)\//.test(url)) throw new Error('outside request: ' + url);
  requests.push(options.method + ':' + url);
  if (url === 'ui/api/fleet') return Promise.resolve(reply({ servers: [{ node_id: 'main', role: 'exit', live: true,
    revision: 2, applied_revision: 1, in_sync: false, traffic_bytes: 0, errors: [], agent_version: '0.2.8',
    runtime_available: true, runtime: { versions: { xray: '26.7.28' }, health: {
      status: 'degraded', uptime_seconds: 60, load1_milli: 100, memory_available_bytes: 1048576,
      disk_free_bytes: 2097152, checks: [{ name: 'stats', status: 'warn', message: 'collection stalled' },
        { name: 'xray.uptime', status: 'pass', message: 'up 30 s' }],
    } } }] }));
  if (url === 'api/nodes/main/command-targets') return Promise.resolve(reply({ units: [{ resource_id: 'unit:netcore', name: 'netcore.service', core: true }] }));
  if (url === 'api/nodes/main/commands' && options.method === 'POST') {
    writes.push({ body: JSON.parse(options.body), csrf: options.headers['X-Meridian-Request'] });
    return Promise.resolve(reply({ ...record, status: 'pending', command: { command_id: id, kind: 'restart_unit', resource_id: 'unit:netcore' } }));
  }
  if (url === 'api/users/person-a' && options.method === 'GET') return Promise.resolve(reply(user));
  if (url === 'ui/api/people/person-a/share') return Promise.resolve(reply({ subscription_url: '', declared: mode === 'declared' }));
  if (url === 'api/users/person-a/cut-off') return Promise.resolve(reply({ nodes: mode === 'no-nodes' ? [] : [{ node_id: 'main', inbound_tags: ['vless-reality'] }] }));
  if (url === 'api/users/person-a/reissue' && options.method === 'POST') {
    writes.push({ body: JSON.parse(options.body), csrf: options.headers['X-Meridian-Request'] });
    return Promise.resolve(reply(writes.at(-1).body.cut_off ? { user, commands: [record] } : user));
  }
  if (options.method === 'DELETE') {
    writes.push({ body: JSON.parse(options.body), csrf: options.headers['X-Meridian-Request'] });
    return Promise.resolve(reply({ user: null, commands: writes.at(-1).body.cut_off ? [record] : [] }));
  }
  if (url === 'api/nodes/main/commands/' + id) {
    const failed = mode === 'failed';
    return Promise.resolve(reply({ ...record, status: mode === 'timed_out' ? 'expired' : failed ? 'failed' : 'succeeded',
      result: { status: failed ? 'failed' : 'succeeded', effect: mode === 'fallback' ? 'core_restart' : 'inbound_reset',
        error: failed ? { message: '<img src=x onerror="window.pwned=1">' } : null } }));
  }
  throw new Error('unexpected request: ' + url);
};
for (const script of ['core.js', 'drafts.js', 'qr-export.js', 'commands.js', 'people.js', 'expiry.js', 'person-connections.js', 'person.js', 'fleet.js']) {
  w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
}
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale,
  langs: ['en', 'ru', 'fa', 'zh'], names: { en: 'English', ru: 'Русский', fa: 'فارسی', zh: '中文' } };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = () => {};
M.go = () => {};
M.personHistory = M.devicesCard = () => M.h('div');
M.fleetHistory = () => M.h('div');
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const view = w.document.getElementById('view');
const restarting = mode.startsWith('restart');
if (restarting) M.renderServers(view); else M.renderPerson(view, 'person-a');
await settle();
const report = { requests, writes };
report.serverText = view.querySelector('.server')?.textContent || '';
const remove = view.querySelector(restarting ? '.server .row-actions button' : reissuing ? '.card-danger > .danger-row:first-of-type > button' : '.card-danger button.btn-danger');
if (mode === 'declared') {
  report.hasRemove = !!remove;
  report.declaredText = view.querySelector('.card-danger').textContent;
} else {
  remove.click();
  if (restarting || reissuing) remove.click();
  await settle();
  report.writesBeforeConfirm = writes.length;
  const dialog = w.document.querySelector('dialog');
  report.disclosure = dialog.textContent;
  if (!restarting) {
    const opt = dialog.querySelector('input[name="cut-off"]');
    report.defaultOn = opt ? opt.checked : null;
    report.choiceDisabled = opt ? opt.disabled : null;
    if (opt) opt.checked = !opt.disabled && mode !== 'default' && mode !== 'cancel';
  }
  const buttons = dialog.querySelector('.actions').children;
  buttons[mode === 'cancel' || mode === 'restart-cancel' ? 0 : 1].click();
  await settle();
  report.resultText = w.document.querySelector('dialog')?.textContent || '';
  report.images = w.document.querySelectorAll('img').length;
  report.pwned = !!w.pwned;
}
process.stdout.write(JSON.stringify(report));
dom.window.close();
