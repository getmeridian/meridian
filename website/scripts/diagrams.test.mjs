import assert from 'node:assert/strict';
import test from 'node:test';
import { collectDiagrams, diagramId, remarkDiagrams } from '../src/lib/diagrams.mjs';

test('diagram ids ignore surrounding whitespace and line endings only', () => {
  assert.equal(diagramId('flowchart LR\n  A --> B\n'), diagramId('\r\nflowchart LR\r\n  A --> B'));
  assert.notEqual(diagramId('flowchart LR\n  A --> B'), diagramId('flowchart LR\n  A --> C'));
});

test('the remark plugin inlines the SVG as a left-to-right figure with no hard-coded palette', () => {
  const [diagram] = collectDiagrams();
  const tree = { type: 'root', children: [{ type: 'code', lang: 'mermaid', value: diagram.source }] };
  remarkDiagrams()(tree, { path: diagram.file });
  const [node] = tree.children;
  assert.equal(node.type, 'html');
  assert.match(node.value, /^<figure class="diagram" dir="ltr"><svg /);
  assert.match(node.value, /var\(--sl-color-white, var\(--t1,/);
  assert.doesNotMatch(node.value, /<script/i);
});

test('the remark plugin refuses a Mermaid block without a rendered SVG', () => {
  const tree = { type: 'root', children: [{ type: 'code', lang: 'mermaid', value: 'flowchart LR\n  Unrendered --> Block' }] };
  assert.throws(() => remarkDiagrams()(tree, { path: 'page.md' }), /no rendered diagram .* scripts\/diagrams\.mjs render/);
});
