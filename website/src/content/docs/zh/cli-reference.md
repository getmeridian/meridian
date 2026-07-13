---
title: CLI 参考
description: Meridian CLI 所有命令和标志的完整参考。
order: 10
section: reference
---

## 命令

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
meridian client show NAME [--json]
meridian client list [--json]
meridian client remove NAME [--yes] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--json` | | 将结果作为 `meridian.output/v1` 信封发出（所有客户端命令） |
| `--yes`, `-y` | | 跳过删除确认（适用于 `client remove`） |

**`client add`** — 传递多个名称以同时添加多个客户端（例如 `meridian client add alice bob charlie`）。使用 `--json` 时，为每个创建的客户端返回带有用户名、UUID 和状态的 `data.clients[]`。

**`client list`** — 使用 `--json` 时，返回 `data.summary` 状态计数和 `data.clients[]` 记录，包含用户名、UUID、状态、流量计数器、创建时间和上次见时间。

**`client show`** — 使用 `--json` 时，返回一个 `data.client` 记录以及 `data.handoff.*` 可用性元数据。人类命令仍然打印可用的订阅/共享 URL。

**`client enable`** — 恢复之前暂停的客户端，以便他们可以再次连接。使用 `--json` 时，返回带有用户名和状态的 `data.client`。

**`client disable`** — 临时暂停客户端。他们的配置保持完整，但在重新启用前无法连接。使用 `--json` 时，返回带有用户名和状态的 `data.client`。

### meridian server

管理已知服务器。

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--name NAME` | (自动) | 服务器的显示名称 |

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
| `--yes` | | 跳过确认提示（适用于 `node remove`） |
| `--force` | | 在 `node remove` 时，即使继电器以此节点作为出口也继续 |
| 全局 `--json` | | 放在 `node` 前，将 `node list` 作为 `meridian.output/v1` 信封发出 |

**工作原理**：`meridian node add` 配置节点主机并通过面板 API 注册，然后创建 `reality`、`xhttp` 和 `hysteria2` 主机条目（域名模式还包括 `wss`）。客户端在下次刷新订阅时收到新出口。新条目添加到 `nodes[]`；如果 `desired_nodes[]` 非 null，也会同步更新。

**健康检查**：`meridian node check` 运行面板状态、SSH、容器、端口和 TLS 检查。当检查失败时，它打印修复提示（例如 `Run: docker compose up -d`）。

**删除**：`meridian node remove` SSH 进入节点以在从集群和面板中移除节点之前停止容器。它拒绝删除仍然是一个或多个继电器的 `exit_node` 的节点；传递 `--force` 以覆盖。

**JSON 输出**：`meridian --json node list` 使用 `meridian.output/v1` 信封，其中 `data.nodes[]` 包含 ip、name、uuid、status、xray_version 和 traffic_bytes。

### meridian fleet

检查和修复来自实时面板 API 的舰队。

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet recover --panel-url URL --api-token TOKEN
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--json` | | 将舰队状态作为 JSON 发出（用于脚本编制/CI） |
| `--panel-url URL` | 必需 | 面板 HTTPS URL（用于 `fleet recover`） |
| `--api-token TOKEN` | 必需 | Remnawave API 令牌（用于 `fleet recover`） |

**`fleet status`** — 显示面板健康状况、每个节点的连接 + Xray 版本 + 流量、每个继电器的上游以及用户计数。使用 `--json` 时，输出使用 `meridian.output/v1` 信封。`data` 内的稳定字段访问：`data.panel.url`、`data.panel.healthy`、`data.sources.*`、`data.servers[].roles`、`data.nodes[].status`（`"connected"`、`"disconnected"`、`"disabled"`、`"unknown"`）、`data.relays[].health`（`"healthy"`、`"unhealthy"`、`"unknown"`）和 `data.summary.health/needs_attention/active_users/disabled_users/unknown_nodes/unhealthy_relays`。当无法收集所需的实时数据时，`data.summary.health` 是 `"unknown"`。顶级 `status` 报告命令执行，而不是舰队健康状况。

**`fleet inventory`** — 显示配置的面板、节点、继电器、所需拓扑以及可到达时的实时面板节点状态。它永远不会打印面板 API 令牌或秘密 URL 路径。使用 `--json` 时，输出使用 `meridian.output/v1` 信封。`data` 内的稳定字段访问包括 `data.sources.*`、`data.servers[].roles`、`data.summary.*`、`data.nodes[].desired`、`data.nodes[].protocols`、`data.relays[].exit_node_*` 和 `data.desired_nodes[].present`。库存存在字段不是对账真相；使用 `plan --json` 进行漂移/应用决策。

**`fleet recover`** — 从实时面板重建 `~/.meridian/cluster.yml`。它获取配置文件、节点和入站 UUID，再通过 SSH 恢复服务器端元数据并推导 Reality 公钥。之后请手动检查 SSH 设置、面板主机选择和中继。

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
| `--include-schemas` | | 在 `api schemas --json` 或 `api commands --json` 输出中包含完整的 JSON 架构 |
| `--envelope`, `--json` | | 在 `meridian.output/v1` 信封中包装 `api schema NAME` 而不是打印原始 JSON 架构 |

**`api schemas`** — 列出稳定的架构名称，例如 `output-envelope`、`apply-envelope`、`client-list-envelope`、`client-show-envelope`、`deploy-envelope`、`deploy-command-data`、`deploy-request`、`deploy-workflow-answers`、`deploy-result`、`deploy-plan`、`workflow-plan`、`input-field`、`remote-target`、`command-spec`、`remote-command-result`、`plan-envelope`、`fleet-status-envelope`、`fleet-inventory-envelope`、`event`、`apply`、`plan-result`、`fleet-status` 和 `fleet-inventory`。命令信封架构在目录中包含 `commands` 条目。

**`api commands`** — 列出迁移的命令合约，包含 `command`、`argv`、`envelope_schema`、`data_schema`、可能的 `statuses`、结构化 `outcomes`、退出代码含义、机器标志和稳定性。在将 UI 连接到决定哪个命令有效负载架构验证给定信封之前使用此。`deploy` 宣传 `--json`、`--events=jsonl`、`--request` 和 `--dry-run`。

**`api schema NAME`** — 打印一个 JSON 架构。示例：`meridian api schema output-envelope`。

**`api workflow NAME`** — 打印一个 UI 可渲染的工作流计划。示例：`meridian api workflow deploy --json` 返回部署向导部分和字段。

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
| `--user/-u USER` | root | 中继上的 SSH 用户 |
| `--ssh-port PORT` | 22 | 中继服务器上的 SSH 端口（如果非标准） |
| `--yes/-y` | | 跳过确认提示 |
| 全局 `--json` | | 放在 `relay` 前，将 `relay list` 作为 `meridian.output/v1` 信封发出 |

**中继如何工作**：客户端连接到中继的国内 IP。中继将原始 TCP 转发到国外的出口服务器。所有加密都是端到端的，在客户端和出口之间 — 中继永远看不到明文。所有协议（Reality、XHTTP、WSS）都通过中继工作。

**JSON 输出**：`meridian --json relay list` 使用 `meridian.output/v1` 信封，其中包含 `data.relays[]`。

### meridian plan

显示对账计划 — `meridian apply` 将做什么来使集群收敛到 `cluster.yml` 中声明的所需状态。

从 `cluster.yml` 中读取 `desired_nodes`、`desired_relays`、`desired_clients` 和 `subscription_page`，从面板获取实际状态，并打印 Terraform 风格的差异，其中 `+` 表示添加，`-` 表示删除，`~` 表示更新。

```
meridian plan [--json]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--json` | | 将计划作为 `meridian.output/v1` JSON 信封发出，用于 CI/CD 和 UI 客户端。相同的退出代码；人类计划输出被抑制 |

**退出代码**：
- `0` — 已收敛（无需更改）
- `2` — 待处理更改（运行 `meridian apply` 以收敛）
- 错误也使用非零退出；进程客户端应该将 JSON `status` 和 `errors[].category` 视为权威，因为当 `status` 是 `failed` 时 `2` 也可能意味着用户/配置错误

**JSON 形状**（`--json` 模式）：
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

`data.actions[].from_extras: true` 标记存在于面板上但在 `cluster.yml` 中缺失的资源 — `meridian apply --prune-extras` 操作的输入。`execution_order` 显示 `apply` 将使用的顺序，这可能与显示顺序不同以确保替换安全。`operation: "replace"` 标记破坏性替换，例如中继重新配置。当收敛时 `status` 是 `no_changes`，当应用有工作要做时是 `changed`；`data.exit_code` 镜像进程退出代码。

参见[声明性工作流](/docs/zh/getting-started/#declarative-workflow)了解如何编写 `cluster.yml`。

### meridian apply

使集群收敛到 `cluster.yml` 中声明的所需状态。在内部运行 `plan`，显示差异，要求确认，然后以依赖顺序执行操作（首先删除，然后添加，最后删除节点）。

```
meridian apply [--yes] [--prune-extras=ask|yes|no] [--json]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--yes`, `-y` | | 跳过确认提示 |
| `--parallel N` | 4 | 最大并行节点配置线程数（每个节点获得自己的 SSH 会话和面板客户端） |
| `--prune-extras` | `ask` | 如何处理漂移 — 存在于面板上但在 `cluster.yml` 中缺失的资源。`ask` 提示每个资源（在 `--yes` 下降级为 `no` 以确保安全）；`yes` 自动删除；`no` 跳过并打印单行摘要 |
| `--json` | | 发出 `meridian.output/v1` 最终应用结果，包含每个操作的执行状态 |

破坏性操作（删除、UPDATE_RELAY 重新配置）打印警告并需要单独确认。计划早期的失败会跳过剩余的破坏性操作 — `cluster.yml` 保持真实。

使用 `--json` 时，`data.plan` 包含类型化计划，`data.actions[]` 包含带有 `status: "succeeded" | "failed" | "skipped"` 的执行结果。JSON 模式是非交互式的：如果更改需要确认且缺少 `--yes`，Meridian 返回 `MERIDIAN_CONFIRMATION_REQUIRED` 和计算的计划。如果存在仅面板的漂移且 `--prune-extras` 保持 `ask`，Meridian 返回 `MERIDIAN_DRIFT_DECISION_REQUIRED`；传递 `--prune-extras=no` 以保持漂移或 `--prune-extras=yes` 以删除它。JSON 合约报告执行；它不使破坏性操作成为事务性的。UI 客户端应检查失败/跳过的操作，并在修复基础问题后幂等地重新运行。

**漂移处理示例**：如果 `cluster.yml` 列出 `desired_clients: ['alice']` 但面板也有 `bob`（例如通过面板 UI 创建），`meridian plan` 显示 `- remove client: bob`。使用默认的 `--prune-extras=ask` 系统会询问您是否要删除 `bob` 或保留他。`--yes --prune-extras=yes` 静默运行删除；`--yes` 单独（无显式 `--prune-extras`）跳过它。

### meridian preflight

预检查服务器验证。测试 SNI、端口、DNS、OS、磁盘、ASN，无需安装任何内容。

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

使用 RealiTLScanner 在服务器网络上查找最优 SNI 目标。

```
meridian scan [IP] [--server NAME]
```

### meridian test

测试代理可达性并验证来自客户端设备的实际连接。无需 SSH。

首先检查基本可达性（TCP、TLS 握手、domain HTTPS）。然后下载本地 xray 客户端二进制文件（首次使用后缓存），通过代理为每个活跃协议（Reality、XHTTP、WSS）连接，并确认流量端到端流动。

```
meridian test [IP] [--server NAME]
```

### meridian probe

像审查者一样探测服务器 — 检查部署是否可检测。无需 SSH。适用于任何服务器，不仅仅是 Meridian 部署。接受 IP 地址或域名。

运行 9 项检查：端口表面、HTTP 响应、TLS 证书、SNI 一致性、代理路径探测、WebSocket 升级、反向 DNS、HTTP/2 支持和遗留 TLS 版本。

```
meridian probe [IP|DOMAIN] [--server NAME]
```

### meridian doctor

收集系统诊断信息以便调试。别名：`meridian rage`。

```
meridian doctor [IP] [--ai] [--server NAME]
```

### meridian teardown

从服务器删除代理。

```
meridian teardown [IP] [--server NAME] [--yes]
```

### meridian update

将 CLI 更新到最新版本。

```
meridian update
```

### meridian --version

显示 CLI 版本。

```
meridian --version
meridian -v
```

## 全局标志

这些标志在通过 SSH（部署、节点、中继、预检查、测试、探测、医生、断裂）与服务器交互的命令上可用：

| 标志 | 描述 |
|------|-------------|
| `--server NAME` | 针对特定的已命名服务器 |
| `--user/-u USER` | SSH 用户（默认：root，非 root 获得 sudo 自动） |
| `--sni HOST` | TLS 伪装目标（由部署、预检查、测试、医生使用） |
| `--domain DOMAIN` | Cloudflare CDN 回退域名（由部署、预检查、测试使用） |

客户端命令（`client add/show/list/remove/enable/disable`）直接在集群的面板上操作。

这些命令不接受 `--server`。

## 服务器解析

需要服务器的命令按照此优先级：
1. 显式 IP 参数或 `local` 关键字（在此服务器上部署，无需 SSH）
2. `--server NAME` 标志（也接受 `--server local`）
3. 本地模式检测（在服务器本身运行）
4. 单个服务器自动选择（如果只保存了一个）
5. 交互式提示
