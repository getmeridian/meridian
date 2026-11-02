/* Browser WebAuthn and local console requests; no authenticator or network needed. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en', rawData = 'null'] = process.argv.slice(2);
const data = JSON.parse(rawData === '-' ? fs.readFileSync(0, 'utf8') : rawData) || {};
const dom = new JSDOM('<!doctype html><body><div id="view"></div></body>', {
  url: 'https://console.example.test/console/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
const requests = [], browser = [], report = {};
const b64 = bytes => Buffer.from(bytes).toString('base64url');
const challenge = b64(Buffer.alloc(32, 1)), credentialId = b64(Buffer.alloc(32, 2));
const userId = b64(Buffer.alloc(32, 3));
const credential = {
  id: credentialId, rawId: new w.Uint8Array(Buffer.alloc(32, 2)).buffer, type: 'public-key',
  response: { clientDataJSON: new w.Uint8Array([1, 2, 3]).buffer,
    attestationObject: new w.Uint8Array([4, 5, 6]).buffer,
    authenticatorData: new w.Uint8Array([7, 8, 9]).buffer,
    signature: new w.Uint8Array([10, 11, 12]).buffer,
    userHandle: data.emptyHandle ? new w.ArrayBuffer(0) : new w.Uint8Array(Buffer.alloc(32, 3)).buffer },
};
Object.defineProperty(w, 'isSecureContext', { value: true });
w.PublicKeyCredential = data.unsupported ? undefined : function () {};
Object.defineProperty(w.navigator, 'credentials', { value: {
  create: async options => {
    browser.push({ kind: 'create', binaryChallenge: options.publicKey.challenge instanceof w.Uint8Array,
      binaryUser: options.publicKey.user.id instanceof w.Uint8Array,
      verification: options.publicKey.authenticatorSelection.userVerification,
      attestation: options.publicKey.attestation });
    if (data.cancel) throw new w.DOMException('Cancelled', 'NotAllowedError');
    return credential;
  },
  get: async options => {
    browser.push({ kind: 'get', binaryChallenge: options.publicKey.challenge instanceof w.Uint8Array,
      binaryCredential: options.publicKey.allowCredentials[0].id instanceof w.Uint8Array,
      verification: options.publicKey.userVerification });
    if (data.cancel) throw new w.DOMException('Cancelled', 'NotAllowedError');
    return credential;
  },
} });
let passkeys = [{ id: credentialId, name: 'Desk key', rp_id: 'console.example.test',
  created_at: 1800000000, last_used_at: null }];
const auth = { signed_in: mode !== 'passkey-login', owner_exists: true, configured: true,
  passkeys: { available: !data.noDomain, reason: data.noDomain ? 'no_domain' : 'ready', registered: true } };
w.fetch = async (url, options) => {
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ path: url, method: options.method, body, headers: options.headers });
  let result = {};
  if (url === 'auth/state') result = auth;
  else if (url === 'auth/passkeys' && options.method === 'GET') result = { passkeys };
  else if (url.endsWith('/register/begin')) result = { challenge_id: challenge, public_key: {
    rp: { id: 'console.example.test', name: 'Meridian' }, user: { id: userId, name: 'owner' }, challenge,
    pubKeyCredParams: [{ type: 'public-key', alg: -7 }], excludeCredentials: [],
    authenticatorSelection: { userVerification: 'required', residentKey: 'preferred' }, attestation: 'none',
  } };
  else if (url.endsWith('/begin')) result = { challenge_id: challenge, public_key: {
    rpId: 'console.example.test', challenge, userVerification: 'required',
    allowCredentials: [{ type: 'public-key', id: credentialId }],
  } };
  else if (url.endsWith('/register/finish')) result = { ...passkeys[0], name: body.name };
  else if (url.endsWith('/finish')) result = { confirmed: true, signed_in: true };
  else if (options.method === 'PATCH') { passkeys[0].name = body.name; result = passkeys[0]; }
  else if (options.method === 'DELETE') { passkeys = []; result = { removed: true }; }
  return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(result) };
};
for (const script of ['core.js', 'drafts.js', 'components.js', 'passkeys.js', 'auth.js']) {
  w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
}
const M = w.M;
M.i18n = { locale: () => locale === 'zh' ? 'zh-CN' : locale };
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.toast = text => { report.toast = text; };
M.state.auth = auth;
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function () { this.open = false; };
const settle = () => new Promise(setImmediate);
const view = w.document.getElementById('view');
const findButton = label => [...w.document.querySelectorAll('button')].find(button => button.textContent === catalog[label]);
if (mode === 'passkey-login') {
  M.renderLogin(view, auth, () => { report.signedIn = true; });
  report.initial = view.textContent;
  const button = findButton('passkeys.sign_in');
  report.offered = !!button;
  if (button) button.click();
  await settle(); await settle();
  report.after = view.textContent;
} else if (mode === 'passkey-confirm') {
  const confirmation = M.confirmIdentity().then(() => { report.confirmed = true; });
  findButton('passkeys.confirm').click();
  await settle(); await settle();
  await confirmation;
} else {
  view.appendChild(M.passkeyCard());
  await settle();
  report.initial = view.textContent;
  const action = data.action || 'add';
  const button = findButton('passkeys.' + action);
  if (button) button.click();
  await settle();
  const dialog = w.document.querySelector('dialog');
  if (dialog) {
    if (action === 'remove') [...dialog.querySelectorAll('button')].find(button => button.textContent === catalog['passkeys.remove']).click();
    else {
      const name = dialog.querySelector('input');
      name.value = data.name || 'Travel key';
      name.dispatchEvent(new w.Event('input', { bubbles: true }));
      dialog.querySelector('form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    }
  }
  await settle(); await settle(); await settle();
  report.after = w.document.body.textContent;
  report.dialogOpen = !!w.document.querySelector('dialog');
}
report.requests = requests;
report.browser = browser;
console.log(JSON.stringify(report));
w.close();
