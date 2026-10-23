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

- 两个步骤：安装为其平台推荐的应用（首选 Happ，其次是 FlClashX 和 Clash Mi，桌面端还有 Throne），然后轻点一下添加连接
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

这会打印连接页面和订阅 URL，以及状态、已用流量和客户端最近在线时间。如果连接页面缺失（例如上传失败），可以重新发布：

```
meridian client show alice --repair-page
```

## 列出客户端

```
meridian client list
```

显示所有客户端的状态、流量、创建时间和最近在线时间。

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

Remnawave 会为该客户端生成新的订阅 ID 和新的密钥。旧的订阅 URL 和连接页面返回 404，服务器拒绝旧密钥。Meridian 删除旧页面、发布新页面，并打印新的页面和订阅 URL，供你发给该客户端。他们需要重新导入一次新链接：应用无法刷新一个已经不存在的订阅 URL。面板为该客户端保存的其他内容都保持不变：状态、流量限额和用量、到期时间、描述以及 HWID 设备数限制。

HWID 设备数限制保存在面板中，而不是链接里。重新签发后，已注册的设备仍留在该客户端的 HWID 列表中并继续占用名额，尽管它们的旧链接已无法连接。请在面板中打开该客户端，删除你不认识的设备，让限制只计算客户端本人的设备。Meridian 不会额外添加领取凭证或设备绑定。

使用 `--json` 时请传入 `--yes`。新链接只出现在该命令的输出中。

## 删除客户端

`meridian client remove NAME` 永久删除客户端：他们的链接停止工作，他们的连接页面被删除。集群至少保持一个客户端。不要直接在 Remnawave 面板中删除用户 — Meridian 在下次应用时重新创建拓扑中保留的任何客户端。

## 订阅链接

订阅 URL 看起来像 `https://198.51.100.10/<page-path>/sub/<id>`。它们位于连接页面路径下；Remnawave 管理面板位于共享链接永远不会暴露的单独密钥路径上。

每个订阅都会要求应用每 6 小时刷新一次（通过 `profile-update-interval` 响应头，Happ 和 Clash Verge 等应用会遵循它），因此当你添加出口或中继时，客户端会在几小时内自动获取，无需发送新链接。忽略该响应头的应用会按各自的周期刷新；在应用中手动刷新则会立即生效。

应用中的支持按钮会打开该用户的连接页面，其中带有“无法连接？”排查指南。若要把用户引导到其他地址，可以在面板的订阅设置中配置你自己的 `support-url` 响应头；Meridian 会保留它。

### 已测试的应用和引擎

同一个订阅 URL 会向每个应用提供它能读取的格式。Meridian 的每次变更都会运行一个多节点测试实验室，从真实的 Remnawave 面板获取每种格式，并用这些应用所基于的引擎进行检查：

| 格式 | 应用 | 测试的引擎 | 实验室检查内容 |
|--------|------|---------------|---------------------|
| Xray JSON | Happ、v2rayNG、v2rayN、Streisand、V2Box | Xray 26.7.28 | 流量通过每个入口以及自动故障转移；错误的密钥会被拒绝 |
| Mihomo | FlClashX, Clash Mi | Mihomo 1.19.32 | `mihomo -t` 加载配置文件、其规则及其回退组 |
| sing-box | sing-box (SFA, SFI), Karing | sing-box 1.14.3 | `sing-box check` 加载配置文件，其自动组是默认路由 |
| Base64 链接 | 其他 Xray/V2Ray 应用（v2RayTun、Shadowrocket） | — | 每个链接都能解析，并指向与其他格式相同的服务器和密钥 |

应用会自行发布引擎更新，因此应用运行的引擎可能比测试的版本略旧或略新。

### Xray 应用中的 BitTorrent 路由

Xray JSON 订阅（包括 Happ）会将识别到的未加密 TCP BitTorrent 对等握手通过设备上的 `DIRECT` 发送。对等方看到的是客户端的公网 IP，而不是 VPN 出口 IP。运行 `meridian apply` 后在应用中刷新订阅；应用必须保留收到的路由和 sniffing 设置。当应用把 TUN 流量交给这些 Xray 入站时，同样适用。

现有规则顺序保持为：私有和俄罗斯目的地 DIRECT、国外 UDP/443 阻断、BitTorrent DIRECT，最后是自动 VPN 路由。Sniffing 仍使用 route-only：`enabled: true` 启用协议识别，`destOverride` 控制基于域名的路由。不要把 `bittorrent` 加入 `destOverride`，Xray 会拒绝该值。

这只是尽力让已识别的 TCP 流量绕过 VPN，并非排除整个种子应用。UDP DHT/uTP、加密对等握手和 tracker 流量无法被可靠覆盖，仍可能进入隧道（UDP/443 仍受现有阻断规则限制）。保留 Torrent Blocker 作为服务器端的额外保护；到达它的流量仍可能触发 IP 封禁。要让全部种子流量避开 VPN，请在客户端支持时使用按应用排除或独立连接。Mihomo、sing-box 和 Base64 订阅不会新增此规则。

## 客户端数据存放在哪里

客户端账户（密钥、状态、流量）保存在控制服务器上的 Remnawave 面板数据库中。Meridian 管理的客户端列表保存在本地 `~/.config/meridian/cluster.yml` 的拓扑配置中。客户端命令调用面板 API，不需要 SSH；发布连接页面则会通过 SSH 连接控制服务器。

## Web 面板

[Remnawave](https://remna.st/) 管理面板可在浏览器中显示流量、用户和节点，你可以随时使用。`meridian panel` 会打印面板 URL 和管理员用户名；`meridian panel --show-password` 还会一并打印密码。

Meridian 与面板明确分工，因此你的修改不会被覆盖：

| 由 Meridian 管理（下次 `apply` 会恢复为 Meridian 的版本） | 由你自由修改（`apply` 绝不会改动这些） |
|---|---|
| 节点、配置文件、入站、发布的 Host、内外小队（squads） | 每个客户端的状态：启用/禁用、流量限额、到期时间、HWID 设备限制、备注描述 |
| 订阅模板以及由 Meridian 维护的设置（标题、刷新间隔、向支持的应用下发 Xray JSON、未自定义时的支持链接） | 你在面板中手动创建的用户 |
| 受管客户端的存在与否、名称和所属小队 | 通知、插件、IP 限制及其他面板设置 |

在改动任何内容之前，`meridian plan` 会显示受 Meridian 管理的资源是否存在差异。你在面板中手动添加的用户绝不会被 Meridian 列出、修改或删除；如果要为某人创建由 Meridian 管理的链接和连接页面，请使用 `meridian client add`。

### 节点插件

Remnawave 的节点插件（Ingress Filter、Egress Filter、Torrent Blocker、Connection Drop）是可选功能，在面板中配置。如需使用，请在管理界面打开 **Node Plugins**，创建插件配置，开启所需的插件并关联到你的节点。Meridian 已经以插件所需的 `NET_ADMIN` 权限运行每个节点，并且从不修改或删除你的插件配置。

插件会在宿主机上写入 nftables 规则，因此上游还要求 Linux 内核版本为 5.7 或更高；在更旧的内核上插件会静默失效。`meridian check` 会在每个出口和网关上将其报告为 **Node plugin readiness**，并对旧内核给出警告（请升级到 Ubuntu 22.04+ 或 Debian 11+）。如果你不使用节点插件，可以忽略该警告。

Torrent Blocker 通过流量嗅探来识别 BitTorrent，Meridian 会在其生成的每个入站上开启嗅探（仅用于路由，因此不会改写目标地址）。在面板中开启该插件，它就会在每个出口上生效。

### 将机器人连接到面板

社区机器人（例如告知用户剩余流量的 Telegram 机器人）直接与 Remnawave API 通信。请为每个机器人单独创建令牌，只授予它所需的最小权限：

1. 打开面板（`meridian panel` 会打印 URL），进入 **Remnawave Settings → API tokens**（Remnawave 设置 → API 令牌），选择 **Create API token**。
2. 以机器人名称为令牌命名，并将 **Expires in (days)** 设为你确实会轮换的期限，例如 30 或 90 天。
3. 从 **Read only** 预设开始，或选择单个端点，只授予机器人用到的权限。面板只显示一次令牌；请将其与机器人的密钥一起保存。
4. 将机器人指向 `meridian panel` 给出的面板 URL，并以 `Authorization: Bearer <token>` 发送令牌。API 路径位于该 URL 之下，例如 `https://198.51.100.10/<secret-path>/api/users/by-username/alice`。该 URL 包含面板的秘密路径，因此要像令牌一样严格保密。

权限范围命名为 `resource:endpoint`；`resource:read` 授予某资源的所有读取端点，`resource:write` 授予所有写入端点。典型的只读机器人需要：

| 范围 | 端点 | 返回 |
|---|---|---|
| `users:by-username` | `GET /api/users/by-username/{username}` | 单个用户的状态、已用流量和限额、过期时间 |
| `users:by-short-uuid` | `GET /api/users/by-short-uuid/{shortUuid}` | 同上，通过用户订阅链接中的 ID 查找 |
| `users:list` | `GET /api/users` | 所有用户，分页 |
| `system:stats` | `GET /api/system/stats` | 面板范围内的用户和流量总计 |
| `nodes:list` | `GET /api/nodes` | 每个节点的状态和流量 |

仅当机器人需要修改账户时才添加写入范围，例如用于续期的支付机器人的 `users:extend`（`POST /api/users/{id}/actions/extend`）。状态、限额和过期时间由你自行修改（见上表），因此下一次应用会保留机器人的修改。机器人不得重命名或删除 Meridian 的客户端：下一次应用会重新创建它们。

不要把 Meridian 自己的令牌（令牌列表中的 `meridian-provisioner`）交给机器人。它拥有面板的完全访问权限，而且几乎不会过期，机器人所在主机一旦泄露，所有节点、密钥和用户都会暴露。单独的令牌把损失限制在机器人能做的范围内，会自行过期，并且删除它不影响 Meridian。请不要动 `meridian-provisioner`；所有客户端命令和应用命令都使用它。
