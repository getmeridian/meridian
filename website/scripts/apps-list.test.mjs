import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { APPS_MARKER, appListMarkdown, remarkAppList } from '../src/lib/apps-list.mjs';
import { markdownBody } from './ai-docs.mjs';

const apps = JSON.parse(fs.readFileSync('src/data/apps.json', 'utf8'));
const featured = apps.filter((app) => (app.recommended ?? []).length > 0);

test('the app list holds exactly the featured apps of apps.json, in order', () => {
  const names = [...appListMarkdown().matchAll(/^- \[([^\]]+)\]/gm)].map((match) => match[1]);
  assert.deepEqual(names, featured.map((app) => app.name));
  assert.ok(featured.length < apps.length, 'apps.json also lists apps that are not featured');
});

test('the remark plugin and the Markdown twins expand the marker to the same apps', () => {
  const tree = { type: 'root', children: [{ type: 'html', value: APPS_MARKER }] };
  remarkAppList()(tree);
  const [list] = tree.children;
  assert.equal(list.type, 'list');
  assert.deepEqual(
    list.children.map((item) => item.children[0].children[0].url),
    featured.map((app) => app.url),
  );
  assert.equal(markdownBody(`Intro\n\n${APPS_MARKER}\n`), `Intro\n\n${appListMarkdown()}`);
});
