import { defineEndpoint } from '@grammyjs/tgcloud';
import { db, EndpointError } from 'sdk';
import { config } from '../lib/config.generated.js';
import { Store } from '../lib/store.js';

// The daily workflow calls this with CLI credentials. Mini App calls require
// platform-verified init data and must also belong to the configured owner.
export default defineEndpoint(async (_input, ctx) => {
  if (!ctx.initData.user || !config.adminIds.includes(ctx.initData.user.id)) {
    throw new EndpointError('Not allowed');
  }
  await new Store(db).purge(Math.floor(Date.now() / 1000) - config.retentionDays * 86400);
  return { cleaned: true };
});
