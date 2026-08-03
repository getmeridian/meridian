---
title: CLI 参考
description: Meridian CLI 所有命令和标志的完整参考。
order: 10
section: reference
---

## 命令

### meridian setup

通过可恢复的引导向导配置完整的 V4 拓扑。

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| 标志 | 默认值 | 描述 |
|------|--------|------|
| `--intent FILE` | （无） | 从完整的 `SetupIntent` JSON 文档开始审核；使用 `-` 读取 stdin |
| `--restart` | | 启动前明确丢弃已保存的设置进度 |
| `--yes`, `-y` | | 批准已审核的计划并应用，无需再次确认 |

Setup 会将不含秘密的检查点写入 `~/.meridian/setup.json`，因此再次运行命令会从最后完成的阶段继续。凭据和私钥不会存入该草稿。除非同时传入 `--restart`，否则 `--intent` 不会覆盖现有草稿。

### meridian deploy

部署代理服务器到 VPS。

```
meridian deploy [IP] [flags]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--sni HOST` | www.microsoft.com | TLS 伪装目标 |
| `--domain DOMAIN` | (无) | Cloudflare CDN 回退域名 |
| `--client-name NAME` | default | 第一个客户端的名称 |
| `--display-name NAME` | (无) | 连接页面上的标签 |
| `--icon EMOJI_OR_URL` | (无) | 页面图标 — 表情符号或图片 URL |
| `--color PALETTE` | ocean | 页面颜色主题 (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH 用户 |
| `--harden / --no-harden` | 启用 | SSH + 防火墙加固 |
| `--warp / --no-warp` | 禁用 | 通过 Cloudflare WARP 路由出站流量 |
| `--server NAME` | | 目标服务器（名称或 IP） |
| `--geo-block` / `--no-geo-block` | 启用 | 阻止俄罗斯域名和 IP (geosite:category-ru + geoip:ru) |
| `--ssh-port PORT` | 22 | SSH 端口（如果非标准） |
| `--yes` | | 跳过确认提示 |
| `--json` | | 将最终部署结果作为 `meridian.output/v1` 信封发出 |
| `--events=jsonl` | | 在 stderr 上以 JSONL 形式流式传输类型化进度事件 |
| `--request FILE` | | 从文件读取 `deploy-request` JSON 有效负载；使用 `-` 从 stdin 读取 |
| `--dry-run` | | 验证和规划部署，无需 SSH 或面板更改 |

**机器/UI 流程**：`meridian api workflow deploy --json` 返回可渲染的向导合约。UI 收集这些字段，根据 `deploy-request` 验证，然后运行 `meridian deploy --request deploy.json --json --events=jsonl`。机器部署是非交互式的：请求必须在用户确认后包含 `yes: true`。`--dry-run --json` 在 `data` 下返回 `deploy-plan`，以便 UI 可以在打开 SSH 之前预览模式、端口和生成的路径。

### meridian client

管理客户端访问密钥和连接详情。

```
meridian client add NAME [NAME...]  [--json]
meridian client show NAME [--repair-page] [--json]
meridian client list [--json]
meridian client remove NAME [--yes] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--json` | | 将结果作为 `meridian.output/v1` 信封发出（所有客户端命令） |
| `--yes`, `-y` | | 跳过删除确认（适用于 `client remove`） |
| `--repair-page` | | 仅限旧版部署：在 `client show` 时明确重建缺失的连接页面 |

**`client add`** — 传入多个名称可同时添加多个客户端（例如 `meridian client add alice bob charlie`）。使用 `--json` 时，即使后续旧版客户端创建、连接信息交付或本地状态保存失败，`data.clients[]` 仍会保留所有成功创建的客户端。已完成的远程更改不会回滚，可重试失败会写入 `warnings[]`，命令在普通与 JSON 模式下都以退出码 `3` 结束。

**`client list`** — 使用 `--json` 时，返回 `data.summary` 状态计数和 `data.clients[]` 记录，包含用户名、UUID、状态、流量计数器、创建时间和上次见时间。

**`client show`** — 使用 `--json` 时返回 `data.client` 和 `data.handoff.*` 可用性元数据。只有磁盘上的部署得到确认时才显示连接页面；旧版部署可用 `--repair-page` 明确重建缺失页面。如果修复失败，命令仍会返回 client 与 handoff 证据，同时给出警告并以退出码 `3` 结束。V4 不创建自定义 PWA 页面，只显示面板提供的规范订阅 URL。

**`client enable`** — 恢复之前暂停的客户端，以便他们可以再次连接。使用 `--json` 时，返回带有用户名和状态的 `data.client`。

**`client disable`** — 临时暂停客户端。他们的配置保持完整，但在重新启用前无法连接。使用 `--json` 时，返回带有用户名和状态的 `data.client`。

**V4 所有权** — `client add` 将用户加入 access intent 并应用更新后的 intent。`list` 和 `show` 只读取已声明用户，`enable`/`disable` 也只允许操作这些用户。V4 的 disable 只持续到下一次 `meridian apply`。由于尚未实现安全的托管用户退役，V4 会拒绝 `client remove`；需要立即临时撤销时请用 `client disable`。部分成功批次和 PWA 页面属于旧版行为。

**`client remove`** — 如果面板用户已删除，但本地状态持久化或旧版 share-page 清理尚未完成，删除会返回强类型部分结果，退出码为 `3`。JSON 会在 `data.client` 中保留已删除的客户端记录，并在 `warnings[]` 中报告未完成的后续工作。

**JSON 模式下的变更命令**是非交互式的。`client remove --json` 必须同时传入 `--yes`；否则 Meridian 返回强类型确认错误，而不会打开提示。

### meridian server

管理已知服务器。

```
meridian server add IP
meridian server list
meridian server remove NAME [--yes]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--name NAME` | (自动) | 服务器的显示名称 |
| `--user/-u USER` | root | 服务器上的 SSH 用户 |
| `--ssh-port PORT` | 22 | 非标准 SSH 端口 |
| `--yes`, `-y` | | 跳过删除确认（适用于 `server remove`） |

`server add` 会先验证 SSH；名称、host 和稳定 ID 必须唯一，已有配置不能被静默改指向另一台 host。`server remove` 默认要求确认，并且只删除本地配置。旧版状态或 V4 topology intent 仍引用该配置时，命令会拒绝执行；请先通过 setup/apply 移除服务器角色。仅对已审核的非交互式删除使用 `--yes`。

### meridian node

在多节点舰队中管理其他出口节点。第一个服务器（面板主机）使用 `meridian deploy` 部署；后续出口节点使用 `meridian node add` 添加。

```
meridian node add IP [flags]
meridian node list
meridian --json node list
meridian node remove IP [--yes] [--force]
meridian node check IP
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--user USER` | root | 节点上的 SSH 用户 |
| `--ssh-port PORT` | 22 | 节点上的 SSH 端口（如果非标准） |
| `--name NAME` | (自动，来自 IP) | 在面板/订阅中显示的友好名称 |
| `--domain DOMAIN` | (无) | 节点的 WSS/CDN 回退域名 |
| `--sni HOST` | www.microsoft.com | 该节点的 Reality 伪装目标 |
| `--harden / --no-harden` | 启用 | 节点的 OS + SSH + 防火墙加固 |
| `--yes` | | 跳过 `node add` 或 `node remove` 的确认 |
| `--force` | | 在 `node remove` 时先删除依赖的中继，再删除节点 |
| 全局 `--json` | | 放在 `node` 前，将 `node list` 作为 `meridian.output/v1` 信封发出 |

**旧版**：`meridian node add` 配置主机、通过面板 API 注册，并创建 `reality`、`xhttp`、`hysteria2`，有域名时还会创建 `wss`。条目写入 `nodes[]`，并与启用的 `desired_nodes[]` 同步。`node remove` 会先停止容器；存在依赖中继时会拒绝，`--force` 则先删除这些中继。

**V4**：`node add` 将 exit 加入 `topology_intent` 并应用已审核计划。安全加固是强制的，因此 `--no-harden` 会被拒绝。不传 `--domain` 时只声明 Reality 路由；域名会增加 TLS/WSS。V4 托管节点不允许使用 `node remove`；请在 `meridian setup` 中删除 exit 或 routing gateway 角色，再应用计划。

**健康检查**：`meridian node check` 检查面板、SSH、容器、端口和 TLS。对于 V4 拓扑，它按协议路径中配置的公网端口检查，而不是一律假定为 443。退出码 `0` 表示健康，`4` 表示检查完成但发现问题，`3` 表示所需证据不可用。缺少检查工具或 SSH 连接中断属于证据不可用，不是负面结果。

**JSON 输出**：`meridian --json node list` 使用 `meridian.output/v1` 信封，其中 `data.nodes[]` 包含 ip、name、uuid、status、xray_version 和 traffic_bytes。如果面板状态不可用，已配置节点仍以 `unknown` 状态保留；Meridian 发出警告并以退出码 `3` 结束。

### meridian fleet

检查和修复来自实时面板 API 的舰队。

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet recover --legacy --panel-url URL [--api-token-file FILE]
                       [--profile NAME_OR_UUID] [--squad NAME_OR_UUID]
                       [--panel-node SELECTOR]
                       [--panel-server IP] [--user USER] [--ssh-port PORT] [--force]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--json` | | 将舰队状态作为 JSON 发出（用于脚本编制/CI） |
| `--panel-url URL` | 必需 | 完整的 HTTPS 面板 URL，包括秘密路径 |
| `--api-token-file FILE` | 安全提示或环境变量 | 从权限为 600 的文件读取令牌，而不是 `MERIDIAN_API_TOKEN` 或交互提示 |
| `--profile NAME_OR_UUID` | 唯一时自动选择 | 面板有多个配置文件时选择确切的旧版配置文件 |
| `--squad NAME_OR_UUID` | 仅一个符合条件时自动选择 | 多个旧版访问组都授予全部恢复入站时，选择确切的访问组 |
| `--panel-node SELECTOR` | 无歧义时自动选择 | 通过确切节点名称、UUID 或地址指定面板主机 |
| `--panel-server IP` | 面板 URL 中的 IP | Remnawave 主机的公网 SSH IPv4/IPv6；面板 URL 使用域名时必需 |
| `--user`, `-u USER` | root | 公网面板服务器的 SSH 用户 |
| `--ssh-port PORT` | 22 | 公网面板服务器的 SSH 端口，范围 1 到 65535 |
| `--legacy` | 必需 | 确认目标是使用共享配置文件的旧版部署 |
| `--force` | | 备份并替换现有本地 `cluster.yml` |

**`fleet status`** — 显示面板健康、节点 heartbeat、公网中继 listener、V4 内部中继 hop 和托管用户数量。已声明但缺失或未激活的 V4 访问用户会使健康状态降级，并记录在 `data.summary.missing_access_users` 和 `data.summary.nonactive_access_users` 中。中继 listener 可达只能证明 TCP 连通；任何未通过其他方式验证的中继路径都会使舰队健康保持 `unknown`。请用 `meridian test` 验证完整的加密路径。退出码 `0` 表示已完整观测且健康，`4` 表示降级，`3` 表示 unknown 或 unavailable。

**`fleet inventory`** — 显示已配置面板、节点、中继、期望拓扑，以及可用时的实时面板节点状态。如果实时面板证据不可用，结果仍会保留已配置的 inventory，将受影响的数据源标记为 `unavailable`，用警告说明缺失证据，并以退出码 `3` 结束，而不会丢弃部分数据。它不会打印面板 API 令牌或秘密 URL 路径。`data` 中稳定可用的字段包括 `data.sources.*`、`data.servers[].roles`、`data.summary.*`、`data.nodes[].desired`、`data.nodes[].protocols`、`data.relays[].exit_node_*` 和 `data.desired_nodes[].present`。inventory 中的存在性字段不代表已收敛；漂移和 apply 决策请使用 `plan --json`。

**`fleet recover`** — 只从实时面板导入一个无歧义的旧版共享配置文件。`--legacy` 必需，且 `--panel-url` 必须包含真实秘密路径，而不能只是站点根路径。Recovery 会验证 access squad 和 panel node，通过 SSH 读取 share path，重新填充 `servers.json`；除非能安全确定唯一有效域名，否则拒绝 WSS metadata。面板 URL 使用域名时必须提供 `--panel-server`。V4 intent 无法从面板重建；请恢复备份或重新运行 `meridian setup`。令牌来自安全交互提示、`MERIDIAN_API_TOKEN` 或权限模式为 600 的文件。只有使用 `--force` 并先创建备份后，才会替换现有本地状态。

### meridian api

检查 JSON 输出和未来 UI 客户端使用的机器可读 meridian-core 合约。

```
meridian api schemas [--json] [--include-schemas]
meridian api commands [--json] [--include-schemas]
meridian api schema NAME [--envelope|--json]
meridian api workflow NAME [--json]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--json` | | 将架构目录作为 `meridian.output/v1` 信封发出 |
| `--include-schemas` | | 包含完整 JSON Schema，并自动让 `api schemas` 或 `api commands` 输出 JSON 封装 |
| `--envelope`, `--json` | | 在 `meridian.output/v1` 信封中包装 `api schema NAME` 而不是打印原始 JSON 架构 |

**`api schemas`** — 列出稳定的架构名称，例如 `output-envelope`、`apply-envelope`、`client-list-envelope`、`client-show-envelope`、`deploy-envelope`、`deploy-command-data`、`deploy-request`、`deploy-workflow-answers`、`deploy-result`、`deploy-plan`、`workflow-plan`、`input-field`、`remote-target`、`command-spec`、`remote-command-result`、`plan-envelope`、`fleet-status-envelope`、`fleet-inventory-envelope`、`event`、`apply`、`plan-result`、`fleet-status` 和 `fleet-inventory`。命令信封架构在目录中包含 `commands` 条目。

**`api commands`** — 列出命令合约，包含 `command`、`argv`、`envelope_schema`、`data_schema`、可能的 `statuses`、结构化 `outcomes`、退出代码含义、机器标志、稳定性和 `interrupt_behavior`。其中 `statuses` 与 `outcomes` 只描述已经完成的 JSON 信封。`interrupt_behavior: "exit_130_without_envelope"` 表示 Ctrl-C/SIGINT 会以退出码 `130` 终止进程，但不保证 stdout 上有 JSON。UI 应先查询此目录，再选择用于验证信封的命令数据架构。`deploy` 声明 `--json`、`--events=jsonl`、`--request` 和 `--dry-run`。

**`api schema NAME`** — 打印一个 JSON 架构。示例：`meridian api schema output-envelope`。

**`api workflow NAME`** — 打印一个 UI 可渲染的工作流计划。示例：`meridian api workflow deploy --json` 返回部署向导部分和字段。

### meridian studio

打开 Meridian Studio 及其本地 Engine API。服务器仅绑定到 `127.0.0.1`。

```
meridian studio [--port PORT] [--assets-dir DIRECTORY] [--no-open]
```

| 标志 | 默认值 | 描述 |
|------|--------|------|
| `--port PORT` | 0 | 0 到 65535 的本地主机端口；`0` 自动选择空闲的高位端口 |
| `--assets-dir DIRECTORY` | 内置资源 | 使用已构建的 Studio 资源目录，而非软件包内资源 |
| `--no-open` | | 打印本地 URL，但不打开浏览器 |

如果请求的端口被占用，或内置/显式资源缺失，Studio 会明确失败。显式资源目录必须包含 `studio/index.html`。

### meridian relay

管理中继节点 — 轻量级 TCP 转发器，通过国内服务器将流量路由到国外的出口服务器。

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT]
meridian --json relay list [--exit EXIT]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--exit/-e EXIT` | (部署时必需) | 出口服务器 IP 或名称 |
| `--name NAME` | (自动) | 中继的友好名称（例如 "ru-moscow"） |
| `--port/-p PORT` | 443 | 中继服务器上的监听端口 |
| `--sni HOST` | (自动) | 中继本地的 Reality 伪装目标 |
| `--user/-u USER` | root | 中继上的 SSH 用户 |
| `--ssh-port PORT` | 22 | 中继服务器上的 SSH 端口（如果非标准） |
| `--yes/-y` | | 跳过确认提示 |
| 全局 `--json` | | 放在 `relay` 前，将 `relay list` 作为 `meridian.output/v1` 信封发出 |

**旧版**：省略 `--sni` 时，`relay deploy` 使用 RealiTLScanner 选择本地目标。Realm 只发布 Reality，因为专用 SNI 路由终止于 Reality 入站。`relay check` 检查服务、到出口的 TCP 路径、公网 listener 和面板 Host；`0` 健康，`4` 有问题，`3` 缺少必需证据。缺少远程检查工具、命令超时或 SSH 连接中断时返回 `3`，而不会误报为退出码 `4` 的确定问题。`relay remove` 会清理服务、binary/config、精确 UFW 规则、出口 routing 和面板 Host，并在全部成功前保留本地状态。

**V4**：`relay deploy` 把链加入 intent 并应用；`--exit` 必须匹配已有 V4 exit，空 SNI 会继承出口的 Reality 路由。托管链会拒绝 `relay check` 和 `relay remove`。用 `meridian test` 验证端到端链路，并在 `meridian setup` 中审核和应用链的删除。

**JSON 输出**：`meridian --json relay list` 使用 `meridian.output/v1` 信封，其中包含 `data.relays[]`。

### meridian plan

显示对账计划 — `meridian apply` 将做什么来使集群收敛到 `cluster.yml` 中声明的所需状态。

在 V4 中，setup 创建的已编译 `topology_intent` 是权威来源。普通输出会显示计划 hash 和已编译资源计数，然后报告需要修复的资源、不可用观测以及已保存 generation state 的变更。JSON 数据包含 `plan_hash`、带期望 hash 的 `resources[]`、`drifted_resources[]`、`observation_errors[]` 和 `state_changes[]`；它不使用通用 Terraform 操作符号。

在旧版模式中，可选的 `desired_nodes`、`desired_relays`、`desired_clients` 和 `subscription_page` 是所需输入。只有旧版 plan 才会打印 Terraform 风格的操作差异：`+` 表示添加，`-` 表示删除，`~` 表示更新。

```
meridian plan [--json]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--json` | | 将计划作为 `meridian.output/v1` JSON 信封发出，用于 CI/CD 和 UI 客户端。相同的退出代码；人类计划输出被抑制 |

**退出代码**：
- `0` — 已收敛（无需更改）
- `2` — 待处理更改（运行 `meridian apply` 以收敛）
- `3` — 所需观测证据不可用，包括旧版订阅页面的 SSH 检查超时、连接中断或无法执行检查命令
- 错误也使用非零退出；进程客户端应该将 JSON `status` 和 `errors[].category` 视为权威，因为当 `status` 是 `failed` 时 `2` 也可能意味着用户/配置错误

**旧版 JSON 形状**（`--json` 模式）：
```json
{
  "schema": "meridian.output/v1",
  "meridian_version": "4.x.x",
  "command": "plan",
  "operation_id": "9d0f...",
  "started_at": "2026-05-04T21:00:00Z",
  "duration_ms": 128,
  "status": "changed",
  "exit_code": 2,
  "summary": {
    "text": "Plan: 1 to add, 1 to remove",
    "changed": true,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1}
  },
  "data": {
    "converged": false,
    "summary": "Plan: 1 to add, 1 to remove",
    "exit_code": 2,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1},
    "actions": [
      {"plan_index": 0, "execution_order": 2, "kind": "add_client", "operation": "add", "resource_type": "client",
       "resource_id": "alice", "target": "alice", "detail": "create client alice",
       "phase": "provision", "requires_confirmation": false,
       "destructive": false, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "", "from_extras": false,
       "change_set": [], "symbol": "+", "can_run_parallel": false},
      {"plan_index": 1, "execution_order": 1, "kind": "remove_client", "operation": "remove", "resource_type": "client",
       "resource_id": "ghost", "target": "ghost", "detail": "delete client ghost",
       "phase": "deprovision", "requires_confirmation": true,
       "destructive": true, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "delete client ghost",
       "from_extras": true, "change_set": [], "symbol": "-", "can_run_parallel": false}
    ]
  },
  "warnings": [],
  "errors": []
}
```

在旧版结果中，`data.actions[].from_extras: true` 标记存在于面板上但在 `cluster.yml` 中缺失的资源，即 `meridian apply --prune-extras` 的操作对象。`execution_order` 显示 `apply` 将使用的顺序；为保证替换安全，该顺序可能与显示顺序不同。`operation: "replace"` 标记破坏性替换，例如重新部署中继。在两种 plan 合约中，已收敛时 `status` 为 `no_changes`，apply 尚有工作时为 `changed`；观测失败则使用 `failed` 和退出码 `3`。`data.exit_code` 与进程退出码一致。

V4 的 setup/plan/apply 流程请参阅[声明式工作流](/docs/zh/getting-started/#声明式工作流)。

### meridian apply

使集群收敛到 `cluster.yml` 中声明的所需状态。旧版 apply 执行操作计划、显示差异、请求确认，并按依赖顺序执行操作。V4 应用已经审核的已编译资源图，并报告计划 hash、generation 以及各资源的观测状态。

```
meridian apply [--yes] [--parallel N] [--prune-extras=ask|yes|no] [--json]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--yes`, `-y` | | 跳过确认提示 |
| `--parallel N` | 4 | 仅限旧版：1 到 32 个并行节点配置线程（每个线程使用独立 SSH 会话和面板客户端） |
| `--prune-extras` | `ask` | 仅限旧版：处理存在于面板上但在 `cluster.yml` 中缺失的资源。`ask` 逐项询问（使用 `--yes` 时为安全起见降级为 `no`）；`yes` 自动删除；`no` 跳过并打印单行摘要 |
| `--json` | | 发出 `meridian.output/v1` 最终应用结果，包含每个操作的执行状态 |

V4 接受 `--yes` 和 `--json`。旧版专用选项的非默认值（`--parallel` 不为 `4`，或 `--prune-extras` 不为 `ask`）会在 apply 开始前作为用户错误被拒绝，并返回退出码 `2`。

V4 会在 apply 过程中保存带来源标记的本地检查点。如果检查点或收敛状态保存失败，Meridian 会返回退出码 `3` 的系统错误，并警告远程状态可能已经改变。请先修复本地状态持久化，再重新运行 `meridian plan` 和 `meridian apply`，以观测并对账实际结果。

破坏性操作（删除、UPDATE_RELAY 重新配置）打印警告并需要单独确认。计划早期的失败会跳过剩余的破坏性操作 — `cluster.yml` 保持真实。

在旧版 `--json` 输出中，`data.plan` 包含类型化计划，`data.actions[]` 包含 `status: "succeeded" | "failed" | "skipped"` 的执行结果。V4 则返回已编译操作，其状态为 `"converged" | "applied" | "failed" | "unknown" | "skipped"`。JSON 模式是非交互式的：如果更改需要确认且缺少 `--yes`，Meridian 返回 `MERIDIAN_CONFIRMATION_REQUIRED` 以及计算出的计划或已编译计划预览。旧版仅面板漂移配合 `--prune-extras=ask` 时会返回 `MERIDIAN_DRIFT_DECISION_REQUIRED`；传入 `--prune-extras=no` 可保留漂移，传入 `--prune-extras=yes` 可删除漂移。如果旧版操作已经执行，但变更后的状态保存失败（包括 `cluster.yml` 被并发修改），信封会保留操作结果，并以退出码 `3` 返回 `MERIDIAN_STATE_SAVE_FAILED`。JSON 合约只报告执行结果，不会使破坏性操作具备事务性。UI 客户端应检查失败或跳过的操作，并在修复根因后幂等重试。

**旧版 drift 示例**：若 `cluster.yml` 包含 `desired_clients: ['alice']`，但面板还有 `bob`，`meridian plan` 会显示 `- remove client: bob`。`--prune-extras=ask` 要求明确决定；`--yes --prune-extras=yes` 删除 extra，而只传 `--yes`、不明确选择时会跳过。

### meridian preflight

部署前验证服务器。无需安装即可测试 SNI、端口、DNS、操作系统、磁盘和时钟。DNS 使用服务器配置的解析器；Meridian 不会把服务器地址发送给第三方 ASN 服务。退出码 `0` 表示全部通过，`4` 表示发现需要处理的问题，`3` 表示所需证据不可用。

```
meridian preflight [IP] [--domain NAME] [--sni HOST] [--user USER]
                   [--ai] [--server NAME]
```

`--domain` 验证域名是否解析到目标服务器。`--sni` 覆盖伪装目标，`--user` 覆盖已保存的 SSH 用户，`--ai` 生成可直接交给 AI 的诊断提示。

### meridian scan

使用固定版本的 RealiTLScanner 在服务器网络上查找最优 SNI 目标。Meridian 会在执行前验证发布的 SHA-256，并在扫描后删除隔离的远程工作目录。

```
meridian scan [IP] [--user USER] [--server NAME]
```

RealiTLScanner 仅支持 IPv4；仅 IPv6 目标会以无法判定的退出码 `3` 结束。所选目标只会保存到已跟踪的旧版节点。对于 V4，Meridian 仅报告选择，不会改变已审核的 intent；请通过 `meridian setup` 应用。空白输入返回 `0`，无效选择返回 `2`，没有候选项返回 `4`，扫描器或下载失败返回 `3`。

### meridian test

测试代理可达性并验证来自客户端设备的实际连接。无需 SSH。

默认完整测试会按确定性规则选择活跃客户端，获取 Remnawave 下发的准确 Xray 订阅 JSON，并执行 automatic fallback 和所选目标的每个受支持 outbound。V4 只能从 `topology_intent.access.users` 中选择用户；service user 既不能被明确选择，也不会作为 fallback 被选中。首次运行可能花费最多 120 秒从 GitHub 下载并验证固定的 Xray runtime。流量检查会轮换独立 IP observer，并与直连控制请求比较，因此 observer 故障不会被误判为协议失败。在受控环境中可用 `MERIDIAN_CONNECT_TEST_URL` 覆盖 observer。

`--client` 用于选择特定的活跃托管客户端。`--basic` 仅执行网络/TLS 观测，无法验证仅使用 UDP 的流量。`--basic` 与 `--client NAME` 不兼容；同时使用会在任何网络 I/O 之前以用户错误退出，退出码为 `2`。`--timeout` 默认值为 `5` 秒，接受 `1` 到 `30`；它限制每个网络操作，与首次 Xray bootstrap 分开计算。

完整测试期间，如果发生面板传输错误，或收到 HTTP `429`、HTTP `5xx` 响应，则规范订阅证据不可用（`PANEL_UNAVAILABLE`），结果无法判定并以退出码 `3` 结束。相反，确定性的非身份验证订阅 HTTP `4xx` 拒绝（例如 `400` 或 `422`，不包括 `429`）属于已完成的负面结果（`PANEL_REQUEST_FAILED`），退出码为 `4`。

```
meridian test [IP|DOMAIN] [--server NAME] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--timeout SECONDS] [--json]
```

退出码 `0` 表示所有必需检查均已通过，`4` 表示测试已完成但发现了负面结果，`3` 表示所需证据不可用。`--json` 会输出包含每项检查和发现的强类型 `meridian.output/v1` 封装。

### meridian probe

从审查者的视角探测服务器，检查部署是否可被识别。无需 SSH。它适用于任意 IP/域名；当目标属于已保存的 Meridian 拓扑时，会自动应用更严格的策略。

检查项由实际部署形态决定：公网和内部端口暴露、HTTP/TLS 行为、SNI 一致性与伪装、常见代理/面板路径、WebSocket 升级、反向 DNS、HTTP/2、旧版 TLS、加固的根路径以及已配置的域名。`--sni` 可覆盖通用 TLS SNI。探测不会根据一个空数据报猜测仅限 UDP 的监听器；而是报告这些监听器需要规范的完整连接测试。

```
meridian probe [IP|DOMAIN] [--server NAME] [--sni NAME]
               [--timeout SECONDS] [--json]
```

Probe 与 `test` 使用相同的退出契约：`0` 表示通过，`4` 表示已完成但有负面结果，`3` 表示无法判定。`--timeout` 范围为 1–30 秒并作用于每个操作；TARGET 与 `--server` 互斥。重复观测不完整，或可信 CDN edge 返回不同证书时，会报告无法判定而不是失败；证书 trust 或 hostname 无效仍是失败。被跳过的证据绝不会报告为通过。

### meridian doctor

收集系统诊断信息以便调试。别名：`meridian rage`。

```
meridian doctor [IP] [--sni HOST] [--user USER] [--ai] [--server NAME]
```

`--sni` 选择要诊断的伪装目标，`--user` 覆盖已保存的 SSH 用户，`--ai` 复制已脱敏且可直接交给 AI 的报告。

Doctor 根据服务器角色和 V4 已配置的公网端口生成诊断部分与监听端口。缺少检查工具或 SSH 连接中断会使报告无法判定并返回 `3`；已收集的部分仍会显示。

### meridian teardown

从服务器删除代理。

```
meridian teardown [IP] [--user USER] [--server NAME] [--yes]
```

`--yes` 跳过破坏性操作确认，`--user` 覆盖已保存的 SSH 用户。目标选择是非交互式的：未知的 `--server`，或没有唯一的隐式候选目标，都会直接以退出码 `2` 结束，而不会提示输入 IP。拒绝破坏性确认时退出码为 `1`。在 V4 中，只要服务器仍承担 control、exit、routing gateway、relay hop 或活跃 workload，teardown 就会拒绝；请先在 setup 中移除角色并应用计划。只要仍有其他节点或中继，面板主机也不能删除。中继目标会走完整 relay cleanup，服务器配置只在清理成功后删除。

### meridian update

将 CLI 更新到 PyPI 的最新版本。退出码 `0` 表示版本已是最新或更新成功；`3` 表示版本检查或升级失败。

```
meridian update
```

## 全局选项

全局选项必须放在命令之前，例如 `meridian --quiet doctor`。命令本地选项（如 `test --json`）仍放在命令之后。

| 标志 | 描述 |
|------|------|
| `--version`, `-v` | 显示 CLI 版本并退出 |
| `--verbose` | 启用调试日志 |
| `--quiet`, `-q` | 隐藏进度输出 |
| `--json` | 请求受支持命令的强类型 JSON 封装；不支持的命令会明确失败 |
| `--install-completion` | 为当前 shell 安装自动补全 |
| `--show-completion` | 打印当前 shell 的自动补全脚本 |

全局 `--json` 支持 deploy、plan、apply、test、probe、所有 client 命令、fleet status/inventory、node/relay list 和 API 命令。对于提供本地 `--json` 的命令，也可将其放在命令之后。运行 `meridian api commands --json` 可发现稳定的机器合约。

## 常用服务器选项

这些名称会出现在多条服务器相关命令中，但每条命令只接受其自身章节列出的选项：

| 标志 | 适用命令 |
|------|----------|
| `--server NAME` | deploy、preflight、scan、test、probe、doctor、teardown |
| `--user`, `-u USER` | deploy、node add/check、relay 命令、preflight、scan、doctor、teardown |
| `--sni HOST` | deploy、node add、relay deploy、preflight、test、probe、doctor |
| `--domain DOMAIN` | deploy、node add、preflight、test |

Client 命令直接操作集群面板，不接受 `--server`。`test` 和 `probe` 在客户端设备上运行，不使用 SSH。

## 服务器解析

使用共享 SSH resolver 的命令遵循以下优先级：
1. 显式 IP 参数或 `local` 关键字（在此服务器上部署，无需 SSH）
2. `--server NAME` 标志（也接受 `--server local`）
3. 本地模式检测（在服务器本身运行）
4. 单个服务器自动选择（如果只保存了一个）

没有可选服务器或有多台服务器时，包括 `teardown` 在内的命令会以退出码 `2` 结束并要求显式名称；共享 resolver 不会打开交互提示。`test` 和 `probe` 使用独立的外部目标解析：TARGET 或 `--server` 二选一，然后本地检测，再尝试唯一保存的配置；零个或多个配置都会报错。
