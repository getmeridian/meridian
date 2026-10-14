import assert from 'node:assert/strict';
import test from 'node:test';
import { findExternalLoads } from './check-runtime-assets.mjs';

const LOADS = [
  'import("https://cdn.example.com/mod.js")',
  "await import(`https://cdn.example.com/${name}.js`)",
  'import{a as b}from"https://cdn.example.com/x.js"',
  "import 'https://cdn.example.com/side-effect.js';",
  "fetch('https://api.example.com/data')",
  'fetch("//cdn.example.com/data.json")',
  "importScripts('https://cdn.example.com/sw-lib.js')",
  "navigator.sendBeacon('https://stats.example.com/hit', body)",
  "new Worker('https://cdn.example.com/worker.js')",
  "new WebSocket('wss://live.example.com/socket')",
  "img.src = 'https://cdn.example.com/pixel.gif'",
  "el.setAttribute('src', 'https://cdn.example.com/x.js')",
];

const TEXT = [
  'throw new Error("See https://docs.example.com/errors for help")',
  'var thanks="Translator <https://github.com/someone>"',
  'new URL(`https://example.com${path}`)',
  'document.createElementNS("http://www.w3.org/2000/svg", "svg")',
  "location.href = 'https://github.com/getmeridian/meridian'",
  "fetch('../pwa/apps.json')",
  "import(`${base}pagefind.js`)",
  "script.src = '../pwa/selfcheck.js'",
  'const doc = "Run fetch(url) with https://example.com"',
  "fetch('http://127.0.0.1:8765/api/health')",
  "new WebSocket('ws://localhost:8765/events')",
];

test('absolute-URL loads in JavaScript are external runtime assets', () => {
  for (const code of LOADS) assert.equal(findExternalLoads(code, 'js').length, 1, code);
});

test('URLs that are only text are not loads', () => {
  for (const code of TEXT) assert.deepEqual(findExternalLoads(code, 'js'), [], code);
});

test('inline scripts are checked, JSON-LD and external script tags by their own rules', () => {
  assert.equal(findExternalLoads('<script>fetch("https://api.example.com")</script>', 'html').length, 1);
  assert.deepEqual(findExternalLoads('<script type="application/ld+json">{"url":"https://getmeridian.org"}</script>', 'html'), []);
  assert.equal(findExternalLoads('<script src="https://cdn.example.com/x.js"></script>', 'html').length, 1);
  assert.deepEqual(findExternalLoads('<p>fetch("https://example.com") in prose</p>', 'html'), []);
});

test('external stylesheets, fonts, and images are still caught', () => {
  assert.equal(findExternalLoads('<link rel="stylesheet" href="https://fonts.example.com/css">', 'html').length, 1);
  assert.equal(findExternalLoads("@font-face{src:url('https://fonts.example.com/a.woff2')}", 'css').length, 1);
  assert.equal(findExternalLoads('<img src="https://cdn.example.com/a.png">', 'html').length, 1);
});
