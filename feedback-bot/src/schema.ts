import { integer, table, text, index } from 'sdk/db';

export const feedback = table('feedback', {
  id: text('id').primaryKey(),
  chatId: integer('chat_id').notNull(),
  messageId: integer('message_id').notNull(),
  sender: text('sender').notNull(),
  language: text('language').notNull(),
  body: text('body'),
  createdAt: integer('created_at').notNull(),
  status: text('status').notNull().default('open'),
  headerId: integer('header_id').notNull().default(0),
  copyId: integer('copy_id').notNull().default(0),
  acknowledged: integer('acknowledged').notNull().default(0),
  lease: text('lease').notNull().default(''),
  leaseUntil: integer('lease_until').notNull().default(0),
}, (t) => ({ byAge: index('feedback_age').on(t.createdAt), byChat: index('feedback_chat').on(t.chatId, t.createdAt) }));

export const replies = table('replies', {
  id: text('id').primaryKey(),
  feedbackId: text('feedback_id').notNull(),
  createdAt: integer('created_at').notNull(),
  copyId: integer('copy_id').notNull().default(0),
  lease: text('lease').notNull().default(''),
  leaseUntil: integer('lease_until').notNull().default(0),
}, (t) => ({ byAge: index('replies_age').on(t.createdAt), byFeedback: index('replies_feedback').on(t.feedbackId) }));
