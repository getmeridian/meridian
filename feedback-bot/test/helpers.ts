import { DatabaseSync } from 'node:sqlite';
import { Bot } from 'grammy';
import type { Db } from 'sdk/db';
import type { Update } from 'grammy/types';
import { Store } from '../src/lib/store.js';
import { install } from '../src/lib/app.js';

export const OWNER = 123456789;
export const USER = 987654321;
export const NOW = 1_800_000_000;

export function fixture() {
  const db = new DatabaseSync(':memory:');
  db.exec(`CREATE TABLE feedback (
    id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    sender TEXT NOT NULL, language TEXT NOT NULL, body TEXT, created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', header_id INTEGER NOT NULL DEFAULT 0,
    copy_id INTEGER NOT NULL DEFAULT 0, acknowledged INTEGER NOT NULL DEFAULT 0,
    lease TEXT NOT NULL DEFAULT '', lease_until INTEGER NOT NULL DEFAULT 0
  ); CREATE TABLE replies (
    id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL, created_at INTEGER NOT NULL,
    copy_id INTEGER NOT NULL DEFAULT 0, lease TEXT NOT NULL DEFAULT '', lease_until INTEGER NOT NULL DEFAULT 0
  );`);
  const sql = {
    async run(query: string, params = {}) {
      const row = db.prepare(query).run(params);
      return { rowsAffected: Number(row.changes), lastInsertRowid: Number(row.lastInsertRowid), rows: [] };
    },
    async get(query: string, params = {}) { return db.prepare(query).get(params) ?? null; },
    async all(query: string, params = {}) { return db.prepare(query).all(params); },
  } as unknown as Pick<Db, 'run' | 'get' | 'all'>;
  const store = new Store(sql);
  const bot = new Bot('test-token', { botInfo: {
    id: 111111111, is_bot: true, first_name: 'Feedback', username: 'example_feedback_bot',
    can_join_groups: false, can_read_all_group_messages: false, supports_inline_queries: false,
    can_connect_to_business: false, has_main_web_app: false, has_topics_enabled: false,
    allows_users_to_create_topics: false, can_manage_bots: false, supports_join_request_queries: false,
  } });
  const calls: { method: string; payload: Record<string, any> }[] = [];
  let nextMessage = 1000;
  let now = NOW;
  let nextNonce = 0;
  let failure: ((method: string, payload: Record<string, any>) => unknown) | undefined;
  bot.api.config.use(async (_previous, method, payload) => {
    calls.push({ method, payload });
    const failed = failure?.(method, payload);
    if (failed) return failed as any;
    if (method === 'copyMessage') return { ok: true, result: { message_id: ++nextMessage } } as any;
    if (method === 'sendMessage') {
      const data = payload as Record<string, any>;
      return { ok: true, result: { message_id: ++nextMessage, chat: { id: data.chat_id, type: 'private' }, date: now, text: data.text } } as any;
    }
    return { ok: true, result: true } as any;
  });
  install(bot, store, { inboxChatId: OWNER, adminIds: [OWNER], retentionDays: 90 }, () => now, () => `test-${++nextNonce}`);
  return {
    db, sql, store, bot, calls,
    advance(seconds: number) { now += seconds; },
    fail(fn?: typeof failure) { failure = fn; },
    send(update: Update) { return bot.handleUpdate(update); },
  };
}

export function message(id = 1, user = USER, body = 'Feedback text', options: Record<string, unknown> = {}): Update {
  return { update_id: id, message: {
    message_id: id, date: NOW, from: { id: user, is_bot: false, first_name: 'Example', language_code: 'en' },
    chat: { id: user, type: 'private', first_name: 'Example' }, text: body,
    ...(body.startsWith('/') ? { entities: [{ type: 'bot_command', offset: 0, length: body.split(' ')[0].length }] } : {}),
    ...options,
  } } as Update;
}

export function callback(id: string, headerId: number, user = OWNER): Update {
  return { update_id: 9000, callback_query: {
    id: 'callback', from: { id: user, is_bot: false, first_name: 'Example' }, chat_instance: 'test',
    data: `done:${id}`, message: { message_id: headerId, date: NOW, chat: { id: OWNER, type: 'private', first_name: 'Example' } },
  } };
}
