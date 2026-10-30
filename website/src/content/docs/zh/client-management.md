---
title: 客户端管理
description: 添加用户、分享连接信息和管理访问密钥。
---

## 添加客户端

```
meridian client add alice
```

这会为“alice”生成专属密钥，并打印两项可供分享的内容：

- **一个带 QR 码的连接页面链接** — 最容易发送的
- **一个订阅 URL** 用于直接导入到 Xray/V2Ray 应用中

一次添加多个人：

```
meridian client add alice bob charlie
```

客户端属于你的整个集群，而非单台服务器。你部署的每个出口和中继都会出现在他们的订阅中。

### 收件人看到的内容

连接页面从该人的真实订阅构建，所以它列出的正是他们的应用会收到的内容：直接出口、中继以及每种已启用的方式。页面包含：

- 三个步骤：安装为其平台推荐的应用（首选 Happ，其次是 FlClashX 和 Clash Mi，桌面端还有 Throne），然后轻点一下添加连接，最后按照应用中的说明开启连接
- 订阅的二维码；在电脑上它显示在步骤旁边，方便用来设置手机
- 每个单独的连接，都带有“打开”“复制”和二维码按钮，平时折叠起来，需要时再展开
- “无法连接？”部分，包含一些提示和“检查我的连接”测试，会复制一份不含链接的报告，方便他们发给你

在应用中，条目按出口所在国家和方式命名：“🇩🇪 DE · Reality · Chrome”“🇩🇪 DE · XHTTP · Chrome”“🇩🇪 DE · WebSocket · CDN”“🇩🇪 DE · Hysteria2 · UDP”，中继条目另加“via relay”。同一国家有两个出口时，旗帜后显示它们的名称（“🇩🇪 main”）；国家未知的出口在有多个出口时显示其名称。Reality 和 XHTTP 还提供使用另一种 TLS 指纹的“· Firefox”副本，以防某一种指纹被封锁。会自动切换的应用（Xray JSON 应用如 Happ，以及 Mihomo 和 sing-box）会自行选择可用的条目，但不会在不同指纹之间切换；Happ 为此显示一个单独的“Automatic · Firefox”条目。

通过任何信使发送链接。收件人打开它，安装应用，点击“添加到 …”（或用另一台设备扫描二维码），然后连接。[连接指南](/zh/connecting/)会一步步带他们完成，并说明连接失效时该怎么办；也请把那一页发给他们。

## 显示连接详情

要再次分享客户端的链接：

```
meridian client show alice
```

这会打印个人 URL，以及状态、已用流量、到期时间和客户端最近在线时间。同一个 URL 在浏览器中打开连接页面，在应用中提供配置。控制服务会自动为每个人提供页面，包括从控制台邀请的用户。

## 列出客户端

```
meridian client list
```

显示所有客户端的状态、流量、到期时间和最近在线时间。

## 私人备注和标签

可为每个人保存一条私人备注和一个标签，包括在控制台添加的人：

```bash
meridian client set sample --note "临时邀请" --tag TRIAL_7
meridian client show sample
meridian client list --tag TRIAL_7
meridian client set sample --note "" --tag none
```

备注为纯文本，最多 500 个字符，支持换行。标签使用 1–16 个大写字母 A–Z、数字或下划线。空备注和 `--tag none` 清除相应字段；未指定的选项保留原值。`client list` 显示标签列，`--tag none` 可查找没有标签的人。

控制台的个人页面可编辑相同字段。API 接受带有 `username`、`note` 和 `tag` 的 `PATCH /api/users`，或不带 `username` 的 `PATCH /api/users/NAME`。读取和编辑需要 `people` 或 `admin` 权限；较低权限收到空字符串。字段存放在控制服务中，随个人数据进入加密快照，不会进入订阅、连接页面、事件或日志。应用配置会保留运营者的编辑。迁移会保留 Remnawave 描述和有效标签，并报告描述截断到 500 个字符及跳过的无效标签。

## 流量限额和到期时间

给客户端设置每月流量额度和结束日期：

```
meridian client set alice --limit 50GB --reset month --expires 2026-12-31
```

添加客户端时也可以使用这些选项（`meridian client add alice --limit 50GB --reset month`）。之后只会修改你传入的选项：

```
meridian client set alice --expires 2027-06-30
meridian client set alice --limit none --expires never
```

- `--limit` 接受 `500MB`、`50GB` 或 `1.5TB` 这样的大小（1 GB 为 1024³ 字节，与控制服务的计算方式一致）；`none` 取消限额。
- `--reset` 每天（`day`）、每周（`week`）或每月（`month`）把已用流量清零；`never` 表示一直累计。
- `--expires` 接受日期（访问在当天结束时终止，按 UTC）、ISO 时间如 `2026-12-31T18:00:00+03:00`，或 `never`。

用完额度的客户端显示为 `limited`，过了到期时间的显示为 `expired`。在下一次重置之前，或在你提高限额、推迟日期之前，他们都无法连接。要立即恢复额度：

```
meridian client reset-traffic alice
```

`client show` 会显示当前情况：

```
  状态     ● active
  流量     12.4 GB / 50 GB (每月重置)
  到期     2026-12-31
```

应用、重新部署和服务器变更都不会改动客户端的限额和到期时间。

## 限制设备数量

设备限制**默认关闭**：Meridian 不读取或存储设备信息。需要上限时，在 **Settings → Privacy** 中选择 `private` 或 `full`。隐私级别和每个人的设备上限保存在控制服务中，应用配置不会改动它们。

- **Private：**每个人的设备通过不可读的指纹计数。Meridian 不存储设备名称、系统或应用。夺取控制服务器的人只能核查自己已经持有的某台设备是否被使用过。
- **Full：**Meridian 存储每台设备的 ID、系统、型号、应用以及最近获取订阅的时间。夺取控制服务器的人会知道每个人使用哪些设备。地址永远不会被存储。

Private 模式也不存储首次或最近获取订阅的时间。指纹包含在加密快照中，但其集群密钥留在控制服务器上。恢复后会清除 private 登记，设备在下次刷新订阅时重新登记。从 full 降到 private 会将 ID 转为指纹并删除元数据；关闭设备限制会删除所有登记。

添加用户时或之后可设置 1 到 100 的上限。`default` 使用集群上限，初始值为 3：

```
meridian client add alice --devices 2
meridian client set alice --devices 4
meridian client set alice --devices default
```

查看或清除登记：

```
meridian client devices alice
meridian client devices alice --json
meridian client devices alice --remove ID
meridian client devices alice --clear
```

Private 模式只显示“N / M 台设备”；JSON 还返回指纹 ID，以便单独移除。Full 模式列出 ID、系统及版本、型号、应用、首次和最近获取时间。控制台中的用户页面显示相同的数量或列表，提供全部清除按钮，full 模式还提供单台设备移除按钮。

降低上限后，已登记设备仍可使用。超过上限的新设备会收到提示移除设备或申请提高上限的订阅占位配置。计数需要应用发送有效的 `x-hwid` 请求头；浏览器、连接页面的请求以及不发送受支持 HWID 的应用始终正常获取订阅，且不计数。清除登记会腾出名额，设备可在下次刷新时重新登记。上限只控制订阅刷新，不会撤销已导入应用的配置。`client reissue` 更换凭证并清除所有登记，同时保留上限。`migrate` 永远不会导入 Remnawave 的设备登记。

## 暂停和恢复客户端

```
meridian client disable alice
meridian client enable alice
```

`disable` 会立即暂停该客户端的访问；`enable` 则允许他们继续使用原有链接连接。

禁用会持续到执行 `client enable`：应用、重新部署和集群变更都不会改动它。

## 替换泄露的链接

如果客户端的链接或连接页面落到了不该拿到的人手里，请替换它：

```
meridian client reissue alice
```

控制服务会为该客户端生成新的订阅 ID 和新的密钥。旧的订阅 URL 和连接页面返回 404，服务器拒绝旧密钥。Meridian 打印新的订阅 URL，它同时打开新的连接页面。他们需要重新导入一次新链接：应用无法刷新一个已经不存在的订阅 URL。状态、流量限额和用量、到期时间及设备上限保持不变。设备登记会被清除。

使用 `--json` 时请传入 `--yes`。新链接只出现在该命令的输出中。

## 删除客户端

`meridian client remove NAME` 永久删除客户端：他们的链接停止工作，他们的连接页面不再可用。集群至少保持一个客户端。

已建立的 VLESS 会话可能在凭证撤销后继续存在。`client remove` 或 `client reissue` 的 **`--cut-off`** 会等待每个受影响节点应用不含旧凭证的版本，再重置相关入站。这些入站上的所有会话都会终止，包括其他用户的会话；他们会短暂重连。若入站 API 失败，会重启 Xray 并终止该节点上的所有 Xray 会话。此选项需主动启用，建议在链接泄露时使用。JSON 在 `data.cut_off` 中报告各节点的 `done`、`core_restart`、`failed` 或 `timed_out` 状态。最多等待五分钟；失败退出码为 `4`，超时为 `3`。删除在控制台中添加的用户时，控制台也提供此选项。

## 订阅链接

Xray JSON 首先列出 **Automatic**，然后为每个主要连接提供一个独立命名的配置。每个命名配置保留订阅的路由规则，只使用对应连接。指纹备用连接仍放在独立的自动分组中。

订阅 URL 看起来像 `https://198.51.100.10/<page-path>/sub/<id>`。它们位于连接页面路径下；控制服务的控制台位于共享链接永远不会暴露的单独秘密路径上。

每个订阅都会要求应用每 6 小时刷新一次（通过 `profile-update-interval` 响应头，Happ 和 Clash Verge 等应用会遵循它），因此当你添加出口或中继时，客户端会在几小时内自动获取，无需发送新链接。忽略该响应头的应用会按各自的周期刷新；在应用中手动刷新则会立即生效。

应用中的支持按钮会打开该用户的连接页面，其中带有“无法连接？”排查指南。

### 已测试的应用和引擎

同一个订阅 URL 会向每个应用提供它能读取的格式；Stash 会收到它自己方言的配置。Meridian 的每次变更都会用这些应用所基于的引擎检查控制服务提供的每种格式：

| 格式 | 应用 | 测试的引擎 | 实验室检查内容 |
|--------|------|---------------|---------------------|
| Xray JSON | Happ、v2rayNG、v2rayN、Streisand、V2Box | Xray 26.7.28 | 流量通过每个入口以及自动故障转移；错误的密钥会被拒绝 |
| Mihomo | FlClashX, Clash Mi | Mihomo 1.19.32 | `mihomo -t` 加载配置文件、其规则及其回退组 |
| sing-box | sing-box (SFA, SFI), Karing | sing-box 1.14.3 | `sing-box check` 加载配置文件，其自动组是默认路由 |
| Base64 链接 | 其他 Xray/V2Ray 应用（v2RayTun、Shadowrocket） | — | 每个链接都能解析，并指向与其他格式相同的服务器和密钥 |

应用会自行发布引擎更新，因此应用运行的引擎可能比测试的版本略旧或略新。

### Xray 应用中的 BitTorrent

Xray JSON 订阅（Happ 及其他基于 Xray 的应用）会把识别出的 BitTorrent 对等连接直接从设备发出，因此种子流量走用户自己的网络，而不是出口的地址。这只是尽力而为：加密的对等连接和 UDP（DHT/uTP）仍可能经过 VPN。Mihomo、sing-box 和 Base64 订阅不包含这条规则。

## 客户端数据存放在哪里

客户端账户（密钥、状态、限额、流量）保存在控制服务器上控制服务的数据库中。Meridian 管理的客户端列表保存在本地 `~/.config/meridian/cluster.yml` 的拓扑配置中。客户端命令调用控制服务的 API，控制服务直接根据数据库提供连接页面。每次变更后，你的机器都会保存一份该数据库的加密快照（见[备份](/zh/deploy/#备份)）。

## 网页控制台

到期时间使用浏览器时区下的原生 **日期** 和 **时间** 字段编辑，保存前会显示准确的 UTC 时间。**+1 个月** 和 **+3 个月** 从当前时间与账户现有到期时间中较晚的一项计算草稿，保留 UTC 时刻；目标月份较短时，日期取该月最后一天。保存只修改已编辑字段。**永不过期** 保存为 `null`；有限到期时间必须在未来且早于 2099 年。

订阅二维码和每个单独连接的二维码都提供 **下载 PNG** 和 **下载 SVG**，图片下方附有人员姓名及服务器组标题。浏览器支持图片剪贴板时会显示 **复制二维码图片**；如果复制被拒绝，则改为下载 PNG。图片在浏览器本地生成，不发送外部请求。

展开主要订阅交接区域下方的 **单独连接**，即可按订阅顺序复制指定连接或查看其二维码。该区域默认折叠，并列出 Xray JSON、Mihomo 和 sing-box 的指定格式链接。这些链接和二维码允许持有者以该人员的身份连接，请妥善保管。`meridian client show NAME --connections` 和 `GET /api/users/{username}/connections` 提供相同列表。API 需要 people/admin 权限或所有者会话；普通 read 令牌无法读取凭据。

操作结果会显示对应人员的姓名。启用已过期或流量额度已用尽的账户时，控制台会说明仍有哪些条件阻止连接。切换页面后才返回的结果会保留当前页面。

控制服务带有一个网页控制台，你可以在浏览器（包括手机）中管理用户。`meridian panel` 会打印它的地址；第一次还会打印一个一次性设置链接，用来设置所有者的密码。之后可以在 **Settings** 中添加来自身份验证器应用的登录验证码。

| 在控制台中修改 | 只读显示（用 `meridian` 修改） |
|---|---|
| **People**：添加用户、分享链接和二维码、启用或禁用、设置到期时间、流量限额和重置周期、重置流量、签发新链接、删除 | **Servers**：每个出口和网关，其 agent 是否在线、是否已应用最新配置、版本和流量 |
| **Tokens**：创建和撤销 API 令牌 | **Revisions**：从你电脑上应用的每次变更及其计划 |
| 你的密码和登录验证码 | 应用中显示的订阅标题 |

在控制台和 CLI 中所做的修改是同一回事：`meridian client list` 也会显示在控制台中添加的用户，而无论在哪边设置的状态、限额或到期时间，应用都不会撤销。用 `meridian client add` 添加的用户仍归 CLI 管理：控制台可以修改他们的限额和状态，但删除他们或签发新链接时，会显示相应的 `meridian client` 命令，因为这些用户属于已审核的拓扑。每个人都能在自己的订阅 URL 打开连接页面，包括在控制台中添加的用户。

控制台不保存任何 SSH 密钥，因此被盗的会话无法修改你的服务器。每次登录在闲置 30 分钟后过期，最长 12 小时；创建令牌时会再次要求输入密码。

### API 令牌

脚本和社区机器人（例如告知用户剩余流量的 Telegram 机器人）通过控制服务的 API 通信。请为每个机器人单独创建令牌：

1. 打开控制台（`meridian panel` 会打印地址），进入 **Tokens**，选择 **Create token**。
2. 以机器人的名字命名令牌，并选择 **Read**。控制台只显示一次令牌；请将其与机器人的密钥一起保存。
3. 将机器人指向控制台地址，并以 `Authorization: Bearer <token>` 发送令牌。API 路径位于该地址之下，例如 `https://198.51.100.10/<secret-path>/api/users/alice`。该地址包含秘密路径，因此要像令牌一样严格保密。

#### OpenAPI 契约

在控制台地址下请求 **`GET /api/openapi.json`**，并发送相同的 `Authorization: Bearer <token>` 请求头。所有有效的 API 令牌都能读取该文档，包括 `people`；已过期或撤销的令牌会收到 `401`。这是英文的 **OpenAPI 3.1** 契约，包含已安装服务的版本、请求与响应模式、错误和所需权限。仓库中的副本位于 `contracts/meridian/v1/admin/openapi.json`；使用该副本时，请将客户端的基础 URL 设置为控制台地址，包含秘密路径。

`x-meridian-scopes` 列出可选的所需权限。操作及其成功响应上的 `x-meridian-credentials: true` 表示响应可能包含凭据：请妥善保密，不要写入日志。用户连接凭据仅对 `people` 或 `admin` 返回；只读令牌收到的 UUID 和订阅令牌为空。连接端点响应可能包含传输混淆密码，加密快照也包含凭据。agent 注册与协议、订阅、连接页面和控制台会话使用独立接口，不包含在此文档中。

下方操作表会与契约核对。只读令牌可以看到：

| 端点 | 返回 |
|---|---|
| `GET /api/users/{username}` | 某个用户的状态、已用流量和限额、重置周期以及到期时间 |
| `GET /api/users` | 所有用户 |
| `GET /api/traffic` | 每个用户的流量总计 |
| `GET /api/health` | 服务的版本 |
| `GET /api/openapi.json` | 管理 API 契约 |
| `GET /api/events` | 有数量上限的本地事件分页 |
| `GET /api/attention` | 根据当前状态计算的账户、节点和服务器警告 |

只读令牌看不到用户的密钥或订阅 ID。如果机器人需要管理账户并分发连接凭据，请选择**用户**权限。创建这种令牌与创建其他控制台令牌一样，需要再次确认密码。

| 权限 | 允许的操作 |
|---|---|
| `read` | 查看服务器和用户，不包含连接凭据 |
| `propose` | 只读权限，以及提交修订提案 |
| `approve` | 提案权限，以及批准其他调用者的修订 |
| `people` | 查看和管理用户，包括 UUID 和订阅令牌 |
| `admin` | 包含两组权限，还可修改拓扑、注册 agent、管理令牌和备份；通过部署者的 API 管理权限签发 |

`people` 独立于 read → propose → approve 权限链。它可以访问 `/api/users` 并读取 `/api/openapi.json`，无法访问节点、连接端点、应用锁、修订、备份、agent 注册、API 令牌、隐私或设置。Admin 包含 people 权限。

| 用户 API 端点 | 操作 |
|---|---|
| `POST /api/users` | 在一个事务中创建用户，同时设置 `username`、`status`、`expire_at`、`traffic_limit_bytes`、`traffic_limit_strategy` 和 `device_limit` |
| `PATCH /api/users/{username}` | 修改状态、到期时间、流量限额或重置策略、设备上限；未发送的字段保持原值 |
| `POST /api/users/{username}/reset-traffic` | 重置已用流量 |
| `POST /api/users/{username}/extend` | 发送 `{"days": N}`（1–3650），从当前时间和原到期时间中较晚者起增加天数；未设到期时间则从现在起算 |
| `POST /api/users/{username}/reissue` | 更换连接凭据并清除设备注册 |
| `DELETE /api/users/{username}` | 删除用户 |
| `GET /api/users/{username}/devices` | 按所有者选择的隐私级别读取设备注册 |
| `DELETE /api/users/{username}/devices` | 清除全部注册 |
| `DELETE /api/users/{username}/devices/{device_key}` | 删除一项注册 |

People 令牌无法删除服务器配置文件中声明的用户，也无法为他们重新签发连接凭据。API 会返回 `409 declared_user` 和需要运行的 CLI 命令。已经批准或失败的应用仍声明该用户时，这项限制同样有效；未经批准的提案不会声明任何用户。状态、限额、到期时间和设备上限仍可修改。

重新签发和删除操作接受可选的 `{"cut_off": true}` 请求体，在凭据变更应用后结束现有会话。根据 `x-meridian-conditional-scopes`，这一选择还需要 `admin` 权限，并可能让相关入站上的其他用户短暂断线。不选择时，响应仍为用户账户或删除确认；选择后，响应包含已排入队列的命令。

可以选择 **7、30 或 90 天**，或**永不过期**；控制台新令牌默认有效期为 90 天。列表会显示到期时间并标记已过期令牌。到期和撤销都会在下一次请求时返回相同的 `401 unauthenticated` 响应。现有令牌仍保持无到期时间。请像保护密码一样保护 people 令牌，因为它可以读取用户的连接凭据。

`POST /api/tokens` 接受 1–3650 的 `expires_in_days`，或用 `null` 表示永不过期。省略该字段也表示永不过期。也可改为传入将来的 UTC 时间 `expires_at`；只能选择一个到期字段。响应总会包含 `TokenRecord.expires_at`，无到期时间时为 `null`。管理令牌需要 admin API 权限或控制台所有者会话；在控制台创建令牌还需要再次确认密码。People 令牌无法签发令牌。可通过 `meridian api schema NAME` 查看 `token-create`、`token-record`、`token-issued` 和 `token-list` 模式。

## 连接页面上的所有者联系信息

`meridian fleet contact` 显示已审阅的服务器配置中保存的联系信息。先审阅公开说明和联系链接：

```bash
meridian fleet contact --text "需要帮助时请联系我" --link mailto:help@example.com --dry-run --json
meridian fleet contact --text "需要帮助时请联系我" --link mailto:help@example.com --yes
meridian fleet contact --text "" --link none --yes
```

说明为一行纯文本，最多 120 个字符。链接只能使用 `https:`、`tg:` 或 `mailto:`；Meridian 会验证并规范化链接。两个字段相互独立：未指定的选项保留原值，`--text ""` 清除说明，`--link none` 清除链接。使用 JSON 更改时需要 `--yes`；试运行和拒绝确认都会保留原配置。导出和导入会保留 `delivery.owner_contact.text` 与 `delivery.owner_contact.link`。控制台设置显示联系信息及用于更改的 CLI 命令。

连接页面的帮助区和支持页面会显示公开说明及 **联系所有者** 链接，并支持全部四种界面语言。链接仅在点击后打开；页面资源均在本地。当联系信息已设置时，支持页面会保持显示它，并提供 **打开连接页面**。应用通过 `support-url` 接收链接；没有链接时，该响应头保留本地支持页面的地址。个人连接页面地址保持不变。

停用的订阅可将说明附加到状态提示，但所有提示都必须保持在 100 个字符以内。较长说明不会更改这些提示，仍会显示在页面上。
