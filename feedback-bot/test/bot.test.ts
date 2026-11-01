import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fixture, message, callback, OWNER, USER, NOW } from './helpers.js';

test('feedback is persisted and copied once, including duplicate updates', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message());
  await f.send(message());
  const row = await f.store.get(`${USER}:1`);
  assert.equal(row?.body, 'Feedback text');
  assert.equal(row?.acknowledged, 1);
  assert.equal(f.calls.filter((c) => c.method === 'copyMessage').length, 1);
  assert.equal(f.calls.filter((c) => c.method === 'sendMessage').length, 2);
  assert.ok(!f.calls.some((c) => c.method === 'setMessageReaction'));
});

test('owner replies route back to the sender once, even when replying to the media copy', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message());
  const row = (await f.store.get(`${USER}:1`))!;
  const reply = message(20, OWNER, 'Thanks for reporting', { reply_to_message: { message_id: row.copy_id } });
  await f.send(reply); await f.send(reply);
  const copies = f.calls.filter((c) => c.method === 'copyMessage');
  assert.equal(copies.length, 2);
  assert.equal(copies[1].payload.chat_id, USER);
  assert.equal(copies[1].payload.from_chat_id, OWNER);
  assert.equal(copies[1].payload.reply_parameters.message_id, 1);
});

test('a stranger cannot impersonate the owner or close another person’s feedback', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message());
  const row = (await f.store.get(`${USER}:1`))!;
  await f.send(message(30, USER, 'forged admin reply', {
    chat: { id: OWNER, type: 'private', first_name: 'Example' },
    reply_to_message: { message_id: row.header_id },
  }));
  await f.send(callback(row.id, row.header_id, USER));
  assert.equal(f.calls.filter((c) => c.method === 'copyMessage').length, 1);
  assert.equal((await f.store.get(row.id))?.status, 'open');
});

test('Done requires the actual card and does not misroute a mismatched button', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message());
  const row = (await f.store.get(`${USER}:1`))!;
  await f.send(callback(row.id, row.header_id + 99));
  assert.equal((await f.store.get(row.id))?.status, 'open');
  await f.send(callback(row.id, row.header_id));
  assert.equal((await f.store.get(row.id))?.status, 'done');
});

test('group chatter is ignored and media feedback is copied without downloads', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message(1, USER, 'group chatter', { chat: { id: -100123, type: 'supergroup', title: 'Example' } }));
  assert.equal(f.calls.length, 0);
  await f.send(message(2, USER, undefined, { text: undefined, voice: { file_id: 'file', file_unique_id: 'unique', duration: 3 } }));
  assert.equal(f.calls.filter((c) => c.method === 'copyMessage').length, 1);
  assert.ok(!f.calls.some((c) => c.method === 'getFile'));
});

test('failed copying can be retried without duplicating the existing header', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  f.fail((method) => method === 'copyMessage' ? { ok: false, error_code: 429, description: 'retry later', parameters: { retry_after: 1 } } : undefined);
  await assert.rejects(f.send(message()));
  const row = (await f.store.get(`${USER}:1`))!;
  assert.ok(row.header_id); assert.equal(row.copy_id, 0);
  f.fail();
  await f.send(message(12, OWNER, `/retry ${row.id}`));
  assert.ok((await f.store.get(row.id))?.copy_id);
  const headers = f.calls.filter((c) => c.method === 'sendMessage' && String(c.payload.text).startsWith('Meridian feedback #'));
  assert.equal(headers.length, 1);
});

test('90-day expiry removes content and routing, including replies', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message());
  const row = (await f.store.get(`${USER}:1`))!;
  await f.send(message(20, OWNER, 'Reply', { reply_to_message: { message_id: row.header_id } }));
  f.advance(90 * 86400 + 1);
  await f.send(message(21, OWNER, 'Late reply', { reply_to_message: { message_id: row.header_id } }));
  assert.equal(await f.store.get(row.id), null);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM replies').get()?.n, 0);
  assert.equal(f.calls.filter((c) => c.method === 'copyMessage').length, 2);
});

test('/delete removes the caller’s records but not other users’ feedback', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message()); await f.send(message(2, USER + 1));
  await f.send(message(3, USER, '/delete'));
  assert.equal(await f.store.get(`${USER}:1`), null);
  assert.ok(await f.store.get(`${USER + 1}:2`));
});

test('rate limit rejects a sixth new submission but not an existing retry', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  for (let id = 1; id <= 6; id++) await f.send(message(id));
  assert.equal(f.calls.filter((c) => c.method === 'copyMessage').length, 5);
  assert.equal(await f.store.get(`${USER}:6`), null);
  await f.send(message());
  assert.equal(f.calls.filter((c) => c.method === 'copyMessage').length, 5);
});

test('leased submissions cannot be delivered concurrently and recover after expiry', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  const row = await f.store.receive({ id: `${USER}:1`, chat_id: USER, message_id: 1, sender: 'Example', language: 'en', body: 'test', created_at: NOW });
  assert.ok(row);
  assert.equal(await f.store.claim(row.id, 'first', NOW), true);
  assert.equal(await f.store.claim(row.id, 'second', NOW), false);
  assert.equal(await f.store.claim(row.id, 'second', NOW + 301), true);
  await assert.rejects(f.store.updateDelivery(row.id, 'first', 'copy_id', 1));
});

test('a stale reply cannot create a route after its feedback has been forgotten', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message());
  await f.store.forget(USER);
  assert.equal(await f.store.claimReply('stale-reply', `${USER}:1`, 'lease', NOW), false);
});

test('Russian start explains retention without collecting the command as feedback', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message(1, USER, '/start', { from: { id: USER, is_bot: false, first_name: 'Example', language_code: 'ru' } }));
  assert.match(String(f.calls[0].payload.text), /90 дней/);
  assert.equal(await f.store.get(`${USER}:1`), null);
});

test('an unavailable source gets a bounded fallback with a working reply route', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  f.fail((method) => method === 'copyMessage' ? { ok: false, error_code: 400, description: 'message to copy not found' } : undefined);
  await f.send(message(1, USER, 'x'.repeat(4096)));
  const row = (await f.store.get(`${USER}:1`))!;
  assert.equal(row.acknowledged, 1);
  assert.ok(row.copy_id > 0);
  assert.ok(f.calls.filter((c) => c.method === 'sendMessage').every((c) => String(c.payload.text).length <= 4096));
  f.fail();
  await f.send(message(20, OWNER, 'Reply to fallback', { reply_to_message: { message_id: row.copy_id } }));
  assert.equal(f.calls.at(-1)?.payload.chat_id, USER);
});

test('a blocked user produces an explicit owner error, not a reply loop', async (t) => {
  const f = fixture(); t.after(() => f.db.close());
  await f.send(message());
  const row = (await f.store.get(`${USER}:1`))!;
  f.fail((method, payload) => method === 'copyMessage' && payload.chat_id === USER ? { ok: false, error_code: 403, description: 'bot blocked' } : undefined);
  const reply = message(20, OWNER, 'Reply', { reply_to_message: { message_id: row.header_id } });
  await f.send(reply); await f.send(reply);
  assert.match(String(f.calls.at(-1)?.payload.text), /could not be delivered/);
  assert.equal(f.calls.filter((c) => c.method === 'copyMessage' && c.payload.chat_id === USER).length, 1);
});
