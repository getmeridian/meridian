---
title: AI 智能体
description: 让 Claude Code、Codex、Cursor 或其他 AI 智能体安全地替你运行 Meridian。
---

Meridian 既面向人，也面向 AI 智能体。智能体使用与你相同的命令，就能部署服务器、添加用户、查看健康检查并解释故障。

## 让智能体认识 Meridian

请智能体在做任何事之前先运行：

```bash
meridian help agents
```

它会输出一份面向智能体的简短 Markdown 指南：Meridian 的功能、集群如何组成、状态存放在哪里、安全使用 CLI 的规则、退出码以及常见任务。同一份指南也可在 [getmeridian.org/agents.md](https://getmeridian.org/agents.md) 查看。

若想长期生效，请在项目的智能体说明文件（`AGENTS.md`、`CLAUDE.md` 或你所用工具的对应文件）中加一行：

```markdown
我们的 VPN 服务器由 Meridian 管理。使用它之前，请先运行 `meridian help agents`。
```

## CLI 为智能体提供什么

- **无需交互。** 会修改内容的命令都接受 `--yes`。`setup` 向导需要终端，因此智能体使用 `deploy`、`node add`、`relay add` 和 `client add`，或 `setup --intent FILE --yes`。
- **JSON 信封。** 带 `--json` 的命令会在 stdout 输出一个 `meridian.output/v1` 信封，错误也不例外。进度和警告输出到 stderr。
- **契约。** `meridian api commands` 列出所有命令及其机器可读的契约；`meridian api schema NAME` 输出对应的 JSON Schema。
- **有意义的退出码。** `0` 表示成功，`2` 表示用户错误，`3` 表示缺少证据（SSH、控制服务或本地状态），`4` 表示检查已完成但发现了问题。各命令的详情见 [CLI 参考](/zh/cli-reference/)。
- **先审核，再变更。** `meridian plan` 显示将发生的变更，`meridian apply --yes` 则原样应用这些变更。

## 文档为智能体提供什么

- **每个页面都有 Markdown 版本。** 在页面地址后加 `.md`，例如 [/zh/relay.md](/zh/relay.md)。每个页面的 `<head>` 中也链接了它的 Markdown 版本。
- **“复制页面”和“在其中打开”按钮。** 在每个页面标题下，可将页面复制为 Markdown，或在 ChatGPT、Claude 中打开。
- **llms.txt。** [/llms.txt](/llms.txt) 为语言模型索引了全部文档，[/llms-full.txt](/llms-full.txt) 则把所有文档合并在一个文件中。

## 不要把机密带进聊天

连接页面和订阅 URL 会让持有者获得对你服务器的访问权限。智能体指南要求智能体只把每条链接交给它所属的人，除非你提出要求，否则绝不打印网页面板设置链接。不要把链接粘贴到与他人共享的聊天中。`meridian doctor` 会生成已脱敏的报告，可以放心分享。
