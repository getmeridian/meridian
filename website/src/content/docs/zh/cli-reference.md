---
title: CLI 参考
description: Meridian CLI 所有命令和标志的完整参考。
order: 10
section: reference
---

## 命令

### meridian deploy

部署一个服务器并获得可分享的连接链接。服务器既是控制平面（Remnawave 面板）又是出口。部署构建与 `meridian setup` 相同的已审核拓扑，用检查点应用它，验证通过交付的订阅的真实流量，并打印连接页面和订阅 URL。重新运行相同命令恢复中断的部署或重新部署，保持路径、密钥和用户。

```
meridian deploy [SERVER] [flags]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--sni HOST` | (自动) | 伪装目标。不指定时，服务器尝试 `www.google.com`、`dl.google.com`、`www.cloudflare.com` 并使用第一个能承载流量的；重新部署保持保存的目标。显式 `--sni` 永不被交换 |
| `--domain DOMAIN` | (无) | 用于 XHTTP 和 WSS 的你的域名（Cloudflare CDN 回退）。面板和连接页面保持在 IP 证书上 |
| `--hysteria2` | 关 | 在 UDP/443 上也提供 Hysteria2。需要 `--domain`；QUIC 在某些地区被限制 |
| `--fingerprint NAME` | chrome | 客户端在 Reality、XHTTP 和 WSS 上呈现的 TLS 指纹（`chrome`、`firefox`、`safari`、`ios`、`android`、`edge`、`random`、`randomized`）。如果你的地区开始标记当前指纹，请更改它；重新部署保留已保存的值 |
| `--client-name NAME` | default | 第一个客户端的名称 |
| `--display-name NAME` | (无) | 连接页面的标签 |
| `--icon EMOJI_OR_URL` | (无) | 页面图标 — 表情符号或图片 URL |
| `--color PALETTE` | ocean | 页面色彩主题 (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | 已保存或 root | SSH 用户（非 root 需无密码 sudo） |
| `--ssh-port PORT` | 已保存或 22 | SSH 端口 |
| `--harden / --no-harden` | 启 | 仅 SSH 密钥登录、fail2ban 和 UFW 防火墙。`--no-harden` 用于与其他服务共享的服务器 |
| `--warp / --no-warp` | 关 | 通过 Cloudflare WARP 发送出站流量 |
| `--geo-block` / `--no-geo-block` | 启 | 在出口拒绝俄罗斯域名和 IP（`geosite:category-ru` + `geoip:ru`）。Xray JSON 和 Mihomo 订阅已经从设备直接发送它们 |
| `--yes` | | 跳过确认提示 |
| `--json` | | 将最终部署结果作为 `meridian.output/v1` 信封发出 |
| `--events=jsonl` | | 在 stderr 上以 JSONL 形式流式传输类型化进度事件 |
| `--request FILE` | | 从文件读取 `deploy-request` JSON 有效负载；用 `-` 表示 stdin |
| `--dry-run` | | 编译和预览部署无需 SSH 或本地变更 |

不指定 IP，部署会提示输入。`local` 在运行所在的机器上部署。部署仅重新部署已经是舰队中出口的服务器；用 `meridian node add` 添加其他服务器。变更目前是增量的：删除已部署的东西（如去掉 `--domain`）被拒绝。

**机器/UI 流程**：`meridian api workflow deploy --json` 返回可渲染的向导契约。UI 收集这些字段，针对 `deploy-request` 验证它们，然后运行 `meridian deploy --request deploy.json --json --events=jsonl`。机器部署非交互式：请求必须在用户确认后包含 `yes: true`。`--dry-run --json` 返回 `data` 下的 `deploy-plan`，所以 UI 可以在打开 SSH 之前预览模式、端口和生成的路径。

### meridian setup

用可恢复的引导向导配置多个服务器：出口、透明 Realm 中继链、带故障转移池的路由网关和访问用户。

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--intent FILE` | (无) | 从完整的拓扑 JSON 文档开始审核；用 `-` 表示 stdin |
| `--restart` | | 启动前放弃保存的设置进度 |
| `--yes`, `-y` | | 批准审核并应用无需询问 |

向导走过服务器、角色、协议路径、路由、访问、审核和应用。在任何提示处你可以输入 `back`、`details`、`save` 或 `quit`。进度在每个阶段后保存在 `~/.meridian/setup.json` 中，所以再次运行 `meridian setup` 继续；密码和私钥永不存储在那里。`--intent` 不会替换现有草稿除非也给出 `--restart`。当应用成功时，设置验证每个节点和订阅并打印每个用户的连接页面。

### meridian plan

显示 `meridian apply` 会变更什么，比较 `~/.meridian/cluster.yml` 中的拓扑与实时服务器和面板。没有变更任何东西。

```
meridian plan [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--json` | | 将计划作为 `meridian.output/v1` 信封为 CI 和 UI 客户端发出 |

输出显示计划哈希、按类型的资源计数和任何需要修复或无法观察的资源。JSON `data` 包含 `plan_hash`、`converged`、`resources[]`、`drifted_resources[]`、`observation_errors[]` 和 `state_changes[]`。

**退出码**：
- `0` — 已收敛，无事可做
- `1` — 无计划（还没有舰队或不可用的本地状态）
- `2` — 变更待定（运行 `meridian apply`）
- `3` — 需要的证据（面板、SSH、本地状态）不可用

不要手工编辑 `cluster.yml` 中的拓扑。用 `meridian setup`、`deploy`、`client add`、`node add` 或 `relay add` 变更它。

### meridian apply

应用 `cluster.yml` 中已审核的拓扑到你的服务器。应用显示计划哈希和资源计数，要求确认，然后应用它显示的确切计划，带检查点。中断的应用从停止的地方恢复。

```
meridian apply [--yes] [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--yes`, `-y` | | 跳过确认 |
| `--json` | | 将最终应用结果作为 `meridian.output/v1` 信封发出 |

JSON `data` 包含 `plan_hash`、`generation` 和 `actions[]` 带 `status: "converged" | "applied" | "failed" | "unknown" | "skipped"`。JSON 模式永不提示：没有 `--yes` 它返回 `MERIDIAN_CONFIRMATION_REQUIRED` 带计划哈希并退出 `2`。失败的资源退出 `3`；修复原因并再次运行应用。如果远程变更后本地状态无法保存，应用退出 `3` 并说明；修复 `~/.meridian` 权限，然后再次运行 `plan` 和 `apply`。

删除用户或协议路径被应用：删除的资源在计划中剩下的所有内容收敛后被删除，`plan` 首先列出它们。删除出口、中继或路由网关目前被拒绝。

### meridian client

管理连接的人。

```
meridian client add NAME [NAME...] [--json]
meridian client show NAME [--repair-page] [--json]
meridian client list [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
meridian client remove NAME [--yes] [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--json` | | 将结果作为 `meridian.output/v1` 信封发出（所有客户端命令） |
| `--repair-page` | | 重新发布客户端的连接页面（`client show`） |
| `--yes`, `-y` | | 确认 `client remove`；`--json` 需要 |

**`client add`** — 添加一个或多个人到拓扑，应用它，并为每个人打印连接页面和订阅 URL。页面从他们的真实订阅构建，所以中继和 Hysteria2 在存在时显示。拓扑中已有的或面板中已存在的名称被拒绝。如果客户端的订阅链接无法构建，客户端仍被添加命令退出 `3`；仅缺失连接页面是警告（用 `client show --repair-page` 修复）。

**`client show`** — 打印连接页面、订阅 URL、状态、流量和最后看到时间。`--repair-page` 重新发布缺失的页面；如果失败，链接仍显示带警告和退出 `3`。

**`client list`** — 列出客户端带状态、流量、创建时间和最后看到。带 `--json`，返回 `data.summary` 计数和 `data.clients[]`。

**`client enable` / `client disable`** — 立即恢复或暂停客户端。禁用持续到下次应用（包括重新部署和 `client add`、`node add`、`relay add`），它打开所有声明的客户端。

**`client remove`** — 从拓扑中删除客户端，删除面板用户和其连接页面。除非 `--yes`，否则要求确认。最后一个客户端无法删除。不要直接在面板中删除用户；Meridian 在下次应用时重新创建拓扑中保留的任何客户端。

### meridian server

管理保存的 SSH 服务器（`~/.meridian/servers.json`）。

```
meridian server add [USER@]IP[:PORT] [--name NAME] [--user USER] [--ssh-port PORT]
meridian server list
meridian server remove NAME_OR_IP [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--name NAME` | (无) | 服务器的显示名称 |
| `--user/-u USER` | root | SSH 用户 |
| `--ssh-port PORT` | 22 | SSH 端口 |
| `--yes`, `-y` | | 跳过删除确认（`server remove`） |

名称和地址必须唯一；`server add` 拒绝碰撞而不是重新定向现有服务器。`server remove` 仅删除保存的配置并在服务器仍在拓扑中有角色时拒绝。

### meridian node

管理出口服务器。第一个服务器用 `meridian deploy`（或 `meridian setup`）部署；用 `meridian node add` 添加更多出口。

```
meridian node add [USER@]IP[:PORT] [flags]
meridian node list [--json]
meridian node check NAME_OR_IP [--user USER]
meridian node remove NAME_OR_IP [--yes] [--force]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--name NAME` | (自动) | 节点的友好名称 |
| `--user USER` | 已保存或 root | 节点上的 SSH 用户 |
| `--ssh-port PORT` | 已保存或 22 | 节点的 SSH 端口（`node add`） |
| `--sni HOST` | www.google.com | 此节点的 Reality 伪装目标 |
| `--domain DOMAIN` | (无) | 节点域；在其上添加 XHTTP、WSS 和 Hysteria2 路径 |
| `--harden / --no-harden` | 启 | OS、SSH 和防火墙加固。舰队服务器总是保持它；`--no-harden` 被拒绝 |
| `--yes` | | 跳过确认提示 |
| `--force` | | `node remove`：也删除路由到此出口的中继链 |
| `--json` | | 将 `node list` 作为 `meridian.output/v1` 信封发出 |

**`node add`** — 通过 SSH 连接，添加服务器作为拓扑中的出口，并应用已审核的计划（加固、Docker、Xray、面板注册）。现有客户端在下次订阅刷新时接收新出口。

**`node list`** — 列出节点带实时面板状态、Xray 版本和流量。如果面板状态不可用，已配置的节点显示为 `unknown`，命令退出 `3`。

**`node check`** — 检查面板连接、SSH、容器、节点的已配置公共端口、TLS 证书和磁盘空间，并为每个失败打印修复。退出 `0` 是健康，`4` 意味着发现问题，`3` 意味着需要的证据不可用。

**`node remove`** — 从拓扑中删除出口或路由网关。它列出将被删除的每个资源（面板节点和配置文件、节点容器、防火墙规则、已发布的主机），请求确认，并准确应用该审查；服务器留在保存的服务器中。对于控制服务器拒绝（使用 `meridian teardown`），而中继仍路由到出口（先删除它们或传递 `--force`），而路由网关通过它进入舰队，以及当结果会破坏拓扑时（例如空出口池或默认出口）。范围限定为已删除网关的路由规则与其一起删除。

### meridian relay

管理中继 — Realm TCP 转发器，给用户一个到国外出口的国内入口点。中继承载 Reality 流量。

```
meridian relay add [USER@]RELAY_IP[:PORT] --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay check RELAY [--exit EXIT] [--user USER] [--json]
meridian relay remove RELAY [--exit EXIT] [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--exit/-e EXIT` | (add 需要) | 出口服务器 IP 或名称 |
| `--name NAME` | (自动) | 中继的友好名称（如 "ru-moscow"） |
| `--port/-p PORT` | 443 | 中继服务器上的监听端口 |
| `--sni HOST` | 出口的目标 | 此中继的 Reality 伪装目标；省略以使用出口的 |
| `--user/-u USER` | 已保存或 root | 中继服务器上的 SSH 用户 |
| `--ssh-port PORT` | 已保存或 22 | 中继服务器的 SSH 端口（`relay add`） |
| `--yes/-y` | | 跳过确认提示 |
| `--json` | | 将 `relay list`（`data.relays[]`）或 `relay check` 作为 `meridian.output/v1` 信封发出 |

**`relay add`** — 添加单跳中继到拓扑并应用它。客户端在下次订阅刷新时接收它。对于多于一跳的链，使用 `meridian setup`。

**`relay remove`** — `RELAY` 是中继服务器的 IP 或保存的名称，或中继链 ID。删除通过该服务器的每条链（用 `--exit` 缩小范围），列出将删除的内容（Realm 单元、防火墙规则、出口端侦听器、已发布的主机），确认，并应用审查的删除。服务器留在保存的服务器中。

**`relay check`** — 检查中继服务器运行的每个 Realm 跳跃（或一条链的每个跳跃）：通过 SSH，跳跃的服务是否活跃以及它是否可以打开 TCP 到下一个跳跃；从您的机器，链的公共入口是否接受 TCP。退出 `0` 表示每个检查都通过，`4` 表示发现问题，`3` 表示无法检查跳跃。使用 `meridian test` 通过整条路由推动真实流量。

### meridian fleet

来自实时面板的舰队范围内的健康和库存。

```
meridian fleet status [--json]
meridian fleet inventory [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--json` | | 将结果作为 `meridian.output/v1` 信封发出 |

**`fleet status`** — 面板健康、节点心跳、中继监听器和客户端计数。缺失或不活跃的声明客户端降低健康。可达的中继监听器仅证明 TCP，所以未验证的中继路由保持健康 `unknown`；用 `meridian test` 检查完整路径。退出 `0` 意味着健康，`4` 是降级，`3` 是未知。

**`fleet inventory`** — 你的已配置服务器、角色、节点和中继，可达时带实时面板状态。它永不打印面板 API 令牌或秘密路径。如果实时状态不可用，已配置库存仍显示，带警告，命令退出 `3`。使用 `plan --json` 决定应用是否有工作要做。

### meridian preflight

在部署前检查服务器：伪装目标、端口 443 可用性和外部可达性、DNS、OS、磁盘和时钟。没有安装任何东西。DNS 使用服务器自己的解析器；服务器地址不发送给第三方服务。

```
meridian preflight [SERVER] [--domain NAME] [--sni HOST] [--user USER]
                   [--ssh-port PORT] [--ai]
```

`--domain` 检查域名解析到服务器。`--sni` 检查特定伪装目标，`--user` 和 `--ssh-port` 覆盖保存的 SSH 用户和端口，`--ai` 复制 AI 就绪诊断提示到剪贴板。

前检查接受公共 IP 或主机名，端口 443 空闲时打开短暂监听器，永不把缺失证据当作成功对待。退出 `0` 意味着兼容，`4` 意味着发现问题，`3` 意味着需要的证据不可用，`2` 意味着无效输入。

### meridian scan

使用固定 RealiTLScanner 版本在服务器自己的网络上找伪装目标。Meridian 在运行之前验证其 SHA-256 校验和并之后删除远程工作空间。

```
meridian scan [SERVER] [--user USER] [--ssh-port PORT]
```

RealiTLScanner 扫描 IPv4 范围；仅 IPv6 的服务器退出 `3`。选择结果打印使用它的命令 — 它不改变已部署的拓扑。跳过退出 `0`，无效选择退出 `2`，无合适目标退出 `4`，扫描器或下载失败退出 `3`。

### meridian test

从你的设备测试交付的代理流量。不需要 SSH。

完整测试选择活跃客户端，获取 Remnawave 交付给他们的确切 Xray JSON，并通过自动回退和目标服务器的每个路径推送真实流量。第一次运行可能花费多达 120 秒下载和验证固定 Xray 运行时。流量检查轮转独立 IP 观察者并与直接请求比较，所以观察者停机是不确定的而不是假失败。`MERIDIAN_CONNECT_TEST_URL` 为受控环境覆盖观察者。

面板停机、HTTP `429` 或 HTTP `5xx` 使订阅不可用，所以运行退出 `3`。任何其他订阅拒绝如 `400` 或 `422` 是完成的失败并退出 `4`。

```
meridian test [SERVER|DOMAIN] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--timeout SECONDS] [--json]
```

`--client` 测试特定活跃客户端。`--basic` 仅运行网络和 TLS 检查，无下载 Xray，无法验证纯 UDP 路径；不能与 `--client` 组合（退出 `2`）。`--timeout`（默认 `5`，范围 `1`–`30`）限制每个网络操作，与第一运行 Xray 下载分开。

退出 `0` 意味着每个检查通过，`4` 意味着测试发现问题，`3` 意味着需要的证据不可用。`--json` 发出 `meridian.output/v1` 信封与每个检查和发现。

### meridian probe

探测服务器就像审查者一样，检查部署是否泄露自己。不需要 SSH。在任何 IP 或域工作，当目标属于保存拓扑时应用更严格的检查。

检查遵循真实部署：公共和内部端口暴露、HTTP/TLS 行为、SNI 一致性和伪装、常见代理和面板路径、WebSocket 升级、反向 DNS、HTTP/2、遗留 TLS 版本和已配置域名。`--sni` 覆盖通用 TLS 探测的 SNI。纯 UDP 监听器报告为需要完整 `meridian test` 而不是被猜测。

```
meridian probe [SERVER|DOMAIN] [--sni NAME]
               [--timeout SECONDS] [--json]
```

与 `test` 相同退出码：`0` 通过，`4` 发现问题，`3` 不确定。`--timeout` 默认 `5` 秒（范围 `1`–`30`）。跳过的检查永不报告为通过。

### meridian doctor

为错误报告收集系统诊断。别名：`meridian rage`。

```
meridian doctor [SERVER] [--sni HOST] [--user USER] [--ssh-port PORT] [--ai]
```

`--sni` 选择诊断的伪装目标，`--user` 和 `--ssh-port` 覆盖保存的 SSH 用户和端口，`--ai` 复制重新编辑、AI 就绪报告到剪贴板而不是打印问题模板。

医生遵循服务器的角色：纯中继服务器不诊断为出口，路由网关单独标记。缺失工具或失去 SSH 连接使报告不确定（退出 `3`）；已收集的部分仍显示。

### meridian teardown

从服务器删除 Meridian。

```
meridian teardown [SERVER] [--user USER] [--ssh-port PORT] [--yes]
```

在单服务器部署上，拆除删除所有东西：面板、节点、nginx 配置、证书、中继和服务器上的防火墙规则（Docker 和系统包保留），然后是本地 `cluster.yml` 和保存的服务器配置。它也删除 Meridian 3.x 的 3x-ui。在多服务器舰队中，拆除拒绝仍在拓扑中有角色的服务器。

`--yes` 跳过确认；角色检查仍先运行。拒绝退出 `1`。未知的服务器名称或零个或多个可能目标退出 `2` 而不是提示。

### meridian update

更新 CLI 到 PyPI 上其主要版本的最新版本。较新的主要版本仅被宣布；`--major` 在你阅读发行说明后移至该版本。测试版遵循其主要版本的较新测试版，然后是稳定版本。Meridian 尝试 uv、pipx 和 pip，仅在活跃 `meridian` 命令显示新版本时报告成功。退出 `0` 意味着已是最新或已更新；`3` 意味着更新或其验证失败。更新 CLI 不改变你的服务器。

```
meridian update [--major]
```

### meridian studio

打开 Meridian Studio，一个实验浏览器 UI，带其本地引擎 API。服务器仅绑定到 `127.0.0.1`。

```
meridian studio [--port PORT] [--assets-dir DIRECTORY] [--no-open]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--port PORT` | 0 | 从 0 到 65535 的 localhost 端口；`0` 选择空闲高端口 |
| `--assets-dir DIRECTORY` | 捆绑资产 | 使用构建的 Studio 资产目录而不是打包复制 |
| `--no-open` | | 打印本地 URL 无需打开浏览器 |

如果端口被占用或资产缺失，Studio 失败清晰。显式资产目录必须包含 `studio/index.html`。

### meridian api

检查 JSON 输出背后的机器可读契约，用于脚本和 UI 客户端。

```
meridian api schemas [--json] [--include-schemas]
meridian api commands [--json] [--include-schemas]
meridian api schema NAME [--envelope|--json]
meridian api workflow NAME [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--json` | | 将目录作为 `meridian.output/v1` 信封发出 |
| `--include-schemas` | | 为 `api schemas` 或 `api commands` 包含完整 JSON Schemas（意味着 JSON 输出） |
| `--envelope`, `--json` | | 将 `api schema NAME` 包裹在 `meridian.output/v1` 信封中而不是打印原始模式 |

**`api schemas`** — 列出模式名称如 `output-envelope`、`deploy-request`、`deploy-result`、`deploy-plan`、`plan-envelope`、`apply-envelope`、`client-list-envelope`、`fleet-status-envelope` 和 `event`。

**`api commands`** — 列出命令契约：argv、信封和数据模式、可能的状态和结果、退出码含义、机器标志和 `interrupt_behavior`。Ctrl-C 退出 `130` 无 JSON 信封（`interrupt_behavior: "exit_130_without_envelope"`）。

**`api schema NAME`** — 打印一个 JSON Schema，例 `meridian api schema output-envelope`。

**`api workflow NAME`** — 打印一个 UI 可渲染的工作流，例 `meridian api workflow deploy --json`。

## 全局选项

全局选项在命令之前，例 `meridian --quiet doctor`。命令选项如 `test --json` 在其后。

| 标志 | 说明 |
|------|-------------|
| `--version`, `-v` | 显示 CLI 版本并退出 |
| `--verbose` | 启用调试日志 |
| `--quiet`, `-q` | 压制进度输出 |
| `--json` | 从支持它的命令请求 JSON 信封；其他命令明确失败 |
| `--install-completion` | 为当前 shell 安装补全 |
| `--show-completion` | 为当前 shell 打印补全脚本 |

JSON 输出使用 `meridian.output/v1` 信封（`schema`、`command`、`operation_id`、`status`、`summary`、`data`、`warnings`、`errors`）。它由 deploy、plan、apply、test、probe、所有客户端命令、舰队状态/库存、节点/中继列表和 api 命令支持。在 JSON 离开进程前秘密被编辑。运行 `meridian api commands --json` 发现契约。

## 常见服务器选项

这些在命令间重复出现；每个命令仅接受其自己部分中列出的选项。

| 标志 | 被使用 |
|------|---------|
| `SERVER` 参数 | deploy、preflight、scan、test、probe、doctor、teardown、node add、relay add、server add |
| `--user`, `-u USER` | deploy、node add/check、relay add/check、server add、preflight、scan、doctor、teardown |
| `--ssh-port PORT` | deploy、node add、relay add、server add、preflight、scan、doctor、teardown |
| `--sni HOST` | deploy、node add、relay add、preflight、test、probe、doctor |
| `--domain DOMAIN` | deploy、node add、preflight、test |

客户端命令直接使用面板 API 并不取服务器。`test` 和 `probe` 从你的设备运行并不使用 SSH。

## 服务器解析

与 `ssh` 一样，先写服务器：IP、已保存的名称、`user@host:port`，或 `local`（在当前机器上运行，不经 SSH）。`--user` 和 `--ssh-port` 补充目标中未写明的部分，且不能与之冲突。用户或端口留空表示使用已保存的值，没有则为 `root` 和 `22`。未指定服务器时，Meridian 使用本地模式（在已部署的服务器上运行时）或唯一已保存的服务器。没有候选或有多个候选时以退出码 `2` 结束，而不会提示选择。旧的 `--server NAME` 参数仍然可用。
