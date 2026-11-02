/* Load the public demo HTML, its adapter, and every real console asset together. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [, , , mode, locale, input] = process.argv;
// Large scenarios arrive on stdin: Linux caps one argument at 128 KiB.
const data = JSON.parse(input === '-' ? fs.readFileSync(0, 'utf8') : input);
const directory = path.resolve(data.directory);
const html = fs.readFileSync(path.join(directory, 'console/index.html'), 'utf8');
const dom = new JSDOM(html, { url: `https://198.51.100.1/demo/console/?lang=${locale}`, runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
let demoNow;
if (mode === 'showcase-queries' || mode.startsWith('showcase-schedule-') || mode.startsWith('showcase-undo-') || mode.startsWith('showcase-share-') || mode.startsWith('showcase-token-')) {
  const generated = Date.parse(JSON.parse(fs.readFileSync(path.join(directory, 'console/demo-data.json'), 'utf8')).generated_at);
  demoNow = generated;
  w.Date = class extends Date {
    constructor(...args) { super(...(args.length ? args : [demoNow])); }
    static now() { return demoNow; }
  };
}
const report = { outside: [], nativeApi: [], errors: [], responses: {} };
w.Response = Response;
w.URL.createObjectURL = () => 'blob:demo';
w.URL.revokeObjectURL = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
w.addEventListener('error', event => report.errors.push(event.message));
w.fetch = async input => {
  const url = new URL(String(input), w.location.href);
  if (url.origin !== w.location.origin) { report.outside.push(url.href); throw new Error('outside request'); }
  if (/\/(api|auth)\//.test(url.pathname)) report.nativeApi.push(url.pathname);
  const filename = path.join(directory, url.pathname.replace(/^\/demo\//, ''));
  if (!fs.existsSync(filename)) return new Response('{}', { status: 404 });
  return new Response(fs.readFileSync(filename), { headers: { 'Content-Type': 'application/json' } });
};
for (const script of dom.window.document.querySelectorAll('script[src]')) {
  // document.currentScript is available only to a browser's real loader.
  Object.defineProperty(w.document, 'currentScript', { configurable: true, value: script });
  w.eval(fs.readFileSync(path.join(directory, 'console', script.getAttribute('src')), 'utf8'));
}
Object.defineProperty(w.document, 'currentScript', { configurable: true, value: null });
const intercepted = w.fetch;
w.fetch = async (input, options) => {
  const response = await intercepted(input, options);
  const request = new URL(String(input), w.location.href);
  const relative = request.pathname.replace(/^\/demo\/console\//, '') + request.search;
  if (response.ok && /^(api|ui\/api|auth\/state|auth\/sessions)/.test(relative)) {
    report.responses[(options?.method || 'GET') + ' ' + relative] = await response.clone().json();
  }
  return response;
};
async function until(check, label) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  throw new Error('Timed out: ' + label + '\n' + w.document.body.textContent);
}
const M = w.M;
try {
await until(() => w.document.querySelector('#view .list'), 'people');
await until(() => w.document.querySelector('#view')?.getAttribute('aria-busy') !== 'true', 'people query');
report.lang = w.document.documentElement.lang;
report.dir = w.document.documentElement.dir;
report.people = (await M.api('GET', 'api/users')).users.length;
report.rendered_people = w.document.querySelectorAll('#view .people-item').length;

if (mode === 'showcase-navigation') {
  await M.go('people', 'Luma');
  await until(() => w.document.querySelector('.individual-connections'), 'person');
  report.person = w.document.querySelector('#view h1').textContent;
  const disclosure = w.document.querySelector('.individual-connections');
  disclosure.open = true;
  report.connections = disclosure.querySelectorAll('.connection-entry').length;
  const qrButton = [...disclosure.querySelectorAll('button')].find(button => button.textContent === M.t('person.show_qr'));
  qrButton.click();
  await until(() => w.document.querySelector('.connection-qr'), 'qr');
  report.qr = w.document.querySelector('.connection-qr').src.startsWith('data:image/svg+xml');
  w.document.querySelector('dialog .btn-icon').click();
  await M.go('people');
  await until(() => w.document.querySelector('#view .list'), 'back');
  await M.go('servers');
  await until(() => w.document.querySelector('#view').textContent.includes('Example Hosting'), 'servers');
  report.servers = (await M.api('GET', 'ui/api/fleet')).servers.length;
  await M.go('settings');
  await until(() => w.document.querySelector('[data-notice="text"]')?.value, 'settings');
  report.settings = !!w.document.querySelector('.privacy-choice') && !!w.document.querySelector('[data-language]');
  await M.go('tokens');
  await until(() => w.document.querySelector('#view').textContent.includes('People helper'), 'tokens');
  report.tokens = (await M.api('GET', 'api/tokens')).tokens.length;
  report.banner = w.document.querySelector('#demo-banner a').getAttribute('href');
} else if (mode === 'showcase-mutations') {
  await M.api('PATCH', 'api/users/Luma', { note: 'Literal <b>demo note</b>', tag: 'TRIAL' });
  report.edited = (await M.api('GET', 'api/users/Luma')).note;
  await M.api('GET', 'ui/api/people/Luma/expiry?months=3');
  await M.api('DELETE', 'api/users/Luma/devices');
  report.devices = (await M.api('GET', 'api/users/Luma/devices')).count;
  const before = (await M.api('GET', 'api/privacy')).settings;
  await M.api('PUT', 'api/privacy', { ...before, device_limits: 'private' });
  await M.api('PUT', 'api/notice', { notice: { text: 'Hello {name}', until: null }, for_user: 'Luma', confirm: false });
  await M.api('PUT', 'api/notice', { notice: { text: 'Hello {name}', until: null }, for_user: 'Luma', confirm: true });
  report.notice = (await M.api('GET', 'api/notice?for=Luma')).preview;
  await M.api('PUT', 'api/language', { language: 'zh' });
  report.language = (await M.api('GET', 'api/language')).language;
  const original = (await M.api('GET', 'api/users/Luma')).sub_token;
  const reissued = await M.api('POST', 'api/users/Luma/reissue', {});
  report.reissued = reissued.sub_token !== original;
  const reissuedShare = await M.api('GET', 'ui/api/people/Luma/share');
  report.reissuedGenerationMatches = reissuedShare.flow_blocks.credential_generation === reissued.credential_generation &&
    reissuedShare.flow_blocks.nodes.every(node => node.reason !== 'credentials' || node.credential_generation < reissued.credential_generation);
  const token = await M.api('POST', 'api/tokens', { name: 'Demo reader', scope: 'read', expires_in_days: 7 });
  report.token_revoked = !!(await M.api('DELETE', 'api/tokens/' + token.id)).revoked_at;
  await M.api('POST', 'api/users', { username: 'DemoGuest', expire_at: null, traffic_limit_bytes: 0, traffic_limit_strategy: 'no_reset' });
  await M.api('GET', 'api/users/DemoGuest/usage?days=7');
  await M.api('GET', 'api/users/DemoGuest/cut-off');
  await M.api('POST', 'api/users/DemoGuest/reissue', {});
  report.guestReissued = (await M.api('POST', 'api/users/DemoGuest/reissue', {})).username === 'DemoGuest';
  await M.api('PATCH', 'api/users/Vela', { status: 'active' });
  report.velaLinks = (await M.api('GET', 'ui/api/people/Vela/share')).connections.filter(entry => entry.available).length;
  report.week = (await M.api('GET', 'api/users/Luma/usage?days=7')).daily.length;
  const privacy = (await M.api('GET', 'api/privacy')).settings;
  await M.api('PUT', 'api/privacy', { ...privacy, usage_history: 'off' });
  await M.api('PUT', 'api/privacy', { ...privacy, usage_history: 'daily' });
  const restarted = await M.api('GET', 'api/users/Luma/usage?days=7');
  report.historyRestarted = restarted.daily.every(row => !row.uplink_bytes && !row.downlink_bytes);
  report.serverDays = (await M.api('GET', 'api/traffic/daily?days=7')).days;
  await M.api('GET', 'api/users/Luma/connections');
  report.events = (await M.api('GET', 'api/events?after=0&limit=2000')).events.map(event => event.kind);
  await M.api('DELETE', 'api/users/DemoGuest', { cut_off: false });
  report.removed = !(await M.api('GET', 'api/users')).users.some(user => user.username === 'DemoGuest');
} else if (mode === 'showcase-language') {
  const state = w.history.state;
  const hash = w.location.hash;
  w.document.documentElement.lang = 'ru';
  await until(() => w.location.search.includes('lang=ru'), 'language in the address');
  report.search = w.location.search;
  report.hash = w.location.hash === hash;
  report.state = JSON.stringify(w.history.state) === JSON.stringify(state);
  report.banner = w.document.querySelector('#demo-banner a').getAttribute('href');
} else if (mode === 'showcase-queries') {
  const recording = JSON.parse(fs.readFileSync(path.join(directory, 'console/demo-data.json'), 'utf8'));
  report.serviceQueries = 0;
  report.queryMismatches = [];
  function summary(page) {
    return { users: page.users, total: page.total, population: page.population, facets: page.facets };
  }
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
    return value;
  }
  for (const [key, expected] of Object.entries(recording.responses)) {
    if (!key.startsWith('GET api/people') || key.includes('/bulk/') || key.includes('cursor=')) continue;
    const actual = await M.api('GET', key.slice(4));
    report.serviceQueries++;
    if (JSON.stringify(canonical(summary(actual))) !== JSON.stringify(canonical(summary(expected)))) report.queryMismatches.push({ key, actual: summary(actual), expected: summary(expected) });
  }
  const first = await M.api('GET', 'api/people?sort=usage&limit=2');
  report.ordered = (await M.api('GET', 'api/people?sort=usage')).users.map(user => user.username);
  report.paged = first.users.map(user => user.username);
  let cursor = first.next_cursor;
  while (cursor) {
    const next = await M.api('GET', 'api/people?sort=usage&limit=2&cursor=' + encodeURIComponent(cursor));
    report.paged.push(...next.users.map(user => user.username));
    cursor = next.next_cursor;
  }
  try { await M.api('GET', 'api/people?sort=name&limit=2&cursor=' + encodeURIComponent(first.next_cursor)); }
  catch (error) { report.cursorRejectsChangedQuery = error.code === 'invalid_request'; }
  await M.api('POST', 'api/users', { username: 'DemoGuest' });
  const second = await M.api('GET', 'api/people?sort=usage&limit=100&cursor=' + encodeURIComponent(first.next_cursor));
  report.cursorExcludesNewPeople = !second.users.some(user => user.username === 'DemoGuest');
  await M.api('PATCH', 'api/users/Luma', { tag: 'EDITED', note: 'Private demo edit' });
  report.editedSearch = (await M.api('GET', 'api/people?q= lUmA ')).users.map(user => user.username);
  report.editedTag = (await M.api('GET', 'api/people?tag=EDITED')).total;
  report.privateFields = (await M.api('GET', 'api/people')).users.flatMap(user => ['note', 'uuid', 'sub_token', 'delivery_policy'].filter(key => Object.hasOwn(user, key)));
} else if (mode === 'showcase-features') {
  const luma = await M.api('GET', 'api/users/Luma');
  const review = await M.api('POST', 'api/people/bulk/preview', { query: { q: 'Luma' }, action: { action: 'tag', tag: 'EDITED' } });
  report.previewDoesNotWrite = (await M.api('GET', 'api/users/Luma')).tag === luma.tag;
  const receipt = await M.api('POST', 'api/people/bulk/commit', { preview_id: review.id, preview_hash: review.preview_hash });
  const repeat = await M.api('POST', 'api/people/bulk/commit', { preview_id: review.id, preview_hash: review.preview_hash });
  report.commitIdempotent = JSON.stringify(receipt) === JSON.stringify(repeat);
  await M.api('GET', 'api/people/bulk/receipts/' + receipt.id);
  await M.api('POST', 'api/people/bulk/undo', { receipt_id: receipt.id });
  report.undoRestores = (await M.api('GET', 'api/users/Luma')).tag === luma.tag;
  const stale = await M.api('POST', 'api/people/bulk/preview', { usernames: ['Luma', 'Sela'], action: { action: 'disable' } });
  await M.api('PATCH', 'api/users/Sela', { tag: 'CHANGED' });
  try { await M.api('POST', 'api/people/bulk/commit', { preview_id: stale.id, preview_hash: stale.preview_hash }); }
  catch (error) { report.staleRejected = error.code === 'stale_people' && (await M.api('GET', 'api/users/Luma')).status === 'active'; }
  const extension = await M.api('POST', 'api/people/bulk/preview', { usernames: ['Luma'], action: { action: 'extend', months: 1 } });
  report.expiryReviewed = Date.parse(extension.people[0].after.expire_at) > Date.parse(extension.people[0].before.expire_at);
  const catalog = await M.api('GET', 'api/access-profiles');
  report.profiles = catalog.profiles.some(profile => !profile.built_in && profile.members);
  const access = await M.api('GET', 'api/users/Luma/access');
  const links = await M.api('GET', 'ui/api/people/Luma/share');
  const membership = await M.api('POST', 'api/people/bulk/preview', { usernames: ['Luma'], action: { action: 'set-profiles', profiles: ['nearby'] } });
  await M.api('POST', 'api/people/bulk/commit', { preview_id: membership.id, preview_hash: membership.preview_hash });
  report.accessChanged = (await M.api('GET', 'api/users/Luma/access')).connections.length < access.connections.length;
  report.shareChanged = (await M.api('GET', 'ui/api/people/Luma/share')).connections.length < links.connections.length;
  const beforePolicy = await M.api('GET', 'api/users/Luma/delivery-policy');
  const policy = await M.api('PUT', 'api/users/Luma/delivery-policy', { policy: 'full', confirm: false });
  report.policyPreviewDoesNotWrite = (await M.api('GET', 'api/users/Luma/delivery-policy')).override === beforePolicy.override;
  await M.api('PUT', 'api/users/Luma/delivery-policy', { policy: 'full', confirm: true, review_hash: policy.review_hash });
  report.policyConfirmed = (await M.api('GET', 'api/users/Luma/delivery-policy')).effective.source === 'person';
  try { await M.api('PUT', 'api/users/Luma/delivery-policy', { policy: 'full', confirm: true, review_hash: policy.review_hash }); }
  catch (error) { report.stalePolicyRejected = error.code === 'stale_delivery_policy'; }
  report.formats = [];
  for (const suffix of ['', 'info', 'json', 'mihomo', 'stash', 'singbox']) {
    const preview = await M.api('POST', 'api/subscription/preview', { username: 'Luma', suffix });
    report.formats.push(preview.format);
  }
  report.formats.sort();
  report.customRule = (await M.api('POST', 'api/response-rules/test', { user_agent: 'DemoClash/1' })).selection.rule === 'custom';
  report.blockedRule = (await M.api('POST', 'api/response-rules/test', { user_agent: 'DemoBlocked/1' })).block;
  report.passkeys = (await M.api('GET', 'auth/passkeys')).passkeys;
  const beforeKeys = await M.api('GET', 'api/users/Luma');
  const beforeDevices = (await M.api('GET', 'api/users/Luma/devices')).count;
  const rotated = await M.api('POST', 'api/users/Luma/rotate-credentials', { expected_generation: beforeKeys.credential_generation });
  report.rotated = rotated.user.sub_token === beforeKeys.sub_token && rotated.user.uuid !== beforeKeys.uuid && rotated.user.credential_generation === beforeKeys.credential_generation + 1;
  report.rotationDevicesIntact = (await M.api('GET', 'api/users/Luma/devices')).count === beforeDevices;
  try { await M.api('POST', 'api/users/Luma/rotate-credentials', { expected_generation: beforeKeys.credential_generation }); }
  catch (error) { report.staleKeysRejected = error.code === 'credentials_changed'; }
  const reset = await M.api('POST', 'api/users/Luma/reset-traffic', {});
  report.periodReset = !reset.used_traffic_bytes && !reset.period_traffic.upload_bytes && !reset.period_traffic.download_bytes && reset.lifetime_traffic_bytes === beforeKeys.lifetime_traffic_bytes;
  await M.api('POST', 'api/users', { username: 'DemoGuest', device_limit: 0 });
  report.unlimitedDevices = (await M.api('GET', 'api/users/DemoGuest/devices')).limit === 0;
  const deletion = await M.api('POST', 'api/people/bulk/preview', { usernames: ['DemoGuest', 'Neri'], action: { action: 'delete' } });
  const deleted = await M.api('POST', 'api/people/bulk/commit', { preview_id: deletion.id, preview_hash: deletion.preview_hash });
  report.declaredKept = (await M.api('GET', 'api/users/Neri')).username === 'Neri' && deleted.counts.cli_required === 1 && deleted.proposal.usernames.join(',') === 'Neri';
  report.bulkDeleted = !(await M.api('GET', 'api/users')).users.some(user => user.username === 'DemoGuest');
  try { await M.api('POST', 'api/people/bulk/undo', { receipt_id: deleted.id }); }
  catch (error) { report.deleteUndoRefused = error.code === 'undo_refused'; }
} else if (mode === 'showcase-schedule-catchup') {
  const name = data.create ? 'DemoGuest' : 'Luma';
  report.stored = data.create
    ? await M.api('POST', 'api/users', { username: name, traffic_limit_strategy: data.strategy })
    : await M.api('PATCH', 'api/users/' + name, { traffic_limit_strategy: data.strategy });
  const review = await M.api('POST', 'api/people/bulk/preview', { usernames: [name], action: { action: 'tag', tag: 'VIP' } });
  report.review = review.people[0];
  demoNow += 2000;
  try {
    const receipt = await M.api('POST', 'api/people/bulk/commit', { preview_id: review.id, preview_hash: review.preview_hash });
    report.committed = receipt.people[0];
  } catch (error) { report.commitError = error.code; }
  report.after = await M.api('GET', 'api/users/' + name);
} else if (mode === 'showcase-schedule-first-use') {
  report.before = await M.api('GET', 'api/users/Luma');
  report.preview = await M.api('POST', 'api/users/preview', { username: 'Luma', activation_duration_days: 5 });
  report.previewKeptUser = JSON.stringify(await M.api('GET', 'api/users/Luma')) === JSON.stringify(report.before);
  report.after = await M.api('PATCH', 'api/users/Luma', { activation_duration_days: 5 });
  report.fixed = (await M.api('GET', 'api/people?q=Luma&lifecycle=fixed')).users.map(user => user.username);
  report.unlimited = (await M.api('GET', 'api/people?q=Luma&lifecycle=unlimited')).users.map(user => user.username);
} else if (mode === 'showcase-undo-status') {
  if (data.expired) await M.api('PATCH', 'api/users/' + data.person, { expire_at: new Date(demoNow - 86400000).toISOString() });
  report.before = await M.api('GET', 'api/users/' + data.person);
  const action = data.action.action === 'expiry' ? { ...data.action, expire_at: new Date(demoNow - 86400000).toISOString() } : data.action;
  const review = await M.api('POST', 'api/people/bulk/preview', { usernames: [data.person], action });
  const receipt = await M.api('POST', 'api/people/bulk/commit', { preview_id: review.id, preview_hash: review.preview_hash });
  report.committed = receipt.people[0];
  const committedUser = await M.api('GET', 'api/users/' + data.person);
  try {
    const dry = await M.api('POST', 'api/people/bulk/undo', { receipt_id: receipt.id, dry_run: true });
    report.dry = dry.undo_people;
    report.dryDidNotWrite = JSON.stringify(await M.api('GET', 'api/users/' + data.person)) === JSON.stringify(committedUser);
    const undone = await M.api('POST', 'api/people/bulk/undo', { receipt_id: receipt.id });
    report.undone = undone.undo_people;
    report.after = await M.api('GET', 'api/users/' + data.person);
  } catch (error) { report.undoError = error.code; }
} else if (mode === 'showcase-undo-unchanged') {
  await M.api('PATCH', 'api/users/Sela', { tag: 'VIP' });
  const review = await M.api('POST', 'api/people/bulk/preview', { usernames: ['Luma', 'Sela'], action: { action: 'tag', tag: 'VIP' } });
  const receipt = await M.api('POST', 'api/people/bulk/commit', { preview_id: review.id, preview_hash: review.preview_hash });
  report.outcomes = receipt.people.map(person => person.outcome);
  await M.api('PATCH', 'api/users/Sela', { tag: 'AFTER' });
  try {
    const undone = await M.api('POST', 'api/people/bulk/undo', { receipt_id: receipt.id });
    report.undone = undone.undo_people.map(person => person.username);
    report.restored = (await M.api('GET', 'api/users/Luma')).tag === review.people[0].before.tag;
  } catch (error) { report.undoError = error.code; }
  report.untouched = (await M.api('GET', 'api/users/Sela')).tag;
} else if (mode === 'showcase-share-initial') {
  const recording = JSON.parse(fs.readFileSync(path.join(directory, 'console/demo-data.json'), 'utf8'));
  report.recorded = recording.responses['GET ui/api/people/' + data.person + '/share'];
  report.share = await M.api('GET', 'ui/api/people/' + data.person + '/share');
  await M.go('people', data.person);
  await until(() => w.document.querySelector('#view h1')?.textContent === data.person, 'inactive person');
  report.card = !!w.document.querySelector('.flow-block-status');
} else if (mode === 'showcase-share-rotations') {
  if (data.person !== 'Luma') await M.api('POST', 'api/users', { username: data.person });
  report.changes = [];
  for (let i = 0; i < 3; i++) {
    const before = await M.api('GET', 'api/users/' + data.person);
    const result = await M.api('POST', 'api/users/' + data.person + '/' + data.action,
      data.action === 'rotate-credentials' ? { expected_generation: before.credential_generation } : {});
    const share = await M.api('GET', 'ui/api/people/' + data.person + '/share');
    report.changes.push({ before, result, share });
  }
  await M.go('people', data.person);
  await until(() => w.document.querySelector('#view h1')?.textContent === data.person, 'rotated person');
  report.card = !!w.document.querySelector('.flow-block-status');
} else if (mode === 'showcase-token-console') {
  await M.go('tokens');
  await until(() => [...w.document.querySelectorAll('#view button')].some(button => button.textContent === M.t('tokens.create')), 'tokens');
  [...w.document.querySelectorAll('#view button')].find(button => button.textContent === M.t('tokens.create')).click();
  await until(() => w.document.querySelector('dialog form'), 'token form');
  const form = w.document.querySelector('dialog form');
  const name = form.querySelector('input[type=text]');
  name.value = 'Demo reader'; name.dispatchEvent(new w.Event('input', { bubbles: true }));
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => form.querySelector('[type=submit]').textContent === M.t('tokens.create'), 'token review');
  report.preview = report.responses['POST api/tokens/preview'];
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => report.responses['POST api/tokens'], 'token issuance');
  report.issued = report.responses['POST api/tokens'];
  report.metadata = (await M.api('GET', 'api/tokens')).tokens.find(token => token.id === report.issued.id);
} else if (mode === 'showcase-token-api') {
  report.preview = await M.api('POST', 'api/tokens/preview', data.body);
  report.issued = await M.api('POST', 'api/tokens', data.body);
} else if (mode === 'showcase-token-refusals') {
  const catalog = await M.api('GET', 'api/token-capabilities');
  const bodies = data.bodies || [
    { name: 'Demo admin', scope: 'admin', expires_in_days: 7 },
    ...catalog.capabilities.filter(row => !row.console_delegable).map(row => ({ name: 'Demo grant', capabilities: [row.capability], expires_in_days: 7 })),
  ];
  const tokens = JSON.stringify(await M.api('GET', 'api/tokens'));
  const events = JSON.stringify(await M.api('GET', 'api/events?after=0&limit=2000'));
  report.refusals = [];
  for (const body of bodies) {
    for (const route of ['api/tokens/preview', 'api/tokens']) {
      try { await M.api('POST', route, body); report.refusals.push({ route, code: null }); }
      catch (error) { report.refusals.push({ route, code: error.code, status: error.status }); }
    }
  }
  report.keptTokens = JSON.stringify(await M.api('GET', 'api/tokens')) === tokens;
  report.keptEvents = JSON.stringify(await M.api('GET', 'api/events?after=0&limit=2000')) === events;
} else if (mode === 'showcase-unavailable') {
  report.refusals = [];
  report.missing = M.t('error.not_found');
  for (const [method, route] of [
    ['POST', 'auth/password'], ['POST', 'auth/totp/begin'], ['POST', 'auth/totp/enable'], ['POST', 'auth/totp/disable'],
    ['POST', 'auth/passkeys/register/begin'], ['POST', 'auth/passkeys/register/finish'],
    ['POST', 'auth/passkeys/login/begin'], ['POST', 'auth/passkeys/login/finish'],
    ['POST', 'auth/passkeys/confirm/begin'], ['POST', 'auth/passkeys/confirm/finish'],
    ['PATCH', 'auth/passkeys/DemoPasskey'], ['DELETE', 'auth/passkeys/DemoPasskey'],
    ['POST', 'api/nodes/de/commands'],
  ]) {
    try { await M.api(method, route, {}); assert.fail('Demo claimed to perform ' + route); }
    catch (error) { report.refusals.push({ code: error.code, text: M.errorText(error) }); }
  }
} else if (mode === 'showcase-reset') {
  report.note = (await M.api('GET', 'api/users/Luma')).note;
  report.language = (await M.api('GET', 'api/language')).language;
  report.devices = (await M.api('GET', 'api/users/Luma/devices')).count;
} else assert.fail(mode);
} finally { w.close(); }
console.log(JSON.stringify(report));
