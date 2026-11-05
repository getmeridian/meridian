/* Settings journeys run through the real fragment router and shared draft dialogs. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, scenario, locale = 'en', raw = 'null'] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw) || {};
const section = data.section || '';
const hash = '#/settings' + (section ? '/' + section : '');
const dom = new JSDOM('<!doctype html><body><div id="app" class="boot"></div></body>', {
  url: 'https://198.51.100.1/console/' + hash, runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, document = w.document, events = [], writes = [];
const width = data.width || 1040;
w.matchMedia = query => ({ matches: /max-width/.test(query) && width <= 640, media: query,
  addEventListener() {}, removeEventListener() {} });
Object.defineProperty(document, 'hidden', { get: () => false });
w.Date.now = () => Date.parse('2026-11-02T12:00:00Z');
w.localStorage.setItem('mrd.lang', locale);
if (data.detailed) w.localStorage.setItem('mrd.mode', 'detailed');
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const sectionScrolls = [];
w.HTMLElement.prototype.scrollIntoView = function (options) {
  if (this.dataset.settingsSection) sectionScrolls.push({ section: this.dataset.settingsSection, inline: options?.inline, block: options?.block });
};
if (scenario === 'settings-chip-scroll') {
  Object.defineProperties(w.HTMLElement.prototype, {
    clientWidth: { get() { return this.classList.contains('settings-sections') ? 362 : 0; } },
    scrollWidth: { get() { return this.classList.contains('settings-sections') ? 700 : 0; } },
  });
}
let stepUps = 0, reauthenticated = false, privacy = {
  device_limits: data.fullPrivacy ? 'full' : 'off', default_device_limit: 3,
  usage_history: data.fullPrivacy ? 'daily' : 'off', history_days: 90,
};
let notice = { text: 'Saved notice', until: null }, language = 'auto';
function reply(body, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, headers: { get: () => null },
    text: async () => JSON.stringify(body) });
}
w.fetch = (url, options = {}) => {
  const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : undefined;
  events.push({ method, url, passive: options.headers?.['X-Meridian-Passive'] === '1' });
  if (method !== 'GET') writes.push({ url, body });
  if (url.startsWith('ui/locales/')) return reply(JSON.parse(fs.readFileSync(path.join(ui, url.slice(3)), 'utf8')));
  if (url === 'auth/state') return reply({ configured: true, owner_exists: true, signed_in: true,
    totp_enabled: false, passkeys: { available: false, reason: 'no_domain' } });
  if (url === 'ui/api/fleet') return reply({ profile_title: 'Sample VPN', servers: [], display_names: {} });
  if (url === 'api/settings') return reply({ profile_title: 'Sample VPN', public_url: 'https://198.51.100.1',
    owner_contact: { text: '<img src=x onerror="window.pwned=1">', link: 'mailto:owner@example.test' },
    client_routing: 'ru-split', response_rules: [{ name: 'Sample rule', action: { kind: 'format', format: 'mihomo' } }] });
  if (url === 'auth/passkeys') return reply({ passkeys: [] });
  if (url === 'auth/sessions') return reply({ sessions: [{ created_at: '2026-11-02T10:00:00Z',
    last_seen_at: '2026-11-02T12:00:00Z', current: true }] });
  if (url === 'api/language') {
    if (method === 'PUT') language = body.language;
    return reply({ language, changed: method === 'PUT' });
  }
  if (url === 'api/notice') {
    if (method === 'PUT' && body.confirm) notice = body.notice;
    const shown = method === 'PUT' ? body.notice : notice;
    return reply({ notice: shown, preview: shown?.text || '', active: !!shown, changed: !!body?.confirm });
  }
  if (url === 'api/privacy') {
    if (method === 'PUT' && !reauthenticated) return reply({ error: { code: 'reauth_required' } }, 403);
    if (method === 'PUT') privacy = body;
    return reply({ settings: privacy, changes: [], changed: method === 'PUT' });
  }
  if (url === 'auth/password') {
    return reauthenticated ? reply({}) : reply({ error: { code: 'reauth_required' } }, 403);
  }
  throw new Error('Unexpected Settings request: ' + method + ' ' + url);
};
const index = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
const scripts = [...index.matchAll(/<script src="ui\/([a-z0-9-]+\.js)"/g)].map(match => match[1]);
for (const script of scripts.filter(name => name !== 'app.js')) w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
const M = w.M;
M.toast = () => {};
M.confirmIdentity = async () => { stepUps++; reauthenticated = true; };
M.renderOverview = view => M.mount(view, M.h('h1', { text: 'Overview', 'data-overview': '' }));
let prompts = 0;
const ask = M.askToLeave;
M.askToLeave = () => { prompts++; return ask(); };
const settle = async () => {
  for (let i = 0; i < 6; i++) await new Promise(setImmediate);
  await new Promise(resolve => setTimeout(resolve, 10));
};
const view = () => document.querySelector('#view');
const active = () => view()?.querySelector('[data-settings-panel]:not([hidden])') || view();
const selected = () => active()?.dataset.settingsPanel || null;
const tab = name => view()?.querySelector('[data-settings-section="' + name + '"]');
const visibleTabs = () => [...(view()?.querySelectorAll('[data-settings-section]') || [])]
  .filter(node => !node.hidden).map(node => node.dataset.settingsSection);
const guard = () => document.querySelector('dialog [data-draft-choice]');
async function choose(choice) { document.querySelector('[data-draft-choice="' + choice + '"]')?.click(); await settle(); }
function change(field, value) {
  if (!field) return;
  field.value = value; field.dispatchEvent(new w.Event(field.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
}
function edit() {
  let field;
  if (section === 'privacy') { active()?.querySelector('input[value="private"]')?.click(); field = active()?.querySelector('input[max="100"]'); change(field, '4'); }
  else if (section === 'language') { field = active()?.querySelector('[data-language]'); change(field, 'ru'); }
  else if (section === 'security') {
    field = active()?.querySelector('input[type="password"]'); change(field, 'Draft password 123');
    change(active()?.querySelectorAll('input[type="password"]')[1], 'Draft password 123');
  } else if (section === 'advanced') { field = active()?.querySelector('[data-preview="user_agent"]'); change(field, 'Draft app header'); }
  else { field = active()?.querySelector('[data-notice="text"]'); change(field, 'Draft notice'); }
  return field;
}
function currentForm() {
  const selector = { privacy: 'input[max="100"]', language: '[data-language]', security: 'input[type="password"]',
    advanced: '[data-preview="user_agent"]', notice: '[data-notice="text"]' }[section];
  return active()?.querySelector(selector)?.closest('form');
}
w.eval(fs.readFileSync(path.join(ui, 'app.js'), 'utf8'));
await settle();
const report = {};

if (scenario === 'settings-routes' || scenario === 'settings-layout') {
  report.visiblePanels = [...(view()?.querySelectorAll('[data-settings-panel]') || [])]
    .filter(node => !node.hidden).map(node => node.dataset.settingsPanel);
  const tabs = [...(view()?.querySelectorAll('[data-settings-section]') || [])];
  report.linkedPanels = tabs.length === 6 && tabs.every(node => {
    const panel = document.getElementById(node.getAttribute('aria-controls'));
    return panel?.getAttribute('role') === 'tabpanel' && panel.getAttribute('aria-labelledby') === node.id;
  });
  report.selectedTabs = tabs.filter(node => node.getAttribute('aria-selected') === 'true').map(node => node.dataset.settingsSection);
  report.tabStops = tabs.filter(node => node.tabIndex === 0).map(node => node.dataset.settingsSection);
  if (scenario === 'settings-layout') {
    const css = ['tokens.css', 'styles.css', 'screens.css', 'settings.css'].filter(name => fs.existsSync(path.join(ui, name)))
      .map(name => fs.readFileSync(path.join(ui, name), 'utf8')).join('\n')
      .replace(/@media\s*\(pointer:\s*coarse\)(?:\s*,\s*\(max-width:\s*(\d+)px\))?/g,
        (_, limit) => Number(limit) >= width ? '@media all' : '@media not all')
      .replace(/@media\s*\(max-width:\s*(\d+)px\)/g, (_, limit) => Number(limit) >= width ? '@media all' : '@media not all');
    document.head.appendChild(M.h('style', { text: css }));
    const nav = view()?.querySelector('.settings-sections'), layout = view()?.querySelector('.settings-layout');
    const style = nav ? w.getComputedStyle(nav) : {};
    report.orientation = nav?.getAttribute('aria-orientation'); report.layout = layout ? w.getComputedStyle(layout).display : null;
    report.navDirection = style.flexDirection; report.overflow = style.overflowX; report.wrap = style.flexWrap;
    report.targets = tabs.map(node => { const css = w.getComputedStyle(node); return Math.max(parseFloat(css.minHeight) || 0, parseFloat(css.minBlockSize) || 0); });
    report.tabs = visibleTabs();
  }
}
if (scenario === 'settings-chip-scroll') {
  await settle();
  report.initial = sectionScrolls.at(-1) || null;
  await M.navigate('#/settings/notice'); await settle();
  report.afterRoute = sectionScrolls.at(-1) || null;
}
if (scenario === 'settings-keyboard') {
  const navigation = view()?.querySelector('.settings-sections');
  function key(value) { document.activeElement?.dispatchEvent(new w.KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true })); }
  tab('general')?.focus(); key('ArrowRight'); await settle(); report.afterArrow = selected();
  key('Home'); await settle(); report.afterHome = selected(); key('End'); await settle(); report.afterEnd = selected();
  key('ArrowUp'); await settle(); report.afterUp = selected();
  report.focusMatches = document.activeElement === tab(selected()); report.sameNavigation = navigation === view()?.querySelector('.settings-sections');
}
if (scenario === 'settings-advanced') {
  const preview = active()?.querySelector('form');
  report.beforeMode = M.mode(); report.beforeSection = selected(); report.beforeTabs = visibleTabs();
  const note = document.querySelector('.mode-note:not([hidden])');
  report.note = note?.querySelector('.mode-note-text')?.textContent;
  report.notes = document.querySelectorAll('.mode-note:not([hidden])').length;
  note?.querySelector('button')?.click(); await settle();
  report.noteAfter = document.querySelectorAll('.mode-note:not([hidden])').length;
  report.samePreview = !!preview && preview === active()?.querySelector('form');
  report.routing = !!active()?.textContent.includes('ru-split');
  report.responseRules = !!active()?.querySelector('[data-response-rules]')?.textContent.includes('Sample rule');
}
if (scenario === 'settings-draft') {
  if (data.navigation === 'back') { await M.navigate('#/settings'); await M.navigate(hash); await settle(); }
  const field = edit(), value = field?.value, origin = w.location.hash;
  report.dirty = M.hasUnsavedEdits(view());
  tab(section)?.click(); await settle(); report.currentDoesNotPrompt = !guard();
  async function leave() {
    if (data.navigation === 'hash') w.location.hash = '#/settings';
    else if (data.navigation === 'back') w.history.back();
    else tab('general')?.click();
    await settle(); await settle();
  }
  await leave(); report.prompt = !!guard(); await choose('stay');
  report.stayPreserved = !!field?.isConnected && field.value === value && w.location.hash === origin;
  await leave(); report.secondPrompt = !!guard(); await choose('leave');
  report.discarded = !field?.isConnected && !M.hasUnsavedEdits(view());
  report.storedDraft = [...Object.values(w.localStorage), ...Object.values(w.sessionStorage)].includes(value);
}
if (scenario === 'settings-preserve') {
  const field = edit(), navigation = view()?.querySelector('.settings-sections'), reads = events.length;
  field?.focus(); if (field?.type === 'text' || field?.type === 'password' || field?.tagName === 'TEXTAREA') field.setSelectionRange(2, 5);
  await M.chooseMode('detailed'); await settle();
  document.querySelector('[data-theme-choice="dark"]')?.click(); await settle();
  report.sameField = !!field?.isConnected && active()?.contains(field);
  report.focusKept = document.activeElement === field;
  report.sameNavigation = navigation === view()?.querySelector('.settings-sections'); report.sameReads = reads === events.length;
  report.dirty = M.hasUnsavedEdits(view()); report.prompts = prompts;
  report.theme = document.documentElement.getAttribute('data-theme');
}
if (scenario === 'settings-advanced-mode') {
  const field = edit(); M.chooseMode('simple'); await settle(); report.prompt = !!guard();
  await choose('stay'); report.stayed = M.mode() === 'detailed' && w.location.hash === hash && !!field?.isConnected;
  M.chooseMode('simple'); await settle(); report.secondPrompt = !!guard(); await choose('leave');
}
if (scenario === 'settings-clean') {
  report.dirty = M.hasUnsavedEdits(view()); tab('general')?.click(); await settle(); report.prompts = prompts;
}
if (['settings-lowering', 'settings-save', 'settings-clear'].includes(scenario)) {
  const form = currentForm();
  if (scenario === 'settings-lowering') {
    change(form?.querySelector('input[max="100"]'), ''); change(form?.querySelector('input[max="730"]'), '');
    form?.querySelector('input[name="' + (data.lowering === 'private' ? 'device_limits' : 'usage_history') + '"][value="' + (data.lowering === 'private' ? 'private' : 'off') + '"]')?.click();
  } else edit();
  report.dirtyBefore = M.hasUnsavedEdits(view());
  if (scenario === 'settings-clear') {
    change(form?.querySelector('[data-notice="text"]'), '{unfinished'); change(form?.querySelector('[data-notice="until"]'), '2000-01-01T00:00');
    form?.querySelectorAll('.row-actions button')[1]?.click();
  } else form?.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  const dialog = document.querySelector('dialog');
  report.review = !!dialog; report.beforeConfirm = writes.slice();
  report.mutationsBeforeConfirm = writes.filter(write => write.url !== 'api/notice' || write.body.confirm).length;
  const text = dialog?.textContent || '';
  report.disclosuresKept = data.lowering === 'private'
    ? text.includes(M.t('privacy.devices_private')) && text.includes(M.t('privacy.restore_private')) && text.includes(M.t('privacy.rekey_devices'))
    : text.includes(M.t('privacy.delete_history')) && text.includes(M.t('privacy.devices_full'));
  dialog?.querySelectorAll('.actions button')[data.cancel ? 0 : 1]?.click(); await settle();
  report.error = form?.querySelector('.form-error')?.textContent || '';
  report.dirtyAfter = M.hasUnsavedEdits(view());
  report.mutations = new Set(writes.filter(write => write.url !== 'api/notice' || write.body.confirm).map(write => write.url)).size;
}
if (scenario === 'settings-disclosures') {
  const general = active();
  report.generalEditable = general?.querySelectorAll('input,select,textarea,form').length;
  report.commands = [...(general?.querySelectorAll('.settings-facts .cmd code') || [])].map(node => node.textContent);
  report.contactIsText = !!general?.textContent.includes('<img src=x onerror="window.pwned=1">');
  await M.navigate('#/settings/security'); await settle();
  report.securityDisclosures = ['settings.password_text', 'totp.offer_why', 'totp.off_hint', 'passkeys.no_domain', 'passkeys.recovery', 'sessions.text']
    .every(key => active()?.textContent.includes(M.t(key)));
  report.learnMore = active()?.querySelectorAll('details.learn-more').length || 0;
  await M.navigate('#/settings/privacy'); await settle();
  report.privacyDisclosures = ['privacy.intro', 'privacy.no_addresses', 'privacy.devices_off', 'privacy.devices_private', 'privacy.devices_full', 'privacy.restore_private', 'privacy.history_off']
    .every(key => active()?.textContent.includes(M.t(key)));
}
report.selected = selected(); report.hash = w.location.hash; report.mode = M.mode(); report.dir = document.documentElement.dir;
report.writes = writes; report.stepUps = stepUps; report.outside = events.filter(event => event.url.includes('://'));
report.injected = !!view()?.querySelector('img[src="x"]') || !!w.pwned;
dom.window.close();
process.stdout.write(JSON.stringify(report));
