import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('../scripts/configure.mjs', import.meta.url));
const configure = (env: Record<string, string>) => spawnSync(process.execPath, [script], {
  encoding: 'utf8', env,
});

test('production build refuses missing or invalid private configuration instead of deploying the example', () => {
  assert.notEqual(configure({}).status, 0);
  assert.notEqual(configure({ FEEDBACK_INBOX_CHAT_ID: 'not-a-number', FEEDBACK_ADMIN_IDS: '123456789' }).status, 0);
  assert.notEqual(configure({ FEEDBACK_INBOX_CHAT_ID: '123456789', FEEDBACK_ADMIN_IDS: '987654321' }).status, 0);
});

test('deployment cannot silently diverge from the 90-day privacy policy', () => {
  const result = configure({ FEEDBACK_INBOX_CHAT_ID: '123456789', FEEDBACK_ADMIN_IDS: '123456789', FEEDBACK_RETENTION_DAYS: '30' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /matching the user-facing privacy notice/);
});
