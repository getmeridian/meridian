/* Execute the shipped account page with pinned time and a same-origin info response. */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const [ui, mode, locale = 'en', rawData = '{}'] = process.argv.slice(2);
// Large scenarios arrive on stdin: Linux caps one argument at 128 KiB.
const dataText = rawData === '-' ? fs.readFileSync(0, 'utf8') : rawData;
const data = JSON.parse(dataText);
const dom = new JSDOM('<!doctype html><div id="account-panel"></div><a data-import href="#">Import</a>', {
  url: 'https://198.51.100.1/sub/exampleToken0001', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, requests = [];
const now = Date.parse('2027-01-31T12:00:00Z');
const NativeDate = w.Date;
w.Date = class extends NativeDate {
  constructor(...values) { super(...(values.length ? values : [now])); }
  static now() { return now; }
};
w.document.documentElement.lang = locale;
w._meridianConfig = { subscription_url: w.location.href };
w.fetch = (url, options) => {
  requests.push({ url, method: options.method, cache: options.cache });
  return Promise.resolve({ status: 200, json: () => Promise.resolve(data.info) });
};
w.eval(fs.readFileSync(path.join(ui, '../../templates/pwa/account.js'), 'utf8'));
await new Promise(resolve => setTimeout(resolve, 30));
const stored = [...Array(w.localStorage.length)].map((_, i) => JSON.parse(w.localStorage.getItem(w.localStorage.key(i))));
process.stdout.write(JSON.stringify({ shown: w.document.getElementById('account-panel').textContent,
  requests, stored, importDisabled: w.document.querySelector('[data-import]').getAttribute('aria-disabled') }));
dom.window.close();
