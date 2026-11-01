# feedback-bot — Telegram-native Meridian feedback inbox

## Design decisions

- Independent TypeScript service deployed to Telegram Serverless with `@grammyjs/tgcloud`; never installed on a user's proxy/control server.
- `src/` is canonical; `tgcloud/` is generated deployment output and `.tgcloud/` holds ignored CLI credentials/state.
- Pure application logic accepts storage and Telegram interfaces; runtime SDK imports stay in adapters so offline tests never call Telegram.
- No Telegram reactions by default; use explicit messages and buttons for feedback handling.
- Deployment is manual, separate from PR validation. The Serverless CLI token is a GitHub secret, never source or generated config.

## What's done well

- `npm ci && npm run check` tests the bot offline, type-checks it, and builds the actual serverless modules.

## Pitfalls

- Telegram's runtime is neither Node nor a browser: no filesystem or timer assumptions; external packages must bundle through the adapter.
- `tgcloud run` executes code against the live platform and can mutate production; it is not an offline test command.
