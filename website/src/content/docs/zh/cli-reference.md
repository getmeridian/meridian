---
title: CLI 参考
description: Meridian CLI 所有命令和标志的完整参考。
---

## 命令

### meridian deploy

部署一台服务器并获得可分享的连接链接。服务器既是控制服务器（运行 Meridian 的控制服务）又是出口。部署会构建与 `meridian setup` 相同的已审核拓扑，带检查点应用它，通过交付的订阅验证真实流量，并输出连接页面和订阅 URL。重新运行相同命令可继续中断的部署或重新部署，并保留路径、密钥和用户。集群的首次部署结束时会显示一次恢复密钥（见 [meridian backup](#meridian-backup)）。

```
meridian deploy [SERVER] [flags]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--sni HOST` | (自动) | 伪装目标。不指定时，首次部署会先尝试服务器所在子网内的网站，再尝试 `www.google.com`、`dl.google.com`、`www.cloudflare.com`，并使用第一个能承载流量的；重新部署保持保存的目标。显式 `--sni` 永远不会被替换 |
| `--domain DOMAIN` | (无) | 你的域名：XHTTP 迁移到该域名上并新增 WSS，二者都经由 Cloudflare CDN（没有域名时 XHTTP 也能在 IP 上工作）。控制服务和连接页面保持在 IP 证书上 |
| `--self-steal` | 关 | 用你自己的域名（`--sni`，否则 `--domain`）伪装，该域名通过仅 DNS 的记录指向服务器：Meridian 为它签发 Let's Encrypt 证书，服务器自己用 nginx 自带的 404 页面回应探测。部署会先检查域名解析到哪里：无法解析时停止，指向别处时警告。重新部署会保留该设置；不带此标志的新 `--sni` 会恢复借用的网站 |
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
| `--country CC` | 地址注册机构 | 显示在连接名称中和控制台服务器列表中的两字母国家代码。不指定时，首次部署会向服务器地址所属的区域互联网注册机构查询（通过 IANA 的 RDAP 引导服务，不使用 geo-IP 服务）；重新部署则沿用已保存的国家 |
| `--yes` | | 跳过确认提示 |
| `--json` | | 将最终部署结果作为 `meridian.output/v1` 信封发出 |
| `--events=jsonl` | | 在 stderr 上以 JSONL 形式流式传输类型化进度事件 |
| `--request FILE` | | 从文件读取 `deploy-request` JSON 有效负载；用 `-` 表示 stdin |
| `--dry-run` | | 编译和预览部署无需 SSH 或本地变更 |

不指定 IP 时，`deploy` 会提示输入。`local` 在当前运行的机器上直接部署。`deploy` 仅重新部署已经是集群中出口的服务器；其他服务器请用 `meridian node add` 添加。如果集群仍在运行 Remnawave 面板，`deploy` 会先提议运行 [`meridian migrate`](#meridian-migrate)。使用不同标志重新运行会更新该出口以匹配新设置（例如添加或移除 `--domain` 或 `--no-hysteria2`），同时保留其他出口、中继、网关和客户端。

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
| `--devices N\|default` | 集群默认值 | 每人上限，1–100（`client add`、`client set`）；`default` 跟随集群上限 |
| `--remove ID` | | 清除一条登记（`client devices`）；设备可在下次刷新时重新登记 |
| `--clear` | | 清除全部登记（`client devices`）；不能与 `--remove` 同时使用 |
| `--yes`, `-y` | | 批准审核并应用无需询问 |

第一个问题是语言（English, Русский, فارسی, 中文），从你的终端预选并记住在 `~/.config/meridian/preferences.json`；`--lang` 或 `MERIDIAN_LANG` 跳过它。然后向导走过服务器（一个 IP、`user@IP:port` 或 `local` 用于此机器）、每个服务器做什么、连接方法、路由（仅当你有路由网关时才被提问：每条规则把匹配的流量发往出口或出口池、直接从网关发出，或不发送）、用户、平实语言摘要和应用。选项以列表形式呈现：用方向键移动（或按选项的编号），用空格键切换复选项，按 `?` 查看详情，按 Enter 确认，按 Esc 返回，按 Ctrl+C 退出。凡是列表提供“其他”的地方，你都可以输入自己的值。在文本字段中，你还可以输入 `back`、`details`、`save` 或 `quit`（或你的语言中的相同单词）。对于伪装网站，向导会先扫描出口所在的网络（约一分钟），并把这些网站排在知名网站之前。设置 `MERIDIAN_PLAIN_PROMPTS=1` 可使用编号提示（当终端无法绘制列表时会自动使用）。在终端中不带命令运行 `meridian`，在任何 VPN 存在之前，提供启动向导。进度在每个阶段后保存在 `~/.config/meridian/setup.json` 中，所以再次运行 `meridian setup` 继续；密码和私钥永不存储在那里。`--intent` 不会替换现有草稿除非也给出 `--restart`。当应用成功时，设置验证每个节点和订阅并打印每个用户的连接页面。

### meridian plan

显示 `meridian apply` 会变更什么，比较 `~/.config/meridian/cluster.yml` 中的拓扑与实时服务器和控制服务。没有变更任何东西。

```
meridian plan [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--json` | | 将计划作为 `meridian.output/v1` 信封为 CI 和 UI 客户端发出 |

输出显示计划哈希、按类型的资源计数和任何需要修复或无法观察的资源。JSON `data` 包含 `plan_hash`、`converged`、`resources[]`、`drifted_resources[]`、`observation_errors[]` 和 `state_changes[]`。

**退出码**：
- `0` — 已收敛，无需更改
- `1` — 无可规划内容（尚未部署集群，或本地状态不可用）
- `2` — 有待应用的变更（请运行 `meridian apply`）
- `3` — 所需证据（控制服务、SSH 或本地状态）不可用

不要手工编辑 `cluster.yml` 中的拓扑。用 `meridian setup`、`deploy`、`client add`、`node add` 或 `relay add` 变更它。

### meridian apply

应用 `cluster.yml` 中已审核的拓扑到你的服务器。应用显示计划哈希和资源计数，要求确认，然后应用它显示的确切计划，带检查点。中断的应用从停止的地方恢复。

```
meridian apply [--yes] [--forget-unreachable] [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--yes`, `-y` | | 跳过确认 |
| `--forget-unreachable` | | 被移除的服务器无法通过 SSH 连接（被封锁或已删除）时，忘记其上部署的内容，而不是让清理失败；在你删除它之前，它会继续为原有用户提供服务 |
| `--json` | | 将最终应用结果作为 `meridian.output/v1` 信封发出 |

JSON `data` 包含 `plan_hash`、`generation` 和 `actions[]` 带 `status: "converged" | "applied" | "failed" | "unknown" | "skipped"`。JSON 模式永不提示：没有 `--yes` 它返回 `MERIDIAN_CONFIRMATION_REQUIRED` 带计划哈希并退出 `2`。失败的资源退出 `3`；修复原因并再次运行应用。如果远程变更后本地状态无法保存，应用退出 `3` 并说明；修复 `~/.config/meridian` 权限，然后再次运行 `plan` 和 `apply`。

删除同样会被应用：离开拓扑的所有内容（用户、协议路径、出口、中继、路由网关）在计划中剩下的所有内容收敛后被删除，`plan` 首先列出它们。控制服务器只能通过 `meridian teardown` 删除，或通过 `meridian restore` 迁移。

每次应用都会在控制服务中记录为一个修订（见 `meridian fleet history`），并在运行期间在那里持有租约，因此两台机器永远不会同时应用。

### meridian client

管理连接的人。

```
meridian client add NAME [NAME...] [--limit SIZE] [--reset PERIOD] [--expires DATE] [--devices N|default] [--json]
meridian client show NAME [--repair-page] [--json]
meridian client usage NAME [--days N] [--json]
meridian client list [--json]
meridian client set NAME [--limit SIZE] [--reset PERIOD] [--expires DATE] [--devices N|default] [--json]
meridian client reset-traffic NAME [--json]
meridian client extend NAME --days N [--json]
meridian client devices NAME [--remove ID | --clear] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
meridian client reissue NAME [--yes] [--json]
meridian client remove NAME [--yes] [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--days N` | 30 | `client usage` 或 `fleet usage` 的 UTC 天数，1–730，受已保存保留时间限制 |
| `--json` | | 将结果作为 `meridian.output/v1` 信封发出（所有客户端命令） |
| `--repair-page` | | 重新发布客户端的连接页面（`client show`） |
| `--limit SIZE` | 无 | 流量限额（`client add`、`client set`）：`500MB`、`50GB`、`1.5TB`；`none` 或 `0` 取消限额。单位按二进制计算（1 GB = 1024³ 字节），与控制服务一致 |
| `--reset PERIOD` | never | 已用流量何时从零重新计算：`day`、`week`、`month` 或 `never`，按自然日、周、月 |
| `--expires DATE` | never | 访问何时结束：`YYYY-MM-DD`（当天结束时，UTC）、ISO 时间如 `2026-12-31T18:00:00+03:00`（不带时区偏移的时间按 UTC 计），或 `never`。必须是将来的时间 |
| `--yes`, `-y` | | 确认 `client reissue` 或 `client remove`；`--json` 需要 |

**`client add`** — 把一个或多个人添加到拓扑并应用，然后为每个人打印连接页面和订阅 URL。页面根据他们真实的订阅生成，因此中继和 Hysteria2 存在时都会显示。拓扑中已有或控制服务中已存在的名称会被拒绝。名称可以使用任何文字（`client add Биба 王小明`）：页面和输出按输入原样显示名称，而控制服务只接受拉丁字母、数字、`.`、`-` 和 `_`，会得到音译后的用户名（`biba`、`wangxiaoming`）。其他 client 命令接受任一形式。如果无法生成客户端的订阅链接，客户端仍会被添加，命令以退出码 `3` 结束；仅缺少连接页面时只给出警告（用 `client show --repair-page` 修复）。`--limit`、`--reset`、`--expires` 和 `--devices` 都包含在审阅后的计划中，并与每个账户在同一事务中创建。应用失败时不会打印链接；重新运行 `meridian apply` 即可继续。之后的应用会保留管理者所做的修改。

**`client show`** — 打印连接页面、订阅 URL、状态、相对限额的流量及其重置周期、到期时间和最后在线时间。`--repair-page` 重新发布缺失的页面；如果失败，链接仍显示带警告和退出 `3`。

首次记录的流量时间和最近报告流量的节点来自已接受的计数增长（JSON 中的 `first_traffic_at` 和 `last_traffic_node`）。升级前的首次使用时间可能未知；这些仅是汇总数据，不包含会话或客户端地址。

**`client list`** — 列出客户端及其状态、流量、到期时间和最后在线时间，包括在网页控制台中添加的用户（JSON 中为 `declared: false`）。带 `--json`，返回 `data.summary` 计数和 `data.clients[]`。

**`client set`** — 修改客户端的流量限额、重置周期或到期时间；只修改你传入的选项，至少需要一个。因限额或到期被停用的客户端（状态 `limited` 或 `expired`），只要新的值允许，就能重新连接。修改立即生效，应用永远不会改动它们。无效的值会在 Meridian 连接任何地方之前以 `2` 退出。带 `--json` 时，在 `data.client` 中返回控制服务当前的客户端信息，包括 `traffic_limit_bytes`、`traffic_reset` 和 `expires_at`（为空表示永不到期）。

**`client reset-traffic`** — 让客户端的已用流量从零开始计算，与进入新周期相同；因限额被停用的客户端可以重新连接。

**`client extend`** — 从当前时间和原到期时间中较晚者起增加 1–3650 个完整日；未设到期时间则从现在起算。必须提供 `--days N`。计算和限制检查在同一个服务事务中完成，因此并发的延长操作都会累加。已到期用户可以恢复访问，但管理者的禁用设置和流量限额仍然有效。JSON 在 `data.client` 中返回实际状态和到期时间。

**`client enable` / `client disable`** — 立即恢复或暂停客户端。禁用持续到你运行 `client enable`；应用（包括重新部署和 `client add`、`node add`、`relay add`）保持客户端的状态不变。

**`client reissue`** — 在链接泄露后替换客户端的链接。控制服务生成新的订阅 ID 和新的密钥，因此旧的订阅 URL、连接页面和链接全部失效。Meridian 先删除旧页面，再发布新页面并打印新链接。状态、限额和到期时间保持不变。除非 `--yes`，否则要求确认；不属于你的客户端的名称会被拒绝。如果新页面无法发布，仍会显示链接并给出警告，退出码为 `3`（用 `client show --repair-page` 修复）。

**`client remove`** — 从拓扑中删除客户端，删除其在控制服务中的账户和连接页面。除非 `--yes`，否则要求确认。最后一个客户端无法删除。在网页控制台中添加的用户请在控制台中删除。

**`client usage`** — 显示控制服务中任何人员（含控制台添加者）的每日上传、下载及总字节数。须在[隐私](/zh/privacy/)中开启每日历史；否则退出码为 `2`，并提示启用命令。历史开启时，`client show` 附最近七个 UTC 天（JSON 的 `data.usage`，关闭时为 `null`）。空白日期表示无记录的流量，不会补算过去用量。

**`client devices`** — private 显示数量，full 显示设备详情；off 不读取设备。`--json` 返回 `username`、`level`、`count`、有效 `limit`、`devices` 和 `removed`。`--remove ID` 或 `--clear` 清除登记而不撤销凭证；重复操作报告零条删除。也可通过 CLI 查看和清除控制台添加的用户的设备登记。`client set --devices N` 设置上限，`--devices default` 使用集群上限。参见[设备限制](/zh/client-management/#限制设备数量)。重新签发链接会清除全部设备登记并保留上限。

修改服务中用户设置的命令（`client enable`、`disable`、`set`、`reset-traffic` 以及删除设备注册）也适用于控制台添加的用户。精确匹配的服务用户名优先；忽略大小写或按显示名称匹配时必须只对应一个用户。若有歧义，请使用 `client list` 中显示的精确用户名。删除用户、重新签发凭据和发布连接页面仍仅适用于拓扑中声明的用户。

### meridian privacy

选择控制服务保存哪些信息。设备限制和每日历史默认均关闭；应用服务器组配置不会改变这些设置。[隐私](/zh/privacy/)说明每个级别保存什么，以及服务器被夺取后的后果。

```bash
meridian privacy [--json]
meridian privacy show [--json]
meridian privacy set [--devices off|private|full] [--device-limit N] [--history off|daily] [--history-days N] [--yes] [--json]
```

| 选项 | 默认 | 描述 |
| --- | --- | --- |
| `--devices LEVEL` | 不变 | `off`、带密钥指纹 `private` 或设备详情 `full` |
| `--device-limit N` | 不变（初始 3） | 未设置个人上限时使用的默认上限，1–100 |
| `--history LEVEL` | 不变 | `off`：累计总量和最后活跃；`daily`：人员和服务器的 UTC 每日总量 |
| `--history-days N` | 不变（初始 90） | 保留 7–730 个 UTC 天，含今天 |
| `--yes`, `-y` | | 跳过确认；JSON 变更必须提供 |
| `--json` | | 输出 `meridian.output/v1` envelope |

单独运行 `privacy` 等同于 `privacy show`：显示设置、隐私说明和最近 50 次变更。`set` 只更改传入选项，说明提高级别会保存什么、降低级别会删除什么，并要求确认。相同值不会产生变更。JSON 的 `data` 包含设置和变更；`set` 还报告 `deleted` 中的删除记录数和 `rekeyed_devices` 中的转换数。日志保存令牌名称、`owner` 或 `root`、时间和前后值。恢复后清空私密设备记录，因为指纹密钥永远不包含在快照中。

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

`server add local` 会保存当前这台机器；之后的命令会直接在本机上运行，而不通过 SSH。名称和地址必须唯一；`server add` 遇到冲突时会报错拒绝，而不会改写已有服务器。`server remove` 仅删除已保存的连接配置，只要该服务器仍在拓扑中承担角色就会拒绝删除。

### meridian node

管理出口服务器。第一个服务器用 `meridian deploy`（或 `meridian setup`）部署；用 `meridian node add` 添加更多出口。

```
meridian node add [USER@]IP[:PORT] [flags]
meridian node list [--json]
meridian node remove NAME_OR_IP [--yes] [--force] [--forget-unreachable]
meridian node replace EXIT [USER@]NEW_IP[:PORT] [--name NAME] [--yes] [--forget-unreachable]
meridian node sni EXIT NEW_SNI [--self-steal] [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--name NAME` | (自动) | 节点的友好名称 |
| `--user USER` | 已保存或 root | 节点上的 SSH 用户 |
| `--ssh-port PORT` | 已保存或 22 | 节点的 SSH 端口（`node add`、`node replace`） |
| `--sni HOST` | (自动) | 此节点的 Reality 伪装目标（不指定时优先选择节点同子网内的网站，否则为 `www.google.com`） |
| `--domain DOMAIN` | (无) | 节点域名：XHTTP 迁移到该域名上并新增 WSS，二者都经由 Cloudflare CDN；Hysteria2 使用其证书 |
| `--self-steal` | 关 | `node add`：用指向该节点的你自己的域名（`--sni`，否则 `--domain`）伪装。`node sni`：`NEW_SNI` 是你自己的域名 |
| `--country CC` | 地址注册机构 | 显示在连接名称中的国家；注册机构信息有误时（任播或租用地址段）请手动指定 |
| `--harden / --no-harden` | 启 | 操作系统、SSH 和防火墙加固。集群服务器始终保持启用；`--no-harden` 会被拒绝 |
| `--yes` | | 跳过确认提示 |
| `--forget-unreachable` | | `node remove`, `node replace`: 被移除的服务器无法通过 SSH 连接（被封锁或已删除）时，忘记其上部署的内容，而不是让清理失败；在你删除它之前，它会继续为原有用户提供服务 |
| `--force` | | `node remove`：同时删除指向此出口的中继链 |
| `--json` | | 将 `node list` 作为 `meridian.output/v1` 信封发出 |

**`node add`** — 通过 SSH 连接，将服务器作为出口加入拓扑，并应用已审核的计划（系统加固、节点 agent 和 Xray、向控制服务注册）。现有客户端会在下次订阅刷新时收到新出口。

**`node list`** — 列出节点及其 agent 的实时状态、版本和流量。如果控制服务无法访问，已配置的节点会显示为 `unknown`，命令以退出码 `3` 结束。

**`node remove`** — 从拓扑中移除一个出口或路由网关。它会列出将被删除的所有资源（节点记录和文档、其 agent 和 Xray、防火墙规则、客户端接入点），请求确认后原样执行该审查计划；服务器本身仍保留在已保存的服务器列表中。控制服务器不能通过此命令移除（请使用 `meridian teardown`）；仍有中继指向该出口时也会拒绝（请先移除中继或传入 `--force`）；有路由网关经由该出口接入集群，或移除后会破坏拓扑（例如导致出口池为空或失去默认出口）时同样会拒绝。绑定到被移除网关的路由规则会一并删除。

**`node replace`** — 将一个出口移到另一个服务器。`EXIT` 是出口 ID 或其服务器的保存名称或 IP；新服务器使用与 `node add` 相同的目标语法（保存的名称或 `local` 也可用）并像 `server add` 一样被保存。出口保留其 ID、Reality 密钥、协议路径、SNI、WARP 和地理设置以及指向它的每个中继，所以中继用户保持工作，直接用户在下一次订阅刷新时获得新地址。审查列出将部署在新服务器上的内容以及在旧服务器上清理的每个资源（agent 和 Xray、nginx 文件、防火墙规则和证书）；保存的服务器保留。对于控制服务器拒绝（移动它会改变每个订阅 URL；丢失时请使用 `meridian restore`），以及对于已经有角色的新服务器拒绝。

**`node sni`** — 为出口的 Reality 路径设置新的伪装 SNI。`EXIT` 是出口 ID 或其服务器的保存名称或 IP。它拒绝 `apple.com`、`icloud.com` 名称和 `www.microsoft.com`，通过 SSH 检查出口能否以 TLS 1.3 和 HTTP/2 访问新站点，显示审查（跟随出口 SNI 的中继、保留自己 SNI 的中继、基于该路径的路由网关以及将清理的内容），确认后应用。旧名称会被替换而不是并存：应用在下次订阅刷新（每 6 小时）或手动刷新时切换，在此之前无法通过 Reality 连接。XHTTP、WSS 和 Hysteria2 不变。当 `meridian test --probe` 报告伪装 SNI 被封锁时使用。使用 `--self-steal` 时，`NEW_SNI` 是你拥有的域名：它不做可达性检查，而是检查该域名是否解析到出口（无法解析时停止，指向别处时警告），出口会为它申请 Let's Encrypt 证书并自己回应探测。

### meridian relay

管理中继 — Realm TCP 转发器，给用户一个到国外出口的国内入口点。中继承载 Reality 流量。

```
meridian relay add [USER@]RELAY_IP[:PORT] --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay remove RELAY [--exit EXIT] [--yes] [--forget-unreachable]
meridian relay sni RELAY NEW_SNI [--exit EXIT] [--yes]
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
| `--forget-unreachable` | | `relay remove`: 被移除的服务器无法通过 SSH 连接（被封锁或已删除）时，忘记其上部署的内容，而不是让清理失败；在你删除它之前，它会继续为原有用户提供服务 |
| `--json` | | 将 `relay list`（`data.relays[]`）作为 `meridian.output/v1` 信封发出 |

**`relay add`** — 添加单跳中继到拓扑并应用它。客户端在下次订阅刷新时接收它。对于多于一跳的链，使用 `meridian setup`。

**`relay remove`** — `RELAY` 是中继服务器的 IP 或保存的名称，或中继链 ID。删除通过该服务器的每条链（用 `--exit` 缩小范围），列出将删除的内容（Realm 单元、防火墙规则、出口端侦听器、客户端接入点），确认，并应用审查的删除。服务器留在保存的服务器中。

**`relay sni`** — 为通过某服务器的中继链（或按 ID 指定的一条链；用 `--exit` 缩小范围）设置新的伪装 SNI。它拒绝与 `node sni` 相同的名称，检查出口能否访问该站点（中继自己的名称由出口上的 Reality 侦听器提供），审查、确认并应用。传入出口自己的 SNI 会让中继重新跟随出口。应用在下次订阅刷新时切换。

要检查中继的跳跃，运行 `meridian check RELAY`。

### meridian route

从出口将选定的目标通过 Cloudflare WARP、直接或不通过任何地方发送，领先于出口的默认出站。

```
meridian route add EXIT VALUE... --via warp|direct|block [--name ID] [--json]
meridian route add EXIT --preset ai [--via warp|direct|block] [--name ID] [--json]
meridian route list [EXIT] [--json]
meridian route remove EXIT ROUTE [--yes] [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--via ACTION` | 使用 `--preset` 时为 `warp`，否则 add 必填 | `warp`、`direct` 或 `block` |
| `--preset NAME` | | 用命名的目标集合代替 `VALUE`：`ai`（AI 服务） |
| `--name ID` | 预设名，或带编号的操作 | 路由 ID 显示在 `route list` 并由 `route remove` 使用 |
| `--yes/-y` | | 跳过删除确认（需要用 `--json`） |
| `--json` | | 发出 `meridian.output/v1` 信封（`data.override` 用于 add/remove，`data.exits[]` 用于 list） |

`EXIT` 是出口 ID、保存的服务器名称或 IP。每个 `VALUE` 是一个域名（包括子域名）、`geosite:NAME` 或 `geoip:NAME`；一个路由持有一种。

**`route add`** — 将路由添加到拓扑中的出口并应用它。路由按添加它们的顺序匹配。没有 WARP 的出口上的 `warp` 路由仅为该路由设置 WARP；其他流量保持直接。

**`route list`** — 显示每个出口的默认出站及其路由的匹配顺序。

**`route remove`** — 删除一个路由并应用更改；其目标遵循出口的默认出站。WARP 保持安装在服务器上但不承载任何东西，直到路由或 `--warp` 再次使用它。

### meridian fleet

基于实时控制服务的集群健康状态与资源清单、应用历史、升级，以及可编辑的集群文件。

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet history [--json]
meridian fleet usage [--days N] [--json]
meridian fleet upgrade [--tested] [--yes]
meridian fleet export [--json]
meridian fleet import FILE [--dry-run] [--yes] [--forget-unreachable] [--json]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--days N` | 30 | `client usage` 或 `fleet usage` 的 UTC 天数，1–730，受已保存保留时间限制 |
| `--json` | | 将结果作为 `meridian.output/v1` 信封发出（`status`、`inventory`、`history`、`export`、`import`） |
| `--tested` | | `fleet upgrade`：跳过 GitHub；Realm 保持在已测试的版本或你已升级到的版本 |
| `--yes`, `-y` | | `fleet upgrade`、`fleet import`：跳过确认 |
| `--dry-run` | | `fleet import`：显示变更和审查结果，不应用任何内容 |
| `--forget-unreachable` | | `fleet import`：忘记已移除且 SSH 端口无响应的服务器上部署的内容 |

**`fleet status`** — 控制服务健康状态、每个 agent 的最近报告及其是否已应用当前配置、中继监听端口以及客户端数量。已声明但缺失的客户端会使健康状态降级；已禁用、达到限额或过期的客户端会正常统计，属于你的主动设置而非故障。中继监听端口可达只能证明 TCP 连通，因此未经完整验证的中继路由会使健康状态保持为 `unknown`；请用 `meridian test` 测试完整路径。退出码 `0` 表示健康，`4` 表示降级，`3` 表示未知。

`active` 列统计最近一次报告中流量有所增长的人数，报告采集时间显示在表格下方。迟到的旧报告不会覆盖较新的活跃数据。节点流量总计在计数器更换周期后仍会保留；`traffic_counting_since` 标记完整持久统计的起点，升级前仅补入仍保留的计数证据。

**`fleet inventory`** — 列出已配置的服务器、角色、节点和中继，并在控制服务可达时附带实时状态。绝不会输出 API 令牌或秘密路径。如果实时状态不可用，仍会显示已配置的清单并附带警告，命令以退出码 `3` 结束。要判断 `apply` 是否有待执行的变更，请使用 `plan --json`。

**`fleet history`** — 控制服务记录的每一次经过审核的应用，最新的在前：编号、时间、作者（机器的令牌名称；CLI 自身的令牌为 `root`，控制台为 `owner`）、状态（`proposed`、`approved`、`applied` 或 `failed`）以及计划哈希的开头部分。`--json` 返回 `data.revisions[]`。

**`fleet upgrade`** — 通过一次已审核的应用让集群保持最新。控制服务和每个节点 agent 会升级到当前 Meridian 附带的构建，每个 agent 就地升级并保留其节点身份；要获取更新的构建，请先运行 `meridian update`。中继无需等待 Meridian 更新即可升级到最新的 Realm 版本：选用 GitHub 上已测试主版本内的最新版本，连同 GitHub 为每个压缩包发布的 sha256 一起记录到 `cluster.yml`，每个中继在安装前都会校验。它会显示每台服务器上运行的版本并标为 `current` 或 `upgrade`，确认后应用已审核的计划，并在每台服务器上运行 `meridian check`。如果某个版本无法读取，命令会在任何改动之前以退出码 `3` 结束。

**`fleet export`** — 把集群已审查的拓扑输出为可编辑的 YAML 文件：出口及其协议路径、中继、网关、出口池、路由规则、路由、客户端和集群设置。服务器以保存的名称（没有名称时用地址）出现，从不使用内部 ID；文件以 `schema: meridian.fleet/v1` 开头。Reality 密钥、API 令牌、客户端链接以及 XHTTP 和 WSS 的传输路径都留在 Meridian 中，不会列出。`--json` 在 `data.document` 下返回同一份文档。

**`fleet import`** — 读取编辑后的文件（或用 `-` 从标准输入读取），每处变更一行：`+` 表示添加，`-` 表示删除，`~` 表示修改字段（`~ exit-2 sni www.example.com -> www.example.org`）。随后列出变更将删除的每个资源和审查哈希，询问确认，并像 `apply` 一样只应用这次审查。未编辑的文件会输出“没有变更”。从 `access.users` 中删除客户端等同于 `client remove`：其链接失效，连接页面被删除。添加客户端等同于 `client add`，并发布其页面；用 `client show NAME` 查看链接。新的伪装 SNI 会经过与 `node sni` 相同的检查。导入会拒绝：其他 `schema`、未保存的服务器（用 `meridian server add` 保存）、文件中的传输路径、对控制服务器及其域名或后端的更改、删除或重命名控制服务器上的出口，以及把出口移到另一台服务器（请用 `node replace`）。密钥和凭据保持不变。没有终端时请传入 `--yes`；`--dry-run` 只显示变更和审查结果，不应用任何内容。

**`fleet usage`** — 显示每台服务器每个 UTC 天的总字节数，包括已删除服务器仍在保留期内的用量。`--days N` 选择保留期内 1–730 天。每日历史须开启；JSON 在 `data` 中返回服务器组总量和 `nodes[].daily`。

### meridian context

在一台电脑上保存多组互相独立的服务器（例如一组给家人、一组用于工作），并选择命令作用于哪一组。

```
meridian context list [--json]
meridian context use NAME
meridian context rename OLD NEW
meridian context remove NAME [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--json` | | `context list`：输出 `meridian.output/v1` 信封 |
| `--yes`, `-y` | | `context remove`：不经询问删除仍有服务器的上下文 |

**`context list`** — 列出每个上下文的控制服务器以及出口、中继和路由网关的数量，只读取本机保存的信息；`*` 标记当前上下文：

```
  * default   198.51.100.10    2 exits, 1 relay
    family    198.51.100.40    1 exit
```

**`context use`** — 让之后的所有命令都作用于 NAME。`meridian deploy IP --context NAME` 会创建新上下文，任何命令都可以加 `--context NAME` 临时操作另一组服务器。

**`context rename`** — 重命名上下文，不影响其服务器。

**`context remove`** — 只删除本机上该上下文的文件，从不连接服务器：服务器继续运行，但本机将无法再管理它们。它拒绝删除当前上下文；如果该组仍有服务器，会先询问（`--yes` 跳过询问）。`default` 上下文就是 Meridian 的主目录本身，因此不能重命名，只有在另一个上下文接管了这组服务器之后才能删除。这样的副本在 `context list` 中标为已被取代，并拒绝任何更改；此时删除 `default` 只会删除这组服务器的文件，SSH 密钥和偏好设置保留。

### meridian check

检查服务器健康状态，或检查一台新服务器能否运行 Meridian。检查项取决于服务器在集群中的角色：

- **尚未加入集群** — 部署前检查：伪装目标可达性与 DNS、端口 443 是否空闲及外部可达性、域名 DNS、操作系统、磁盘空间和系统时钟。不会安装任何软件；当端口 443 空闲时，会短暂启动监听进程以测试防火墙。DNS 查询使用服务器自身的解析器。
- **控制服务器、出口或路由网关** — 控制服务（能响应、属于此集群、版本兼容）、每个节点的 agent（在线、已应用当前配置、构建为最新）、SSH（或本机访问）、每个已编译的公开与内部端口、TLS 证书和磁盘空间。从另一台机器运行时，还会对控制服务器执行数据流检查：下载约 50 KB 数据，若在约 16–24 KB 后卡住则发出警告（从你所在网络测得的限速特征）。
- **中继** — 服务器上运行的每个 Realm 跃点：服务处于活动状态且能连通下一跳，并且中继链的公开入口接受来自你机器的 TCP 连接。

不指定服务器时，会依次检查集群中的每台服务器。

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

完整测试会选择一个活跃客户端，获取控制服务下发给该客户端的同一份 Xray JSON，并通过自动回退组以及目标服务器的每条路径发送真实流量。首次运行可能需要最多 120 秒下载并校验固定版本的 Xray 运行时。流量检查会轮换多个独立的公网 IP 观测服务并与直连请求比对，因此观测服务本身故障会判定为“不确定”（inconclusive），而不会误报为连接失败。在受控环境中可通过 `MERIDIAN_CONNECT_TEST_URL` 指定观测服务地址。

每条路径都会报告首字节时间（TTFB）。当某条路径失败时，Meridian 会向其端点发起普通 TCP 连接，以区分是**能连上但无流量通过**（服务器有响应，但代理会话无法传输数据：凭据不匹配或连接被阻断）还是**无法连接**（该端口没有任何响应）。使用 `--json` 时，`data.transports` 会列出每条路径的结果（`ok`、`no_traffic`、`unreachable`、`failed`、`egress_mismatch`、`inconclusive`、`invalid`）及 `first_byte_ms`。

如果控制服务宕机、返回 HTTP `429` 或 `5xx` 导致无法获取订阅，命令以退出码 `3` 结束。其他订阅被拒情况（如 `400` 或 `422`）视为测试失败，退出码为 `4`。

```
meridian test [SERVER|DOMAIN] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--probe] [--timeout SECONDS] [--json]
```

未指定目标时，`test` 会测试其所在的已保存服务器，或你唯一已保存的服务器。有多台时，它会依次测试你的集群的每个公共入口（每个出口，然后是每个中继的入口），并以最差的结果退出；此时 `--json` 会在 `data.results` 中为每台服务器返回一个结果。

`--client` 指定要测试的活跃客户端。`--basic` 仅运行网络和 TLS 检查，不下载 Xray，且无法验证纯 UDP 路径；不能与 `--client` 同时使用（退出码 `2`）。`--timeout`（默认 `5`，范围 `1`–`30`）限制单次网络操作的超时时间，不计入首次下载 Xray 的时间。

退出码 `0` 表示所有检查均通过，`4` 表示发现问题，`3` 表示缺少必要证据。`--json` 会输出包含所有检查项与发现的 `meridian.output/v1` 信封。

#### 审查者的视角：`meridian test --probe`

`--probe` 不发送代理流量，而是从审查方主动探测器的视角检查服务器，看部署是否会暴露自身特征。它可用于任意 IP 或域名，当目标属于你保存的拓扑时还会执行更严格的检查。

检查内容紧跟实际部署：公开与内部端口暴露情况、HTTP/TLS 响应行为、SNI 一致性与伪装、常见代理与管理面板路径、WebSocket 升级、反向 DNS、HTTP/2、旧版 TLS 协议以及已配置的域名。`--sni` 可覆盖通用 TLS 探测使用的 SNI。对于纯 UDP 监听端口，命令不会凭空猜测，而是提示需要运行完整的 `meridian test`。

`--probe` 还能回答“到底是什么被封锁了？”。它会先带上伪装 SNI 与服务器建立 TLS 连接；如果失败，则不带 SNI 重试，并直接连接真实的伪装站点，从而判断看起来被封锁的是**服务器 IP** 还是**伪装 SNI**。如果真实站点同样无法直连，结果会判定为“不确定”，而不会误报为问题。使用 self-steal 伪装时，真实站点就是服务器本身，没有独立的对照组：当连接完全不通时，结果为“不确定”。在控制服务器上，它还会下载连接页面的约 50 KB 脚本，检测传输是否会在约 16–24 KB 后卡住——这正是境外 IP 被限速时的典型表现。当 WSS 路径经过 CDN 时，也会通过 CDN 执行同样的下载检测。这些结果只有在受审查的网络内部运行时才有意义。`--probe` 不能与 `--basic`、`--client` 或 `--domain` 同时使用。退出码与 `test` 相同；跳过的检查绝不会算作通过。

`meridian probe [SERVER|DOMAIN] [--sni NAME] [--timeout SECONDS] [--json]` 等同于 `meridian test IP --probe`。

### meridian doctor

收集用于提交错误报告的系统诊断信息。

```
meridian doctor [SERVER] [--sni HOST] [--user USER] [--ssh-port PORT] [--ai]
```

`--sni` 指定要诊断的伪装目标，`--user` 和 `--ssh-port` 覆盖已保存的 SSH 用户和端口，`--ai` 将脱敏后的 AI 诊断提示词复制到剪贴板，而不是打印 issue 模板。

`doctor` 会根据服务器承担的角色进行检查：纯中继服务器不会按出口检查，路由网关也会单独标注。若缺少系统工具或 SSH 连接中断，报告会标记为不确定（退出码 `3`），已收集到的部分仍会正常显示。

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

`logs` 读取服务器各角色所运行的内容：出口和路由网关在 `node` 下显示 agent（`netagent`）和 Xray（`netcore`），控制服务器在 `panel` 下显示控制服务（`ctld`），二者都显示 nginx 错误日志，中继则显示其 Realm 单元的日志。`--user` 和 `--ssh-port` 覆盖保存的 SSH 用户和端口。

每一行都经过与 `meridian doctor` 相同的脱敏处理：令牌、密码、密钥、UUID、订阅 ID，以及控制服务和连接页面的秘密路径都会被替换为 `[REDACTED]`，因此输出可以安全地粘贴到错误报告中。各行以纯文本打印，并已去除终端控制字符。

没有集群角色的服务器，或其未运行的 `--component`，会在连接之前以 `2` 退出。无法读取的日志（服务缺失、SSH 连接丢失）会显示为不可用，命令以 `3` 退出。

### meridian panel

显示网页控制台的地址；在控制台还没有所有者时，同时显示一次性设置链接。

```
meridian panel [--setup-link]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--setup-link` | | 即使控制台已有所有者，也打印一个重置链接：它会设置新密码、关闭登录验证码，并让所有设备退出登录 |

设置链接只能使用一次，有效期 15 分钟；打开它来设置所有者的密码。控制台有所有者之后，`panel` 会打印地址以及是否已开启登录验证码。请像保护密码一样保护地址和设置链接。在控制台中你可以管理用户和 API 令牌；服务器、修订和订阅标题在那里是只读的。详见[网页控制台](/zh/client-management/#网页控制台)。

### meridian teardown

从服务器上卸载 Meridian。

```
meridian teardown [SERVER] [--user USER] [--ssh-port PORT] [--yes]
```

在单服务器部署上，`teardown` 会删除 Meridian 创建的所有内容：服务器上的控制服务及其数据库、agent 和 Xray、nginx 配置、证书、中继和防火墙规则，以及本地的 `cluster.yml` 和已保存的服务器配置。系统软件包、SSH 加固和 fail2ban 会保留；如果 nginx 仍在为你自己的网站提供服务，它也会继续运行。它还会一并清理 Meridian 3.x 留下的 3x-ui。在多服务器集群中，`teardown` 会拒绝卸载仍在拓扑中承担角色的服务器。

`--yes` 跳过确认；角色检查仍先运行。拒绝退出 `1`。未知的服务器名称或零个或多个可能目标退出 `2` 而不是提示。

### meridian backup

控制服务状态的加密快照、接收快照的机器，以及恢复密钥。

```
meridian backup pull [--name NAME]
meridian backup recovery-key [--yes]
meridian backup devices
meridian backup revoke ID
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--name NAME` | 这台电脑的主机名 | `backup pull`：这台机器的密钥首次注册时使用的名称 |
| `--yes`, `-y` | | `backup recovery-key`：不询问直接替换已有的恢复密钥 |

每个修改集群的命令执行之后，Meridian 都会把控制服务的最新快照保存到 `cluster.yml` 旁边的 `snapshots/` 中（保留最近几份）。快照包含用户、限额、流量、修订、API 令牌，以及这台机器的 `cluster.yml` 和 `servers.json` 副本，因此可以在一台什么都没有的机器上重建集群。它加密给每台已注册机器的密钥和恢复密钥，从不包含签名密钥、会话或控制台密码。保存失败只会给出警告，绝不会让触发它的命令失败。

**`backup pull`** — 立即保存一份快照。机器上的首次 pull 会创建它的密钥（`device.key`，只有你能读取）并向控制服务注册。

**`backup recovery-key`** — 生成集群的恢复密钥，或在询问后替换已有的恢复密钥，并只显示一次。Meridian 只保留其公钥部分，所以请写下或打印出来并离线保存。集群首次部署时已经会显示一个。不支持 `--json`。

**`backup devices`** — 列出快照所加密给的机器密钥，包括它们的 ID 和注册时间，以及集群是否已有恢复密钥。

**`backup revoke`** — 不再为某台机器（例如丢失的笔记本电脑）的密钥加密新快照。它已经持有的快照仍可用它的密钥读取；请在它拉取新快照之前撤销。

### meridian restore

用快照在另一台服务器上重建丢失或被查扣的控制服务器。

```
meridian restore --to SERVER [--snapshot FILE] [--recovery-key] [--force]
                 [--name NAME] [--user USER] [--ssh-port PORT] [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--to SERVER` | （必需） | 新的控制服务器：IP、`user@IP:port` 或已保存的名称 |
| `--snapshot FILE` | 最新保存的 | 要恢复的快照 |
| `--recovery-key` | | 用恢复密钥而不是这台机器的密钥打开快照；密钥会以隐藏方式询问，或从标准输入读取一行 |
| `--force` | | 即使旧控制服务仍在响应也执行恢复 |
| `--name NAME` | （无） | 新服务器的名称 |
| `--user/-u USER` | root | 新服务器上的 SSH 用户 |
| `--ssh-port PORT` | 22 | 新服务器上的 SSH 端口 |
| `--yes`, `-y` | | 跳过确认 |

restore 会打开快照，审核它要做的事并请求确认。然后它用快照在新服务器上安装控制服务，在拓扑中把控制服务器（以及旧服务器上的出口，如果有的话）移到新地址，并运行常规的已审核应用。恢复后的服务会获得新的证书颁发机构、新的签名密钥和新的 API 令牌，因此每个 agent 都会重新注册，被查扣的服务器无法签发你的节点会接受的任何内容。用户保留各自的账户、限额和流量。控制台所有者需要通过 `meridian panel` 给出的链接设置新密码。

使用裸 IP 时，每个订阅 URL 都包含控制服务器的地址，因此所有链接都会改变：审核时会说明这一点，结束时会打印每个客户端的新链接。使用域名时，只要域名的 DNS 记录指向新服务器，链接就保持不变；审核时会列出这些记录。

只要旧控制服务器还在响应，restore 就会拒绝执行，因为两个控制服务会让集群分裂；`--force` 可以覆盖这一点，例如旧服务器已被查扣时。如果 `--snapshot` 指定的文件比最新保存的快照旧，它会发出警告：旧快照会带回你之后删除的令牌、机器和用户。中途停止的 restore 可以再次运行，会从中断处继续。

在没有该集群的机器上，请传入快照文件和 `--recovery-key`：会先恢复快照自带的集群文件副本。如果这台机器上的副本比快照中的旧，restore 会把集群放进一个新的上下文，并在审核中说明后，从旧上下文接管集群；旧上下文会被标记为已被取代，并拒绝任何修改，直到你用 `meridian context remove` 删除它。

### meridian migrate

把运行 Remnawave 面板的集群就地迁移到 Meridian 的控制服务，并保留每条链接。

```
meridian migrate [--yes]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--yes`, `-y` | | 跳过确认 |

migrate 会先审核迁移内容：要迁移的客户端、某个客户端无法保留的内容，以及没有任何一方会保留的面板数据（面板的管理员账户和 API 令牌、按天的用量历史、HWID 设备注册、非 Meridian 设置的面板插件和响应规则、客户端 IP 转发（它服务于面板按用户的 IP 限制），以及 Remnawave 订阅页面；Meridian 自己的连接页面会保留）。确认后，它会：

1. 备份面板数据库，连同你的集群文件，保存到你机器上的 `backups/` 和控制服务器上的 `/var/backups/meridian-migration/`
2. 在面板旁边安装控制服务，并以相同的密钥、订阅 ID、状态、限额、到期时间和流量导入每个客户端
3. 停止面板（其数据保留），并删除每个节点的容器
4. 应用集群：每个出口都会装上 agent，并向控制服务注册
5. 检查每个客户端的订阅链接是否完全没有变化
6. 删除 Remnawave

没有回滚；备份就是退路。每一步都会保存，因此中途停止的迁移再次运行时会继续，已完成的迁移不会再做任何事。如果链接检查失败，Remnawave 会保留在原处（处于停止状态），命令会以错误结束。没有终端时请传入 `--yes`，否则 migrate 会在审核后停止。`meridian deploy` 发现 Remnawave 集群时会提议迁移。

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

### meridian serve

运行控制服务。Meridian 会把它安装为控制服务器上的 `ctld` systemd 单元，由该单元运行这条命令；你不需要自己运行它。

```
meridian serve [--config FILE]
```

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--config FILE` | `/etc/ctld/config.json` | 由 Meridian 写入的服务配置 |

该服务在 nginx 之后监听 `127.0.0.1:3000`，并把状态保存在 `/var/lib/ctld/` 下的一个 SQLite 文件中。它不记录任何请求，因此订阅 ID 和秘密路径永远不会进入日志。`meridian logs SERVER -c panel` 可以查看它的日志。

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
| `--context NAME` | 操作另一组服务器（[meridian context](#meridian-context)）；默认：`MERIDIAN_CONTEXT`，否则为当前上下文。也可以写在命令之后，如 `meridian deploy 203.0.113.5 --context work` |
| `--lang CODE` | 设置向导的语言：`en`、`ru`、`fa` 或 `zh`（默认：已保存的选择，否则询问） |
| `--json` | 从支持它的命令请求 JSON 信封；其他命令明确失败 |
| `--install-completion` | 为当前 shell 安装补全 |
| `--show-completion` | 为当前 shell 打印补全脚本 |

JSON 输出使用 `meridian.output/v1` 信封（`schema`、`command`、`operation_id`、`status`、`summary`、`data`、`warnings`、`errors`）。所有列出了 `--json` 的命令都支持该格式：`deploy`、`plan`、`apply`、`check`、`test`、`probe`、所有 `client` 和 `route` 命令、`fleet status`/`inventory`/`history`/`export`/`import`、`node`/`relay list` 以及 `api` 命令。全局 `--json` 与子命令自身的 `--json` 等效。所有敏感信息在输出为 JSON 前都会自动脱敏。运行 `meridian api commands --json` 可查看完整契约列表。

## 常见服务器选项

这些在命令间重复出现；每个命令仅接受其自己部分中列出的选项。

| 标志 | 被使用 |
|------|---------|
| `SERVER` 参数 | deploy、check、scan、test、doctor、logs、teardown、node add、relay add、server add |
| `--user`, `-u USER` | deploy、check、node add、relay add、server add、scan、doctor、logs、teardown、restore |
| `--ssh-port PORT` | deploy、check、node add、relay add、server add、scan、doctor、logs、teardown、restore |
| `--sni HOST` | deploy、check、node add、relay add、test、doctor |
| `--domain DOMAIN` | deploy、check、node add、test |
| `--self-steal` | deploy、node add、node sni |

客户端命令直接使用控制服务的 API，不接受服务器参数。`test` 从你的设备运行并不使用 SSH。

## 服务器解析

与 `ssh` 一样，先写服务器：IP、已保存的名称、`user@host:port`，或 `local`（在当前机器上运行，不经 SSH）。`--user` 和 `--ssh-port` 补充目标中未写明的部分，且不能与之冲突。用户或端口留空表示使用已保存的值，没有则为 `root` 和 `22`。未指定服务器时，Meridian 使用本地模式（在已部署的服务器上运行时）或唯一已保存的服务器。没有候选或有多个候选时以退出码 `2` 结束，而不会提示选择。
