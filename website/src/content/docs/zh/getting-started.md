---
title: 快速开始
description: 在几分钟内安装 Meridian 并部署第一个代理服务器。
---

一条命令将全新的 VPS 变成强化的、无法检测的代理服务器，并给你一个可分享的链接。本指南将帮助你从零到正常工作的连接只需几分钟。

有人给您发了链接？这些您都不需要：请看[连接指南](/zh/connecting/)。

## 前置要求

你需要：
- 运行 Debian 或 Ubuntu 的 **VPS**，可通过 SSH 密钥以 root 或无密码 sudo 用户身份访问
- 你自己机器上的**终端**（macOS、Linux 或 [Windows 上的 WSL](/zh/wsl/)）

## 安装 CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

这会通过 [uv](https://docs.astral.sh/uv/) 或 pipx 作为备选方案安装 `meridian` 命令。详见[安装](/zh/installation/)了解其他选项。

在自己的机器上运行 Meridian，而不是在 VPS 上：它通过 SSH 连接，将凭证保存在你的设备上，让 `meridian test` 从外部检查服务器。

> 从 Meridian 3.x 升级？Meridian 4 不是就地升级。先阅读[从 3.x 升级](/zh/installation/#从-3x-升级)。

## 部署

```
meridian deploy 198.51.100.10
```

不指定 IP 直接运行 `meridian deploy` 会提示输入。如果你在 VPS 本身以 root 身份登录，`meridian deploy local` 可跳过 SSH。

## 会发生什么

1. **加固服务器** — 仅 SSH 密钥登录、fail2ban、UFW 防火墙仅开放必要端口、BBR 拥塞控制
2. **安装控制平面** — [Remnawave](https://remna.st/) 面板加运行 Xray 的节点，全在 Docker 中
3. **选择伪装目标** — 服务器尝试 `www.google.com`、`dl.google.com` 和 `www.cloudflare.com`，使用第一个可达的（用 `--sni` 自己选择）
4. **配置 VLESS+Reality** 在端口 443 背后，带服务器 IP 的 Let's Encrypt 证书
5. **验证真实流量** — Meridian 推送流量通过交付的订阅，然后说"完成"；如果流量不通过则切换到下一个伪装目标
6. **发布连接页面** 供第一个客户端使用

部署有检查点。如果中断，运行相同命令，它会从停止的地方继续。

## 连接

部署完成时，它打印：

- **连接页面链接** 带二维码 — 发给连接的人。页面有应用下载链接、二维码和一键"在应用中打开"按钮。
- **订阅 URL** — 粘贴到任何 Xray/V2Ray 应用。应用会刷新它，所以新服务器和中继自动显示。

推荐应用：

| 应用 | 平台 |
|-----|-----|
| [Happ](https://happ.su/) | iOS、Android、Windows、macOS、Linux |
| [FlClashX](https://github.com/pluralplay/FlClashX/releases/latest) | Android、Windows、macOS、Linux |
| [Clash Mi](https://clashmi.app/download) | iOS、Android、Windows、macOS、Linux |
| [Throne](https://github.com/throneproj/Throne/releases/latest) | Windows、macOS、Linux |

连接页面会优先列出这些应用，并为每个平台提供更多应用的链接。

随时从你的机器检查服务器：

```
meridian test 198.51.100.10
```

## 添加更多人

```
meridian client add alice
```

每个人都有自己的密钥、连接页面和订阅 URL。`meridian client list` 显示所有人，`meridian client disable alice` 立即切断某人。详见[客户端管理](/zh/client-management/)。

## 文件位置

| 什么 | 位置 |
|------|------|
| 拓扑、面板访问和密钥 | 你机器上的 `~/.config/meridian/cluster.yml` |
| 保存的 SSH 服务器 | 你机器上的 `~/.config/meridian/servers.json` |
| 代理服务 | VPS 上的 Docker、Xray 和 nginx |

客户端命令直接与 `cluster.yml` 中记录的 Remnawave 面板 API 通讯；它们不需要 SSH。备份 `~/.config/meridian/` — 这是 Meridian 管理你的服务器的方式。

## 多个服务器

`meridian deploy` 覆盖单个服务器。对于多个出口、通过国内服务器的中继或路由网关，使用引导向导：

```
meridian setup
```

也可以用 `meridian node add` 和 `meridian relay add` 逐步增长部署。每个变更都通过同样的已审核拓扑；`meridian plan` 显示会变更什么，`meridian apply` 应用它。

## 后续步骤

- [部署指南](/zh/deploy/) — 每个部署选项、伪装目标、重新部署
- [域名模式](/zh/domain-mode/) — 通过你的域名添加 Cloudflare CDN 回退
- [中继节点](/zh/relay/) — 给用户一个国内入口点
- [IP 被阻止了？](/zh/recovery/) — 服务器被阻止时的处理方法
- [故障排除](/zh/troubleshooting/) — 常见问题和修复
