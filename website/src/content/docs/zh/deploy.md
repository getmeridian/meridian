---
title: 部署指南
description: 完整的部署演练，包括所有配置选项。
order: 3
section: guides
---

## 基本部署

本页描述直接的旧版 `meridian deploy` 路径。新的托管 V4 拓扑请使用 `meridian setup`；后续变更以其 `topology_intent` 为权威来源。

```
meridian deploy 198.51.100.10
```

向导会指导您完成配置。或者预先指定所有内容：

```
meridian deploy 198.51.100.10 --sni www.microsoft.com --client-name alice --yes
```

## 所有标志

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--sni HOST` | www.microsoft.com | Reality 伪装的站点 |
| `--domain DOMAIN` | （无） | 启用带有 CDN 回退的域名模式 |
| `--client-name NAME` | default | 第一个客户端的名称 |
| `--display-name NAME` | （无） | 连接页面上的显示名称（如 "Alice 的 VPN"） |
| `--icon EMOJI_OR_URL` | （无） | 服务器图标 — 表情符号或图片 URL |
| `--color PALETTE` | ocean | 颜色方案（ocean/sunset/forest/lavender/rose/slate） |
| `--user USER` | root | SSH 用户（非 root 用户自动获得 sudo） |
| `--harden / --no-harden` | 启用 | 加固 SSH + 防火墙（如果其他服务共享该服务器则禁用） |
| `--server NAME` | | 目标服务器（名称或 IP） |
| `--yes` | | 跳过确认提示 |
| `--warp / --no-warp` | 禁用 | 通过 Cloudflare WARP 路由出站流量 |
| `--geo-block / --no-geo-block` | 启用 | 通过代理阻止 `.ru` 域名和俄罗斯 IP |

## 品牌定制

个性化连接页面，让接收者知道谁设置了 VPN：

```
meridian deploy 198.51.100.10 --display-name "Alice 的 VPN" --icon 🚀 --color sunset
```

- **`--display-name`** — 显示在信任栏和页面标题中。使用您的名字或友好的标签。
- **`--icon`** — 连接页面顶部显示的表情符号或图片 URL。
- **`--color`** — 设置强调色方案。选项：`ocean`（默认）、`sunset`、`forest`、`lavender`、`rose`、`slate`。

这些设置只用于旧版 PWA 页面，并保存在 `cluster.yml` 中。V4 提供规范 Remnawave 订阅，不创建独立自定义 PWA 页面。

## 选择 SNI 目标

SNI（服务器名称指示）目标是 Reality 伪装的域。默认值（`www.microsoft.com`）对大多数情况都适用。

如需发现服务器附近的备选目标，请使用固定版本且经过校验和验证的 RealiTLScanner 扫描其 IPv4 子网：

```
meridian scan 198.51.100.10
```

RealiTLScanner 仅支持 IPv4；仅 IPv6 的服务器应明确传入 `--sni`，否则 `scan` 返回 `3`。选择只会保存到已跟踪的旧版节点；V4 只显示结果，intent 必须通过 `meridian setup` 修改。

**良好的目标**（全球 CDN）：
- `www.microsoft.com` — Azure CDN，全球
- `www.twitch.tv` — Fastly CDN，全球
- `dl.google.com` — Google CDN，全球
- `github.com` — Fastly CDN，全球

请选择能够从服务器解析并完成 TLS 握手的稳定主机名。`meridian scan` 会验证实际证书中观察到的域名；`meridian preflight --sni HOST` 可检查手动选择，且不会把服务器地址发送给 ASN 服务。

## 部署前检查

不确定您的服务器是否兼容？

```
meridian preflight 198.51.100.10
```

无需安装即可测试 SNI 可达性与 DNS、端口本地和外部可达性、可选域名 DNS、操作系统兼容性、磁盘空间和时钟偏差。退出码 `0` 表示就绪，`4` 表示有可处理发现，`3` 表示所需证据不可用。

## 重新运行部署

随时重新运行 `meridian deploy` 是安全的。预配程序是完全幂等的：
- 现有拓扑和密钥从 `cluster.yml` 加载，不会重新生成
- 步骤在执行前检查现有状态
- 没有重复工作

## 非 root 部署

```
meridian deploy 198.51.100.10 --user ubuntu
```

非 root 用户会自动获得 `sudo`。用户必须有无密码 sudo 访问权限。

## 地理封锁

默认情况下，Meridian 会通过代理阻止 `.ru` 域名和俄罗斯 IP 段：

```bash
meridian deploy 198.51.100.10 --no-geo-block
```

这是有意的。这样可以让俄罗斯目的地不经过代理路径，降低您的 VPS IP 出现在俄罗斯服务日志中、随后被封锁的概率。

在以下情况下保持启用：
- 俄罗斯网站本来就无需 VPN 也能正常访问
- 您希望共享服务器使用更安全的默认值

在以下情况下关闭：
- 您需要通过 Meridian 访问 `.ru` 网站
- 您希望所有流量都毫无例外地经过 VPN

交互式向导会明确询问这一项。对应的 Xray 规则是 `geosite:category-ru` 和 `geoip:ru`。

## 添加中继节点

部署出口服务器后，添加中继节点以在出口 IP 被阻止时增强抗阻挡能力。有关完整的设置说明，请参阅[中继指南](/docs/zh/relay/)。

```bash
meridian relay deploy RELAY_IP --exit YOUR_EXIT_IP
```

## 管理面板

Meridian 部署 [Remnawave](https://remna.st/) 管理栈，包括现代化面板后端和独立节点服务；两者都由 nginx 通过随机的秘密路径反向代理。您可以直接在浏览器中监控流量、查看入站配置、管理用户和主机，以及检查服务器状态。

面板 URL 和凭据存储在本地 `cluster.yml` 中：

```
cat ~/.meridian/cluster.yml
```

`panel` 部分包含所有必要信息：

```yaml
panel:
  url: https://198.51.100.10/n7kx2m9qp4wj8vh3rf6tby5e/
  api_token: <JWT token>
  admin_user: admin
  admin_pass: <generated>
  secret_path: n7kx2m9qp4wj8vh3rf6tby5e
  sub_path: <subscription page path>
```

在浏览器中打开 `url`，使用管理员凭据登录。

面板路径是随机生成的安全措施——请像对待密码一样保护它。所有 `meridian` CLI 命令底层使用相同的面板 API，因此 CLI 中的操作也会显示在面板中，反之亦然（`meridian plan` 会显示状态漂移）。

> **注意：** 如果您直接在面板中修改设置，下次运行 `meridian deploy` 时可能会被覆盖。
