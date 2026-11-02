---
title: 客户端路由
description: 选择 ru-split 或 full，保留私有网络直连，并在应用前审核每次更改。
---

## 问题背景

Meridian 默认开启**服务器端地理封锁**：出口会丢弃发往俄罗斯域名（`geosite:category-ru`）和俄罗斯 IP 段（`geoip:ru`）的流量。这样可以避免你的 VPS IP 出现在俄罗斯服务的日志里，降低被发现和封锁的风险。

但这也带来一个问题：开启 VPN 时俄罗斯网站打不开，用户必须断开 VPN 才能访问 Yandex、VK、Gosuslugi 等境内服务。

## 解决方案：设备端分流路由

分流路由会让设备把俄罗斯流量**直接**发往本地网络，其余流量则通过 VPN 发送：

```
俄罗斯网站   → 设备 → 直连 → 互联网 (本地 ISP)
其他所有内容 → 设备 → VPN → VPS → 互联网
```

这样可以兼得两点：
- **保护 VPS** — 服务器端封锁继续保留，作为兜底安全网
- **俄罗斯网站正常可用** — 设备直接连接这些网站，从不经过 VPN

## Meridian 中如何实现

默认客户端策略为 **`ru-split`**。Xray JSON、Mihomo 和 Stash 订阅会将以下目标设为直连：

- 局域网与私有地址（`geosite:private`、`geoip:private`）
- `.ru`、`.su` 和 `.рф` 域名，以及 `geosite:category-ru`
- 俄罗斯 IP 段（`geoip:ru`）

sing-box 使用同一策略，但只采用私有 IP 和三个俄罗斯域名后缀的内联规则。它不会下载 geosite 或 geoip 列表，因此其他俄罗斯域名和 IP 段仍可能通过 VPN。

两种策略都会在直连目标规则之后阻止 QUIC（UDP 443），使应用回退到 TCP。两种策略也都保留 **Xray JSON 中的 BitTorrent 直连规则**，以减少针对出口服务器的滥用投诉。该规则仅匹配已识别的对等连接握手；加密的对等连接和 UDP DHT/uTP 仍可能通过 VPN。Mihomo、Stash 和 sing-box 不携带 BitTorrent 匹配规则。

### 选择策略

| 策略 | 设备上的路由 |
|------|--------------|
| `ru-split`（默认） | 私有地址、局域网和匹配规则的俄罗斯目的地直连，其他流量通过 VPN |
| `full` | 互联网流量通过 VPN，包括俄罗斯目的地；私有地址和局域网保持直连 |

```bash
meridian fleet routing
meridian fleet routing full --dry-run
meridian fleet routing full --yes
meridian fleet routing ru-split --yes
```

不带参数的命令会显示当前策略并解释两种选项。更改会编辑拓扑、显示审核结果，并在确认后应用此次审核的内容。没有终端或使用 `--json` 时，须传入 `--yes`；`--dry-run --json` 会返回审核结果而不应用。应用会在下次刷新订阅时（默认每六小时，由 `delivery.update_interval_hours` 设置）或手动刷新后收到策略。设置向导继续使用 `ru-split`，不增加提问。

`fleet export` 和 `fleet import` 会保留 `delivery.client_routing: full`。默认值 `ru-split` 会省略；从导出文件中删除此字段即可恢复默认策略。网页面板在“设置”中只读显示策略，并提供更改策略的 CLI 命令。

仅导入 Base64 链接列表的应用不会收到路由规则。请在这类应用中自行设置分流或全局路由，或使用包含配置的订阅。Hiddify 会接收 sing-box 文档并遵循其内联规则。

### 规则从哪里来

控制服务根据同一份客户端路由规则列表和拓扑中的所选策略生成所有包含配置的订阅。用 [`meridian fleet routing`](/zh/cli-reference/#meridian-fleet) 更改设备路由。[`meridian route`](/zh/cli-reference/#meridian-route) 控制出口服务器的出站流量；[路由网关](#通过路由网关)则有自己的服务器规则。

## 两层机制如何配合

| 层级 | 作用 | 防范的问题 |
|------|-------------|-----------------|
| **服务器端地理封锁** | 在出口丢弃发往俄罗斯的流量 | VPS IP 出现在俄罗斯服务的访问日志中 |
| **设备端分流路由** | 从设备直接发送俄罗斯流量 | 俄罗斯本土网站打不开、VPN 承担不必要的流量 |

在 `ru-split` 下，匹配客户端俄罗斯直连规则的目的地不会到达出口。如果设备没有路由规则（例如 Base64 导入或旧版应用），出口上已开启的地理封锁仍会拦截俄罗斯目的地。

`full` 只更改客户端路由。要通过出口访问俄罗斯网站，出口也必须允许这些目的地：部署该出口时使用 `--no-geo-block`。更改客户端策略不会更改服务器端地理封锁。

## 通过路由网关

路由网关（用 `meridian setup` 配置）是运行在出口之前一台服务器上的 Xray，它按规则逐条决定流量从哪里出去。每条规则会把匹配的流量发往某个出口、出口池、**直接从网关发出**，或者不发送（封锁）。网关位于俄罗斯时，直连规则会用网关自己的俄罗斯地址打开俄罗斯网站，即使应用没有收到任何分流规则也能访问：

如果这部分流量需要经过网关规则，请选择 `full`。使用 `ru-split` 时，匹配规则的俄罗斯目的地已从设备直连。

```
俄罗斯网站   → 设备 → 网关（俄罗斯境内） → 互联网
其他所有内容 → 设备 → 网关 → 出口（境外） → 互联网
```

在向导的路由步骤中，添加按顶级域 `ru、su、рф`（其下所有网站）、网站列表 `category-ru`（位于其他顶级域的俄罗斯服务）和国家 `RU`（俄罗斯 IP 段，适用于按地址连接的应用）匹配的规则，并在每次询问流量去向时选择**直接从网关发出**。顶级域和列表规则直接比对网站名称；国家规则则需要网关先解析该名称。在拓扑文件中，直连规则是 `"action": "direct"` 且没有 `target_ref`；顶级域写在 `"match": "domain"` 规则中，列表写在 `"match": "geosite"` 规则中。规则按顺序匹配，第一条命中的规则生效；审核摘要会列出每条规则及其去向。

这部分流量不会到达出口，因此出口的地理封锁保持不变。与中继不同，网关就是这部分流量的出站点：俄罗斯服务看到的是网关的地址，针对这部分流量的投诉也会发给网关的托管商（参见[选择托管](/zh/hosting/)）。

## DNS 注意事项

在 Xray JSON 订阅中，未命中直连规则的域名会通过 VPN 隧道解析，因此境外域名查询不会泄漏给本地 ISP。使用 `ru-split` 时，Mihomo 仅对直接按 IP 发起的连接匹配俄罗斯 IP 段，不会在本地解析其他域名。如果你的应用带有独立的 DNS 设置，请为隧道流量开启防 DNS 泄漏选项。


## 命名策略与 DNS

在导出的服务器配置中编辑 `delivery.delivery_policies`，再通过 `meridian fleet import` 审阅。每项策略包含 `id`、内置 `preset`（`full` 或 `ru-split`）、有序 `rules` 和可选 `dns`。将名称写入 `delivery.default_policy`，或使用 `meridian fleet routing work --dry-run` 和 `meridian fleet routing work --yes` 选择。内置名称不能被替换。现有配置的内置订阅内容保持完全一致。

以下是 `delivery` 部分的示例：

```json
{
  "delivery_policies": [{
    "id": "work",
    "preset": "full",
    "rules": [{
      "match": {"kind": "domain_suffix", "values": ["example.test"]},
      "action": {"kind": "block"}
    }],
    "dns": {
      "resolvers": [{"transport": "doh", "address": "198.51.100.53", "path": "/dns-query"}],
      "fake_ip": false
    }
  }],
  "default_policy": "work"
}
```

示例 IP 属于文档专用地址，请替换为自己的 DNS 服务器地址。`profile_policies` 将访问配置名称（包括 `default`）映射到策略名称。没有映射的配置使用服务器默认策略；同时属于多个已映射配置的用户，必须获得相同策略。通过 `fleet routing` 选择内置模式会清除命名默认策略。删除可选字段可恢复原有内置行为。

规则可匹配域名后缀、完整域名、ASCII 关键词、固定版本的 `geosite`/`geoip` 列表、CIDR、目标端口或 TCP/UDP。`match.kind` 可选 `domain_suffix`、`domain_full`、`domain_keyword`、`geosite`、`geoip`、`cidr`、`port`、`network`。端口值采用 `{"start": 8443, "end": 8445}`；省略 `end` 表示单个端口。同一匹配中的值是备选项，满足任一项即可；可选 `network` 和 `ports` 条件同时限制整条规则。局域网直连规则最先执行，随后按书写顺序执行自定义规则，再执行内置模式的其余规则。首次匹配决定动作。

Stash 使用 `LAN` 表示私有地址范围，包括带有网络和端口条件的逻辑规则。这些 IP 匹配保留 `no-resolve`。

geosite 属性按固定数据中的完整键名匹配。例如，`@!cn` 只选择带有 `!cn` 属性的条目；没有此属性的条目不会匹配。

动作包括 `{"kind":"proxy"}`（通过 VPN）、`{"kind":"direct"}`（直连）、`{"kind":"block"}`（阻止），以及 `{"kind":"exit","exit":"west"}`（通过已声明的出口）。出口组只包含用户有权使用的连接。如果当前订阅配置没有所需出口，对应目的地会被阻止，不会改用其他出口。Xray 单连接配置及不同 TLS 指纹组保持隔离。BitTorrent 检测仍尽力而为，且仅适用于 Xray。

DNS 使用 `transport: doh | dot | plain`、单播 IP `address` 和可选 `port`，默认端口分别为 443、853、53。DoH 支持受限 `path`，普通 DNS 支持 `protocol: udp | tcp`。加密 DNS 的证书必须对其 IP 有效。需要引导解析的主机名、凭证、任意模板和正则表达式均不接受。Xray 支持 DoH/普通 DNS，省略 DoT 和 fake-IP；Mihomo 与 sing-box 支持三种传输及 fake-IP；Stash 保留原生 DNS 模式并省略 fake-IP。Base64 无法携带路由或 DNS 设置。省略的 DNS 选项不会降级为较弱的传输。

每项策略最多 128 条规则、每个域名/IP 匹配最多 64 个值、最多四个 DNS 服务器；目录最多 64 项策略。单项策略最多 64 KiB，整个目录最多 256 KiB。地理列表名称必须存在于客户端已安装的固定版本文件中。策略不会添加远程规则集下载；sing-box 省略 geosite 和非私有 geoip 匹配。应用更改后刷新订阅。


### 私有个人路由

`meridian client routing person` 显示有效策略。运行 `meridian client routing person work --dry-run --json` 审阅，再通过 `meridian client routing person work --yes --json` 保存个人覆盖；`meridian client routing person --inherit --yes` 清除覆盖。面板的用户卡片使用同一服务审阅，草稿只保存在内存中。个人覆盖优先于访问配置默认，访问配置默认优先于服务器默认。应用配置和重新签发凭证保留覆盖；加密快照保存它，删除用户会移除它。选择会在应用刷新订阅时生效，不修改账户或节点访问权限。

读取需要 `people.read` 与 `people.private`，分配还需要 `people.delivery`。`delivery.read` 用于读取已审阅目录。已签发令牌保留原有固定权限；分配策略需要签发包含新权限的令牌。没有私有权限时覆盖被隐藏，订阅、事件和已审阅配置均不包含覆盖字段。预览不写入数据。确认需要 `review_hash`；覆盖、成员关系或目录改变后须重新预览。

| 操作 | 含义 |
| --- | --- |
| `GET /api/delivery-policies` | 已审阅目录与访问配置默认；`delivery.read` |
| `GET /api/users/{username}/delivery-policy` | 私有覆盖及有效策略；`people.read` 与 `people.private` |
| `PUT /api/users/{username}/delivery-policy` | `{ "policy": "work", "confirm": false }` 返回预览；确认时提交 `review_hash`；需要 `people.delivery`、`people.read`、`people.private`。显式 `policy: null` 表示继承。 |


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
