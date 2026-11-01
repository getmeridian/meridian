/* The real account panel consumes subscriber info; no resource loader or outside request. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale, dataText] = process.argv.slice(2);
const data = JSON.parse(dataText);
const dom = new JSDOM('<!doctype html><body><section id="account-panel"></section></body>', {
  url: 'https://198.51.100.1/sub/sample', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
w.document.documentElement.lang = locale;
w._meridianConfig = { subscription_url: 'https://198.51.100.1/sub/sample' };
let body = data.info;
const requests = [];
w.fetch = (url, options) => {
  if (url !== 'https://198.51.100.1/sub/sample/info') throw new Error('outside request');
  requests.push({ url, cache: options.cache });
  return Promise.resolve({ status: 200, json: () => Promise.resolve(body) });
};
w.eval(fs.readFileSync(path.resolve(ui, '../../templates/pwa/account.js'), 'utf8'));
const settle = () => new Promise(resolve => setTimeout(resolve, 10));
await settle();
const panel = () => w.document.getElementById('account-panel');
const report = { text: panel().textContent, metrics: [...panel().querySelectorAll('dl > div')].map(el => el.textContent) };
report.saved = JSON.parse(w.localStorage.getItem('meridian-account:/sub/sample/info'));
if (mode === 'account-slots-refresh') {
  body = data.next;
  panel().querySelector('button').click();
  await settle();
  report.after = panel().textContent;
}
report.requests = requests;
dom.window.close();
process.stdout.write(JSON.stringify(report));
