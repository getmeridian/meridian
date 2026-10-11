---
title: 部署指南
description: 完整的部署演练，包括所有配置选项。
order: 3
section: guides
---

本指南涵盖 `meridian deploy` 的每个选项。如果这是你的第一次部署，从[快速开始](/docs/zh/getting-started/)开始。

## 基本部署

```
meridian deploy 198.51.100.10
```

一个服务器既是控制平面（Remnawave 面板）又是出口。Meridian 构建 [`meridian setup`](/docs/zh/cli-reference/#meridian-setup) 生成的同样已审核拓扑，并用检查点应用它。在打印"完成"之前，它会通过交付的订阅推送真实流量。

不指定 IP 直接运行 `meridian deploy`，它会提示。添加 `--yes` 跳过确认：

```
meridian deploy 198.51.100.10 --client-name alice --yes
```

## 所有标志

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--sni HOST` | (自动) | 伪装目标。不指定时，服务器从 `www.google.com`、`dl.google.com`、`www.cloudflare.com` 中尝试，使用第一个可达的；重新部署保持保存的目标。显式 `--sni` 永不被交换 |
| `--domain DOMAIN` | (无) | 用于 XHTTP 和 WSS 的你的域名，带 Cloudflare CDN 回退 |
| `--hysteria2` | 关 | 在 UDP/443 上也提供 Hysteria2。需要 `--domain`；QUIC 在某些地区被限制 |
| `--client-name NAME` | default | 第一个客户端的名称 |
| `--display-name NAME` | (无) | 连接页面的标签 |
| `--icon EMOJI_OR_URL` | (无) | 页面图标 — 表情符号或图片 URL |
| `--color PALETTE` | ocean | 页面色彩主题 (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH 用户（非 root 用户需无密码 sudo） |
| `--ssh-port PORT` | 22 | SSH 端口（如果不是 22） |
| `--server NAME` | | 保存的服务器来部署到（名称或 IP） |
| `--harden / --no-harden` | 启 | 仅 SSH 密钥登录、fail2ban 和 UFW 防火墙。`--no-harden` 用于与其他服务共享的服务器 |
| `--warp / --no-warp` | 关 | 通过 Cloudflare WARP 发送出站流量 |
| `--geo-block / --no-geo-block` | 启 | 通过代理阻止俄罗斯域名和 IP |
| `--yes` | | 跳过确认提示 |

脚本和 UI 的标志（`--json`、`--events`、`--request`、`--dry-run`）在 [CLI 参考](/docs/zh/cli-reference/#meridian-deploy)中描述。

## 选择伪装目标

伪装目标（SNI）是你的服务器所模仿的网站。它**不是**你拥有的域名 — 它是具有 TLS 1.3 的热门网站。任何探测你的服务器的人都会看到该网站的真实证书。

不指定 `--sni`，服务器本身会按顺序尝试 `www.google.com`、`dl.google.com` 和 `www.cloudflare.com`，使用第一个可达的。如果真实流量随后无法通过该目标，Meridian 在完成之前切换到下一个。用 `--sni` 传递的目标永不被交换；如果流量失败，部署停止并告诉你。

要找到与服务器同网络的目标，扫描其 IPv4 邻域：

```
meridian scan 198.51.100.10
```

扫描在服务器上运行固定的、校验和验证的 RealiTLScanner 构建，列出它找到的附近的证书域。然后用你的选择部署：

```
meridian deploy 198.51.100.10 --sni dl.google.com
```

要手动检查一个目标，运行 `meridian preflight 198.51.100.10 --sni HOST`。避免 `apple.com` 和 `icloud.com` — 它们的网络与 VPS 提供商不匹配。

### 伪装目标 vs 域名

- **伪装目标** (`--sni`) — 你**不**拥有的热门网站。Reality 模仿它。
- **域名** (`--domain`) — 你**确实**拥有的域名，指向你的服务器。它添加 XHTTP 和通过 Cloudflare CDN 的 WSS 路径。详见[域名模式](/docs/zh/domain-mode/)。

## Hysteria2

```
meridian deploy 198.51.100.10 --domain vpn.example.com --hysteria2
```

在 UDP/443 上使用你域名的证书添加 Hysteria2。它可以在有损或高延迟网络上有帮助，但 QUIC 在某些地区被限制或阻止，所以它是可选的。Hysteria2 不通过中继。

## 品牌定制

个性化连接页面，让接收者知道谁设置了他们的 VPN：

```
meridian deploy 198.51.100.10 --display-name "Alice 的 VPN" --icon 🚀 --color sunset
```

- **`--display-name`** — 显示在页面标题和标题中
- **`--icon`** — 显示在页面顶部的表情符号或图片 URL
- **`--color`** — 强调色盘：`ocean`（默认）、`sunset`、`forest`、`lavender`、`rose`、`slate`

品牌定制保存在 `cluster.yml` 中，应用于每个客户端的连接页面。

## 重新部署

要重新部署，运行相同的命令。Meridian 保持保存的路径、密钥和用户，所以现有链接继续工作。不指定 `--sni` 的重新部署保持保存的伪装目标。

在每次部署的末尾，Meridian 打印确切的重新部署命令及你的当前设置。保存它：目前变更是增量的。你可以添加域名、Hysteria2 或用户，但删除已部署的东西（例如，去掉 `--domain`）被拒绝。

`meridian deploy` 仅重新部署已经是你舰队中出口的服务器。要添加另一个服务器，使用 `meridian node add NEW_IP`。

## 部署前检查

不确定服务器是否合适？

```
meridian preflight 198.51.100.10
```

检查伪装目标、端口 443 是否空闲且可从外部访问、可选的域名 DNS、OS、磁盘空间和时钟偏差，无需安装任何东西。

## 非 root 部署

```
meridian deploy 198.51.100.10 --user ubuntu
```

命令通过 `sudo` 运行，所以用户需要无密码 sudo。

## 本地部署

如果你以 root 身份登录到服务器：

```
meridian deploy local
```

这跳过 SSH。`preflight local`、`scan local` 和 `doctor local` 工作方式相同。仍然建议从你自己的机器运行 Meridian，因为 `meridian test` 需要外部角度。

## Cloudflare WARP

WARP 通过 Cloudflare 发送你的服务器的出站流量，所以网站看到 Cloudflare 地址而不是你的 VPS IP。

```
meridian deploy 198.51.100.10 --warp
```

**WARP 有帮助时：** 网站阻止数据中心 IP 范围，或当你想隐藏 VPS 地址不被目标网站看到。

**你不需要它时：** 大多数网站无需它也能正常工作，它添加一个跳跃（VPS → Cloudflare → 目标）。

Meridian 在代理模式下安装 `cloudflare-warp` 客户端（SOCKS5 在 `127.0.0.1:40000`），使其成为 Xray 的出站。传入连接不受影响。WARP 免费且不需要 Cloudflare 账户。

## 地理封锁

默认情况下，服务器通过代理阻止俄罗斯域名（`geosite:category-ru`）和俄罗斯 IP 范围（`geoip:ru`）。这让你的 VPS IP 远离俄罗斯服务日志，降低被发现和阻止的机会。

如果你的用户需要通过 Meridian 访问这些网站，关闭它：

```
meridian deploy 198.51.100.10 --no-geo-block
```

要在保持阻止的同时打开俄罗斯网站直接访问，详见[分流路由](/docs/zh/split-routing/)。

## 添加中继

出口运行后，在国内服务器上添加中继，这样如果出口 IP 被阻止，用户保持有访问方式。详见[中继指南](/docs/zh/relay/)。

```
meridian relay deploy 203.0.113.10 --exit 198.51.100.10
```

## 管理面板

Meridian 安装 [Remnawave](https://remna.st/) 作为其面板。面板由 nginx 在随机秘密路径上反向代理，与连接页面路径分开，所以你分享的链接永不显示它。部署打印面板 URL 为"Remnawave 面板（高级）"；URL 和管理员登录在 `~/.meridian/cluster.yml` 的 `panel` 部分。

像对待密码一样对待面板路径。CLI 使用相同的面板 API，所以 CLI 变更显示在面板中。Meridian 拥有它创建的主机、配置和模板：如果你在面板中编辑那些，`meridian plan` 报告差异，下次应用恢复 Meridian 的版本。

Meridian 固定了它已测试的 Remnawave 版本。如果你自己升级 Remnawave，Meridian 不会降级它。
