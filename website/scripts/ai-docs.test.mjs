import assert from 'node:assert/strict';
import test from 'node:test';
import { AI_REFERENCE_PAGES, SITE, aiReference, llmsFull, markdownBody } from './ai-docs.mjs';

const PAGE = `---
title: Example
description: One page.
---

Intro with [a page](/en/deploy/#flags) and [the index](/llms.txt).

---

After a horizontal rule.
`;

test('markdownBody strips only the front matter, not horizontal rules', () => {
  const body = markdownBody(PAGE);
  assert.ok(!body.includes('title: Example'));
  assert.match(body, /\n---\n\nAfter a horizontal rule\.$/);
});

test('markdownBody points docs links at Markdown twins', () => {
  assert.match(markdownBody(PAGE), /\[a page\]\(\/en\/deploy\.md#flags\)/);
});

test('absolute bodies link to the site, never site-relative', () => {
  const body = markdownBody(PAGE, { absolute: true });
  assert.match(body, new RegExp(`\\[a page\\]\\(${SITE}/en/deploy\\.md#flags\\)`));
  assert.match(body, new RegExp(`\\[the index\\]\\(${SITE}/llms\\.txt\\)`));
  assert.doesNotMatch(body, /\]\(\/[^/]/);
});

test('bundles carry no site-relative links and no front matter', () => {
  for (const bundle of [aiReference(), llmsFull()]) {
    assert.doesNotMatch(bundle, /\]\(\/[^/]/);
    assert.doesNotMatch(bundle, /^title: /m);
  }
});

test('the AI reference holds its pages in order under their titles', () => {
  const reference = aiReference();
  const positions = AI_REFERENCE_PAGES.map((slug) => reference.indexOf(`(${SITE}/en/${slug}.md)`));
  assert.ok(positions.every((position) => position > 0));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions);
  assert.match(reference, /^# CLI Reference$/m);
});
