import { createBot } from '@grammyjs/tgcloud/grammy';
import { db } from 'sdk';
import { install } from './app.js';
import { config } from './config.generated.js';
import { Store } from './store.js';

export const bot = createBot();
install(bot, new Store(db), config);
