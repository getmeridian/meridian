---
title: 快速开始
description: 在两分钟内安装 Meridian 并部署您的第一个代理服务器。
order: 1
section: guides
---

## 前置要求

您需要：
- 一台运行 Debian 或 Ubuntu 的 **VPS**（具有 root SSH 密钥访问权限）
- 您本地计算机上的 **终端**（macOS、Linux 或 [Windows 上的 WSL](/docs/zh/wsl/)）

## 安装 CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

这会通过 [uv](https://docs.astral.sh/uv/)（首选）或 pipx 安装 `meridian` 命令。

## 部署

```
meridian deploy
```

交互式向导会询问您的服务器 IP、SSH 用户和伪装目标（SNI）。为所有内容提供智能默认值。

或者预先指定所有内容：

```
meridian deploy 1.2.3.4 --sni www.microsoft.com
```

## 发生了什么

1. **安装 Docker** 并通过 3x-ui 管理面板部署 Xray
2. **生成 x25519 密钥对** — Reality 认证的唯一密钥
3. **加固服务器** — UFW 防火墙、SSH 仅密钥认证、BBR 拥塞控制
4. **配置 VLESS+Reality** 在端口 443 上 — 伪装为真实的 TLS 服务器
5. **启用 XHTTP 传输** — 额外的隐身层，通过 nginx 路由
6. **输出 QR 码** 并保存 HTML 连接页面

## 连接

deploy 命令输出：
- 一个可以用手机扫描的 **QR 码**
- 一个可以与家人分享的带有连接链接的 **HTML 文件**
- 一个 **可共享的 URL**（如果启用了服务器托管页面）

安装这些应用之一，然后扫描 QR 码或点击"在应用中打开"：

| 平台 | 应用 |
|------|-----|
| iOS | [v2RayTun](https://apps.apple.com/app/v2raytun/id6476628951) |
| Android | [v2rayNG](https://github.com/2dust/v2rayNG/releases/latest) |
| Windows | [v2rayN](https://github.com/2dust/v2rayN/releases/latest) |
| 所有平台 | [Hiddify](https://github.com/hiddify/hiddify-app/releases/latest) |

## 添加更多用户

```
meridian client add alice
```

每个客户端都有自己的密钥和连接页面。使用 `meridian client list` 列出客户端，使用 `meridian client remove alice` 撤销访问权限。

## 管理服务器

当您管理多个 VPS 部署时：

```
meridian server list                # 查看所有管理的服务器
meridian server add 5.6.7.8        # 添加现有服务器
meridian server remove finland     # 从注册表中删除
```

`--server` 标志可以为任何命令指定特定服务器：`meridian client add alice --server finland`。

## 后续步骤

- [部署指南](/docs/zh/deploy/) — 完整的部署演练，包括所有选项
- [中继节点](/docs/zh/relay/) — 通过国内 IP 路由以增强抗 IP 阻挡能力
- [域名模式](/docs/zh/domain-mode/) — 通过 Cloudflare 添加 CDN 回退
- [IP 被阻止了？](/docs/zh/recovery/) — IP 被阻止时的分步恢复指南
- [故障排除](/docs/zh/troubleshooting/) — 常见问题和解决方案
