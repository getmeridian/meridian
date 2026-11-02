---
title: 客户端管理
description: 通过网页面板或 CLI 管理用户、分享连接页面并设置限额与访问密钥。
---

你可以在任何浏览器中通过内置[网页面板](#网页面板)（`meridian panel`）管理用户，也可以在终端中使用 `meridian client` 命令。两种方式操作的都是控制服务中的同一批账户。

## 添加客户端

在网页面板中，打开 **People** 并点击 **Add person** 即可创建账户，同时可按需设置流量限额、到期时间、设备上限、语言、备注和标签，并复制连接页面链接或二维码。

在终端中：

```
meridian client add alice
```

这会为“alice”生成专属密钥，并打印两项可供分享的内容：

- **一个带 QR 码的连接页面链接** — 最容易发送的
- **一个订阅 URL** 用于直接导入到 VPN 应用中

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

这会打印个人 URL，以及状态、已用流量、到期时间和客户端最近在线时间。同一个 URL 在浏览器中打开连接页面，在应用中提供配置。控制服务会自动为每个人提供页面，包括在网页面板中添加的用户。

## 列出客户端

```
meridian client list
```

人员页面、`client list` 和批量选择使用同一服务端查询：用户名或显示名称、状态、标签、未来 1–365 天内到期、最低配额用量 1–100%、尚未使用、关注原因与周期状态（`pending`、`renewing`、`fixed`、`unlimited`）。计数考虑搜索和其他筛选。可按名称、周期用量、到期时间或最后活动排序，未知日期在最后。控制台每页显示 100 行，刷新时保留行身份。筛选和排序保存在 URL 片段。参数发送到服务，但不会保存为请求历史。游标分页不包含首次查询后新增的人员，在 15 分钟后过期；返回第一页会重新查询。

在人员页面选择**访问配置**，或使用 `meridian client list --profile limited` 和 `meridian client bulk disable --match-profile limited --dry-run`。筛选匹配已保存的成员关系；`default` 选择明确分配了全部服务器访问权的人员。每个配置的计数考虑搜索及其他筛选，每人只计一次，未使用的配置显示零。属于多个配置的人员仍只有一行，也只作为一个批量操作目标。所有配置的总数是人数，不是成员关系数量之和。筛选与计数仍需要 `people.query` 和 `people.private` 权限，不搜索私人备注。

后台刷新保留人员选择、批量操作、分页控件和键盘焦点。刷新失败时，当前行和控件仍可使用；“下一页”或“上一页”请求失败会保留最后成功加载的页面，以便通过分页控件重试。

提交或撤销回复丢失时，会读取回执核实结果；只有回执记录已撤销时，才会报告撤销成功。控制台保留更新后的回执，并在重新打开时再次读取。`client bulk undo ID --dry-run` 不发送写入请求，只检查操作是否可撤销、尚未撤销且仍在撤销期限内；最终撤销还会检查人员当前状态和入站容量。

另一个原因 **已停止使用**（`stopped`）会标记曾使用服务、但超过三天没有新流量的用户。**尚未使用**（`unused`）仍是独立原因。API 参数 `stopped_days` 可独立设置为 1–365 天，默认 3 天；`unused_days` 的范围和默认值保持不变。周期重置不会抹去这些证据，且无需启用每日历史。导入用量缺少最后活动时间时，不会猜测用户已停止使用。

显示所有客户端的状态、流量、到期时间和最近在线时间。

## 私人备注和标签

可为每个人保存一条私人备注和一个标签，包括在网页面板中添加的人：

```bash
meridian client set sample --note "临时邀请" --tag TRIAL_7
meridian client show sample
meridian client list --tag TRIAL_7
meridian client set sample --note "" --tag none
```

备注为纯文本，最多 500 个字符，支持换行。标签使用 1–16 个拉丁字母、数字或下划线，保存为大写（`family` 会变成 `FAMILY`）。空备注和 `--tag none` 清除相应字段；未指定的选项保留原值。`client list` 显示标签列，`--tag none` 可查找没有标签的人。

网页面板的个人页面可编辑相同字段。API 接受带有 `username`、`note` 和 `tag` 的 `PATCH /api/users`，或不带 `username` 的 `PATCH /api/users/NAME`。读取和编辑需要 `people` 或 `admin` 权限；较低权限收到空字符串。字段存放在控制服务中，随个人数据进入加密快照，不会进入订阅、连接页面、事件或日志。应用配置会保留运营者的编辑。迁移会保留 Remnawave 描述和有效标签，并报告描述截断到 500 个字符及跳过的无效标签。

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

应用的 `Subscription-Userinfo` 标头、`/info`、API 的 `period_traffic` 和网页面板共用周期上传与下载计数。手动和定期重置都会清零周期用量，同时保留累计用量。导入数据或旧周期缺少可恢复的方向信息时，每个字节都会保留：未知方向的用量计入下载，在 `unattributed_bytes`（`/info` 中为 `trafficUnattributedBytes`）中列出，面板也会说明，直到下次重置。Meridian 原生记录且从未重置的累计周期会保留已知的方向信息。

`client show` 会显示当前情况：

```
  状态     ● active
  流量     12.4 GB / 50 GB (每月重置)
  到期     2026-12-31
```

应用、重新部署和服务器变更都不会改动客户端的限额和到期时间。

## 账户有效期与自动续期

自动续期和每月指定日的流量重置都会同时清零本期上传、下载计数（包括导入时方向未知的流量），累计流量保持不变。

`--reset month` 在每个 UTC 月的第一天重置流量。`--reset month_rolling` 使用账户保存的每月指定日，默认是创建日。用 `--reset-day 1–31` 设置，或用 `default` 恢复创建日。指定 31 日时，二月使用 28 或 29 日，三月恢复 31 日。停机后，服务会在计入新报告的流量前补齐重置，并只清零一次。Remnawave 迁移会保留 `MONTH_ROLLING` 和原创建日；缺少创建日期时会拒绝迁移，不会猜测。

`--duration N` 设置首次使用后 1–3650 天的有效期，每天为 24 个 UTC 小时。只有节点代理首次报告并被接受的流量会开始计时；打开连接页面或获取订阅不会开始计时。激活前，CLI、面板和页面显示“首次使用后有效 30 天”；激活后显示真实到期时间。有效天数与 `--expires` 二选一。对已使用账户设置有效期时，使用已记录的首次流量时间；历史时间未知时须设置固定到期时间。`--duration none` 清除有效天数并保留已激活的到期时间。延长尚未使用的账户只增加待激活天数，不会激活账户。

```bash
meridian client add example --duration 30 --limit 50GiB --reset month_rolling --reset-day 31
meridian client set example --renew-months 1 --renewals 3 --dry-run --json
meridian client set example --renew-months 1 --renewals 3 --yes --json
meridian client set example --renew-days 7 --renewals unlimited --yes
meridian client set example --no-renew --yes
```

使用 `--renew-days N`（1–3650 个 UTC 天）或 `--renew-months N`（1–120 个日历月）配置自动续期。日期从原到期时间向前推进，在短月后仍保留原 UTC 时间与月日期。`--renewals N` 将每轮续期限制为 1–10000 次；`unlimited` 移除上限。停机期间每个错过的续期日都会计入次数，服务在一个事务中补齐续期，本期流量只清零一次，累计流量保留。已停用账户继续按时续期，但保持停用。受限账户仅在有效期和流量额度允许时恢复有效。待激活账户会等待首次使用。CLI、面板和页面显示下次流量重置、下次续期和剩余续期次数。

`client set --dry-run` 获取服务计算的日期且不保存。JSON 在 `data.schedule_preview` 中返回结果，并标明 `applied: false`。JSON 模式修改时间安排须添加 `--yes`；交互模式会在确认前显示日期。`--no-renew` 关闭自动续期。修改到期时间、有效天数或续期间隔会开始新一轮续期；仅修改次数上限会保留已完成次数。激活后的手动延长也会开始新一轮续期。面板编辑器提供相同选项，日期按浏览器时区显示并附准确 UTC 时间。服务器配置文件、批量导入、加密备份及恢复均保留这些字段；后续 apply 不会覆盖管理员修改。

`POST /api/users/preview` 需要 people/admin 权限或已认证的所有者会话。不传 `username` 可预览创建账户，传入则预览修改。请求使用类型明确的 `traffic_reset_anchor_day`、`activation_duration_days`、`renewal_interval_days`、`renewal_interval_months`、`renewal_limit`、`expire_at` 和 `traffic_limit_strategy`。`POST /api/users` 与 `PATCH /api/users/{username}` 使用相同设置。省略字段保留原值，显式 null 清除选项。只读字段 `renewals_completed`、`renewal_anchor_at` 和 `schedule` 描述已完成次数与计算日期。预览不保存账户修改、设备数据或请求详情。

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
meridian client set alice --devices unlimited
```

使用 `--devices unlimited`（或 `0`）可让此人不受默认设备上限约束。`--devices default` 则恢复服务器默认上限；面板中提供两个独立选项。API 中 `device_limit: 0` 表示不限，`null` 表示继承默认值。隐私级别仍然生效。Remnawave 迁移会保留每人的设备上限，包括零，但不会导入设备登记记录。

查看或清除登记：

```
meridian client devices alice
meridian client devices alice --json
meridian client devices alice --remove ID
meridian client devices alice --clear
```

所有者启用设备上限后，您的账户会显示已用 / 允许的设备名额。名额用满后，新设备刷新订阅时会收到上限提示。不报告设备标识的应用不计数。页面只显示计数，不会将其保存在浏览器中。

Private 模式只显示“N / M 台设备”；JSON 还返回指纹 ID，以便单独移除。Full 模式列出 ID、系统及版本、型号、应用、首次和最近获取时间。网页面板中的用户页面显示相同的数量或列表，提供全部清除按钮，full 模式还提供单台设备移除按钮。

降低上限后，已登记设备仍可使用。超过上限的新设备会收到提示移除设备或申请提高上限的订阅占位配置。计数需要应用发送有效的 `x-hwid` 请求头；浏览器、连接页面的请求以及不发送受支持 HWID 的应用始终正常获取订阅，且不计数。清除登记会腾出名额，设备可在下次刷新时重新登记。上限只控制订阅刷新，不会撤销已导入应用的配置。`client reissue` 更换凭证并清除所有登记，同时保留上限。`migrate` 永远不会导入 Remnawave 的设备登记。

## 暂停和恢复客户端

```
meridian client disable alice
meridian client enable alice
```

`disable` 在每个节点应用变更后阻止新连接；`enable` 用原有链接恢复访问。用户详情页显示哪些节点已确认阻止新连接。

禁用会持续到执行 `client enable`：应用、重新部署和集群变更都不会改动它。


运行中的临时规则只拦截对应入站上的已撤销凭据。有效凭据仍按正常 DNS、私有地址与国家 IP 屏蔽以及 IP 出口规则路由。重新启用同一凭据会移除其屏蔽；轮换后的新凭据不会匹配旧规则。重启 Xray 会终止旧会话，并清除上一轮运行的临时规则及待重试事项。

每次 Xray 运行最多保留 4096 条凭据与入站组合的屏蔽规则。达到上限后，凭据仍会移除，但节点会保持待处理状态，并提示重启 Xray。这会限制保留状态的大小，不会屏蔽有效凭据。


agent 会先把新凭据写入 `core.json`，再修改运行中的账户。这样，即使 apply 期间发生重启，也会加载当前凭据；如果核心实例在文件写入前后发生变化，agent 会先使其与当前文档一致，再确认变更。


撤销回执会显示每个节点上旧凭据的版本及原因。尚未确认的不同版本会分别保留；恢复同一凭据会清除对应回执，即使导入操作增加了版本计数。凭据指纹只保留在服务数据库中，不会出现在 API、事件或控制台中。有更新的回执时，已确认的旧回执会移除。


节点会检查当前运行的核心，并将撤销确认关联到已应用的文档。核心重启会终止旧会话，因此新核心无需临时撤销规则，也会清除先前的待处理状态。


新报告的已应用修订必须达到撤销修订或更新，并属于同一服务器集群时期，才能确认回执。无关修订和 agent 的 hello 消息会保留已有证据。Hello 不会刷新上一次核心状态报告的时间；过期报告仍显示待处理，只有回执状态真正变化时才会产生事件。

## 更换密钥并保留链接

需要更换连接凭据并保留链接时，执行 `meridian client rotate-credentials NAME --yes`。它生成新的 VLESS UUID 和 Hysteria2 密码，保留订阅令牌、页面 URL、设备登记、状态、限额、到期时间、流量、备注、标签和语言。应用下次刷新订阅时获取新密钥；请用户立即刷新。各节点应用变更后会阻止旧身份的新数据流。若订阅 URL 本身泄露，请使用 `client reissue`；该命令签发新 URL 并清除设备登记。

```bash
meridian client rotate-credentials alice --yes
```

网页面板为在面板添加的用户提供**更换连接密钥**。配置文件声明的用户通过 CLI 操作，遵循与重新签发链接相同的所有权规则。JSON 模式需要 `--yes`，结果 `client-rotate-credentials` 包含凭据代次和每个节点的 `flow_blocks`。可选 `--cut-off` 也结束现有数据流；确认前会说明重置入站或重启整个节点核心的影响。


## 替换泄露的链接

如果客户端的链接或连接页面落到了不该拿到的人手里，请替换它：

```
meridian client reissue alice
```

网页面板中的重新签发和删除共用同一确认流程：终止会话选项默认关闭，列出受影响节点，确认后才执行修改。重复点击重新签发只提交一次。重新签发会刷新新链接，并单独显示各节点结果；终止会话失败或超时不会撤销新凭证。配置中声明的用户会显示 CLI 命令。

控制服务会为该客户端生成新的订阅 ID 和新的密钥。旧的订阅 URL 和连接页面返回 404，服务器拒绝旧密钥。Meridian 打印新的订阅 URL，它同时打开新的连接页面。他们需要重新导入一次新链接：应用无法刷新一个已经不存在的订阅 URL。状态、流量限额和用量、到期时间及设备上限保持不变。设备登记会被清除。

使用 `--json` 时请传入 `--yes`。新链接只出现在该命令的输出中。

## 删除客户端

`meridian client remove NAME` 永久删除客户端：他们的链接停止工作，他们的连接页面不再可用。集群至少保持一个客户端。

禁用、到期、达到流量上限、删除用户及更换连接凭据，会阻止已撤销身份的新 TCP 数据流和 UDP 会话，包括已认证的 Hysteria2 QUIC 连接或 VLESS mux 连接上的新数据流。其他用户的会话保持连接。用户详情页和 `GET /api/users/{username}/flow-blocks` 按节点返回 `blocked`、`pending_node` 或 `agent_too_old`。尚未确认的节点在收到并应用变更前可能继续使用旧配置；控制服务不可用时也如此。使用 `meridian apply` 更新旧节点 agent。事件只记录节点和结果，不记录连接地址或设备数据。

撤销前开始的 TCP 流、Hysteria2 UDP 会话或 VLESS XUDP 会话，可能继续直到结束。固定版本的核心没有关闭单个用户会话的接口。`client remove`、`client reissue` 和 `client rotate-credentials` 的 **`--cut-off`** 会等待凭据变更应用。确认时会逐个节点说明范围：核心重启会让使用该节点任意 Xray 入站或桥接连接的所有用户重连；入站重置只影响列出的入站。如果重置失败，回退的核心重启会影响该节点所有入站与桥接连接。VLESS 节点使用核心重启；仅使用 Hysteria2 的节点重置相关入站。JSON 在 `data.cut_off` 下返回 `done`、`core_restart`、`failed` 或 `timed_out`。最多等待五分钟；失败退出码为 `4`，超时为 `3`。网页面板为在面板中添加的用户提供相同的删除、重新签发与凭据轮换选择。

## 订阅链接

Xray JSON 首先列出 **Automatic**，然后为每个主要连接提供一个独立命名的配置。每个命名配置保留订阅的路由规则，只使用对应连接。指纹备用连接仍放在独立的自动分组中。

订阅 URL 看起来像 `https://198.51.100.10/<page-path>/sub/<id>`。它们位于连接页面路径下；控制服务的网页面板位于共享链接永远不会暴露的单独秘密路径上。

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

## 网页面板

控制服务内置了一个网页面板（也称管理面板或管理控制台），你可以在任何浏览器（包括手机）中管理用户。`meridian panel` 会打印它的地址；第一次还会打印一个一次性设置链接，用来设置所有者的密码。之后可以在 **Settings** 中添加来自身份验证器应用的登录验证码。

**通行密钥：**面板使用配置中已确认的 HTTPS 域名时，打开 **设置 → 通行密钥 → 添加通行密钥**，输入名称并在设备上解锁密钥。最多可保存十个密钥，支持重命名，并显示添加和上次使用时间。保存密钥后，登录页会提供 **使用通行密钥登录**。必须通过 PIN、指纹或面容识别验证；已保存的密钥也可确认敏感操作。添加、重命名和移除密钥都需要五分钟内的身份确认。

密钥域名来自已确认的服务器配置，不读取请求标头。浏览器不允许在纯 IP 地址上使用通行密钥；这种情况下仍使用密码和可选的验证器代码。更换域名后，需要为新域名保存密钥。每个请求有效期为五分钟且只能使用一次。服务只保存凭证 ID、公钥、计数器、自选名称和时间，不保存设备身份或地址，也不发送外部请求。

同时出现大量登录尝试时，最早的匿名通行密钥请求可能会失效；请重新开始登录。已登录会话中的密钥注册和身份确认仍可使用。每个会话为每种操作保留自己的当前请求。

如果在设备提示期间身份确认过期，请重新确认；面板会在五分钟挑战仍有效时重试已完成的请求。如果另一把密钥使列表达到上限，也可在删除一把不再需要的密钥后，于同一期限内重试该请求。

验证器未返回账户标识时，通行密钥仍可用于登录和身份确认。

移除密钥会阻止使用该密钥再次登录，并让其他浏览器会话全部退出。密码和登录验证码仍可使用。如果丢失所有登录方式，请在管理机器上运行 `meridian panel --setup-link`：仅根令牌可签发的一次性恢复链接会替换密码、关闭 TOTP、移除全部通行密钥并结束全部会话。所有者凭证（包括通行密钥）不会进入快照；恢复后的服务需要新的设置链接。

| 在网页面板中修改 | 只读显示（用 `meridian` 修改） |
|---|---|
| **People**：添加用户、分享链接和二维码、启用或禁用、设置到期时间、流量限额和重置周期、设备上限、语言、私有备注和标签、重置流量、清除设备、签发新链接、删除（可选立即切断会话） | **Servers**：每个出口、中继和网关，其 agent 是否在线、是否已应用最新配置、版本、流量、每月额度和付款日期；在节点上点击 **Restart Xray** |
| **Settings**：页面和应用的默认语言、面向全员的通知、订阅格式预览，以及 **Privacy**（设备限制与每日用量历史） | **Revisions** 和 **Settings**：从你电脑上应用的每次变更，以及订阅标题、客户端路由策略和公开所有者联系方式 |
| **Tokens**：创建和撤销限定权限的 API 令牌、修改密码和登录验证码 | |

在网页面板和 CLI 中所做的修改是同一回事：`meridian client list` 也会显示在网页面板中添加的用户，而无论在哪边设置的状态、限额或到期时间，应用都不会撤销。用 `meridian client add` 添加的用户仍归 CLI 管理：网页面板可以修改他们的限额和状态，但删除他们或签发新链接时，会显示相应的 `meridian client` 命令，因为这些用户属于已审核的拓扑。每个人都能在自己的订阅 URL 打开连接页面，包括在网页面板中添加的用户。

网页面板不保存任何 SSH 密钥，因此被盗的会话无法修改你的服务器。每次登录在闲置 30 分钟后过期，最长 12 小时；创建令牌需要近期使用密码与 TOTP 或通行密钥确认身份。

人员或密码表单有更改时，在页面跳转、浏览器后退、退出登录或关闭对话框前，会显示 **离开** 和 **留下**。恢复原值后不再询问；保存只清除该表单的未保存状态。草稿仅保存在内存中，不写入浏览器存储。人员状态和用量在页面可见时每 30 秒刷新一次，保留正在编辑的输入及焦点。自动刷新不会延长所有者会话的空闲时限。

到期时间使用浏览器时区下的原生 **日期** 和 **时间** 字段编辑，保存前会显示准确的 UTC 时间。**+1 个月** 和 **+3 个月** 从当前时间与账户现有到期时间中较晚的一项计算草稿，保留 UTC 时刻；目标月份较短时，日期取该月最后一天。保存只修改已编辑字段。**无截止日期** 保存为 `null`；新设置的有限到期时间必须在未来且早于 2099 年。

订阅二维码和每个单独连接的二维码都提供 **下载 PNG** 和 **下载 SVG**，图片下方附有人员姓名及服务器组标题。浏览器支持图片剪贴板时会显示 **复制二维码图片**；如果复制被拒绝，则改为下载 PNG。图片在浏览器本地生成，不发送外部请求。

展开主要订阅交接区域下方的 **单独连接**，即可按订阅顺序复制指定连接或查看其二维码。该区域默认折叠，并列出 Xray JSON、Mihomo 和 sing-box 的指定格式链接。这些链接和二维码允许持有者以该人员的身份连接，请妥善保管。`meridian client show NAME --connections` 和 `GET /api/users/{username}/connections` 提供相同列表。API 需要 people/admin 权限或所有者会话；普通 read 令牌无法读取凭据。

操作结果会显示对应人员的姓名。启用已过期或流量额度已用尽的账户时，网页面板会说明仍有哪些条件阻止连接。切换页面后才返回的结果会保留当前页面。

### 服务器资源

**服务器**页面显示逻辑 CPU 数量、每个 CPU 的负载、已用及总内存、主机接收和发送速率，以及 Xray 进程内存与运行时间。展开**最近一天的资源情况**可查看小时平均值和峰值标记。阴影区间没有测量数据；实测零值仍显示为零。主机流量包含开销和其他服务的流量，**计入用户用量的流量**单独显示。资源采集不会改变设备或个人历史设置。

从服务收到并接受新报告起，样本在 180 秒内有效。旧代理显示**未报告**，采集失败显示**不可用**，旧报告显示**已过期**。图表覆盖当前 UTC 小时和此前 23 个区间，并注明网络测量时长。内存保留一小时的近期样本；168 个小时区间在重启后仍保留。保留时间与快照说明见[隐私](/zh/privacy/)。

`GET /api/nodes/{node_id}/capacity?hours=24` 需要 `fleet.read`，并接受一个 1–168 的整数 `hours`。未知或重复的查询选项会被拒绝。`GET /api/nodes/{node_id}` 和 `meridian fleet status --json` 包含相同的当前 `capacity` 数据；历史契约可通过 `meridian api schema node-capacity` 查看。

### API 令牌

为每个集成签发可单独撤销的令牌。在 **令牌** 中选择具体操作或兼容预设，设置到期时间，然后点击 **预览令牌**。服务会在 **创建令牌** 前显示实际权限和准确到期时间。修改选项后必须重新预览。令牌秘密只显示一次，不写入浏览器存储；响应不确定时会刷新元数据，并说明秘密无法找回。

在管理机器上：

```bash
meridian token capabilities
meridian token create "Account reader" --capability people.read --expires-days 30 --dry-run --json
meridian token create "Account reader" --capability people.read --expires-days 30 --yes --json
meridian token list --json
meridian token revoke TOKEN_ID --yes --json
```

权限相互独立。`people.read` 读取账户状态及隐私级别允许的设备记录，不提供连接密钥或私人备注。`people.private` 读取备注和标签；`people.credentials` 读取密钥、订阅链接及单独连接。`people.edit`、`people.reset`、`people.reissue`、`people.remove`、`people.devices`、`people.bulk` 分别允许账户编辑、周期流量重置、凭证轮换、账户删除、设备注册移除和凭证导入。修改响应与读取使用相同的凭证隐藏规则。

用户查询需要 `people.query` 和 `people.private`；批量预览、提交、回执和撤销需要 `people.batch` 和 `people.private`，因为这些视图包含标签。凭证导入仍使用独立的 `people.bulk` 权限。people/admin 预设保留原有的查询和批量操作权限；read/propose/approve 预设无法调用这些接口。

`commands.restart` 允许重启 Xray；其他维护命令需要 `commands.run`。使用 `cut_off: true` 删除或重新签发时，还需要 `commands.cut_off`。删除或重新签发拓扑中声明的用户需要 `fleet.apply`；面板所有者使用 CLI。`settings.edit` 修改隐私、通知和页面语言；已确认的拓扑设置需要 `fleet.apply`。`backup.read` 下载加密快照并读取备份公钥，不能添加密钥或解密快照；`backup.edit` 管理备份密钥和保存的服务器文件。提出变更、批准他人、批准自己及应用相互独立：`revisions.propose`、`revisions.approve`、`revisions.approve_own`、`fleet.apply`。批准自己的提案还需要普通批准权限。

面板可签发现有 read/propose/approve/people 预设，以及自身运营设置和重启操作的窄权限。拓扑管理和备份权限通过 CLI 或获授权的 API 令牌签发。创建需要近期密码与 TOTP 或通行密钥确认。令牌只能转授自身拥有的权限，不能比签发它的令牌更晚到期；已撤销或过期的签发者不能利用先前验证的请求创建新令牌。

新 API、CLI 和面板令牌默认有效期为 **90 天**。`expires_in_days` 接受 1–3650，也可指定未来 UTC 时间 `expires_at`。不过期必须明确选择 `never_expires: true` 或 `--never-expires`，单独的 `null` 会被拒绝。已有到期时间保持不变。兼容 `read`、`propose`、`approve`、`people`、`admin` 预设仅展开一次并保存等效权限，目录更新不会重新扩展已签发令牌。read 保留现有节点及端点访问（包括端点凭证），用户 UUID 和订阅令牌仍为空。自选令牌使用 `scope: null`，每条记录的 `capabilities` 列出实际权限。到期和撤销会使下一次请求返回 `401 unauthenticated`。包含凭证权限的令牌应像密码一样保护。

#### Prometheus 指标

通过现有控制入口使用专用 **`metrics.read`** 令牌抓取指标。在面板的令牌审阅中明确选择该权限，或使用下面的 CLI 命令。兼容预设（包括 `admin`）保留已冻结的权限，不会自动获得此权限。它只能读取节点汇总及服务运行指标，不能读取或修改账户。未列入令牌列表的根凭据可以签发抓取令牌。

```bash
meridian token create "Metrics scraper" --capability metrics.read --expires-days 90 --dry-run --json
meridian token create "Metrics scraper" --capability metrics.read --expires-days 90 --yes --json
```

签发 JSON 仅在 `data.issued.token` 中显示一次性秘密。仅将该值保存到 Prometheus 可读取的文件中，例如 `/etc/prometheus/meridian.token`。将 `CONTROL_PATH` 替换为 `meridian panel` 显示的私有面板路径，并使用控制服务器地址。HTTPS 使用现有 443 端口，请保持证书验证。

```yaml
scrape_configs:
  - job_name: meridian
    scheme: https
    metrics_path: /CONTROL_PATH/metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/meridian.token
    static_configs:
      - targets: ["198.51.100.10:443"]
```

`/metrics` 相对于私有控制路径，需要 bearer token，支持 GET/HEAD，并返回带有 `Cache-Control: no-store` 的 Prometheus 0.0.4 文本。不会新增监听端口。节点标签仅包含拓扑 ID 和固定状态；服务标签使用固定路由 ID、响应状态类别、延迟区间和任务名称。不导出人员、地址、设备、URL、私有路径或凭据。

文本包含 CPU、负载和内存，按计数周期记录的主机接收/发送计数与速率，Xray 内存和运行时间，以及现有账本中的每个节点用户流量总量。主机流量包括协议开销和其他服务。`meridian_node_capacity_status` 区分最新、过期、不可用和未报告状态；缺失或过期的数值会被省略。重置开始新计数周期；首个样本或未测量区间的速率不发布。请求计数和延迟直方图保存在有限内存中，重启后重置。任务启动、健康、错误和成功时间区分未运行与失败。`meridian_control_sqlite_bytes` 包含数据库、WAL 和共享内存文件。

#### OpenAPI 契约

通过 `GET /api/openapi.json` 或生成文件 `contracts/meridian/v1/admin/openapi.json` 读取 **OpenAPI 3.1**。发送 `Authorization: Bearer <token>`。必须拥有全部 `x-meridian-capabilities`；请求体条件由 `x-meridian-conditional-capabilities` 描述，凭证/私人字段及自我批准条件在 `x-meridian-extra-capabilities` 中。`x-meridian-scopes` 和 `x-meridian-capability-grants` 描述兼容预设。`x-meridian-credentials: true` 表示响应可能包含凭证，请保密且不要记录。agent 注册与协议、订阅、页面和面板认证使用独立接口。

| 操作 | 用途 |
|---|---|
| `GET /api/users/{username}/flow-blocks` | 按节点返回阻止新数据流的结果；不含连接地址或设备数据 |
| `GET /api/users` | 所有用户 |
| `GET /api/traffic` | 每个用户的流量总计 |
| `GET /api/nodes/{node_id}/capacity` | 当前服务器资源及有限的近期/小时历史；`fleet.read`，`hours=1..168` |
| `GET /api/health` | 服务的版本 |
| `GET /api/openapi.json` | 管理 API 契约 |
| `GET /api/events` | 有数量上限的本地事件分页 |
| `GET /api/attention` | 根据当前状态计算的账户、节点和服务器警告 |
| `GET /api/token-capabilities` | 操作、兼容预设及四种语言的权限说明 |
| `POST /api/tokens/preview` | 实际权限及到期时间，不创建令牌、事件或拉取备份 |
| `POST /api/tokens` | 使用 `name` 和一个 `scope` 或非空 `capabilities` 数组签发 |
| `GET /api/tokens` | 无秘密的元数据；`tokens.read` |
| `DELETE /api/tokens/{token_id}` | 撤销；`tokens.revoke` |
| `GET /api/users/{username}` | 账户状态；`people.read`，私人/凭证字段需单独授权 |
| `PATCH /api/users/{username}` | 修改账户字段；`people.edit` |
| `POST /api/users/{username}/reset-traffic` | 重置周期流量；`people.reset` |
| `POST /api/users/{username}/reissue` | 轮换凭证；`people.reissue` |
| `DELETE /api/users/{username}` | 删除；`people.remove` |

通过 `meridian api schema NAME` 查看 `token-create`、`token-record`、`token-issued`、`token-list`、`token-preview` 和 `token-capabilities` 模式。CLI 签发 JSON 包含 `data.preview`，签发后包含 `data.issued.token`；后续响应无法恢复秘密。独立不可变权限模型可在未来组合访问配置边界；目前这类边界不会被接受或静默忽略。

| 用户 API 端点 | 操作 |
|---|---|
| `GET /api/people` | 查找人员并返回带分类计数的游标页面 |
| `POST /api/people/bulk/preview` | 冻结用户名或查询，不写入更改或回执 |
| `POST /api/people/bulk/commit` | 原子提交 preview_id 与 preview_hash，列出变化人员 |
| `GET /api/people/bulk/receipts/{receipt_id}` | 读取不含备注或凭据的回执 |
| `POST /api/people/bulk/undo` | 十分钟内撤销一次未发生后续变化的字段 |
| `POST /api/users/preview` | 预览创建或修改账户的 UTC 日期，不保存；需要 people/admin 或所有者会话 |
| `POST /api/users` | 在一个事务中创建用户，同时设置 `username`、`status`、`expire_at`、`traffic_limit_bytes`、`traffic_limit_strategy` 和 `device_limit` |
| `PATCH /api/users/{username}` | 修改状态、到期时间、流量限额或重置策略、设备上限；未发送的字段保持原值 |
| `POST /api/users/{username}/reset-traffic` | 重置已用流量 |
| `POST /api/users/{username}/extend` | 发送 `{"days": N}`（1–3650），从当前时间和原到期时间中较晚者起增加天数；未设到期时间则从现在起算 |
| `POST /api/users/{username}/reissue` | 更换连接凭据并清除设备注册 |
| `POST /api/users/{username}/rotate-credentials` | 更换 VLESS UUID 和 Hysteria2 密码，保留订阅 URL 和登记；发送当前用户记录的 `expected_generation` |
| `DELETE /api/users/{username}` | 删除用户 |
| `GET /api/users/{username}/devices` | 按所有者选择的隐私级别读取设备注册 |
| `DELETE /api/users/{username}/devices` | 清除全部注册 |
| `DELETE /api/users/{username}/devices/{device_key}` | 删除一项注册 |

先从 `GET /api/users/{username}` 读取 `credential_generation`，再以 `people.reissue` 权限向 `POST /api/users/{username}/rotate-credentials` 发送 `{"expected_generation": N}`。过期代次返回 `409 credentials_changed`，防止响应不确定时重试而再次更换密钥。响应包含 `user`、`subscription_preserved: true`、`flow_blocks` 和 `commands`；用户字段遵循与读取相同的凭证及私人数据权限。添加 `"cut_off": true` 需要 `commands.cut_off`，并排队执行与重新签发相同的版本门控完整终止操作。轮换配置文件声明的用户还需要 `fleet.apply`；所有者会话使用 CLI。

People 令牌无法删除服务器配置文件中声明的用户，也无法为他们重新签发连接凭据。API 会返回 `409 declared_user` 和需要运行的 CLI 命令。已经批准或失败的应用仍声明该用户时，这项限制同样有效；未经批准的提案不会声明任何用户。状态、限额、到期时间和设备上限仍可修改。

重新签发和删除接受可选的 `{"cut_off": true}`；凭据轮换会将该选项与 `expected_generation` 一起发送。按 `x-meridian-conditional-capabilities`，这需要 `commands.cut_off`；admin 预设包含此权限。请逐个节点检查范围：核心重启影响所有 Xray 入站与桥接连接，入站重置只影响列出的入站，失败时会回退到整个核心重启。重新签发与删除未选择切断时保留普通响应，选择后包含排队命令；凭据轮换始终返回命令列表与回执。

## 连接页面上的所有者联系信息

`meridian fleet contact` 显示已审阅的服务器配置中保存的联系信息。先审阅公开说明和联系链接：

```bash
meridian fleet contact --text "需要帮助时请联系我" --link mailto:help@example.com --dry-run --json
meridian fleet contact --text "需要帮助时请联系我" --link mailto:help@example.com --yes
meridian fleet contact --text "" --link none --yes
```

说明为一行纯文本，最多 120 个字符。链接只能使用 `https:`、`tg:` 或 `mailto:`；Meridian 会验证并规范化链接。两个字段相互独立：未指定的选项保留原值，`--text ""` 清除说明，`--link none` 清除链接。使用 JSON 更改时需要 `--yes`；试运行和拒绝确认都会保留原配置。导出和导入会保留 `delivery.owner_contact.text` 与 `delivery.owner_contact.link`。网页面板设置显示联系信息及用于更改的 CLI 命令。

连接页面的帮助区和支持页面会显示公开说明及 **联系所有者** 链接，并支持全部四种界面语言。链接仅在点击后打开；页面资源均在本地。当联系信息已设置时，支持页面会保持显示它，并提供 **打开连接页面**。应用通过 `support-url` 接收链接；没有链接时，该响应头保留本地支持页面的地址。个人连接页面地址保持不变。

停用的订阅可将说明附加到状态提示，但所有提示都必须保持在 100 个字符以内。较长说明不会更改这些提示，仍会显示在页面上。

## 管理者通知

```bash
meridian fleet notice set "{name}，今晚将进行维护" --until 2027-01-01T00:00:00Z --for person --yes
meridian fleet notice show --for person --json
meridian fleet notice clear --yes
```

控制服务保存一条通知，在连接页面横幅以及支持 `announce` 订阅头的应用中显示。请输入单段纯文本，最多 500 个 Unicode 字符。仅允许 `{name}`、`{expires}`、`{days_left}`、`{used}` 和 `{quota}`；未知或不完整的大括号会被拒绝，并列出允许的字段。每位用户的值只替换一次，然后以 UTF-8 Base64 编码，并加上应用所需的 `base64:` 前缀。替换后的通知最多 1,024 个字符。

`--until` 接受未来的 UTC 时间或日期（该日期在 UTC 的最后一秒）；省略则永久有效。通知到期后不再发送。`--for NAME` 在确认预览中使用真实账户信息；省略则使用示例值。`show --for NAME` 读取保存的通知并预览。保存和清除都需要确认，JSON 模式下需要 `--yes`。网页面板设置提供同样的编辑器和预览确认。

这些设置由管理者维护，经过审核的应用操作会保留它们。`GET /api/notice?for=NAME` 需要 read 权限；`PUT /api/notice` 需要 admin 权限或管理者会话。发送 `{"notice":{"text":"你好，{name}","until":null},"for_user":"person","confirm":false}` 可预览；将 `confirm` 设为 `true` 可保存，将 `notice` 设为 `null` 可清除。预览不会写入存储或注册设备。加密快照包含通知；`notice.changed` 事件记录修改者，不记录正文。不会发送推送或即时消息通知。

## 默认语言

```bash
meridian fleet language ru --yes --json
meridian fleet language auto --yes --json
meridian client set person --language fa --json
meridian client set person --language default --json
```

默认值为 `auto`：连接页和帮助页跟随每个人的浏览器语言，应用中的订阅状态提示保持英语。选择 `en`、`ru`、`fa` 或 `zh`，可设置页面的初始语言和应用提示语言。`meridian fleet language` 读取设置；传入语言代码会立即保存，JSON 模式下更改设置需要 `--yes`。网页面板设置提供相同选项。

使用 `client set NAME --language CODE`，或在网页面板的用户设置中选择语言，为一个人单独设置。`--language default` 恢复全局默认值；不提供该选项则保留现有设置。client list/show 的 JSON 输出包含可为 `null` 的 `language` 字段。

页面先使用用户保存的语言选择，然后使用账户设置，再使用全局默认值。只有 `auto` 才跟随浏览器；不支持的浏览器语言会回退到英语。每个人都可随时通过页面上的语言按钮切换语言。

这些服务设置在经过审核的应用操作和加密备份中保留。`GET /api/language` 需要 read 权限，例如返回 `{"language":"auto","changed":false}`。`PUT /api/language` 使用 `{"language":"ru"}` 或 `{"language":"auto"}`，需要 admin 权限或所有者会话。`PATCH /api/users/NAME` 在 people/admin 权限下接受 `{"language":"fa"}` 或 `{"language":null}`。状态代码仍为 `active`、`disabled`、`limited` 和 `expired`；用于解释状态的连接条目无法连接。

## 预览应用收到的订阅格式

```bash
meridian client preview person --user-agent "Hiddify/2" --accept application/json --json
meridian client preview person --user-agent "Hiddify/2" --suffix json --json
```

预览解释所选格式及首个匹配规则：明确指定的 URL 后缀、User-Agent 应用类型、`Accept: text/html` 或默认 Base64 响应。它列出包含的协议和条目，并解释每个被省略的连接：不受支持的协议或指纹副本、已停用端点、账户状态说明，或缺少主连接。预览与实际响应使用同一选择器和过滤规则，并区分浏览器 HTML 页面与 `/info` 账户 JSON。

有两款应用会在订阅末尾多出一个无法连接的说明条目，使用该用户的语言：低于 1.9.17（首个支持 XHTTP 的版本）的 v2rayNG 会被提示更新，已停止维护的桌面版 NekoRay（继任者是 Throne）会被提示改用 Throne。判断只读取该次请求的 User-Agent：不保存、不记录，设备上限、隐私级别和格式选择都不变。已暂停或没有连接的订阅只显示状态条目。预览会把 `stale_client`（`outdated` 或 `abandoned`）作为单独原因报告，仍不回显 User-Agent。

响应头经过脱敏：URL 被替换，编码后的配置名称和通知只保留 `base64:` 前缀。预览不返回订阅正文、URI、密钥、私有备注或标签。User-Agent 最多 512 个 Unicode 字符，Accept 最多 256 个字符，两者均为单行。输入仅用于本次请求，不保存或回显，不注册设备，也不触发备份写入。

`POST /api/subscription/preview` 接受 read 或 people 权限，或管理者会话。配置了访问配置后，需要 people/admin 权限或管理者会话。发送 `{"username":"person","user_agent":"Hiddify/2","accept":"application/json","suffix":""}`，省略的请求头默认为空字符串。网页面板设置提供同样的表单，不接受 HWID 或设备请求头。对于通过 API 管理的端点，`enabled: false` 会将保存的端点从订阅中排除，并显示原因为 `disabled_endpoint`；默认值为 true，序列化时省略。经过审核的端点应用操作会恢复拓扑规定的连接提供方式。

订阅预览还会显示已审阅的应用规则、匹配条件及拒绝结果。设置中的规则列表为只读；测试时将用户留空可检查服务器组默认。通过已审阅的配置导入修改 `delivery.response_rules`。类型字段、响应头限制和策略优先级见[路由指南](/zh/split-routing/)。

## 审核批量更改

使用 Tab 和空格选择行，Shift+空格扩展当前页的范围。**选择全部匹配人员**包含其他页面。更改筛选会清除选择。选择操作，审核每人的更改前后值、警告和将更改的节点文档数量。预览每页显示 100 人，将具体人员与版本冻结十分钟，新匹配人员不会加入。任何人员、限制执行结果或节点配置变化都会拒绝整个提交，并列出变化的人员。更改与回执在同一事务中写入，节点文档仅为整个批次生成一次。

批量更改到期时间时，选择 **无截止日期** 才会清除到期时间。取消该选项后，日期和时间必须有效、在未来且早于 2099 年；空白或无效值会阻止预览，不会清除任何人的到期时间。

有效期按整天或日历月从当前时间与原到期时间中较晚者延长，并处理短月份。等待首次使用的人员只增加时长，不激活；月数换算为从预览 UTC 日期到相应月份日期的天数。可设置或清除到期时间、配额与重置策略、标签、设备限制（继承／不限／N）或续期，也可启用、停用、重置周期流量或删除。省略的字段保持原值，重置策略与续期次数上限可单独更改。现有节点协议每个受管入站最多支持 10,000 名启用人员，预览拒绝超过此限制的更改。

持久回执保存操作、参数、计数及各人的结果。如果人员及限制执行结果未变化，可在十分钟内撤销一次可逆字段，重启服务后也可撤销。流量重置、删除和会重置流量的计划更改不可逆，提交前明确提示。重置保留累计流量。回执不含私人备注、连接凭据、客户端地址或设备标识符；事件仅包含操作与计数及现有不含私人内容的单人事件。`topology_intent.access.users` 中的人员可接受服务字段更改；删除时返回确切的 `meridian client remove USERNAME` 命令以单独审核，服务批次不会删除他们。

`GET /api/people` 需要 people/admin 或所有者会话，返回 `users`、`total`、`population`、分类 `facets` 与 `next_cursor`。查询键：`q`、`status`、`tag`、`profile`、`expires`、`quota`、`never`（0/1）、`attention`、`lifecycle`、`sort`、`order`、`limit`（1–500）、`cursor`。继续游标时保持相同筛选和排序。原有 `GET /api/users` 保留。查询响应不含凭据与私人备注；CLI 的 JSON 通过现有授权单人读取接口保留备注。

批量停用、删除或到期变更使用户失去访问权限时，服务会为每位用户和每个受影响节点记录与单个用户操作相同的流阻断回执。连接密钥和订阅链接保持不变。节点确认已应用配置后才会阻断新流；已经开始的传输和 UDP 会话可能继续。删除用户记录后，回执仍保留，以便后续节点报告确认阻断。

## 访问配置

任何非 `default` 成员关系都需要审核，即使服务中的访问配置映射和声明指纹都已缺失。预览和设置写入会拒绝遗漏用户仍所属配置的映射。请通过已审核的修改恢复缺失配置，或先将相关用户分配到其他配置。

配置授权属于已审核的拓扑；用户的配置成员关系由服务保存并归运营者管理。内置 `default` 配置允许访问所有出口和路径，包括未来新增的连接。用户可属于多个配置，权限取并集。未定义命名配置的部署保持原有节点文档和订阅输出。

访问配置需要 Meridian 控制服务。Remnawave 部署请先运行 `meridian migrate`；新增、导入或应用访问配置会在修改服务器或面板之前被拒绝。

单人分配命令 `client set --profile` 和批量分配命令 `client bulk set-profiles` 也会在 Remnawave 上被拒绝，且拒绝发生在查询用户或联系服务之前。这包括 `--dry-run` 和分配 `default` 配置。

使用 `meridian profile add|set` 创建或替换授权，`profile list` 显示授权及成员数量。支持 `exit:ID`、`gateway:ID`，以及编译器的稳定 `inbound:...` 和 `endpoint:...` 引用。未知或已移除的引用会在审核时被拒绝。网关与出口之间的内部桥接入站不能授权给用户。授权公开连接实际授权其认证入站，因此通向同一入站的其他公开连接也会被授权。`--empty` 可创建不允许任何连接的配置。

使用 `client set person --profile limited --yes` 替换配置列表，为每个需要保留的配置重复 `--profile`。选择 `default` 可恢复完整访问。配置成员变更单独审核，与账户限额、备注及语言分开修改。控制台的访问配置卡显示已授权连接及授权原因，保留未保存的选择，并打开相同的固定预览。批量操作 **设置访问配置** 显示新增和失去的入站访问；提交时若用户或拓扑发生变化，整个操作会被拒绝。

单人和批量访问配置预览、`--dry-run` 与撤销试运行，都会先解析每位用户的有效路由策略。有默认映射的配置若选择不同策略，且用户没有已保存的个人覆盖，预览会拒绝更改。`access.creation_settings` 中的初始成员关系也会在配置验证与编译阶段检查默认策略冲突，在生成计划或应用之前拒绝。

CLI 无法读取用户时会报告服务访问错误。配置变更导致访问丢失的警告，每次审核只显示一次。

批量撤销在预览或写入前都会检查保存的用户版本、授权和节点。访问权限已变更或配置已删除时，会拒绝整个撤销并列出受影响用户，`--dry-run` 也一样。撤销预览显示反向的新增、失去访问及相应的彻底断开范围。API 可向 `POST /api/people/bulk/undo` 发送 `{"receipt_id":"...","dry_run":true}`，获得预计的 `undo_people`，不修改数据库、不记录事件，也不拉取备份。未保存访问前提条件的旧回执需要重新进行批量审核。

失去授权会从所有订阅格式、独立连接链接和连接页面移除该连接，同时从节点入站删除该用户。节点应用新版本后，现有的逐用户流阻断机制会阻止新连接及已打开会话中的新流。连接密钥、订阅 URL 和设备注册保持不变。已经开始的传输及 UDP 关联可能继续。预览列出受影响节点及彻底断开的范围：VLESS 会重启 Xray，让该节点上的所有入站和桥接连接重新连接；入站重置只影响所列入站，失败时可能退回整个 Xray 重启。经过审核的 `meridian node restart NODE --core --yes` 会通过让所有人重新连接来结束该节点上的现有传输。

`GET /api/users/{username}/cut-off` 需要 `people.read` 与 `people.private`。配置变更导致用户失去的入站访问，只要流阻断回执仍未确认，就会保留在预览中，即使账户仍处于活跃状态。后续执行 `--cut-off` 也会重置这些入站。

每条尚未确认的回执都会将其入站纳入预览，与当前记录的原因无关。轮换连接密钥、重新签发链接、导入时同时替换密钥与配置归属，或在缩小访问范围后重新启用账户，都不会移除已失去的入站。只有确认阻断，或恢复同一密钥的访问后，这些入站才会从切断会话的预览中移除。

仍有成员的配置无法删除，错误会报告人数。请先将用户移到其他配置。普通 apply 不会覆盖成员关系。快照保留所有用户的配置；替换式用户导入在省略 `profiles` 时保留现有成员关系。部署导出将已声明用户的当前配置保存为仅用于创建的设置，新部署可据此创建用户，已存在用户继续保留运营者的选择。只在控制台创建的用户仍保存在服务快照中。

恢复会保留历史访问配置使用记录，并在暂存数据库或联系目标服务器前拒绝与本地拓扑不匹配的快照。存在指纹时比较已保存的声明；没有指纹时，编译后的授权映射必须完全一致。请先恢复匹配的状态，再运行 `meridian apply` 审核所需的已保存修改；`--force` 无法跳过匹配检查。参见[恢复](/zh/recovery/)。

`plan` 和 `apply` 会同时审核服务中已有的配置与拟应用的拓扑。移除最后一个配置也必须经过审核和并发变更校验；只要仍有成员，就会被拒绝。

所有应用路径，包括 `setup --intent`、通过 `--restart` 重新设置，以及 `client add`、`fleet contact` 等命令，都会在保存拓扑或写入服务器前，将访问配置 ID 和授权声明与服务核对。已保存但尚未应用的访问配置修改不能随其他命令应用：请使用 `meridian profile` 或 `meridian fleet import` 修改配置，或运行 `meridian apply` 审核已保存的修改。更换协议或中继伪装名称可能改变同一个 `exit:` 或 `gateway:` 授权所包含的连接；这些变化由拓扑命令自身的审核覆盖。

新部署可以通过 `setup --intent` 引入访问配置。只有服务尚无声明指纹、命名访问配置和非 `default` 成员关系时，才允许初始化。如果服务已有访问配置但没有声明指纹，请先运行 `meridian apply` 审核，再使用其他拓扑命令。

Meridian 会记住部署曾使用命名访问配置。保存含有访问配置的拓扑会记录这一事实，删除最后一个配置也不会清除记录。判断服务不可用时是否仍需审核，也会考虑当前的拓扑修改。

如果另一台管理设备的修改导致应用被拒绝，本地拓扑和已忘记服务器的记录会保持原样。应用获准后，即使后续部署步骤失败，Meridian 也会保留访问配置使用记录。

`fleet import` 使用相同的访问配置检查，即使保存的拓扑和导入文件都没有访问配置。如果无法读取服务设置，`plan` 仍会返回已编译的计划，标记不可用的证据，并以代码 `3` 退出。只有首次部署，或保存的代次证据能证明未记录访问配置时，应用或导入才可跳过此审核。其他情况需要恢复控制服务访问后重试。导入预览需要服务的实时证据。

apply 会先保存入站及连接端点引用，再启用配置授权，最后创建账户。同一次 apply 中新增用户不会使自己的访问审核失效，包括移除最后一个配置时。

成员关系属于运营者的私有数据：`people.private` 允许在用户和配置版本响应中读取 `profiles`。实际访问权限和成员数量需要 `people.read` 与 `people.private`；授权审核需要 `fleet.apply` 与 `people.private`。账户编辑使用 `people.edit`，批量分配使用 `people.batch` 与 `people.private`。订阅格式预览需要 `people.read`，启用配置后还需要 `people.private`。people/admin 预设包含所需私有权限；其他预设收到空的 `profiles` 列表，初始成员关系也会隐藏。配置仅保存成员关系，不保存客户端 IP、设备信息或连接观察数据。

JSON 审核和部署导出会保留 `token`、`secret` 等有效配置 ID 和用户名，包括嵌套的授权及创建设置列表。凭证字段仍会隐藏。

`token`、`secret` 和 `api_key` 等用户名也可用于修订提案和经过审核的拓扑应用。其创建设置和显示名称仍会接受常规的敏感数据检查。用户名必须在 `access.users` 中声明；在 `access.creation_settings` 中对应的值必须是映射对象，在 `access.display_names` 中则必须是字符串。否则，类似敏感字段名称的键仍会被拒绝。

| 操作 | 含义 |
| --- | --- |
| `GET /api/access-profiles` | 已审核授权与私有成员数量；`people.read` 与 `people.private` |
| `GET /api/users/{username}/access` | 含 `granted_by` 的实际入站与连接；`people.read` 与 `people.private`，不含密钥 |
| `POST /api/access-profiles/preview` | 预览 `access_profiles` 映射及未来 `inbound_refs`；`fleet.apply` 与 `people.private`，不写入数据 |
| `PATCH /api/users/{username}` | 使用 `people.edit` 设置非空 `profiles` 列表；省略字段会保留成员关系 |

固定分配请向 `POST /api/people/bulk/preview` 发送 `{"usernames":["person"],"action":{"action":"set-profiles","profiles":["limited"]}}`，再提交其 ID 和哈希。配置预览返回 `access_hash`；`PUT /api/settings` 通过 `expected_access_hash` 接收该值，若成员关系已变化则拒绝应用。可选的 `existing_inbounds` 在升级旧节点规格时提供当前节点和标签的引用。预览请求数据不会持久保存。

设置通过 `access_profile_declarations_hash` 保存规范化拓扑访问配置声明的 SHA-256。审核后的写入使用 `expected_access_hash` 设置该值；保持原声明的写入发送 `expected_profile_declarations_hash`，由服务在同一设置事务中校验。删除最后一个访问配置后，仍保留空声明的指纹。`GET /api/access-profiles` 返回 `declarations_hash`，并在存在非默认成员关系时返回 `has_non_default_memberships: true`；读取权限保持不变。未提供任一校验条件时，替换已有指纹或授权映射会被拒绝。

### 私有个人路由

`meridian client routing person` 显示有效策略。运行 `meridian client routing person work --dry-run --json` 审阅，再通过 `meridian client routing person work --yes --json` 保存个人覆盖；`meridian client routing person --inherit --yes` 清除覆盖。面板的用户卡片使用同一服务审阅，草稿只保存在内存中。个人覆盖优先于匹配的应用响应规则，其次是访问配置默认和服务器默认。应用配置和重新签发凭证保留覆盖；加密快照保存它，删除用户会移除它。选择会在应用刷新订阅时生效，不修改账户或节点访问权限。

读取需要 `people.read` 与 `people.private`，分配还需要 `people.delivery`。`delivery.read` 用于读取已审阅目录。已签发令牌保留原有固定权限；分配策略需要签发包含新权限的令牌。没有私有权限时覆盖被隐藏，订阅、事件和已审阅配置均不包含覆盖字段。预览不写入数据。确认需要 `review_hash`；覆盖、成员关系或目录改变后须重新预览。

| 操作 | 含义 |
| --- | --- |
| `GET /api/delivery-policies` | 已审阅目录与访问配置默认；`delivery.read` |
| `GET /api/users/{username}/delivery-policy` | 私有覆盖及有效策略；`people.read` 与 `people.private` |
| `PUT /api/users/{username}/delivery-policy` | `{ "policy": "work", "confirm": false }` 返回预览；确认时提交 `review_hash`；需要 `people.delivery`、`people.read`、`people.private`。显式 `policy: null` 表示继承。 |

| `POST /api/response-rules/test` | 临时应用匹配测试；`delivery.test`，指定用户时还需 `people.read` 和 `people.private` |
| `POST /api/delivery-policies/explain` | 离线首次匹配解释；`delivery.explain`，指定用户时还需 `people.read` 与 `people.private` |

## 预览与解释路由

`meridian client preview person --suffix singbox --json` 与面板订阅预览显示有效策略、选择来源、各格式的有序规则与 DNS，以及省略的功能和原因。可选目的地字段解释当前格式。Xray 预览区分缺少指定出口的单连接配置与指纹配置。规则编号在各格式之间一致：局域网规则最先，其次为自定义规则，最后为内置模式的其余规则。同一编号可能编译为客户端文件中的多条规则。

```bash
meridian fleet routing set work --dry-run --json
meridian fleet routing explain chat.example.test:443 --person person --format singbox --json
meridian fleet routing explain 198.51.100.10 --format xray-json --port 443 --network tcp --protocol other --json
```

`fleet routing explain` 读取服务当前已应用的设置及可选个人私有覆盖，返回首次匹配的规则与动作，例如“此目的地通过出口 west，因为规则 2 匹配”。省略 `--person` 可解释服务器默认策略。`--format` 选择 Xray JSON、Mihomo/Clash、Stash、sing-box 或 Base64。默认假设 TCP 和普通流量；`--protocol bittorrent` 提供模拟的协议检测结果。不发送 DNS、网络或可达性查询。更早的规则需要未知端口或缓存中缺失的固定版本地理数据时，结果显示未知。Xray 使用 `IPIfNonMatch` 先检查域名；最终 TCP/UDP 规则将未匹配的域名送入隧道，路由选择无需本地 DNS 查询。IP 规则匹配以 IP 地址连接的目的地。应用选择与服务器可达性可能改变自动组实际使用的连接。

| 格式 / 应用 | 目的地规则 | DNS | fake-IP | 指定出口 | BitTorrent |
| --- | --- | --- | --- | --- | --- |
| Xray JSON 应用 | 全部八类匹配 | DoH、普通 DNS | 省略 | 仅限当前配置 | 尽力检测 |
| Mihomo / Clash Meta | 全部八类 | DoH、DoT、普通 DNS | 支持 | 仅主连接 | 省略 |
| Stash | 全部八类 | DoH、DoT、普通 DNS | 原生模式，省略自定义 fake-IP | 仅主连接 | 省略 |
| sing-box / Hiddify | 内联域名、CIDR、端口、TCP/UDP、私有 IP | DoH、DoT、普通 DNS | 支持 | 仅受支持连接 | 省略 |
| Base64 链接应用 | 无规则 | 无设置 | 不支持 | 无基于规则的选择 | 不支持 |

服务仅从缓存中读取通过 Meridian 固定哈希验证的 Xray 压缩包，不会因解释路由而开始下载。客户端不会收到远程规则集。Xray 将已配置的公共 DNS 请求通过自动组发送，同时保留局域网 DNS 直连；其他客户端使用可表示的 DNS 设置。不支持的传输不会转换为普通 DNS。原有内置模式的字节内容和默认配置的黄金语料响应保持不变。

`POST /api/delivery-policies/explain` 接受 `destination`、可选 `person`、`format`、`port`、`network`、`protocol`。需要 `delivery.explain`；指定用户时还需要 `people.read` 和 `people.private`。订阅预览中的策略详情或目的地解释需要私有权限，较低权限仅返回原有元数据。连接页面的 `/info` 可包含 `user.deliveryCapabilities`：格式名称、支持标志与省略代码，不含策略名称、个人覆盖、成员关系、目的地或连接详情。页面仅在内存中保留这些新鲜数据。
