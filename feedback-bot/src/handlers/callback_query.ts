import { toHandler } from '@grammyjs/tgcloud/grammy';
import { bot } from '../lib/bot.js';

export default toHandler(bot, 'callback_query');
