---
title: CLI 参考
description: Meridian CLI 所有命令和标志的完整参考。
---

## 命令

### meridian deploy

部署一个服务器并获得可分享的连接链接。服务器既是控制平面（Remnawave 面板）又是出口。部署构建与 `meridian setup` 相同的已审核拓扑，用检查点应用它，验证通过交付的订阅的真实流量，并打印连接页面和订阅 URL。重新运行相同命令恢复中断的部署或重新部署，保持路径、密钥和用户。

```
meridian deploy [SERVER] [flags]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--sni HOST` | (自动) | 伪装目标。不指定时，首次部署会先尝试服务器所在子网内的网站，再尝试 `www.google.com`、`dl.google.com`、`www.cloudflare.com`，并使用第一个能承载流量的；重新部署保持保存的目标。显式 `--sni` 永远不会被替换 |
| `--domain DOMAIN` | (无) | 你的域名：XHTTP 迁移到该域名上并新增 WSS，二者都经由 Cloudflare CDN（没有域名时 XHTTP 也能在 IP 上工作）。面板和连接页面保持在 IP 证书上 |
| `--hysteria2/--no-hysteria2` | 启 | 在 UDP/443 上提供 Hysteria2，用 salamander 伪装，作为最后的回退。使用域名的证书或没有域名的 IP 证书 |
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
| `--client-ips / --no-client-ips` | 关 | 把每个客户端的真实 IP 传给 Xray 和面板，供 Remnawave 的按用户 IP 限制和连接日志使用。关闭时，每个 TCP 连接看起来都来自服务器本身 |
| `--country CC` | 地址注册机构 | 显示在连接名称中、并作为面板节点旗帜的两字母国家代码。不指定时，首次部署会向服务器地址所属的区域互联网注册机构查询（通过 IANA 的 RDAP 引导服务，不使用 geo-IP 服务）；重新部署则沿用已保存的国家 |
| `--yes` | | 跳过确认提示 |
| `--json` | | 将最终部署结果作为 `meridian.output/v1` 信封发出 |
| `--events=jsonl` | | 在 stderr 上以 JSONL 形式流式传输类型化进度事件 |
| `--request FILE` | | 从文件读取 `deploy-request` JSON 有效负载；用 `-` 表示 stdin |
| `--dry-run` | | 编译和预览部署无需 SSH 或本地变更 |

不指定 IP，部署会提示输入。`local` 在运行所在的机器上部署。部署仅重新部署已经是舰队中出口的服务器；用 `meridian node add` 添加其他服务器。变更目前是增量的：删除已部署的东西（如去掉 `--domain`）被拒绝。

**机器/UI 流程**：`meridian api workflow deploy --json` 返回可渲染的向导契约。UI 收集这些字段，针对 `deploy-request` 验证它们，然后运行 `meridian deploy --request deploy.json --json --events=jsonl`。机器部署非交互式：请求必须在用户确认后包含 `yes: true`。`--dry-run --json` 返回 `data` 下的 `deploy-plan`，所以 UI 可以在打开 SSH 之前预览模式、端口和生成的路径。

### meridian setup

用可恢复的引导向导为一个或多个服务器配置：出口、Realm 中继、带故障转移池的路由网关和用户。它是为从未设置过 VPN 的人编写的：每个问题都使用真实术语（出口、中继、SNI）并用一个简单句子解释它，并附带一个可以通过按 Enter 接受的安全默认值。

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--intent FILE` | (无) | 从完整的拓扑 JSON 文档开始审核；用 `-` 表示 stdin |
| `--restart` | | 启动前放弃保存的设置进度 |
| `--yes`, `-y` | | 批准审核并应用无需询问 |

第一个问题是语言（English, Русский, فارسی, 中文），从你的终端预选并记住在 `~/.config/meridian/preferences.json`；`--lang` 或 `MERIDIAN_LANG` 跳过它。然后向导走过服务器（一个 IP、`user@IP:port` 或 `local` 用于此机器）、每个服务器做什么、连接方法、路由（仅当你有路由网关时才被提问）、用户、平实语言摘要和应用。在任何提示处你可以输入 `back`、`details`、`save` 或 `quit`（或你的语言中的相同单词）。在终端中不带命令运行 `meridian`，在任何 VPN 存在之前，提供启动向导。进度在每个阶段后保存在 `~/.config/meridian/setup.json` 中，所以再次运行 `meridian setup` 继续；密码和私钥永不存储在那里。`--intent` 不会替换现有草稿除非也给出 `--restart`。当应用成功时，设置验证每个节点和订阅并打印每个用户的连接页面。

### meridian plan

显示 `meridian apply` 会变更什么，比较 `~/.config/meridian/cluster.yml` 中的拓扑与实时服务器和面板。没有变更任何东西。

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

JSON `data` 包含 `plan_hash`、`generation` 和 `actions[]` 带 `status: "converged" | "applied" | "failed" | "unknown" | "skipped"`。JSON 模式永不提示：没有 `--yes` 它返回 `MERIDIAN_CONFIRMATION_REQUIRED` 带计划哈希并退出 `2`。失败的资源退出 `3`；修复原因并再次运行应用。如果远程变更后本地状态无法保存，应用退出 `3` 并说明；修复 `~/.config/meridian` 权限，然后再次运行 `plan` 和 `apply`。

删除同样会被应用：离开拓扑的所有内容（用户、协议路径、出口、中继、路由网关）在计划中剩下的所有内容收敛后被删除，`plan` 首先列出它们。控制服务器只能通过 `meridian teardown` 删除。

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

**`client enable` / `client disable`** — 立即恢复或暂停客户端。禁用持续到你运行 `client enable`；应用（包括重新部署和 `client add`、`node add`、`relay add`）保持客户端的状态不变。

**`client remove`** — 从拓扑中删除客户端，删除面板用户和其连接页面。除非 `--yes`，否则要求确认。最后一个客户端无法删除。不要直接在面板中删除用户；Meridian 在下次应用时重新创建拓扑中保留的任何客户端。

### meridian server

管理保存的 SSH 服务器（`~/.config/meridian/servers.json`）。

```
meridian server add [USER@]IP[:PORT]|local [--name NAME] [--user USER] [--ssh-port PORT]
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
meridian node remove NAME_OR_IP [--yes] [--force]
meridian node replace EXIT [USER@]NEW_IP[:PORT] [--name NAME] [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--name NAME` | (自动) | 节点的友好名称 |
| `--user USER` | 已保存或 root | 节点上的 SSH 用户 |
| `--ssh-port PORT` | 已保存或 22 | 节点的 SSH 端口（`node add`、`node replace`） |
| `--sni HOST` | www.google.com | 此节点的 Reality 伪装目标 |
| `--domain DOMAIN` | (无) | 节点域名：XHTTP 迁移到该域名上并新增 WSS，二者都经由 Cloudflare CDN；Hysteria2 使用其证书 |
| `--country CC` | 地址注册机构 | 显示在连接名称中的国家；注册机构信息有误时（任播或租用地址段）请手动指定 |
| `--harden / --no-harden` | 启 | OS、SSH 和防火墙加固。舰队服务器总是保持它；`--no-harden` 被拒绝 |
| `--yes` | | 跳过确认提示 |
| `--force` | | `node remove`：也删除路由到此出口的中继链 |
| `--json` | | 将 `node list` 作为 `meridian.output/v1` 信封发出 |

**`node add`** — 通过 SSH 连接，添加服务器作为拓扑中的出口，并应用已审核的计划（加固、Docker、Xray、面板注册）。现有客户端在下次订阅刷新时接收新出口。

**`node list`** — 列出节点带实时面板状态、Xray 版本和流量。如果面板状态不可用，已配置的节点显示为 `unknown`，命令退出 `3`。

**`node remove`** — 从拓扑中删除出口或路由网关。它列出将被删除的每个资源（面板节点和配置文件、节点容器、防火墙规则、已发布的主机），请求确认，并准确应用该审查；服务器留在保存的服务器中。对于控制服务器拒绝（使用 `meridian teardown`），而中继仍路由到出口（先删除它们或传递 `--force`），而路由网关通过它进入舰队，以及当结果会破坏拓扑时（例如空出口池或默认出口）。范围限定为已删除网关的路由规则与其一起删除。

**`node replace`** — 将一个出口移到另一个服务器。`EXIT` 是出口 ID 或其服务器的保存名称或 IP；新服务器使用与 `node add` 相同的目标语法（保存的名称或 `local` 也可用）并像 `server add` 一样被保存。出口保留其 ID、Reality 密钥、协议路径、SNI、WARP 和地理设置以及指向它的每个中继，所以中继用户保持工作，直接用户在下一次订阅刷新时获得新地址。审查列出将部署在新服务器上的内容以及在旧服务器上清理的每个资源（节点容器、镜像和目录；其 nginx 文件、防火墙规则和证书）；Docker 和保存的服务器保留。对于控制服务器拒绝（移动面板会改变每个订阅 URL），以及对于已经有角色的新服务器拒绝。

### meridian relay

管理中继 — Realm TCP 转发器，给用户一个到国外出口的国内入口点。中继承载 Reality 流量。

```
meridian relay add [USER@]RELAY_IP[:PORT] --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
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
| `--json` | | 将 `relay list`（`data.relays[]`）作为 `meridian.output/v1` 信封发出 |

**`relay add`** — 添加单跳中继到拓扑并应用它。客户端在下次订阅刷新时接收它。对于多于一跳的链，使用 `meridian setup`。

**`relay remove`** — `RELAY` 是中继服务器的 IP 或保存的名称，或中继链 ID。删除通过该服务器的每条链（用 `--exit` 缩小范围），列出将删除的内容（Realm 单元、防火墙规则、出口端侦听器、已发布的主机），确认，并应用审查的删除。服务器留在保存的服务器中。

要检查中继的跳跃，运行 `meridian check RELAY`。

### meridian route

从出口将选定的目标通过 Cloudflare WARP、直接或不通过任何地方发送，领先于出口的默认出站。

```
meridian route add EXIT VALUE... --via warp|direct|block [--name ID] [--json]
meridian route list [EXIT] [--json]
meridian route remove EXIT ROUTE [--yes] [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--via ACTION` | （add 需要） | `warp`、`direct` 或 `block` |
| `--name ID` | 操作，编号 | 路由 ID 显示在 `route list` 并由 `route remove` 使用 |
| `--yes/-y` | | 跳过删除确认（需要用 `--json`） |
| `--json` | | 发出 `meridian.output/v1` 信封（`data.override` 用于 add/remove，`data.exits[]` 用于 list） |

`EXIT` 是出口 ID、保存的服务器名称或 IP。每个 `VALUE` 是一个域名（包括子域名）、`geosite:NAME` 或 `geoip:NAME`；一个路由持有一种。

**`route add`** — 将路由添加到拓扑中的出口并应用它。路由按添加它们的顺序匹配。没有 WARP 的出口上的 `warp` 路由仅为该路由设置 WARP；其他流量保持直接。

**`route list`** — 显示每个出口的默认出站及其路由的匹配顺序。

**`route remove`** — 删除一个路由并应用更改；其目标遵循出口的默认出站。WARP 保持安装在服务器上但不承载任何东西，直到路由或 `--warp` 再次使用它。

### meridian fleet

来自实时面板的舰队范围内的健康和库存，以及 Remnawave 升级。

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet upgrade [--tested] [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--json` | | 将结果作为 `meridian.output/v1` 信封发出（`status`、`inventory`） |
| `--tested` | | `fleet upgrade`：仅使用此 Meridian 版本测试过的 Remnawave 版本，不查询 Docker Hub |
| `--yes`, `-y` | | `fleet upgrade`：跳过确认 |

**`fleet status`** — 面板健康、节点心跳、中继监听器和客户端计数。缺失声明的客户端降低健康；禁用、限制或过期的客户端被计数但是您的选择，不是错误。可达的中继监听器仅证明 TCP，所以未验证的中继路由保持健康 `unknown`；用 `meridian test` 检查完整路径。退出 `0` 意味着健康，`4` 是降级，`3` 是未知。

**`fleet inventory`** — 你的已配置服务器、角色、节点和中继，可达时带实时面板状态。它永不打印面板 API 令牌或秘密路径。如果实时状态不可用，已配置库存仍显示，带警告，命令退出 `3`。使用 `plan --json` 决定应用是否有工作要做。

**`fleet upgrade`** — 无需等待 Meridian 更新，就把 Remnawave 升级到最新版本。它向 Docker Hub 查询此 Meridian 版本所支持主版本内最新的后端、节点和订阅页面版本（跳过预发布版和其他主版本），按多架构摘要固定每一个，并把选择保存到 `cluster.yml`，让之后的应用保持这一选择；之后 Meridian 的版本若带有更新的固定版本，则以它为准。`--tested` 只使用此版本测试过的版本。PostgreSQL 和 Valkey 始终使用 Meridian 的固定版本。随后它读取每个服务器上运行的后端、订阅页面、PostgreSQL、Valkey 和节点镜像（通过 SSH 只读执行 `docker inspect`），并把每个显示为 `current`、`upgrade`、`install` 或保留。版本政策是设置使用的：更新的版本标签或您选择的浮动标签（`latest`、`3`）被保留并报告，永远不降级。PostgreSQL 主版本永远不在原地移动 — 旧主版本保持运行，命令解释需要的 dump-and-restore。在您确认后，它应用已审查的计划并在每个服务器上运行 `meridian check`。一个其镜像无法读取的服务器在任何改变前用退出 `3` 停止命令；如果应用收敛但服务器仍运行旧镜像，它也退出 `3`。

### meridian check

检查服务器的健康，或新服务器是否能运行 Meridian。运行的内容取决于服务器在舰队中的角色：

- **还未在舰队中** — 部署前检查：伪装目标可达性和 DNS、端口 443 可用性和外部可达性、域名 DNS、OS、磁盘和时钟。没有安装任何东西；当端口 443 空闲时打开短暂监听器来测试防火墙。DNS 使用服务器自己的解析器。
- **控制、出口或路由网关** — 面板连接、SSH（或本地访问）、容器（包括订阅页面的健康）、每个已编译的公共和内部端口、TLS 证书和磁盘空间。从另一台机器运行，控制服务器还会获得数据流检查：一个约 50 KB 的下载，当它在大约 16-24 KB 后冻结时发出警告（节流，从您的位置测量）。
- **中继** — 服务器运行的每个 Realm 跳跃：服务活跃并可到达下一跳，链的公共入口从你的机器接受 TCP。

不指定服务器时，舰队中的每个服务器都被检查。

```
meridian check [SERVER] [--domain NAME] [--sni HOST] [--user USER]
               [--ssh-port PORT] [--ai] [--json]
```

`--domain` 和 `--sni` 适用于尚未部署的服务器：检查域名解析到服务器以及特定伪装目标可达。`--user` 和 `--ssh-port` 覆盖保存的 SSH 用户和端口。`--ai` 复制 AI 就绪诊断提示到剪贴板。`--json` 发出单个 `meridian.output/v1` 信封，每个服务器和范围都有一份报告。

缺失证据永不视为成功。退出 `0` 表示每个检查都通过，`4` 表示发现问题，`3` 表示需要的证据不可用，`2` 表示无效输入。每个失败都打印其修复。使用 `meridian test` 通过整条路由推动真实流量。

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
              [--client NAME] [--basic] [--probe] [--timeout SECONDS] [--json]
```

未指定目标时，`test` 会测试其所在的已保存服务器，或你唯一已保存的服务器。有多台时，它会依次测试你的舰队的每个公共入口（每个出口，然后是每个中继的入口），并以最差的结果退出；此时 `--json` 会在 `data.results` 中为每台服务器返回一个结果。

`--client` 测试特定活跃客户端。`--basic` 仅运行网络和 TLS 检查，无下载 Xray，无法验证纯 UDP 路径；不能与 `--client` 组合（退出 `2`）。`--timeout`（默认 `5`，范围 `1`–`30`）限制每个网络操作，与第一运行 Xray 下载分开。

退出 `0` 意味着每个检查通过，`4` 意味着测试发现问题，`3` 意味着需要的证据不可用。`--json` 发出 `meridian.output/v1` 信封与每个检查和发现。

每条路径报告其首字节的时间。当路径失败时，Meridian 会打开与其端点的普通 TCP 连接，以说明它是否**连接，但没有流量通过**（服务器应答但代理会话不传输任何内容：凭证错误或会话被切断）或**无法访问**（该端口没有任何应答）。使用 `--json` 时，`data.transports` 列出每条路径的结果（`ok`、`no_traffic`、`unreachable`、`failed`、`egress_mismatch`、`inconclusive`、`invalid`）和 `first_byte_ms`。

#### 审查者的视角：`meridian test --probe`

`--probe` 像活跃探测者一样查看服务器，检查部署是否泄露自己，而不是发送流量。在任何 IP 或域工作，当目标属于保存拓扑时应用更严格的检查。

检查遵循真实部署：公共和内部端口暴露、HTTP/TLS 行为、SNI 一致性和伪装、常见代理和面板路径、WebSocket 升级、反向 DNS、HTTP/2、遗留 TLS 版本和已配置域名。`--sni` 覆盖通用 TLS 探测的 SNI。纯 UDP 监听器报告为需要完整 `meridian test` 而不是被猜测。

`--probe` 还回答「什么被阻止了？」。它使用其伪装 SNI 打开到服务器的 TLS；如果失败，它将重试而不使用 SNI 并直接打开真实的伪装站点，然后报告**服务器 IP** 或**伪装 SNI** 是否看起来被阻止。如果也无法连接到真实站点，结果是不确定的而不是一个发现。在控制服务器上，它还下载连接页面的脚本（约 50 KB）并监视传输在约 16-24 KB 后冻结的情况，这是受限外部 IP 的行为方式。当 WSS 路径通过 CDN 时，相同的下载通过 CDN 运行。这些结果仅在从审查网络内部运行时才有意义。`--probe` 不能与 `--basic`、`--client` 或 `--domain` 结合使用。退出码与 `test` 相同；跳过的检查从不报告为通过。

`meridian probe [SERVER|DOMAIN] [--sni NAME] [--timeout SECONDS] [--json]` 等同于 `meridian test IP --probe`。

### meridian doctor

为错误报告收集系统诊断。

```
meridian doctor [SERVER] [--sni HOST] [--user USER] [--ssh-port PORT] [--ai]
```

`--sni` 选择诊断的伪装目标，`--user` 和 `--ssh-port` 覆盖保存的 SSH 用户和端口，`--ai` 复制重新编辑、AI 就绪报告到剪贴板而不是打印问题模板。

医生遵循服务器的角色：纯中继服务器不诊断为出口，路由网关单独标记。缺失工具或失去 SSH 连接使报告不确定（退出 `3`）；已收集的部分仍显示。

### meridian logs

无需登录服务器，即可显示其最近的日志（已脱敏）。

```
meridian logs [SERVER] [--lines N] [--grep TEXT] [--component NAME]
              [--user USER] [--ssh-port PORT]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--lines`, `-n N` | `100` | 每个日志显示的行数（`1`–`10000`） |
| `--grep`, `-g TEXT` | | 仅显示包含此文本的行；不区分大小写，按字面匹配而非正则表达式 |
| `--component`, `-c NAME` | 全部 | 仅 `node`、`panel`、`nginx` 或 `relay`；重复使用可选择多个 |

`logs` 读取服务器各角色所运行的内容：出口和路由网关显示 Remnawave 节点容器，控制服务器显示面板容器（后端、数据库、Valkey、订阅页面），二者都显示 nginx 错误日志，中继则显示其 Realm 单元的日志。`--user` 和 `--ssh-port` 覆盖保存的 SSH 用户和端口。

每一行都经过与 `meridian doctor` 相同的脱敏处理：令牌、密码、密钥、UUID、订阅 ID，以及面板和连接页面的秘密路径都会被替换为 `[REDACTED]`，因此输出可以安全地粘贴到错误报告中。各行以纯文本打印，并已去除终端控制字符。

没有舰队角色的服务器，或其未运行的 `--component`，会在连接之前以 `2` 退出。无法读取的日志（容器缺失、SSH 连接丢失）会显示为不可用，命令以 `3` 退出。

### meridian panel

显示 Remnawave 管理面板 URL 和管理员用户名，以及 Meridian 管理的内容与您可以改变的内容。

```
meridian panel [--show-password]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--show-password` | | 也打印管理员密码 |

对客户端状态、流量限制、过期和描述的编辑，以及您在面板中创建的用户，在每次应用中幸存。对节点、配置文件、主机、小队和 Meridian 创建的模板的编辑由下一次应用恢复；`meridian plan` 首先显示它们。

### meridian teardown

从服务器删除 Meridian。

```
meridian teardown [SERVER] [--user USER] [--ssh-port PORT] [--yes]
```

在单服务器部署上，拆除会删除 Meridian 创建的所有内容：服务器上的面板、节点、nginx 配置、证书、中继和防火墙规则，然后是本地 `cluster.yml` 和保存的服务器配置。Docker、系统包、SSH 加固和 fail2ban 会保留；如果 nginx 仍在为你自己的站点提供服务，它也会继续运行。它也删除 Meridian 3.x 的 3x-ui。在多服务器舰队中，拆除拒绝仍在拓扑中有角色的服务器。

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
| `--port PORT` | 7391 | 从 0 到 65535 的本地端口；`0` 表示任选一个空闲端口 |
| `--assets-dir DIRECTORY` | 捆绑资产 | 使用构建的 Studio 资产目录而不是打包复制 |
| `--no-open` | | 打印本地 URL 无需打开浏览器 |

Studio 会打印并打开形如 `http://127.0.0.1:7391/studio/#k=…` 的地址。`#k=` 后面的部分是密钥，每次启动都会变化；只有从该地址打开的浏览器标签页才能使用 Engine，因此同一台电脑上的其他程序和其他用户都无法使用。请妥善保密。重启 Studio 后，请打开它打印的新地址。

不带 `--port` 时，Studio 使用端口 7391；如果被占用，则依次尝试后面的空闲端口，最多到 7400，这样浏览器为 Studio 保存的草稿下次仍然在。如果显式指定的 `--port` 已被占用，Studio 会给出清晰的错误并退出，缺少资产时也一样。显式指定的资产目录必须包含 `studio/index.html`。

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

**`api commands`** — 列出命令契约：argv、信封和数据模式、可能的状态和结果、退出码含义、命令接受的每个选项（`machine_flags`）以及 `interrupt_behavior`。参数、选项和说明都读取自 CLI 本身。Ctrl-C 退出 `130` 无 JSON 信封（`interrupt_behavior: "exit_130_without_envelope"`）。

**`api schema NAME`** — 打印一个 JSON Schema，例 `meridian api schema output-envelope`。

**`api workflow NAME`** — 打印一个 UI 可渲染的工作流，例 `meridian api workflow deploy --json`。

### meridian help

向 AI 智能体介绍 Meridian，或显示某条命令的帮助。

```
meridian help [agents | COMMAND...]
```

**`meridian help agents`** 打印一份面向 AI 智能体的 Markdown 指南：Meridian 的用途、集群与其已审核拓扑如何配合、状态存放在哪里、安全驱动 CLI 的规则（非交互标志、JSON 信封、先计划后应用、删除前确认、把链接当作机密）、退出码以及常见任务。它始终输出纯 Markdown，因此智能体可以从管道中读取。

**`meridian help COMMAND`** 显示与 `meridian COMMAND --help` 相同的帮助，例如 `meridian help node add`。不带参数时打印顶层帮助。未知主题以退出码 `2` 退出。

## 全局选项

全局选项在命令之前，例 `meridian --quiet doctor`。命令选项如 `test --json` 在其后。

| 标志 | 说明 |
|------|-------------|
| `--version`, `-v` | 显示 CLI 版本并退出 |
| `--verbose` | 启用调试日志 |
| `--quiet`, `-q` | 压制进度输出 |
| `--lang CODE` | 设置向导的语言：`en`、`ru`、`fa` 或 `zh`（默认：已保存的选择，否则询问） |
| `--json` | 从支持它的命令请求 JSON 信封；其他命令明确失败 |
| `--install-completion` | 为当前 shell 安装补全 |
| `--show-completion` | 为当前 shell 打印补全脚本 |

JSON 输出使用 `meridian.output/v1` 信封（`schema`、`command`、`operation_id`、`status`、`summary`、`data`、`warnings`、`errors`）。每个列出 `--json` 的命令都支持它：deploy、plan、apply、check、test、probe、所有客户端和 route 命令、舰队状态/库存、节点/中继列表和 api 命令。全局 `--json` 与命令自己的 `--json` 可互换使用。在 JSON 离开进程前秘密被编辑。运行 `meridian api commands --json` 发现契约。

## 常见服务器选项

这些在命令间重复出现；每个命令仅接受其自己部分中列出的选项。

| 标志 | 被使用 |
|------|---------|
| `SERVER` 参数 | deploy、check、scan、test、doctor、logs、teardown、node add、relay add、server add |
| `--user`, `-u USER` | deploy、check、node add、relay add、server add、scan、doctor、logs、teardown |
| `--ssh-port PORT` | deploy、check、node add、relay add、server add、scan、doctor、logs、teardown |
| `--sni HOST` | deploy、check、node add、relay add、test、doctor |
| `--domain DOMAIN` | deploy、check、node add、test |

客户端命令直接使用面板 API 并不取服务器。`test` 从你的设备运行并不使用 SSH。

## 服务器解析

与 `ssh` 一样，先写服务器：IP、已保存的名称、`user@host:port`，或 `local`（在当前机器上运行，不经 SSH）。`--user` 和 `--ssh-port` 补充目标中未写明的部分，且不能与之冲突。用户或端口留空表示使用已保存的值，没有则为 `root` 和 `22`。未指定服务器时，Meridian 使用本地模式（在已部署的服务器上运行时）或唯一已保存的服务器。没有候选或有多个候选时以退出码 `2` 结束，而不会提示选择。
