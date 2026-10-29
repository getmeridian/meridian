---
title: 部署指南
description: 完整的部署演练，包括所有配置选项。
---

本指南涵盖 `meridian deploy` 的每个选项。如果这是你的第一次部署，从[快速开始](/zh/getting-started/)开始。

## 基本部署

```
meridian deploy 198.51.100.10
```

一台服务器既是控制服务器（运行 Meridian 的控制服务）又是承载流量的出口。Meridian 构建与 [`meridian setup`](/zh/cli-reference/#meridian-setup) 相同的已审核拓扑，并用检查点应用它。在打印“Done”之前，它会通过交付的订阅发送真实流量。

不指定 IP 直接运行 `meridian deploy`，它会提示。添加 `--yes` 跳过确认：

```
meridian deploy 198.51.100.10 --client-name alice --yes
```

## 仅密码登录的服务器

Meridian 使用 SSH 密钥登录。许多 VPS 提供商只给你一个 root 密码，所以当服务器拒绝你的密钥但接受密码时，交互式的 `meridian deploy` 会询问：

```
→ This server accepts only a password. Create a key for Meridian and install it now? [y/N]
```

回答 `y` 并输入密码（输入内容不可见）。Meridian 会在 `~/.config/meridian/ssh/meridian_ed25519` 创建专用密钥，将其加入服务器的 `authorized_keys`，检查能否登录，保存到服务器配置中，然后继续部署。密码只用于这一条命令，从不保存或记录。主机的指纹会先经过验证，因此密码只会发送给你已确认指纹的服务器。`meridian setup` 在添加服务器时也提供同样的功能。

没有终端、使用 `--yes` 或使用 `--json` 时，Meridian 不会交互提示，而是停止并给出手动命令。请先手动安装一次你自己的密钥，然后重新运行：

```
ssh-copy-id root@198.51.100.10
```

加固步骤会在部署后关闭密码登录，因此请妥善保管密钥，之后的命令会自动使用它。

## 所有标志

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--sni HOST` | (自动) | 伪装目标。不指定时，部署会优先选择服务器所在子网内的网站，然后是 `www.google.com`、`dl.google.com`、`www.cloudflare.com` |
| `--domain DOMAIN` | (无) | 你的域名：XHTTP 迁移到该域名上并新增 WSS，二者都经由 Cloudflare CDN |
| `--self-steal` | 关 | 用你自己的域名（`--sni`，否则 `--domain`）伪装，而不是别人的网站（见[使用自己的域名](#使用自己的域名-self-steal)） |
| `--hysteria2/--no-hysteria2` | 启 | 在 UDP/443 上提供 Hysteria2 作为最后的回退 |
| `--client-name NAME` | default | 第一个客户端的名称 |
| `--display-name NAME` | (无) | 连接页面的标签 |
| `--icon EMOJI_OR_URL` | (无) | 页面图标 — 表情符号或图片 URL |
| `--color PALETTE` | ocean | 页面色彩主题 (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH 用户（非 root 用户需无密码 sudo） |
| `--ssh-port PORT` | 22 | SSH 端口（如果不是 22） |
| `--harden / --no-harden` | 启 | 仅 SSH 密钥登录、fail2ban 和 UFW 防火墙。`--no-harden` 用于与其他服务共享的服务器 |
| `--warp / --no-warp` | 关 | 通过 Cloudflare WARP 发送出站流量 |
| `--geo-block / --no-geo-block` | 启 | 在服务器上屏蔽俄罗斯域名和 IP |
| `--country CC` | 自动查询 | 显示在连接名称中的国家，例如 `DE`。Meridian 根据地址注册机构信息查询；如果不对，请手动指定 |
| `--yes` | | 跳过确认提示 |

脚本和 UI 的标志（`--json`、`--events`、`--request`、`--dry-run`）在 [CLI 参考](/zh/cli-reference/#meridian-deploy)中描述。

## 选择伪装目标

伪装目标（SNI）是你的服务器所模仿的网站。它通常**不是**你拥有的域名，而是一个支持 TLS 1.3 的真实网站，任何探测你的服务器的人都会看到该网站的真实证书。如果想改用自己的域名，见[使用自己的域名](#使用自己的域名-self-steal)。

最好的目标就在你的服务器旁边。审查者看到的是 SNI 和你服务器 IP 的组合；当该网站的 DNS 确实指向同一网络时，这个组合看起来就是普通流量，而 VPS 地址上出现一个知名的国外网站则会很显眼。

不指定 `--sni` 时，首次部署会扫描服务器所在子网内的此类网站（约一分钟），保留 DNS 指向该子网、且通过 Reality 的 TLS 1.3 和 HTTP/2 检查的网站，然后回退到 `www.google.com`、`dl.google.com` 和 `www.cloudflare.com`，并使用真实流量能通过的第一个目标。`meridian node add` 以同样的方式选择邻近网站。用 `--sni` 指定的目标永远不会被替换；如果流量失败，部署会停止并告诉你。重新部署会保留已保存的目标。

想自己查看候选网站，可以扫描服务器的 IPv4 邻域：

```
meridian scan 198.51.100.10
```

扫描会在服务器上运行版本固定且经过校验和验证的 RealiTLScanner，并列出它在同网段内找到的证书域名。然后用你选定的域名部署：

```
meridian deploy 198.51.100.10 --sni dl.google.com
```

要手动检查单个目标，可运行 `meridian check 198.51.100.10 --sni HOST`。请避免使用 `apple.com` 和 `icloud.com`——它们的自有网络与 VPS 主机商不匹配。

### 以后更换目标

当 `meridian test --probe` 报告伪装 SNI 被封锁时，无需重新部署即可把出口切换到另一个目标。Meridian 会拒绝 `apple.com`、`icloud.com` 和 `www.microsoft.com`（其证书对 Reality 来说太大），检查出口能否以 TLS 1.3 和 HTTP/2 访问新站点，显示审查并应用。旧名称会立即被替换而不是并存，因为一个 Reality 侦听器只模仿一个站点。应用在下次订阅刷新（每 6 小时）或用户手动刷新订阅时切换。中继请使用 `meridian relay sni RELAY NEW_SNI`。

```
meridian node sni 198.51.100.10 www.cloudflare.com
```

### 使用自己的域名 (self-steal)

服务器也可以模仿你自己拥有的域名，而不是借用别人的网站。先添加一条指向服务器的仅 DNS 的 A 记录（Cloudflare 中为灰色云朵，不经代理），然后用 `--self-steal` 部署：

```
meridian deploy 198.51.100.10 --sni www.example.com --self-steal
```

Meridian 会为该域名签发 Let's Encrypt 证书，并由服务器自己提供。没有 Reality 密钥的探测，或使用任何未知 SNI 的探测，都会得到这张证书和 nginx 自带的 404 页面，就像任何尚未发布内容的 nginx 服务器一样。域名解析到这台服务器，证书也是真实的，因此看不出任何不一致；也不存在可能宕机、更换证书或被单独封锁的外部网站。

在做任何改动之前，Meridian 会从服务器上检查该域名指向哪里。如果域名无法解析（Let's Encrypt 无法签发证书），它会停止；如果指向别处（例如 Cloudflare 代理），它会给出警告。

如果使用 `--domain` 而不带 `--sni`，该域名会同时承担两项工作：Reality 模仿它，XHTTP 和 WSS 继续在它后面工作。但经 Cloudflare 代理的域名会解析到 Cloudflare，因此若要同时使用 self-steal 和 CDN，请使用两个域名：一个仅 DNS 的用于 `--sni`，一个经代理的用于 `--domain`。`meridian node add` 和 `meridian node sni` 也支持 `--self-steal`。拥有自己 SNI 的中继仍然借用它们各自的网站。

### 伪装目标 vs 域名

- **伪装目标** (`--sni`) — 你**不**拥有的热门网站，Reality 模仿它；或者在使用 `--self-steal` 时，是你拥有并指向本服务器的域名。
- **域名** (`--domain`) — 你**确实**拥有的域名，指向你的服务器。XHTTP 和 Hysteria2 移到它，它还添加通过 Cloudflare CDN 的 WSS 路径。详见[域名模式](/zh/domain-mode/)。

## Hysteria2

```
meridian deploy 198.51.100.10 --no-hysteria2
```

每次部署都会在 UDP/443 上提供 Hysteria2，使用你的域名证书（未配置域名时则使用服务器 IP 证书）。其流量经过 salamander 混淆封装，因此审查方看到的是随机 UDP 数据包而非 QUIC。它在丢包或高延迟网络下很有帮助；如果所在网络封锁了 UDP，应用会自动回退到排在它前面的 TCP 连接方式。`--no-hysteria2` 可将其关闭。Hysteria2 不经过中继服务器。

## 品牌定制

自定义连接页面外观，让收到链接的人一看就知道是谁搭建的 VPN：

```
meridian deploy 198.51.100.10 --display-name "Alice 的 VPN" --icon 🚀 --color sunset
```

- **`--display-name`** — 显示在页面顶部标题和浏览器标题栏中
- **`--icon`** — 显示在页面顶部的表情符号或图片 URL
- **`--color`** — 主题配色：`ocean`（默认）、`sunset`、`forest`、`lavender`、`rose`、`slate`

品牌定制保存在 `cluster.yml` 中，对所有客户端的连接页面生效。

## 重新部署

要重新部署，直接再次运行同一条命令即可。Meridian 会保留已保存的路径、密钥和用户，因此现有链接会继续有效。不带 `--sni` 重新部署会沿用已保存的伪装目标。

每次部署结束时，Meridian 都会打印包含当前设置的完整重新部署命令。修改标志后再次运行即可按新设置更新服务器：添加或去掉 `--domain` 或 `--no-hysteria2` 会相应地新增或移除这些连接路径。

`meridian deploy` 只会重新部署已经在你的集群中作为出口的服务器。要添加更多服务器，请使用 `meridian node add NEW_IP`。如果集群仍在运行 Remnawave 面板，部署会先提议把它迁移到控制服务（见[从 Remnawave 迁移](#从-remnawave-迁移)）。

## 用文件编辑集群

要一次修改多处，或把集群布局纳入版本控制，可以导出、编辑后再导入：

```bash
meridian fleet export > fleet.yml
$EDITOR fleet.yml
meridian fleet import fleet.yml
```

导入会在执行任何操作之前显示每处变更：

```
  + client carol
  ~ exit-2 sni www.example.com -> www.example.org
  → 应用？ [y/N]
```

文件以保存的名称指代服务器，不包含密钥、凭据、链接或传输路径，因此可以放进代码仓库。文件中出现的服务器必须先保存（`meridian server add`）。导入会拒绝哪些内容，见 [`fleet import`](/zh/cli-reference/#meridian-fleet)。

## 部署前检查

不确定某台服务器是否可用？

```
meridian check 198.51.100.10
```

无需安装任何软件，即可检查伪装目标、端口 443 是否空闲且可从外部访问、可选域名的 DNS、操作系统、磁盘空间以及时钟偏差。

## 非 root 用户部署

```
meridian deploy 198.51.100.10 --user ubuntu
```

命令会通过 `sudo` 执行，因此该用户需要免密 `sudo` 权限。

## 本机部署

如果你已经以 `root` 身份登录在服务器本机上：

```
meridian deploy local
```

这会跳过 SSH。`check local`、`scan local` 和 `doctor local` 的用法相同；部署完成后，在该服务器上运行后续命令也会直接在本机执行。不过仍建议从你自己的电脑上运行 Meridian，因为 `meridian test` 需要从外部网络视角进行测试。

## Cloudflare WARP

WARP 会将服务器的出站流量通过 Cloudflare 转发，使目标网站看到的是 Cloudflare 地址，而不是你的 VPS IP。

```
meridian deploy 198.51.100.10 --warp
```

**适用场景：** 目标网站封锁了数据中心 IP 段，或者你希望向目标网站隐藏 VPS 的真实地址。

**无需开启的场景：** 大多数网站不开启也能正常访问，而且开启后会多经过一跳（VPS → Cloudflare → 目标网站）。

Meridian 会以代理模式安装 `cloudflare-warp` 客户端（在 `127.0.0.1:40000` 上监听 SOCKS5），并将其设为 Xray 的出站。入站连接不受影响。WARP 免费且无需 Cloudflare 账号。

### 仅对某些网站使用 WARP

要仅让选定的网站走 WARP、其余流量保持直连，可以在出口上添加分流路由，而不必为全部流量开启 WARP。当某条路由第一次用到 WARP 时，Meridian 会自动在出口上配置 WARP：

```
meridian route add exit-1 --via warp chatgpt.com netflix.com
meridian route add exit-1 --via block geosite:category-ads-all
meridian route list
meridian route remove exit-1 warp
```

匹配值可以是域名（包含子域名）、内置 v2fly 数据文件中的 `geosite:NAME` 列表或 `geoip:NAME` 列表；同一条路由只能包含同一种类型。在启用了全局 WARP 的出口上，`--via direct` 作用相反：让匹配的目标绕过 WARP 直接出站。路由按添加顺序生效，优先级高于出口默认出站和地理封锁。这些路由运行在服务器端，与用户设备上将俄罗斯网站直连的客户端分流规则相互独立，也与路由网关规则无关；后者可以让流量直接从境内网关发出（[分流路由](/zh/split-routing/#通过路由网关)）。

### AI 服务

AI 服务（ChatGPT、Claude、Gemini、Cursor 等）会拒绝部分托管商地址；如果会话的地址在不同国家之间跳动，或短暂暴露出家庭网络连接，可能导致账号被登出或封禁。一条路由即可让它们全部走 WARP：

```
meridian route add exit-1 --preset ai
```

该预设是一条名为 `ai` 的路由，涵盖 `geosite:category-ai-!cn`、`anthropic`、`openai`、`cursor` 和 `google-gemini`；加上 `--via direct` 则让它们使用出口自身的地址。WARP 在出口所在地区出站，因此请让使用 AI 服务的用户固定在同一国家的出口上。服务器端路由无法拦截设备绕过 VPN 发出的流量：在 macOS 上，Happ 可能在打开笔记本盖子后的片刻直接发送流量。如果 Google 服务仍认为你身处其他国家，可在 Google 账号帮助中提交“更改与账号关联的国家/地区”申请，通常几小时内即可解决。

## 地理封锁

默认情况下，服务器会拒绝通过代理访问俄罗斯域名（`geosite:category-ru`）和俄罗斯 IP 段（`geoip:ru`）。这样可以避免你的 VPS IP 出现在俄罗斯服务的日志中，降低被发现和封锁的风险。

如果你的用户需要通过 Meridian 访问这些网站，可以将其关闭：

```
meridian deploy 198.51.100.10 --no-geo-block
```

如果想在保留服务器端封锁的同时让用户的设备直连打开俄罗斯网站，详见[分流路由](/zh/split-routing/)。

## 客户端地址

控制服务从不记录你的用户从哪里连接。每个 TCP 连接都经由服务器上的 nginx 到达 Xray，服务只保存每个人的流量总量，不保存地址，也没有连接日志。
## 添加中继

出口运行后，可以在境内服务器上添加一台 TCP 中继，这样即使出口 IP 被封锁，用户仍然有可用的连接入口。详见[中继节点](/zh/relay/)。

```
meridian relay add 203.0.113.10 --exit 198.51.100.10
```

## 网页控制台

控制服务自带一个网页控制台，供唯一的所有者——你——使用。它位于随机生成的秘密路径上，与连接页面路径相互独立，因此你分享的链接绝不会暴露它。`meridian panel` 会打印它的地址；在控制台还没有所有者时，还会打印一个一次性设置链接：

```
  Meridian console
  URL         https://198.51.100.10/<secret-path>/
  Setup link  https://198.51.100.10/<secret-path>/#setup=…
              Works once, for 15 minutes. Open it to choose the owner's password.
```

设置密码后，可以开启来自身份验证器应用的登录验证码。在控制台中，你可以添加用户、分享他们的链接和二维码、设置限额和到期时间，以及创建 API 令牌；服务器、修订记录和订阅标题以只读方式显示，并附上修改它们的 `meridian` 命令。详见[网页控制台](/zh/client-management/#网页控制台)。

请像保护密码一样保护控制台地址。如果你丢失了密码或身份验证器，`meridian panel --setup-link` 会打印一个重置链接：它会设置新密码、关闭登录验证码，并让所有设备退出登录。

## 备份

首次部署结束时，Meridian 会显示一次恢复密钥：

```
  Recovery key (shown once; Meridian keeps no copy)

    AGE-SECRET-KEY-1…

  Write it down or print it, and keep it offline, away from this computer.
```

请把它离线保存，远离管理集群的机器。每次变更后，Meridian 都会在你的机器上保存一份控制服务的加密快照，可以用你机器自己的密钥或恢复密钥读取。如果控制服务器丢失，[`meridian restore`](/zh/recovery/#控制服务器丢失) 可以用最新的快照在新服务器上重建它。

## 升级

控制服务和节点 agent 随 Meridian 一起发布。运行 `meridian update` 之后，再运行 `meridian apply`：它会把控制服务升级到新版本，并就地升级每个 agent，保留每个节点的身份。`meridian fleet upgrade` 会把中继升级到最新的 Realm 版本，无需等待 Meridian 更新。

## 从 Remnawave 迁移

由更早的 Meridian 4 版本部署的集群运行的是 Remnawave 面板。`meridian migrate` 会在原服务器上就地把它迁移到控制服务：

```
meridian migrate
```

它会备份面板的数据库（备份到你的机器和控制服务器上），带着每个客户端的密钥、状态、限额、到期时间和流量迁移过去，用 agent 替换每个节点的容器，并在删除 Remnawave 之前检查每条订阅链接都没有变化。客户端保留各自的链接，不会察觉任何变化。只存在于面板中的数据（例如 HWID 设备列表、按天的用量历史和插件设置）不会迁移，客户端 IP 转发（它服务于面板按用户的 IP 限制）也会关闭；审核时会列出这些内容。迁移可以继续：如果中途停止，再次运行 `meridian migrate` 即可。详见 [`meridian migrate`](/zh/cli-reference/#meridian-migrate)。