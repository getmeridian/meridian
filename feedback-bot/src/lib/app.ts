import { GrammyError, type Bot, type Context } from 'grammy';
import type { Feedback, Store } from './store.js';
import { text } from './text.js';

export interface Settings {
  inboxChatId: number;
  adminIds: readonly number[];
  retentionDays: number;
}

const supported = (message: Record<string, unknown>) =>
  ['text', 'photo', 'document', 'voice', 'audio', 'video', 'video_note', 'animation', 'sticker'].some((key) => key in message);

export function install(bot: Bot, store: Store, config: Settings, clock = () => Math.floor(Date.now() / 1000), nonce: () => string = () => crypto.randomUUID()) {
  const admin = (ctx: Context) => ctx.chat?.id === config.inboxChatId && ctx.from !== undefined && config.adminIds.includes(ctx.from.id);

  async function deliver(row: Feedback) {
    const lease = nonce();
    if (!await store.claim(row.id, lease, clock())) return;
    try {
      if (!row.header_id) {
        const header = await bot.api.sendMessage(config.inboxChatId,
          `Meridian feedback #${row.id}\nFrom: ${row.sender}\nReply to this card or its copied message to answer.`, {
            reply_markup: { inline_keyboard: [[{ text: 'Done', callback_data: `done:${row.id}` }]] },
          });
        await store.updateDelivery(row.id, lease, 'header_id', header.message_id);
        row.header_id = header.message_id;
      }
      if (!row.copy_id) {
        try {
          const copy = await bot.api.copyMessage(config.inboxChatId, row.chat_id, row.message_id, {
            reply_parameters: { message_id: row.header_id, allow_sending_without_reply: true },
          });
          await store.updateDelivery(row.id, lease, 'copy_id', copy.message_id);
          row.copy_id = copy.message_id;
        } catch (error) {
          if (error instanceof GrammyError && error.error_code === 400) {
            // A deleted/protected source cannot be fixed by retrying the same reference.
            const fallback = await bot.api.sendMessage(config.inboxChatId,
              `Feedback #${row.id}: original message could not be copied. Saved text: ${row.body?.slice(0, 3500) ?? '(attachment only)'}`, {
                reply_parameters: { message_id: row.header_id, allow_sending_without_reply: true },
              });
            await store.updateDelivery(row.id, lease, 'copy_id', fallback.message_id);
            try {
              await bot.api.sendMessage(row.chat_id, text(row.language, 'unavailable'));
            } catch (noticeError) {
              if (!(noticeError instanceof GrammyError && noticeError.error_code === 403)) throw noticeError;
            }
            await store.updateDelivery(row.id, lease, 'acknowledged', 1);
            return;
          }
          throw error;
        }
      }
      if (!row.acknowledged) {
        try {
          await bot.api.sendMessage(row.chat_id, text(row.language, 'received'));
        } catch (error) {
          if (!(error instanceof GrammyError && error.error_code === 403)) throw error;
          // The submission is delivered even if the sender has since blocked the bot.
        }
        await store.updateDelivery(row.id, lease, 'acknowledged', 1);
      }
    } finally {
      await store.release(row.id, lease);
    }
  }

  bot.use(async (_ctx, next) => {
    await store.purge(clock() - config.retentionDays * 86400);
    await next();
  });

  const help = 'Meridian feedback inbox. Reply to a feedback card or copied message to answer the user; any copied media is sent as well. Use Done to close a card. /pending lists up to 20 open items; /retry <id> retries intake delivery; /privacy explains retention.';
  bot.command(['start', 'help'], async (ctx) => {
    await ctx.reply(admin(ctx) ? help : text(ctx.from?.language_code, 'welcome'));
  });
  bot.command('privacy', async (ctx) => { await ctx.reply(text(ctx.from?.language_code, 'privacy')); });
  bot.command('delete', async (ctx) => {
    if (ctx.chat.type !== 'private') return;
    await store.forget(ctx.chat.id);
    await ctx.reply(text(ctx.from?.language_code, 'deleted'));
  });
  bot.command('pending', async (ctx) => {
    if (!admin(ctx)) return;
    const rows = await store.pending();
    await ctx.reply(rows.length ? rows.map((r) => `#${r.id} ${r.copy_id ? 'delivered' : 'delivery pending'} — ${r.sender}`).join('\n') : 'No open feedback.');
  });
  bot.command('retry', async (ctx) => {
    if (!admin(ctx)) return;
    const id = ctx.match.trim();
    if (!/^\d+:\d+$/.test(id)) { await ctx.reply('Usage: /retry <feedback-id>'); return; }
    const row = await store.get(id);
    if (!row) { await ctx.reply('Feedback is missing, deleted or expired.'); return; }
    await deliver(row);
    await ctx.reply('Delivery checked. A currently running delivery is left untouched.');
  });

  bot.callbackQuery(/^done:(\d+:\d+)$/, async (ctx) => {
    if (!admin(ctx)) { await ctx.answerCallbackQuery({ text: 'Not allowed.' }); return; }
    const row = await store.get(ctx.match[1]);
    if (!row || row.header_id !== ctx.callbackQuery.message?.message_id) {
      await ctx.answerCallbackQuery({ text: 'Feedback is missing, deleted or expired.' }); return;
    }
    await store.done(row.id);
    // Repeating an already-applied callback is harmless.
    try {
      await ctx.editMessageReplyMarkup({ reply_markup: { inline_keyboard: [] } });
    } catch (error) {
      if (!(error instanceof GrammyError && error.error_code === 400 && error.description.includes('message is not modified'))) throw error;
    }
    await ctx.answerCallbackQuery({ text: 'Done.' });
  });

  bot.on('message', async (ctx) => {
    const message = ctx.message;
    if (ctx.from.is_bot) return;
    if (admin(ctx)) {
      const repliedTo = message.reply_to_message?.message_id;
      if (!repliedTo) { await ctx.reply('Reply to a feedback card or copied message to answer its sender.'); return; }
      const row = await store.forInbox(repliedTo);
      if (!row) { await ctx.reply('No active feedback route for that message (it may have expired).'); return; }
      if (!supported(message as unknown as Record<string, unknown>)) { await ctx.reply('That message type cannot be relayed.'); return; }
      const replyId = `${ctx.chat.id}:${message.message_id}`;
      const lease = nonce();
      if (!await store.claimReply(replyId, row.id, lease, clock())) return;
      try {
        const sent = await bot.api.copyMessage(row.chat_id, ctx.chat.id, message.message_id, {
          reply_parameters: { message_id: row.message_id, allow_sending_without_reply: true },
        });
        await store.finishReply(replyId, lease, sent.message_id);
      } catch (error) {
        if (error instanceof GrammyError && [400, 403].includes(error.error_code)) {
          await ctx.reply('Reply could not be delivered: the user may have blocked the bot, or the message cannot be copied. Resend as a new reply after resolving it.');
          await store.finishReply(replyId, lease, -1);
        } else {
          throw error;
        }
      } finally {
        await store.releaseReply(replyId, lease);
      }
      return;
    }
    // Never collect group chatter or an unauthorized sender in the inbox chat.
    if (ctx.chat.type !== 'private' || ctx.chat.id === config.inboxChatId) return;
    if (!supported(message as unknown as Record<string, unknown>)) {
      await ctx.reply(text(ctx.from.language_code, 'unsupported')); return;
    }
    const id = `${ctx.chat.id}:${message.message_id}`;
    const row = await store.receive({
      id, chat_id: ctx.chat.id, message_id: message.message_id,
      sender: `${ctx.from.first_name.replace(/[\r\n]/g, ' ').slice(0, 80)}${ctx.from.username ? ` (@${ctx.from.username})` : ''}`,
      language: ctx.from.language_code ?? 'en', body: message.text ?? message.caption ?? null,
      created_at: clock(),
    });
    if (!row) { await ctx.reply(text(ctx.from.language_code, 'limited')); return; }
    await deliver(row);
  });
}
