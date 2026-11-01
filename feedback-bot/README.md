# Meridian feedback bot

A separate, maintainer-operated Telegram bot hosted on **Telegram Serverless**.
It uses `@grammyjs/tgcloud` and grammY, not a VPS or Cloudflare Worker, and is
never installed on Meridian users' servers. The project website remains static.

## Behaviour

- Users send text, screenshots, documents, voice, audio, video, animations or
  stickers in a private chat with the bot. Albums arrive as individually routed
  messages; original media stays within Telegram (no download/re-upload).
- The bot stores a submission and copies it to the maintainer's **personal DM**
  with a feedback card. Reply to the card or copied message to answer the sender.
  Replies may include media. **Done** closes the card; it does not delete it or
  stop later replies while its route remains active.
- `/pending` lists up to 20 open items. `/retry <id>` retries a pending intake
  delivery. `/start` and `/help` show instructions; users get English or Russian
  according to their Telegram language. Unknown languages use English.
- `/privacy` explains storage. `/delete` removes the caller's feedback and
  reply routes from the bot database, **not messages already delivered to Telegram**.
- Feedback text/captions, sender display labels, chat/message references and
  delivery metadata expire after **90 days**. Every interaction purges expired
  records; daily maintenance also purges them while the bot is idle. The daily
  job can lag or be disabled by GitHub, so monitor it; this is not a hard real-time
  deletion guarantee. Existing Telegram inbox copies are not automatically deleted.
- Only the configured administrator in the configured inbox can reply or mark
  Done. Group chatter and bot senders are ignored. New submissions are limited
  to five per sender per minute using an atomic database insert.
- No Telegram reactions are sent by default.

## Local checks (no credentials, no Telegram calls)

Requires Node.js 24+:

```sh
cd feedback-bot
npm ci --ignore-scripts
npm run check
```

`check` generates **example-only configuration**, runs offline grammY tests
against real in-memory SQLite, checks TypeScript, and bundles the actual
Serverless modules. The private inbox ID is never committed to this repository.
`src/` is the source; `tgcloud/` is generated and ignored.

## Configure and deploy

1. Create a dedicated bot in BotFather and enable **Serverless** for it.
2. From **Serverless → CLI Access**, get its CLI token (`app<id>:<secret>`).
   The normal Bot API token is **not** this credential and is not used by this app.
3. In this repository's GitHub settings, add:
   - Actions **secret** `MERIDIAN_FEEDBACK_TGCLOUD_TOKEN`: the CLI token.
   - Actions **variable** `MERIDIAN_FEEDBACK_INBOX_CHAT_ID`: the maintainer's
     numeric Telegram user ID. That identity is the sole reply administrator.
4. The maintainer must **start the new bot** before it can deliver DM feedback.
5. Merge the PR, then run **Actions → Feedback Bot → Run workflow → deploy**
   on `main`. Opening a PR or pushing code never deploys the bot.

The deploy job checks the code, builds real config, fetches remote state,
applies only additive/safe local-schema migrations, then atomically pushes code.
It refuses to activate new code if any schema changes remain after migration.
Schema changes needing manual/destructive migrations require an operator review;
this workflow never auto-confirms destructive operations.

For an explicit local deploy, place non-secret settings in ignored
`config.local.json`:

```json
{"inboxChatId":123456789,"adminIds":[123456789],"retentionDays":90}
```

Use your own ID, then `npx tgcloud login` for the separate CLI token. Build
with `npm run build`, fetch with `npx tgcloud fetch`, migrate with
`npx tgcloud migrate --local --safe`, and deploy with `npx tgcloud push`.
**Do not deploy the generated example configuration from `npm run check`.**
Local config or deployment env is required by `npm run build`; configuration
and the user-facing privacy notice must agree on the fixed 90-day policy.

The daily cleanup workflow runs the **local, checked-in** cleanup endpoint on
Telegram using the CLI credential, without deploying or sending chat messages.
Ordinary endpoint calls require platform-verified Mini App identity and are
restricted to the administrator. Manual workflow action `cleanup` is available.
Do not enable maintenance until the first migration/deployment has succeeded.

## Failure behaviour and limits

- Each intake/reply has a persisted identity and a five-minute delivery lease.
  Duplicate successful updates are ignored; retries resume after a saved header
  without normally copying it again. A failed call releases its lease. A crashed
  call can be recovered after five minutes with `/retry` for intake; resend a
  failed owner reply as a new reply if Telegram does not redeliver its update.
- Exactly-once delivery is impossible across SQLite and the Bot API: an API
  request may succeed and its response/database acknowledgement may be lost.
  Such an ambiguous failure can produce a duplicate. Telegram's native retry
  guarantees are not documented; inspect `/pending` rather than assume retries.
- If the source is unavailable/protected, the bot delivers saved text as a
  fallback and asks the sender to resend the attachment. If a sender blocks
  the bot, the owner sees a reply failure instead of a success confirmation.
- Sending to an inbox that has not started the bot fails; start it and retry.
  Bot API flood limits still apply; bursts into one DM may need manual retries.
- No user content or token is explicitly logged. BotFather/platform logs and
  Telegram messages are separate from the application's database retention.
- This implementation was tested offline, **not deployed or live-verified**.
  Smoke-test text, screenshot, voice, owner reply, Done and `/delete` before
  advertising the bot. Never send credentials/subscription URLs as test data.

References: [Telegram Serverless](https://core.telegram.org/bots/serverless),
[@grammyjs/tgcloud](https://github.com/grammyjs/tgcloud).
