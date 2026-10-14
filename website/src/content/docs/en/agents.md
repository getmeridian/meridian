---
title: AI Agents
description: Let Claude Code, Codex, Cursor, or another AI agent run Meridian for you, safely.
---

Meridian is built to be driven by AI agents as well as people. An agent can deploy servers, add people, read health checks, and explain failures, using the same commands you would.

## Point your agent at Meridian

Ask the agent to run this before it does anything else:

```bash
meridian help agents
```

It prints a short Markdown guide for agents: what Meridian does, how the fleet fits together, where its state lives, the rules for using the CLI safely, exit codes, and common tasks. The same guide is at [getmeridian.org/agents.md](https://getmeridian.org/agents.md).

To make this permanent, add a line to your project's agent instructions (`AGENTS.md`, `CLAUDE.md`, or your tool's equivalent):

```markdown
Our proxy servers are managed with Meridian. Run `meridian help agents` before using it.
```

## What the CLI gives agents

- **No prompts.** Commands that change something accept `--yes`. The `setup` wizard needs a terminal, so agents use `deploy`, `node add`, `relay add`, and `client add`, or `setup --intent FILE --yes`.
- **JSON envelopes.** Commands with `--json` print one `meridian.output/v1` envelope on stdout, errors included. Progress and warnings go to stderr.
- **Contracts.** `meridian api commands` lists every command with a machine-readable contract; `meridian api schema NAME` prints its JSON Schema.
- **Meaningful exit codes.** `0` is success, `2` a user error, `3` missing evidence (SSH, panel, or local state), and `4` a completed check that found problems. See the [CLI reference](/en/cli-reference/) for each command.
- **Review before change.** `meridian plan` shows what would change, and `meridian apply --yes` applies exactly that.

## What the docs give agents

- **Every page as Markdown.** Add `.md` to a page's address, for example [/en/relay.md](/en/relay.md). Each page also links its Markdown version in its `<head>`.
- **Copy page and Open in buttons.** Under each page title, copy the page as Markdown or open it in ChatGPT or Claude.
- **llms.txt.** [/llms.txt](/llms.txt) indexes the docs for language models, and [/llms-full.txt](/llms-full.txt) holds all of them in one file.

## Keep secrets out of the chat

Connection pages and subscription URLs give whoever holds them access to your servers. The agent guide tells agents to hand each link only to the person it belongs to and never to reveal the panel password unless you ask. Don't paste links into a chat you share with others. `meridian doctor` produces a redacted report that is safe to share.
