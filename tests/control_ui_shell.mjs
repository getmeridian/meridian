/* Exercise the real shell and Overview with local service replies and a visible DOM. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, scenario, locale = 'en', raw = 'null'] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw) || {};
const dom = new JSDOM('<!doctype html><body><div id="app" class="boot"></div></body>', {
  url: 'https://198.51.100.1/console/' + (data.hash || '#/'),
  runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, document = w.document;
let now = Date.parse('2026-11-02T12:00:00Z'), hidden = !!data.hidden, timerId = 0, failReads = !!data.initialFailure;
const timers = new Map(), requests = [], events = [], writes = [], scrolls = [];
let pendingPicker;
let historyState = 'good';
w.Date.now = () => now;
w.setTimeout = (fn, delay = 0) => { const id = ++timerId; timers.set(id, { fn, at: now + delay }); return id; };
w.clearTimeout = id => timers.delete(id);
Object.defineProperty(document, 'hidden', { get: () => hidden });
w.localStorage.setItem('mrd.lang', locale);
for (const [key, value] of Object.entries(data.storage || {})) w.localStorage.setItem(key, value);
if (data.noStorage) {
  w.Storage.prototype.getItem = w.Storage.prototype.setItem = w.Storage.prototype.removeItem = () => { throw new Error('storage disabled'); };
}
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
w.HTMLElement.prototype.scrollIntoView = function (options) {
  scrolls.push({ server: this.closest('[data-server]')?.dataset.server, section: this.dataset.modeSection,
    open: this.open, connected: this.isConnected, block: options?.block });
};
if (scenario === 'overview-server-jump') {
  w.requestAnimationFrame = fn => w.setTimeout(() => fn(now), 16);
  w.cancelAnimationFrame = w.clearTimeout;
}

const server = { node_id: 'main', role: 'node', region: 'DE', live: true, in_sync: true,
  agent_version: '0.2.17', revision: 2, applied_revision: 2, failed: [], errors: [], traffic_bytes: 1024,
  last_seen_at: '2026-11-02T12:00:00Z', runtime_available: true,
  runtime: { health: { status: 'healthy', memory_available_bytes: 4096, load1_milli: 120,
    disk_free_bytes: 8192, uptime_seconds: 1000, checks: [{ name: 'xray.uptime', status: 'pass', message: 'up 512 s' }] },
    versions: { xray: '26.7.28' } },
  capacity: { status: 'not_reported', recent: [], hourly: [] } };
const fleet = data.fleet || { profile_title: 'Sample VPN', servers: [server], declared_users: [], display_names: {},
  server_usage: data.server_usage || [], server_attention: data.server_attention || [] };
function reply(body, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(body)) });
}
w.fetch = (url, options = {}) => {
  requests.push({ method: options.method, url, headers: options.headers });
  if (options.method !== 'GET') writes.push({ url, body: JSON.parse(options.body || '{}') });
  if (url.startsWith('ui/locales/')) return reply(JSON.parse(fs.readFileSync(path.join(ui, url.slice(3)), 'utf8')));
  if (url === 'auth/state') return reply({ configured: true, owner_exists: true, signed_in: true });
  if (failReads && !url.startsWith('auth/')) return reply({ error: { code: 'network' } }, 503);
  if (url === 'ui/api/fleet') return reply(fleet);
  if (url.startsWith('api/people?')) {
    const params = new URLSearchParams(url.split('?')[1]);
    const key = params.has('expires') ? 'expires' : params.has('quota') ? 'quota'
      : params.get('limit') !== '1' ? 'picker' : 'all';
    let page = params.get('q') ? data.search_pages?.[params.get('q')]
      : params.get('cursor') ? data.pages?.picker_next : data.pages?.[key];
    page = page || { users: [], total: 0, population: 0, facets: { status: {}, never: 0 }, next_cursor: null };
    if (data.expiredPicker && params.get('cursor')) return reply({ error: { code: 'invalid_request', message: 'This page expired. Open the first page.' } }, 400);
    if (data.pickerRace && params.get('cursor')) return new Promise(resolve => { pendingPicker = () => resolve(reply(page)); });
    return reply(page);
  }
  if (url.startsWith('api/traffic/daily')) {
    if (data.historyOff || historyState === 'off') return reply({ error: { code: 'history_off' } }, 409);
    if (data.historyError || historyState === 'error') return reply({ error: { code: 'network' } }, 503);
    return reply(data.daily || { days: 30, history_days: 90, daily: [], nodes: [] });
  }
  if (url.startsWith('api/revisions')) return reply({ revisions: data.revisions || [] });
  if (url.startsWith('api/events')) return reply({ events: data.events || [], next_cursor: data.events?.at(-1)?.id || 0, has_more: false });
  if (url === 'api/settings') return reply({ profile_title: 'Sample VPN', client_routing: 'ru-split', response_rules: [] });
  if (url === 'auth/sessions') return reply({ sessions: [] });
  if (url.startsWith('ui/api/people/') && url.endsWith('/share')) return reply(data.share || {
    username: 'sample', declared: false, subscription_url: 'https://198.51.100.1/sub/sample-only',
    page_url: 'https://198.51.100.1/sub/sample-only', qr_svg: 'data:image/svg+xml,%3Csvg/%3E',
    profile_title: 'Sample VPN', connections: [], formats: [],
  });
  return reply({});
};
const index = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
const scripts = [...index.matchAll(/<script src="ui\/([a-z0-9-]+\.js)"/g)].map(match => match[1]);
for (const script of scripts.filter(name => name !== 'app.js')) w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
const M = w.M;
let screenDraws = 0, leavePrompts = 0;
const screen = name => view => { screenDraws++; M.mount(view, M.h('h1', { 'data-screen': name, text: name })); };
M.renderPeople = view => {
  screenDraws++;
  const form = M.h('form', {}, M.h('input', { 'data-draft': '', type: 'text' }));
  M.mount(view, form); M.trackForm(form);
};
M.renderPerson = M.renderPeople;
M.renderTokens = screen('tokens');
M.renderRevisions = screen('revisions');
if (scenario.startsWith('shell-')) M.renderOverview = screen('overview');
if (scenario === 'shell-settings') {
  M.noticeSection = M.languageSection = M.privacySection = M.passkeyCard = () => null;
  M.formatPreviewSection = () => M.h('section', { class: 'card' }, M.h('h2', { text: M.t('preview.title') }),
    M.h('input', { type: 'text', 'data-format-person': '' }));
}
M.toast = () => {};
const confirm = M.confirm;
M.confirm = (...args) => { if (args[0] === M.t('draft.title')) leavePrompts++; return confirm(...args); };
w.addEventListener('meridian:mode', event => events.push(event.detail.mode));
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(setImmediate); };
async function advance(ms) {
  const end = now + ms;
  while (true) {
    const due = [...timers].filter(([, item]) => item.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    now = due[1].at; timers.delete(due[0]); due[1].fn(); await settle();
  }
  now = end; await settle();
}
const modeButton = value => document.querySelector('[data-mode-choice="' + value + '"]');
const clickMode = async value => { modeButton(value)?.click(); await settle(); };
const menu = () => document.querySelector('.account-menu');
const report = {};
w.eval(fs.readFileSync(path.join(ui, 'app.js'), 'utf8'));
await settle();

if (scenario === 'shell-switch') await clickMode(data.choose || 'detailed');
if (scenario === 'shell-deep-link') {
  report.beforeMode = M.mode?.() || null;
  report.beforeScreen = document.querySelector('[data-screen]')?.dataset.screen || null;
  report.note = document.querySelector('.mode-note:not([hidden]) .mode-note-text')?.textContent || null;
  document.querySelector('.mode-note:not([hidden]) button')?.click(); await settle();
  report.noteAfter = document.querySelector('.mode-note:not([hidden])')?.textContent || null;
}
if (scenario === 'shell-draft') {
  const input = document.querySelector('[data-draft]');
  input.value = 'Unsaved text'; input.dispatchEvent(new w.Event('input', { bubbles: true })); input.focus();
  await clickMode('detailed');
  menu()?.setAttribute('open', '');
  document.querySelector('[data-theme-choice="dark"]')?.click(); await settle();
  report.sameInput = input === document.querySelector('[data-draft]');
  report.focusKept = document.activeElement === input;
  report.draft = document.querySelector('[data-draft]')?.value;
  report.unsaved = M.hasUnsavedEdits();
  report.leavePrompts = leavePrompts; report.screenDraws = screenDraws;
}
if (scenario === 'shell-account') {
  menu()?.setAttribute('open', '');
  report.themes = [...document.querySelectorAll('[data-theme-choice]')].map(el => el.dataset.themeChoice);
  const language = document.querySelector('[data-console-language]');
  report.languages = [...(language?.options || [])].map(el => el.value);
  report.signOutInside = !!menu()?.querySelector('[data-sign-out]');
  document.querySelector('[data-theme-choice="dark"]')?.click();
  if (language) { language.value = 'fa'; language.dispatchEvent(new w.Event('change')); }
  await settle(); menu()?.setAttribute('open', '');
  document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  report.closedOnEscape = menu()?.open === false;
}
if (scenario === 'shell-keyboard') {
  const first = modeButton('simple'); first?.focus();
  first?.dispatchEvent(new w.KeyboardEvent('keydown', { key: locale === 'fa' ? 'ArrowLeft' : 'ArrowRight', bubbles: true, cancelable: true }));
  await settle();
  report.keyboardFocus = document.activeElement?.dataset.modeChoice || null;
  report.radioStates = [...document.querySelectorAll('.mode-switch [role="radio"]')].map(el => el.getAttribute('aria-checked'));
}
if (scenario === 'shell-server-disclosure') {
  const detail = document.querySelector('[data-mode-section="capacity"]');
  report.initialOpen = detail?.open ?? null;
  report.capacityNote = detail?.querySelector('.mode-caption')?.textContent || null;
  if (detail) detail.open = true;
  const more = document.querySelector('.secondary-actions'); if (more) more.open = true;
  await advance(11000);
  report.afterPollOpen = document.querySelector('[data-mode-section="capacity"]')?.open ?? null;
  report.targetServer = document.querySelector('.server-focused')?.dataset.server || null;
  report.moreStayedOpen = document.querySelector('.secondary-actions')?.open === true;
}
if (scenario === 'shell-server-facts') {
  const card = document.querySelector('[data-server="main"]');
  const hostLabels = ['servers.version', 'servers.memory', 'servers.load', 'servers.disk', 'servers.uptime',
    'servers.core_uptime', 'servers.core_version'].map(key => M.t(key));
  const shown = () => [...card.querySelectorAll('dt')].filter(el => !el.closest('details') || el.closest('details').open)
    .map(el => el.textContent);
  const hiddenHost = () => hostLabels.every(label => !shown().includes(label));
  const more = card.querySelector('.secondary-actions');
  report.hostInitiallyHidden = hiddenHost();
  report.healthFactsVisible = ['servers.country', 'servers.revision', 'servers.traffic'].every(key => shown().includes(M.t(key)));
  report.statusChips = card.querySelector('.server-badges').children.length;
  report.restartInMenu = !!more.querySelector('button');
  more.open = true;
  report.hostAvailableInMore = hostLabels.every(label => shown().includes(label));
  await clickMode('detailed');
  report.sameCard = card === document.querySelector('[data-server="main"]');
  report.hostDetailedOutsideMore = hostLabels.every(label => [...card.querySelectorAll('dt')]
    .some(el => el.textContent === label && !el.closest('.secondary-actions')));
  more.open = false;
  await clickMode('simple');
  report.hostHiddenAgain = hiddenHost();
  more.open = true;
  await advance(11000);
  report.hostMoreAfterPoll = hostLabels.every(label => [...document.querySelectorAll('[data-server="main"] .secondary-actions dt')]
    .some(el => el.textContent === label));
  report.moreAfterPollOpen = document.querySelector('[data-server="main"] .secondary-actions')?.open;
}
if (scenario === 'shell-cli-hint') {
  const hint = document.querySelector('.cli-hint');
  report.hintParagraphs = hint.querySelectorAll('p').length + (hint.tagName === 'P' ? 1 : 0);
  report.hintCommand = [...hint.querySelectorAll('code')].map(el => el.textContent);
  report.hintCopyCount = hint.querySelectorAll('.cmd-copy').length;
  report.hintTitle = hint.textContent.includes(M.t('cli.title'));
}
if (scenario === 'shell-settings') {
  report.appearanceCards = [...document.querySelectorAll('#view h2')].filter(el => el.textContent === M.t('settings.appearance')).length;
  const sections = [...document.querySelectorAll('[data-mode-section]')];
  report.advancedInitiallyClosed = sections.length >= 2 && sections.every(el => !el.open);
  const input = document.querySelector('[data-format-person]');
  if (input) input.value = 'sample';
  await clickMode('detailed');
  report.samePreviewInput = !!input && input === document.querySelector('[data-format-person]');
  report.previewValue = document.querySelector('[data-format-person]')?.value;
  report.advancedAfterDetailedOpen = sections.length >= 2 && sections.every(el => el.open);
}

const clean = text => String(text || '').replace(/[\u2066-\u2069]/g, '').replace(/[۰-۹]/g, char => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(char)));
const attention = () => Object.fromEntries([...document.querySelectorAll('.overview-attention a:not([hidden])')]
  .map(el => [el.getAttribute('href'), clean(el.querySelector('[data-attention-count]')?.textContent || '')]));
function loadStyles(width = 390) {
  const css = ['tokens.css', 'styles.css', 'screens.css', 'people.css'].map(name => fs.readFileSync(path.join(ui, name), 'utf8')).join('\n')
    .replace(/@media\s*\(pointer:\s*coarse\)/g, width <= 640 ? '@media all' : '@media not all')
    .replace(/@media\s*\(max-width:\s*(\d+)px\)/g, (_, limit) => Number(limit) >= width ? '@media all' : '@media not all');
  document.head.appendChild(M.h('style', { text: css }));
}
if (scenario === 'overview-layout') {
  loadStyles(data.width);
  const actions = document.querySelector('.overview-actions'), header = document.querySelector('.page-head');
  const style = w.getComputedStyle(actions);
  const pageStyle = w.getComputedStyle(document.querySelector('.overview-page') || document.querySelector('#view'));
  report.layout = {
    singleActionGroup: document.querySelectorAll('.overview-actions').length === 1,
    headerActions: header.contains(actions),
    headerDisplay: w.getComputedStyle(header).display,
    position: style.position || 'static',
    order: style.order || '0',
    surface: style.background,
    border: style.borderBlockStart,
    shadow: style.boxShadow,
    safePadding: style.paddingBlockEnd,
    pagePadding: pageStyle.paddingBlockEnd,
    dates: [...document.querySelectorAll('.overview-traffic .usage-dates bdi')].map(el => el.textContent),
    peak: clean(document.querySelector('.overview-traffic svg + p')?.textContent),
    primary: actions.querySelector('.btn-primary')?.hasAttribute('data-overview-add'),
  };
}
if (scenario === 'overview-visual') {
  loadStyles();
  const peopleFigure = document.querySelector('[data-overview-active]').parentElement;
  const trafficFigure = document.querySelector('[data-overview-traffic]').textContent;
  report.visual = {
    background: w.getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
    columns: w.getComputedStyle(document.querySelector('.overview-grid')).gridTemplateColumns,
    readAlignment: w.getComputedStyle(document.querySelector('.kv-row dd')).textAlign,
    menuEdge: w.getComputedStyle(document.querySelector('.account-panel')).insetInlineEnd,
    targets: [...document.querySelectorAll('.mode-switch button,.account-toggle,.overview-actions .btn')].map(el => {
      const style = w.getComputedStyle(el);
      return Math.max(parseFloat(style.minHeight) || 0, parseFloat(style.minBlockSize) || 0);
    }),
    ageIsolated: document.querySelector('.live-updated').textContent.includes('\u2068'),
    peopleIsolated: peopleFigure.tagName === 'BDI' && peopleFigure.dir === 'ltr',
    trafficIsolated: trafficFigure.startsWith('\u2068') && trafficFigure.endsWith('\u2069'),
    windowIsolated: document.querySelector('[data-overview-window]').textContent.includes('\u2068'),
    meter: !!document.querySelector('.overview-fleet meter[aria-label]'),
  };
}
const reads = () => requests.filter(item => !item.url.startsWith('auth/') && !item.url.startsWith('ui/locales/'));
if (scenario === 'overview-server-jump') {
  const link = document.querySelector('.overview-attention a[href^="#/servers/"]');
  report.serverHref = link?.getAttribute('href');
  link?.click(); await settle(); await advance(20);
  const allowance = () => document.querySelector('.server-focused [data-mode-section="allowance"]');
  report.allowanceOpen = allowance()?.open;
  report.jumpScroll = scrolls.at(-1);
  report.focusedSummary = document.activeElement?.closest('[data-mode-section]')?.dataset.modeSection;
  await advance(11000);
  report.allowanceAfterPollOpen = allowance()?.open;
  report.scrollCountAfterPoll = scrolls.length;
  if (allowance()) allowance().open = false;
  await advance(11000);
  report.closedAfterPoll = allowance()?.open === false;
}
if (scenario === 'overview-mode') {
  report.beforeAttention = attention(); await clickMode('detailed');
}
if (scenario === 'overview-history-poll') {
  const traffic = () => clean(document.querySelector('[data-overview-traffic]')?.textContent);
  const historyReads = () => requests.filter(item => item.url.startsWith('api/traffic/daily')).length;
  report.beforeTraffic = traffic(); historyState = 'error'; await advance(31000);
  report.staleTraffic = traffic(); report.historyStale = !!document.querySelector('[data-history-stale]');
  report.partialRetry = !!document.querySelector('.live-retry:not([hidden])');
  const failedReads = historyReads(); await advance(31000); report.historyBackoff = historyReads() === failedReads;
  historyState = 'off'; document.querySelector('.live-retry')?.click(); await settle();
  report.offTraffic = traffic(); report.offUnavailable = !!document.querySelector('[data-history-unavailable]');
}
if (scenario === 'overview-recover') {
  report.noFalseEmpty = !document.querySelector('[data-overview-empty]');
  failReads = false; document.querySelector('.live-retry')?.click(); await settle();
  report.recovered = !!document.querySelector('[data-overview-empty]') && !document.querySelector('#view')?.classList.contains('view-stale');
}
if (scenario === 'overview-poll') {
  report.initialReads = reads().length;
  await advance(31000); report.visibleRefresh = reads().length;
  report.passiveRefresh = reads().slice(report.initialReads).every(item => item.headers['X-Meridian-Passive'] === '1');
  hidden = true; document.dispatchEvent(new w.Event('visibilitychange'));
  await advance(90000); report.hiddenReads = reads().length;
  hidden = false; document.dispatchEvent(new w.Event('visibilitychange')); await settle();
  const before = JSON.stringify(attention()); failReads = true;
  await advance(31000);
  report.keptDataOnFailure = before === JSON.stringify(attention());
  report.stale = document.querySelector('#view')?.classList.contains('view-stale');
  report.retry = !!document.querySelector('.live-retry:not([hidden])');
  report.resumedReads = reads().length;
  await M.navigate('#/people'); await advance(31000); report.disposedReads = reads().length;
}
if (scenario === 'overview-share') {
  document.querySelector('[data-overview-share]')?.click(); await settle();
  report.pickerPeople = [...document.querySelectorAll('[data-share-person]')].map(el => el.dataset.sharePerson);
  document.querySelector('[data-share-person]')?.click(); await settle();
  report.shareImage = !!document.querySelector('dialog .share img.qr');
  report.shareCopy = [...document.querySelectorAll('dialog button')].some(el => el.textContent.includes(M.t('person.copy_link')));
  report.qrFormats = [...document.querySelectorAll('dialog .qr-export-actions button')].map(el => /PNG/.test(el.textContent) ? 'PNG' : 'SVG');
}
if (scenario === 'overview-picker' || scenario === 'overview-picker-race' || scenario === 'overview-picker-expired') {
  document.querySelector('[data-overview-share]')?.click(); await settle();
  const people = () => [...document.querySelectorAll('[data-share-person]')].map(el => el.dataset.sharePerson);
  const search = document.querySelector('dialog input[type="search"]');
  const before = JSON.stringify(people());
  document.querySelector('.share-picker-pages button:last-child')?.click(); await settle();
  report.pickerNext = people();
  const pickerReads = requests.filter(item => item.url.startsWith('api/people?') && !item.url.includes('limit=1'));
  report.cursorRecovered = pickerReads.length === 3 && !pickerReads.at(-1).url.includes('cursor=');
  if (search) { search.value = 'sample-2'; search.dispatchEvent(new w.Event('input', { bubbles: true })); }
  if (pendingPicker) { pendingPicker(); await settle(); report.ignoredOldPage = JSON.stringify(people()) === before; }
  await advance(200); report.pickerSearch = people();
  report.sameSearch = !!search && document.querySelector('dialog input[type="search"]') === search;
  report.searchFocus = document.activeElement === search;
}
if (scenario === 'overview-add') {
  document.querySelector('[data-overview-add]')?.click(); await settle();
  report.addForm = !!document.querySelector('dialog form') && document.querySelector('dialog h2')?.textContent === M.t('people.add_title');
}
if (scenario.startsWith('overview-')) {
  report.attention = attention();
  report.people = { active: clean(document.querySelector('[data-overview-active]')?.textContent), total: clean(document.querySelector('[data-overview-total]')?.textContent) };
  report.calmEmpty = !!document.querySelector('[data-overview-empty]');
  report.emptyCheck = !!document.querySelector('[data-overview-empty] .icon');
  report.emptyCatalogText = M.t('overview.attention_empty');
  report.add = !!document.querySelector('[data-overview-add]'); report.share = !!document.querySelector('[data-overview-share]');
  report.fleetHealth = clean(document.querySelector('[data-overview-health]')?.textContent);
  report.healthKind = document.querySelector('[data-overview-health]')?.dataset.healthKind;
  report.traffic = clean(document.querySelector('[data-overview-traffic]')?.textContent);
  report.trafficDays = document.querySelector('[data-overview-window]')?.dataset.overviewWindow;
  report.historyUnavailable = !!document.querySelector('[data-history-unavailable]');
  report.sparkline = !!document.querySelector('.overview-traffic:not([hidden]) svg');
  report.recent = [...document.querySelectorAll('.overview-recent [data-recent-kind]')].map(el => el.dataset.recentKind);
}

report.mode = M.mode?.() || null;
report.route = M.route(); report.hash = w.location.hash;
report.tabs = [...document.querySelectorAll('.tabs a')].map(el => el.getAttribute('href').split('/').at(-1));
report.brand = document.querySelector('.top-brand')?.textContent || '';
report.brandHref = document.querySelector('.top-brand')?.getAttribute('href') || null;
report.events = events; report.writes = writes; report.requests = requests;
report.theme = document.documentElement.getAttribute('data-theme');
report.language = M.i18n.lang; report.dir = document.documentElement.dir;
function stored(key) { try { return w.localStorage.getItem(key); } catch { return null; } }
report.storedMode = stored('mrd.mode'); report.storedTheme = stored('mrd.theme'); report.storedLanguage = stored('mrd.lang');
report.misleadingDisclosureCaptions = [...document.querySelectorAll('[data-mode-section] > summary')]
  .filter(el => el.textContent.includes(M.t('mode.shown_detailed'))).length;
report.text = document.getElementById('app').textContent;
process.stdout.write(JSON.stringify(report));
dom.window.close();
