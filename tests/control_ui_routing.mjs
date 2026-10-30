/* Render the shipped Settings screen against same-origin, read-only API replies. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, locale, policy] = process.argv.slice(2);
const dom = new JSDOM('<!doctype html><body><main id="view"></main></body>', {
  url: 'https://198.51.100.1/console/', runScripts: 'outside-only',
});
const w = dom.window;
const requests = [];
for (const script of ['core.js', 'settings.js']) w.eval(fs.readFileSync(path.join(ui, script), 'utf8'));
const M = w.M;
const catalog = JSON.parse(fs.readFileSync(path.join(ui, 'locales', locale + '.json')));
M.t = (key, params = {}) => (catalog[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
M.i18n = { langs: [locale], names: { [locale]: locale }, lang: locale };
M.state = { auth: { passkeys: { reason: 'no_domain' } } };
M.privacySection = M.cliHint = () => M.h('section');
M.api = (method, url) => {
  requests.push({ method, url });
  const settings = { profile_title: 'Meridian', public_url: '' };
  if (policy !== 'absent') settings.client_routing = policy;
  return Promise.resolve(url === 'api/settings' ? settings : { sessions: [] });
};
const view = w.document.getElementById('view');
M.renderSettings(view);
await new Promise(setImmediate);
const fleet = view.querySelector('.card');
process.stdout.write(JSON.stringify({
  text: fleet.textContent,
  controls: fleet.querySelectorAll('input,button,select,textarea,form').length,
  requests,
}));
w.close();
