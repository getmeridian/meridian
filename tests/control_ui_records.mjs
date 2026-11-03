/* List and review journeys use the real console router with local service metadata. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, scenario, locale = 'en', raw = 'null'] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw) || {};
const screen = data.screen || 'tokens', width = data.width || 1040;
const dom = new JSDOM('<!doctype html><body><div id="app" class="boot"></div></body>', {
  url: 'https://198.51.100.1/console/#/' + screen, runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, document = w.document, events = [], writes = [];
const created = '2026-10-01T12:00:00Z';
let tokens = data.empty ? [] : [
  { id: '000000000001', name: '<img src=x onerror="window.pwned=1">', scope: null,
    capabilities: ['people.read', 'people.private'], created_at: created, expires_at: '2027-01-01T12:00:00Z',
    last_used_at: created, revoked_at: null },
  { id: '000000000002', name: 'Expired integration', scope: 'read', capabilities: ['people.read'],
    created_at: created, expires_at: '2026-10-02T12:00:00Z', last_used_at: null, revoked_at: null },
  { id: '000000000003', name: 'Retired integration', scope: 'people', capabilities: ['people.read'],
    created_at: created, expires_at: null, last_used_at: null, revoked_at: '2026-10-03T12:00:00Z' },
];
const revisions = data.empty ? [] : ['applied', 'failed', 'approved', 'proposed'].map((status, index) => ({
  number: 4 - index, status, author: { name: index ? 'Sample deployer' : '<img src=x onerror="window.pwned=1">' },
  created_at: created, detail: index === 1 ? 'The reviewed change could not be applied.' : '',
  plan_hash: '0'.repeat(64), intent: { delivery: { profile_title: 'Sample ' + (4 - index) } },
}));
let delayOlder = false, pendingOlder;
w.Date.now = () => Date.parse('2026-11-02T12:00:00Z');
Object.defineProperty(document, 'hidden', { get: () => false });
w.localStorage.setItem('mrd.lang', locale);
if (data.detailed) w.localStorage.setItem('mrd.mode', 'detailed');
if (data.dark) w.localStorage.setItem('mrd.theme', 'dark');
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
function reply(body) {
  return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) });
}
w.fetch = (url, options = {}) => {
  const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : null;
  events.push({ method, url });
  if (method !== 'GET') writes.push({ method, url, body });
  if (url.startsWith('ui/locales/')) return reply(JSON.parse(fs.readFileSync(path.join(ui, url.slice(3)), 'utf8')));
  if (url === 'auth/state') return reply({ configured: true, owner_exists: true, signed_in: true });
  if (url === 'ui/api/fleet') return reply({ profile_title: 'Sample VPN', servers: [], declared_users: [], display_names: {} });
  if (url === 'api/token-capabilities') return reply(data.capability_catalog);
  if (url === 'api/tokens') return reply({ tokens });
  if (url === 'api/tokens/000000000001' && method === 'DELETE') {
    tokens = tokens.map(token => token.id === '000000000001' ? { ...token, revoked_at: '2026-11-02T12:00:00Z' } : token);
    return reply({ revoked: true });
  }
  if (url === 'api/revisions') return reply({ revisions });
  if (url.startsWith('api/revisions/')) {
    const record = revisions.find(revision => revision.number === Number(url.split('/').at(-1)));
    if (delayOlder && record.number === 1) return new Promise(resolve => { pendingOlder = () => resolve(reply(record)); });
    return reply(record);
  }
  throw new Error('Unexpected record request: ' + method + ' ' + url);
};
const index = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
const scripts = [...index.matchAll(/<script src="ui\/([a-z0-9-]+\.js)"/g)].map(match => match[1]);
for (const script of scripts.filter(name => name !== 'app.js')) w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
const M = w.M;
M.toast = () => {};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(setImmediate); };
w.eval(fs.readFileSync(path.join(ui, 'app.js'), 'utf8')); await settle();
const view = () => document.querySelector('#view');
const rows = () => [...(view()?.querySelectorAll('[data-record]') || [])];
const menu = () => rows()[0]?.querySelector('.row-menu');
const report = {};

if (scenario === 'records-empty') {
  const empty = view()?.querySelector('.empty-state');
  report.emptyTitle = empty?.querySelector('.strong')?.textContent;
  report.emptyCaption = empty?.querySelector('.muted')?.textContent;
  report.emptyStatus = empty?.getAttribute('role');
  report.hasCreate = !!view()?.querySelector('[data-token-create]');
  report.note = !!document.querySelector('.mode-note:not([hidden])');
}
if (scenario === 'records-rows') {
  report.semanticList = view()?.querySelector('.record-list')?.tagName === 'UL';
  report.rows = rows().map(row => {
    const chip = row.querySelector('.status-chip'), name = row.querySelector('.record-name');
    const kind = ['success', 'danger', 'warn', 'neutral'].find(kind => chip?.classList.contains('status-chip-' + kind));
    return { nameIsolated: name?.tagName === 'BDI', status: !!chip, kind, facts: !!row.querySelector('.record-facts.row-sub'),
      menu: !!row.querySelector('.row-menu'), menuName: !!row.querySelector('.row-menu > summary')?.getAttribute('aria-label')?.includes(name?.textContent),
      actionsOnlyInMenu: ![...row.querySelectorAll('button')].some(button => !button.closest('.row-menu')),
      dim: row.classList.contains('row-dim'), canRevoke: !!row.querySelector('[data-record-action="revoke"]'),
      mainLink: !!row.querySelector('.record-main[href^="#/revisions/"]'),
      diffLink: !!row.querySelector('.row-menu [data-record-action="changes"][href^="#/revisions/"]') };
  });
  report.textOnly = view()?.textContent.includes('<img src=x onerror="window.pwned=1">') && !view()?.querySelector('img[src="x"]') && !w.pwned;
}
if (scenario === 'records-layout') {
  const css = ['tokens.css', 'styles.css', 'screens.css', 'settings.css', 'records.css'].filter(name => fs.existsSync(path.join(ui, name)))
    .map(name => fs.readFileSync(path.join(ui, name), 'utf8')).join('\n')
    .replace(/@media\s*\(pointer:\s*coarse\)(?:\s*,\s*\(max-width:\s*(\d+)px\))?/g,
      (_, limit) => Number(limit) >= width ? '@media all' : '@media not all')
    .replace(/@media\s*\(max-width:\s*(\d+)px\)/g, (_, limit) => Number(limit) >= width ? '@media all' : '@media not all');
  document.head.appendChild(M.h('style', { text: css }));
  const list = view()?.querySelector('.record-list'), row = rows()[0], panel = menu()?.querySelector('.row-menu-panel');
  report.targets = [...(view()?.querySelectorAll('.row-menu > summary, .row-menu-panel .btn') || [])].map(node => {
    const style = w.getComputedStyle(node); return Math.max(parseFloat(style.minHeight) || 0, parseFloat(style.minBlockSize) || 0);
  });
  report.menuOverflow = list ? w.getComputedStyle(list).overflow : null;
  report.phoneCards = !!list && w.getComputedStyle(list).display === 'flex' && !!row && w.getComputedStyle(row).borderRadius === 'var(--r2)';
  report.menuBounded = !!panel && w.getComputedStyle(panel).maxInlineSize.includes('100vw') && w.getComputedStyle(panel).overflowY === 'auto';
  report.surface = w.getComputedStyle(document.documentElement).getPropertyValue('--s1').trim();
}
if (scenario === 'records-permissions') {
  const actionMenu = menu(), summary = actionMenu?.querySelector('summary');
  if (actionMenu) actionMenu.open = true;
  summary?.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  report.escapeClosed = !!actionMenu && !actionMenu.open; report.focusReturned = document.activeElement === summary;
  if (actionMenu) actionMenu.open = true;
  document.body.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  report.outsideClosed = !!actionMenu && !actionMenu.open;
  if (actionMenu) actionMenu.open = true;
  const action = actionMenu?.querySelector('[data-record-action="permissions"]');
  action?.focus(); action?.click(); await settle();
  report.permissions = [...document.querySelectorAll('dialog li')].map(node => node.textContent);
  report.menuClosedForDialog = !!actionMenu && !actionMenu.open;
  document.querySelector('dialog .btn-icon')?.click(); await settle();
  report.dialogFocusReturned = document.activeElement === summary;
}
if (scenario === 'records-revoke') {
  const actionMenu = menu(); if (actionMenu) actionMenu.open = true;
  actionMenu?.querySelector('[data-record-action="revoke"]')?.click(); await settle();
  report.beforeConsent = writes.slice(); report.review = document.querySelector('dialog p')?.textContent;
  report.menuClosedForDialog = !!actionMenu && !actionMenu.open;
  document.querySelectorAll('dialog .actions button')[data.cancel ? 0 : 1]?.click(); await settle();
  report.revoked = !!tokens[0]?.revoked_at;
}
if (scenario === 'records-diff') {
  const actionMenu = menu(); if (actionMenu) actionMenu.open = true;
  actionMenu?.querySelector('[data-record-action="changes"]')?.click(); await settle();
  const compare = view()?.querySelector('select'), diff = view()?.querySelector('.diff');
  delayOlder = true;
  if (compare) { compare.value = '1'; compare.dispatchEvent(new w.Event('change')); } await settle();
  if (compare) { compare.value = '2'; compare.dispatchEvent(new w.Event('change')); } await settle();
  report.afterNewest = diff?.textContent; pendingOlder?.(); await settle(); report.afterOlder = diff?.textContent;
  report.hasDiff = !!diff?.querySelector('.diff-list li'); report.compare = compare?.value;
}
report.hash = w.location.hash; report.dir = document.documentElement.dir; report.theme = document.documentElement.getAttribute('data-theme');
report.writes = writes; report.outside = events.filter(event => event.url.includes('://'));
report.storedSecrets = [...Object.values(w.localStorage), ...Object.values(w.sessionStorage)].some(value => value.includes('test-only-secret'));
dom.window.close();
process.stdout.write(JSON.stringify(report));
