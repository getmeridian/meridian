/* Run the shipped ES5 scripts at their DOM, storage, and network boundaries.
 * This limited DOM needs no npm package; browser layout is checked separately. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const decode = text => text.replace(/&(?:amp|lt|gt|quot|apos|#\d+|#x[\da-f]+);/gi, entity => {
  const named = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'" };
  return named[entity] || String.fromCodePoint(entity[2].toLowerCase() === 'x' ? parseInt(entity.slice(3), 16) : parseInt(entity.slice(2), 10));
});
const escape = text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

class Element {
  constructor(tag, document, text) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = document; this.children = [];
    this.attributes = {}; this.parentNode = null; this.nodeType = text === undefined ? 1 : 3;
    this.text = text; this.style = { setProperty() {} }; this.listeners = {};
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: name => { if (!this.classList.contains(name)) this.className = (this.className + ' ' + name).trim(); },
      remove: name => { this.className = this.className.split(/\s+/).filter(item => item !== name).join(' '); },
      toggle: (name, force) => {
        const active = force === undefined ? !this.classList.contains(name) : force;
        this.classList[active ? 'add' : 'remove'](name); return active;
      }
    };
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
  hasAttribute(name) { return Object.hasOwn(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; }
  get id() { return this.getAttribute('id') || ''; } set id(value) { this.setAttribute('id', value); }
  get className() { return this.getAttribute('class') || ''; } set className(value) { this.setAttribute('class', value); }
  get dataset() {
    const data = {};
    for (const [key, value] of Object.entries(this.attributes)) {
      if (key.startsWith('data-')) data[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    return data;
  }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(value) { if (value) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
  get open() { return this.hasAttribute('open'); }
  set open(value) { if (value) this.setAttribute('open', ''); else this.removeAttribute('open'); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(value) { if (value) this.setAttribute('disabled', ''); else this.removeAttribute('disabled'); }
  get value() { return this._value === undefined ? (this.getAttribute('value') || this.textContent) : this._value; }
  set value(value) { this._value = String(value); }
  get textContent() { return this.nodeType === 3 ? this.text : this.children.map(child => child.textContent).join(''); }
  set textContent(text) { this.children = []; this.appendChild(this.ownerDocument.createTextNode(String(text))); }
  get innerHTML() { return this.children.map(child => child.outerHTML).join(''); }
  set innerHTML(html) {
    this.children = []; const stack = [this];
    for (const token of String(html).match(/<!--[\s\S]*?-->|<[^>]*>|[^<]+/g) || []) {
      if (token.startsWith('<!--')) continue;
      if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
      if (token.startsWith('<')) {
        const tag = /^<([\w-]+)/.exec(token); if (!tag) continue;
        const node = this.ownerDocument.createElement(tag[1]);
        const attrs = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
        attrs.lastIndex = tag[0].length; let match;
        while ((match = attrs.exec(token))) node.setAttribute(match[1], decode(match[2] ?? match[3] ?? match[4] ?? ''));
        stack.at(-1).appendChild(node);
        if (!/^(img|input|meta|link|br|hr)$/i.test(tag[1]) && !token.endsWith('/>')) stack.push(node);
      } else stack.at(-1).appendChild(this.ownerDocument.createTextNode(decode(token)));
    }
  }
  get outerHTML() {
    if (this.nodeType === 3) return escape(this.text);
    const attrs = Object.entries(this.attributes).map(([key, value]) => ' ' + key + '="' + escape(value).replace(/"/g, '&quot;') + '"').join('');
    return '<' + this.tagName.toLowerCase() + attrs + '>' + this.innerHTML + '</' + this.tagName.toLowerCase() + '>';
  }
  appendChild(child) {
    child.parentNode = this; this.children.push(child);
    if (child.tagName === 'SCRIPT' && child.src && this.ownerDocument.onScript) this.ownerDocument.onScript(child);
    return child;
  }
  get firstElementChild() { return this.children.find(child => child.nodeType === 1) || null; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  replaceChild(child, old) {
    const index = this.children.indexOf(old);
    if (index < 0) throw new Error('NotFoundError');
    this.children[index] = child; child.parentNode = this; old.parentNode = null; return old;
  }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
  focus() { this.ownerDocument.activeElement = this; }
  select() { this.focus(); this.selected = true; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; this.selected = true; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatchEvent(event) { for (const callback of this.listeners[event.type] || []) callback(event); return !event.defaultPrevented; }
  matches(selector) {
    if (this.nodeType !== 1) return false;
    const tag = /^[\w-]+/.exec(selector); if (tag && this.tagName !== tag[0].toUpperCase()) return false;
    for (const [, name] of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(name)) return false;
    for (const [, id] of selector.matchAll(/#([\w-]+)/g)) if (this.id !== id) return false;
    for (const [, attr, value] of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
      if (!this.hasAttribute(attr) || (value !== undefined && this.getAttribute(attr) !== value)) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(item => item.trim()), found = [];
    const visit = node => { for (const child of node.children) { if (selectors.some(part => child.matches(part))) found.push(child); visit(child); } };
    visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode && this.parentNode.closest(selector); }
}

function createBrowser(config, catalog, options = {}) {
  const document = new Element('document', null); document.ownerDocument = document;
  document.createElement = tag => new Element(tag, document);
  document.createTextNode = text => new Element('#text', document, String(text));
  document.getElementById = id => document.querySelector('#' + id);
  document.documentElement = document.appendChild(document.createElement('html'));
  document.documentElement.lang = 'en'; document.documentElement.dir = 'ltr';
  document.body = document.documentElement.appendChild(document.createElement('body'));
  document.body.innerHTML = '<div id="app"><div class="skeleton"></div></div><div id="toast"><span data-t="copied">Copied</span></div>';
  document.readyState = 'loading'; document.visibilityState = 'visible';
  const storage = { ...(options.storage || {}) }, requests = [], timers = new Map();
  let time = 0, timerId = 0;
  const location = new URL(options.url || 'https://198.51.100.1/connect/demo/');
  location.replace = value => { location.replaced = value; };
  const context = {
    document, location, URL, Intl, console, Promise,
    navigator: { userAgent: options.userAgent || 'Android', language: options.language || 'en', maxTouchPoints: 0 },
    localStorage: { getItem: key => storage[key] ?? null, setItem: (key, value) => { storage[key] = String(value); }, removeItem: key => { delete storage[key]; } },
    btoa: text => { if (/[^\x00-\xff]/.test(text)) throw new Error('InvalidCharacterError'); return Buffer.from(text, 'latin1').toString('base64'); },
    atob: text => Buffer.from(text, 'base64').toString('latin1'), AbortController,
    Event: function(type) { this.type = type; },
    CustomEvent: function(type, init) { this.type = type; this.detail = init && init.detail; },
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, at: time + delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    addEventListener: document.addEventListener.bind(document), dispatchEvent: document.dispatchEvent.bind(document),
    performance: { now: () => time },
  };
  context.window = context;
  context.fetch = (url, init = {}) => {
    requests.push({ url: String(url), init });
    if (options.fetch) return options.fetch(String(url), init);
    const body = String(url).endsWith('config.json') ? config : String(url).endsWith('apps.json') ? catalog : options.info;
    return Promise.resolve(response(body, body === undefined ? 404 : 200));
  };
  vm.createContext(context); const directory = path.resolve('src/meridian/templates/pwa');
  function load(name, expose = []) {
    let source = fs.readFileSync(path.join(directory, name), 'utf8');
    if (expose.length) source = source.replace(/\}\)\(\);\s*$/, 'window.page = {' + expose.map(key => key + ':' + key).join(',') + '};\n})();');
    vm.runInContext(source, context, { filename: name });
  }
  document.onScript = script => { const name = script.src.split('/').at(-1); if (fs.existsSync(path.join(directory, name))) load(name); };
  load('app.js', ['renderPage', 'init', 'switchLang', 'buildDeepLink', 'renderSubUrl', 'renderSetup', 'renderAppRow', 'renderConnection', 'renderConnections', 'T']);
  function fire(type, target, extra = {}) {
    const event = { type, target: target || document, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
    document.dispatchEvent(event); return event;
  }
  async function flush() { for (let i = 0; i < 12; i++) await new Promise(setImmediate); }
  async function advance(ms) { time += ms; for (const [id, timer] of [...timers]) if (timer.at <= time && timers.delete(id)) timer.callback(); await flush(); }
  return { context, document, requests, storage, load, fire, flush, advance, start: async () => { fire('DOMContentLoaded'); await flush(); } };
}
function response(body, status = 200) { return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body === undefined ? body : JSON.parse(JSON.stringify(body))), text: () => Promise.resolve(String(body)) }; }
module.exports = { createBrowser, response };
