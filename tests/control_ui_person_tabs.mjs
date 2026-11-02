/* The person page keeps its summary, route tabs, and inline drafts on one mounted screen. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, scenario, locale = 'en', raw = '{}'] = process.argv.slice(2);
const data = JSON.parse(raw === '-' ? fs.readFileSync(0, 'utf8') : raw) || {};
const calendarWaiters = [];
const hash = '#/people/sample' + (data.tab ? '/' + data.tab : '');
const dom = new JSDOM('<!doctype html><html><body><div id="app"></div></body></html>', {
  url: 'https://198.51.100.1/console/' + hash, runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, document = w.document, events = [], writes = [], report = {};
const previewWaiters = [], patchWaiters = [];
document.documentElement.lang = locale;
document.documentElement.dir = locale === 'fa' ? 'rtl' : 'ltr';
if (data.mode) w.localStorage.setItem('mrd.mode', data.mode);
let now = Date.parse('2026-11-02T12:00:00Z'), timerId = 0;
const timers = new Map();
w.Date.now = () => now;
if (scenario === 'person-tabs-refresh' || data.clock) {
  w.setTimeout = (fn, delay = 0) => { const id = ++timerId; timers.set(id, { fn, at: now + delay }); return id; };
  w.clearTimeout = id => timers.delete(id);
}
Object.defineProperty(document, 'hidden', { get: () => false });
Object.defineProperty(w, 'isSecureContext', { value: true });
Object.defineProperty(w.navigator, 'clipboard', { value: { writeText: () => Promise.resolve() } });
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
let user = {
  username: 'sample', status: 'active', created_at: '2026-10-29T00:00:00Z',
  expire_at: '2026-12-02T12:00:00Z', traffic_limit_bytes: 100 * 1024 ** 3,
  traffic_limit_strategy: 'month', used_traffic_bytes: 51 * 1024 ** 3,
  lifetime_traffic_bytes: 61 * 1024 ** 3, language: null, note: 'Private note', tag: 'GROUP_1',
  activation_duration_days: null, traffic_reset_anchor_day: null,
  renewal_interval_days: null, renewal_interval_months: null, renewal_limit: null,
  device_limit: null, credential_generation: 1, profiles: ['default'],
  schedule: { expire_at: '2026-12-02T12:00:00Z', next_reset_at: '2026-12-01T00:00:00Z',
    activation_pending: false, renewal_enabled: false, renewals_left: null },
  ...data.user,
};
if (data.pending) user = { ...user, expire_at: null, activation_duration_days: 30,
  schedule: { ...user.schedule, expire_at: null, activation_duration_days: 30, activation_pending: true } };
const svg = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>').toString('base64');
const share = { username: 'sample', declared: false, subscription_url: 'https://198.51.100.1/sub/test-only',
  page_url: 'https://198.51.100.1/sub/test-only', qr_svg: svg,
  connections: [{ remark: 'Test connection', protocol: 'reality', uri: 'vless://test-only@198.51.100.1:443', available: true, qr_svg: svg }],
  formats: [], flow_blocks: { nodes: [] }, ...data.share };
function reply(body, status = 200) {
  return { ok: status < 400, status, headers: { get: () => null }, text: async () => JSON.stringify(body) };
}
w.fetch = async (url, options = {}) => {
  const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : undefined;
  events.push({ url, method, body, passive: options.headers?.['X-Meridian-Passive'] === '1' });
  if (url === 'auth/state') return reply({ configured: true, owner_exists: true, signed_in: true });
  if (url === 'ui/api/fleet') return reply({ profile_title: 'Test fleet', display_names: { sample: 'Sample person' }, servers: [], declared_users: [] });
  if (url === 'api/users/preview') {
    const extra = data.previewFields && Object.keys(body || {}).filter(key => !data.previewFields.includes(key));
    const answer = data.failPreview || extra?.length
      ? reply({ error: { code: 'invalid_request', message: 'Choose a valid account schedule.' } }, 400)
      : reply({ ...user.schedule, ...data.schedulePreview, expire_at: body.expire_at === undefined ? user.expire_at : body.expire_at });
    return data.holdPreview ? new Promise(resolve => previewWaiters.push(() => resolve(answer))) : answer;
  }
  if (url === 'api/people/bulk/preview') return reply(data.pendingPreview || { people: [{ after: {
    expire_at: null, activation_duration_days: user.activation_duration_days + body.action.months * 30,
  } }] });
  if (url.startsWith('ui/api/people/sample/expiry?')) {
    const answer = reply({ expire_at: '2032-06-01T12:00:00Z', months: Number(url.split('=').at(-1)) });
    return data.holdCalendar ? new Promise(resolve => calendarWaiters.push(() => resolve(answer))) : answer;
  }
  if (url === 'ui/api/people/sample/share') return reply(share);
  if (url.startsWith('api/users/sample/usage?')) return reply({ days: 7, history_days: 30, daily: [{ day: '2026-11-01', uplink_bytes: 4, downlink_bytes: 6 }] });
  if (url === 'api/users/sample/devices') return reply({ level: 'private', count: 1, limit: 3, devices: [] });
  if (url === 'api/access-profiles') return reply({ profiles: [{ id: 'default', built_in: true }] });
  if (url === 'api/users/sample/access') return reply({ connections: [{ remark: 'Test connection', granted_by: ['default'], available: true }] });
  if (url === 'api/delivery-policies') return reply({ policies: [{ id: 'full' }] });
  if (url === 'api/users/sample/delivery-policy') return reply({ override: null, effective: { policy: { id: 'full' }, source: 'fleet' } });
  if (url === 'api/users/sample') {
    if (method === 'PATCH') {
      writes.push(body);
      if (data.holdPatch) await new Promise(resolve => patchWaiters.push(resolve));
      user = { ...user, ...body, schedule: { ...user.schedule,
        ...(body.traffic_limit_strategy ? data.updatedSchedule : {}),
        expire_at: body.expire_at === undefined ? user.expire_at : body.expire_at } };
    }
    return reply(user);
  }
  throw new Error('Unexpected request: ' + method + ' ' + url);
};
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const index = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
for (const match of index.matchAll(/<script src="ui\/([a-z0-9-]+\.js)"/g)) {
  if (!['boot.js', 'i18n.js', 'app.js'].includes(match[1])) w.eval(fs.readFileSync(path.join(ui, match[1]), 'utf8'));
}
const M = w.M;
M.i18n = { lang: locale, locale: () => locale === 'zh' ? 'zh-CN' : locale, load: async () => {},
  langs: ['en', 'ru', 'fa', 'zh'], names: { en: 'English', ru: 'Русский', fa: 'فارسی', zh: '中文' } };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = text => events.push({ toast: text });
M.takeSetupToken = () => '';
M.renderServers = view => M.mount(view, M.h('h1', { text: 'Servers' }));
M.renderOverview = view => M.mount(view, M.h('h1', { text: 'Overview' }));
w.eval(fs.readFileSync(path.join(ui, 'app.js'), 'utf8'));
const settle = () => new Promise(resolve => setTimeout(resolve, 15));
async function advance(ms) {
  const target = now + ms;
  while (true) {
    const due = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    now = due[1].at; timers.delete(due[0]); due[1].fn(); await settle();
  }
  now = target; await settle();
}
await settle(); await settle();
const view = () => document.getElementById('view');
const summary = () => view().querySelector('[data-person-summary]');
const tabs = () => [...view().querySelectorAll('[role="tab"]')].filter(el => !el.closest('[hidden]'));
const selected = () => view().querySelector('[role="tab"][aria-selected="true"]')?.dataset.personTab;
const panels = () => [...view().querySelectorAll('[data-person-panel]')].filter(el => !el.hidden);
const fieldRow = key => view().querySelector('[data-person-field="' + key + '"]');
async function edit(key) {
  const row = fieldRow(key);
  if (row?.closest('details')) row.closest('details').open = true;
  row?.querySelector('[data-person-edit]')?.click(); await settle();
  return fieldRow(key)?.querySelector('form');
}
function change(field, value) {
  if (!field) return;
  field.value = value; field.dispatchEvent(new w.Event(field.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
}
async function navigate(tab) {
  await M.navigate('#/people/sample' + (tab && tab !== 'access' ? '/' + tab : '')); await settle();
}
function stored() { return [...Object.keys(w.localStorage), ...Object.keys(w.sessionStorage)].filter(key => key !== 'mrd.mode'); }
const guard = () => document.querySelector('[data-draft-choice="stay"]')?.closest('dialog');
async function choose(choice) { document.querySelector('[data-draft-choice="' + choice + '"]')?.click(); await settle(); await settle(); }

if (scenario === 'person-tabs-header') {
  const head = summary();
  report.summary = !!head; report.back = !!head?.querySelector('a[href="#/people"]');
  report.name = head?.querySelector('h1')?.textContent; report.status = head?.querySelector('.status-chip')?.textContent;
  report.tag = head?.querySelector('[data-person-tag]')?.textContent;
  report.meter = head?.querySelector('meter')?.getAttribute('value');
  report.reset = head?.querySelector('[data-person-reset]')?.textContent;
  const expiry = head?.querySelector('[data-person-expiry]');
  const meta = head?.querySelector('[data-person-meta]');
  report.meta = { together: !!meta && meta.contains(expiry) && meta.contains(head.querySelector('[data-person-reset]')),
    resetFirst: !!meta && !!(head.querySelector('[data-person-reset]').compareDocumentPosition(expiry) & w.Node.DOCUMENT_POSITION_FOLLOWING),
    text: meta?.textContent || '' };
  report.expiryFocusable = expiry?.tabIndex === 0;
  report.exactExpiry = expiry?.getAttribute('title') || '';
  report.actions = [...(head?.querySelectorAll('[data-person-action]') || [])].map(el => el.dataset.personAction);
  report.pageQr = view().querySelectorAll('.qr').length;
  head?.querySelector('[data-person-action="share"]')?.click(); await settle();
  const dialog = document.querySelector('dialog');
  report.dialogHeadings = dialog?.querySelectorAll('h2').length || 0;
  report.dialogCards = dialog?.querySelectorAll('.share-dialog-body .card').length || 0;
  report.shareCaption = dialog?.textContent.includes(M.t(share.subscription_url ? 'person.share_text' : 'person.no_public_url')) || false;
  report.dialogQr = dialog?.querySelectorAll('.qr').length || 0;
  report.shareActions = !!dialog && ['person.copy_link', 'person.open_page', 'person.copy_page', 'person.download_png', 'person.download_svg']
    .every(key => [...dialog.querySelectorAll('button, a')].some(el => el.textContent.includes(M.t(key))));
  report.connections = !!dialog?.querySelector('.individual-connections');
  report.dir = document.documentElement.dir;
}

if (scenario === 'person-tabs-toggle-action') {
  function snapshot() {
    const button = summary().querySelector('[data-person-action="toggle"]');
    return { label: button.textContent, pressed: button.getAttribute('aria-pressed'),
      expectedAction: button.textContent === M.t(user.status === 'disabled' ? 'person.enable' : 'person.disable'),
      status: summary().querySelector('.status-chip')?.textContent === M.t('status.' + user.status) };
  }
  report.states = [snapshot()];
  summary().querySelector('[data-person-action="toggle"]').click(); await settle(); await settle(); report.states.push(snapshot());
  summary().querySelector('[data-person-action="toggle"]').click(); await settle(); await settle(); report.states.push(snapshot());
  report.writes = writes;
}

if (scenario === 'person-tabs-route') {
  report.tabs = tabs().map(el => el.dataset.personTab); report.selected = selected();
  report.panels = panels().map(el => el.dataset.personPanel);
  report.labelled = panels().every(panel => {
    const tab = document.getElementById(panel.getAttribute('aria-labelledby'));
    return tab?.getAttribute('aria-controls') === panel.id;
  });
  report.hash = w.location.hash; report.content = panels()[0]?.textContent.trim() || '';
}

if (scenario === 'person-tabs-single') {
  const panel = panels()[0];
  report.before = { tabs: tabs().length, stripHidden: !!view().querySelector('[role="tablist"]')?.hidden,
    role: panel?.getAttribute('role'), label: panel?.getAttribute('aria-label'), content: panel?.textContent.trim() };
  await M.chooseMode('detailed'); await settle();
  report.after = { tabs: tabs().length, stripHidden: !!view().querySelector('[role="tablist"]')?.hidden,
    role: panel?.getAttribute('role'), labelled: !!document.getElementById(panel?.getAttribute('aria-labelledby')),
    samePanel: panel === panels()[0] };
}

if (scenario === 'person-tabs-access-focus') {
  view().querySelector('[data-person-tab="access"]').click(); await settle(); await settle();
  report.stripHidden = view().querySelector('[role="tablist"]').hidden;
  report.focusedTab = document.activeElement?.dataset.personTab || null;
  report.focusedPanel = document.activeElement?.dataset.personPanel || null;
  report.hiddenFocus = !!document.activeElement?.closest('[hidden]');
}

if (scenario === 'person-tabs-keyboard') {
  async function key(name) {
    const active = view().querySelector('[role="tab"][aria-selected="true"]');
    active?.focus(); active?.dispatchEvent(new w.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
    await settle(); await settle();
  }
  await key('ArrowRight'); report.afterArrow = selected();
  await key('Home'); report.afterHome = selected();
  await key('End'); report.afterEnd = selected();
  report.focused = document.activeElement?.dataset.personTab; report.hash = w.location.hash;
  w.history.back(); await settle(); await settle(); report.backSelected = selected(); report.backHash = w.location.hash;
}

if (scenario === 'person-tabs-simple-link') {
  report.selected = selected();
  const head = summary(), panel = panels()[0], note = view().querySelector('.person-mode-note');
  report.note = !!note && !note.hidden && note.textContent.includes(M.t('mode.shown_detailed'));
  note?.querySelector('button')?.click(); await settle();
  report.switched = M.mode() === 'detailed'; report.sameSummary = head === summary(); report.samePanel = panel === panels()[0];
  report.tabsAfter = tabs().map(el => el.dataset.personTab);
}

if (scenario === 'person-tabs-more') {
  report.tabs = tabs().map(el => el.dataset.personTab);
  const more = view().querySelector('[data-person-more]');
  report.metadataClosed = !!more && !more.open;
  if (more) more.open = true;
  report.notes = !!more?.querySelector('[data-person-field="note"]');
  report.tag = !!more?.querySelector('[data-person-field="tag"]');
  more?.querySelector('a[href="#/people/sample/devices"]')?.click(); await settle();
  report.devicesHash = w.location.hash;
  await navigate('access');
  view().querySelector('[data-person-more] a[href="#/people/sample/keys"]')?.click(); await settle();
  report.keysHash = w.location.hash; report.removal = panels()[0]?.textContent.includes(M.t('person.remove')) || false;
  report.mode = M.mode();
}

if (scenario === 'person-tabs-inline') {
  report.initialForms = view().querySelectorAll('form').length;
  const other = data.field === 'note' ? 'tag' : 'note';
  const otherForm = await edit(other);
  const otherField = otherForm?.querySelector('textarea, input');
  change(otherField, other === 'note' ? 'Other private draft' : 'KEEP_7');
  const form = await edit(data.field);
  report.expanded = !!form && !form.closest('[hidden]');
  const values = { expiry: ['input[type="date"]', '2032-06-01'], traffic: ['select[name="traffic_limit_strategy"]', 'week'],
    language: ['select[name="language"]', 'ru'], note: ['textarea', 'Changed private note'], tag: ['input[name="tag"]', 'TRIAL_7'] };
  const [selector, value] = values[data.field]; change(form?.querySelector(selector), value);
  form?.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
  report.savedClosed = !fieldRow(data.field)?.querySelector('form');
  report.otherDraft = data.field === 'note' ? otherField?.value === 'KEEP_7' ? 'Other private draft' : '' : otherField?.value;
  report.dirty = M.hasUnsavedEdits(view()); report.writes = writes; report.stored = stored();
}

if (scenario === 'person-tabs-guard') {
  if (data.navigation === 'back') { await navigate('usage'); await navigate('access'); }
  const form = await edit('note'), field = form?.querySelector('textarea'); change(field, 'Private draft');
  const origin = w.location.hash;
  async function leave() {
    if (data.navigation === 'cancel') form?.querySelector('[data-person-cancel]')?.click();
    else if (data.navigation === 'hash') w.location.hash = '#/people/sample/usage';
    else if (data.navigation === 'back') w.history.back();
    else view().querySelector('[data-person-tab="usage"]')?.click();
    await settle(); await settle();
  }
  await leave(); report.prompt = !!guard();
  await choose('stay'); report.stayPreserved = !!field?.isConnected && field.value === 'Private draft' && w.location.hash === origin;
  await leave(); report.secondPrompt = !!guard(); await choose('leave');
  report.discarded = !field?.isConnected && !M.hasUnsavedEdits(view()); report.writes = writes;
}

if (scenario === 'person-tabs-extend') {
  if (data.noteDraft) change((await edit('note'))?.querySelector('textarea'), 'Private draft');
  const menu = view().querySelector('[data-person-action="extend"]')?.closest('details');
  if (data.beforePreset === 'custom') {
    if (menu) menu.open = true;
    menu?.querySelector('[data-person-custom-expiry]')?.click(); await settle();
  } else if (data.beforePreset === 'first_use') {
    const editor = await edit('expiry'); change(editor?.querySelector('[name="expiry_mode"]'), 'first_use');
  }
  report.menu = !!menu;
  if (menu) menu.open = true;
  if (data.custom) menu?.querySelector('[data-person-custom-expiry]')?.click();
  else menu?.querySelector('[data-months="' + data.months + '"]')?.click();
  await settle(); await settle();
  const form = fieldRow('expiry')?.querySelector('form');
  report.expiryMode = form?.querySelector('[name="expiry_mode"]')?.value;
  report.dateEnabled = !!form && !form.querySelector('input[type="date"]').disabled;
  if (data.custom) change(form?.querySelector('input[type="date"]'), '2032-06-01');
  report.editor = !!form; report.beforeSave = writes.slice();
  report.noScheduleFieldset = !form?.querySelector('fieldset.lifecycle-fields');
  report.utcLines = form?.querySelectorAll('.expiry-utc').length;
  form?.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
  report.previewReads = events.filter(event => event.url?.includes('/expiry?')).map(event => event.url);
  report.reviews = events.filter(event => event.url === 'api/users/preview'); report.writes = writes;
  report.error = form?.querySelector('.form-error')?.textContent || '';
  report.dirty = M.hasUnsavedEdits(view());
  report.pendingReview = events.some(event => event.url === 'api/people/bulk/preview');
  report.prompt = !!guard(); report.noteDraft = fieldRow('note')?.querySelector('textarea')?.value;
}

if (scenario === 'person-tabs-custom-entry') {
  if (data.existing) await edit('expiry');
  const menu = view().querySelector('[data-person-action="extend"]')?.closest('details');
  if (menu) menu.open = true;
  menu?.querySelector('[data-person-custom-expiry]')?.click(); await settle();
  const form = fieldRow('expiry')?.querySelector('form'), date = form?.querySelector('input[type="date"]');
  const errors = () => [...form.querySelectorAll('[role="alert"]')].filter(el => el.textContent.trim()).map(el => el.textContent);
  report.initial = { mode: form?.querySelector('[name="expiry_mode"]')?.value, enabled: !date?.disabled,
    invalid: date?.getAttribute('aria-invalid'), errors: errors(), dirty: M.formDirty(form), focused: document.activeElement === date };
  if (data.submitEmpty) {
    form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle();
    report.invalidSave = { errors: errors(), focused: document.activeElement === date, writes: writes.slice() };
  }
  form?.querySelector('[data-person-cancel]')?.click(); await settle();
  report.prompt = !!guard(); report.closed = !fieldRow('expiry')?.querySelector('form');
}

if (scenario === 'person-tabs-custom-race') {
  const menu = view().querySelector('[data-person-action="extend"]').closest('details'); menu.open = true;
  menu.querySelector('[data-months="1"]').click(); await settle(); await settle();
  menu.open = true; menu.querySelector('[data-person-custom-expiry]').click(); await settle();
  calendarWaiters.splice(0).forEach(resolve => resolve()); await settle(); await settle();
  const form = fieldRow('expiry').querySelector('form');
  report.date = form.querySelector('input[type="date"]').value;
  report.mode = form.querySelector('[name="expiry_mode"]').value; report.dirty = M.formDirty(form); report.writes = writes;
}

if (scenario === 'person-tabs-traffic-preview') {
  const form = await edit('traffic'), anchor = form.querySelector('[name="traffic_reset_anchor_day"]');
  const preview = form.querySelector('.lifecycle-preview');
  const requests = () => events.filter(event => event.url === 'api/users/preview');
  const errors = () => [...form.querySelectorAll('.field-error')].filter(el => el.textContent.trim()).map(el => el.textContent);
  report.initialReset = preview.querySelector('bdi')?.title || '';
  change(form.querySelector('[name="traffic_limit_strategy"]'), 'month_rolling'); change(anchor, '0');
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle();
  report.invalid = { fields: errors(), summary: form.querySelector('.form-error').textContent,
    focused: document.activeElement === anchor, previews: requests().length, writes: writes.slice() };
  change(anchor, '15');
  report.corrected = { fields: errors(), summary: form.querySelector('.form-error').textContent };
  await advance(250);
  report.editing = { reset: preview.querySelector('bdi')?.title || '', previews: requests(), writes: writes.slice() };
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
  report.writes = writes; report.closed = !fieldRow('traffic').querySelector('form');
}

if (scenario === 'person-tabs-traffic-quota') {
  const form = await edit('traffic'), limits = form.querySelector('.limit-fields');
  const gb = limits.querySelector('input'), mode = limits.querySelector('select');
  const errors = () => [...form.querySelectorAll('[role="alert"]')].filter(el => el.textContent.trim()).map(el => el.textContent);
  if (data.emptyLimit) change(mode, 'limited'); else change(gb, '200');
  change(form.querySelector('[name="traffic_limit_strategy"]'), 'week'); await advance(250);
  report.editing = { errors: errors(), invalid: gb.getAttribute('aria-invalid'),
    previews: events.filter(event => event.url === 'api/users/preview').map(event => event.body) };
  if (!data.emptyLimit) {
    form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
    report.saved = { errors: errors(), writes: writes.slice(), closed: !fieldRow('traffic').querySelector('form') };
  }
}

if (scenario === 'person-tabs-sibling-focus') {
  const traffic = await edit('traffic'), anchor = traffic.querySelector('[name="traffic_reset_anchor_day"]');
  change(traffic.querySelector('[name="traffic_limit_strategy"]'), 'month_rolling'); change(anchor, '0');
  const note = await edit('note'), area = note.querySelector('textarea');
  change(area, 'Updated private note');
  note.querySelector('[type="submit"]').focus();
  note.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
  report.saved = writes.some(body => body.note === 'Updated private note');
  report.anchorFocused = document.activeElement === anchor;
}

if (scenario === 'person-tabs-custom-preview') {
  const form = await edit('expiry');
  const count = () => events.filter(event => event.url === 'api/users/preview').length;
  change(form.querySelector('[name="expiry_mode"]'), 'first_use'); await advance(250);
  report.firstUse = { previews: count() };
  const menu = view().querySelector('[data-person-action="extend"]').closest('details'); menu.open = true;
  menu.querySelector('[data-person-custom-expiry]').click(); await settle(); await advance(250);
  report.custom = { previews: count(), last: events.filter(event => event.url === 'api/users/preview').at(-1)?.body,
    mode: form.querySelector('[name="expiry_mode"]').value, date: form.querySelector('input[type="date"]').value };
}

if (scenario === 'person-tabs-saving-preview') {
  const sibling = await edit('note'), note = sibling.querySelector('textarea'); change(note, 'Other private draft');
  const form = await edit('expiry');
  if (data.firstUse) change(form.querySelector('[name="expiry_mode"]'), 'first_use');
  else change(form.querySelector('input[type="date"]'), '2032-06-01');
  change(form.querySelector('[name="renewal_mode"]'), 'months');
  change(form.querySelector('[name="renewal_interval"]'), '1');
  change(form.querySelector('[name="renewal_cap_mode"]'), 'limited');
  change(form.querySelector('[name="renewal_limit"]'), '2');
  const frozen = () => [...form.querySelectorAll('input, select, textarea')].every(field => field.disabled);
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle();
  report.startedFrozen = frozen(); await advance(300);
  report.review = { frozen: frozen(), saving: !!form.meridianSaving,
    previews: events.filter(event => event.url === 'api/users/preview').length };
  previewWaiters.splice(0).forEach(resolve => resolve()); await settle(); await settle();
  await advance(300);
  report.patch = { frozen: frozen(), saving: !!form.meridianSaving, writes: writes.length };
  patchWaiters.splice(0).forEach(resolve => resolve()); await settle(); await settle();
  report.closed = !fieldRow('expiry').querySelector('form');
  report.sibling = { connected: note.isConnected, value: note.value, enabled: !note.disabled };
}

if (scenario === 'person-tabs-sibling-refresh') {
  const expiry = await edit('expiry'), date = expiry.querySelector('input[type="date"]');
  if (data.dirty) change(date, '2032-06-01');
  date.focus();
  const traffic = await edit('traffic'); change(traffic.querySelector('[name="traffic_limit_strategy"]'), 'week');
  traffic.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
  await advance(300);
  const current = fieldRow('expiry').querySelector('form');
  report.expiry = { sameForm: current === expiry, sameDate: current.querySelector('input[type="date"]') === date,
    date: date.value, dirty: M.formDirty(current), reset: current.querySelector('.lifecycle-preview bdi')?.title || '' };
  report.writes = writes; report.trafficClosed = !fieldRow('traffic').querySelector('form');
}

if (scenario === 'person-tabs-clean-refresh') {
  const key = data.field, before = await edit(key);
  const old = before.querySelector(key === 'note' ? 'textarea' : '[name="expiry_mode"]');
  old.focus(); if (key === 'note') old.setSelectionRange(1, 2);
  if (key === 'note') user = { ...user, note: 'New' };
  else user = { ...user, expire_at: '2026-12-02T12:00:00Z',
    schedule: { ...user.schedule, expire_at: '2026-12-02T12:00:00Z', activation_pending: false } };
  view().meridianLiveStop.refresh(true); await settle(); await settle();
  const form = fieldRow(key).querySelector('form'), field = form.querySelector(key === 'note' ? 'textarea' : '[name="expiry_mode"]');
  report.refreshed = { value: field.value, dirty: M.formDirty(form), focused: document.activeElement === field,
    selection: key === 'note' ? [field.selectionStart, field.selectionEnd] : null,
    date: form.querySelector('input[type="date"]')?.value || '' };
  if (key === 'note') change(field, 'Old');
  else {
    const menu = view().querySelector('[data-person-action="extend"]').closest('details'); menu.open = true;
    menu.querySelector('[data-months="1"]').click(); await settle(); await settle();
  }
  fieldRow(key).querySelector('form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await settle(); await settle();
  report.writes = writes; report.pendingReview = events.some(event => event.url === 'api/people/bulk/preview');
  report.calendarReview = events.some(event => event.url?.includes('/expiry?'));
  report.passive = events.filter(event => event.url === 'api/users/sample' && event.method === 'GET').at(-1)?.passive;
}

if (scenario === 'person-tabs-passive-draft') {
  const form = await edit('expiry'), date = form.querySelector('input[type="date"]');
  change(date, '2032-06-01'); await advance(300);
  const previews = () => events.filter(event => event.url === 'api/users/preview').length;
  report.before = previews(); await advance(31000);
  report.after = previews(); report.sameField = date === fieldRow('expiry').querySelector('input[type="date"]');
  report.draft = date.value; report.dirty = M.formDirty(form);
  report.passive = events.filter(event => event.url === 'api/users/sample' && event.method === 'GET').at(-1)?.passive;
}

if (scenario === 'person-tabs-mode') {
  const field = view().querySelector('[name="device_mode"]');
  if (data.dirty) change(field, 'unlimited');
  M.chooseMode('simple'); await settle(); await settle();
  report.prompt = !!guard();
  if (data.dirty) {
    await choose('stay');
    report.stayed = M.mode() === 'detailed' && w.location.hash === hash && field?.value === 'unlimited' && field.isConnected;
    M.chooseMode('simple'); await settle(); report.secondPrompt = !!guard(); await choose('leave');
  }
  report.hash = w.location.hash; report.mode = M.mode();
}

if (scenario === 'person-tabs-appearance') {
  for (const name of ['tokens.css', 'styles.css', 'screens.css', 'person.css']) {
    document.head.appendChild(M.h('style', { text: fs.readFileSync(path.join(ui, name), 'utf8') }));
  }
  const form = await edit('expiry');
  function target(type) {
    const field = form?.querySelector('input[type="' + type + '"]');
    if (!field) return 0;
    const style = w.getComputedStyle(field);
    return Math.max(parseFloat(style.minHeight) || 0, parseFloat(style.minBlockSize) || 0);
  }
  report.dateTarget = target('date'); report.timeTarget = target('time');
  report.expiryIsolated = !!summary()?.querySelector('[data-person-expiry]')?.closest('bdi');
}

if (scenario === 'person-tabs-reviews') {
  const expiry = await edit('expiry'); change(expiry?.querySelector('input[type="date"]'), '2032-06-01');
  const traffic = await edit('traffic'); change(traffic?.querySelector('[name="traffic_limit_strategy"]'), 'week');
  traffic?.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
  expiry?.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await settle(); await settle();
  report.writes = writes;
  report.expiryPreview = events.filter(event => event.url === 'api/users/preview' && 'expire_at' in (event.body || {})).at(-1)?.body;
}

if (scenario === 'person-tabs-refresh') {
  const head = summary(), form = await edit('note'), field = form?.querySelector('textarea'); change(field, 'Private draft');
  field?.focus(); field?.setSelectionRange(2, 5);
  const menu = view().querySelector('[data-person-action="extend"]')?.closest('details'); if (menu) menu.open = true;
  user = { ...user, status: 'limited', used_traffic_bytes: 90 * 1024 ** 3, note: 'Remote note' };
  const target = now + 30000;
  while (true) {
    const due = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break; now = due[1].at; timers.delete(due[0]); due[1].fn(); await settle();
  }
  now = target; await settle();
  report.sameField = field === fieldRow('note')?.querySelector('textarea');
  report.focusKept = document.activeElement === field && field?.selectionStart === 2 && field?.selectionEnd === 5;
  report.sameSummary = head === summary(); report.menuOpen = menu?.open;
  report.note = field?.value; report.status = head?.querySelector('.status-chip')?.textContent === M.t('status.limited');
  report.meter = head?.querySelector('meter')?.getAttribute('value');
  report.passive = events.filter(event => event.url === 'api/users/sample' && event.method === 'GET').map(event => event.passive);
  await M.chooseMode('simple'); await settle();
  report.shownInSimple = !!field?.isConnected && !!field.closest('details')?.open;
  report.dirty = M.hasUnsavedEdits(view()); report.stored = stored();
}

report.outside = events.filter(event => event.url?.includes('://'));
dom.window.close();
process.stdout.write(JSON.stringify(report));
