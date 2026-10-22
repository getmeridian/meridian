---
title: 快速开始
description: 在几分钟内安装 Meridian 并部署第一台代理服务器。
---

一条命令即可把全新的 VPS 变成加固过的代理服务器，并生成一个可分享的链接。本指南带你在几分钟内从零完成可用连接。

有人给你发了链接？这些你都不需要：请看[连接指南](/zh/connecting/)。

## 前置要求

你需要：
- 一台运行 Debian 或 Ubuntu 的 **VPS**，可通过 SSH 密钥以 `root` 或免密 `sudo` 用户身份登录
- 你自己电脑上的**终端**（macOS、Linux 或 [Windows 上的 WSL](/zh/wsl/)）

## 安装 CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

该脚本会通过 [uv](https://docs.astral.sh/uv/)（或以 pipx 作为备选）安装 `meridian` 命令。其他安装方式见[安装](/zh/installation/)。

请在自己的电脑上运行 Meridian，而不是在 VPS 上运行：它会通过 SSH 连接服务器，把凭据保存在你的设备上，并让 `meridian test` 能从外部测试服务器。

> 从 Meridian 3.x 升级？Meridian 4 不是就地升级。请先阅读[从 3.x 升级](/zh/installation/#从-3x-升级)。

## 部署

```
meridian deploy 198.51.100.10
```

不指定 IP 直接运行 `meridian deploy` 时，命令会提示输入。如果你已经以 `root` 身份登录在 VPS 本机上，运行 `meridian deploy local` 可跳过 SSH。

## 部署过程会做什么

1. **加固服务器** — 仅允许 SSH 密钥登录、启用 fail2ban、配置只开放必要端口的 UFW 防火墙，并启用 BBR 拥塞控制
2. **安装控制平面** — 在 Docker 中部署 [Remnawave](https://remna.st/) 面板以及运行 Xray 的节点
3. **选择伪装目标** — 先在服务器自身所在网络中寻找合适的 TLS 1.3 网站，找不到时回退到 `www.google.com`、`dl.google.com` 和 `www.cloudflare.com`（也可用 `--sni` 手动指定）
4. **配置连接方式** — 在 nginx 后的 TCP 443 端口上配置 VLESS+Reality 和 XHTTP（使用为服务器 IP 签发的 Let's Encrypt 证书），并在 UDP 443 上配置 Hysteria2
5. **验证真实流量** — 在输出“Done”之前，Meridian 会通过交付的订阅发送真实流量；若流量不通则自动尝试下一个伪装目标
6. **发布连接页面** — 为第一个客户端生成专属连接页面

部署带有检查点。如果中途中断，重新运行同一条命令即可从中断处继续。

## 连接

部署完成后会输出：

- **连接页面链接**（带二维码）— 发给需要连接的人即可。页面上有应用下载链接、二维码和一键导入按钮。
- **订阅 URL** — 可直接粘贴到任何 Xray/V2Ray 应用中。应用会自动刷新订阅，因此后续添加的服务器和中继会自动出现。

推荐应用：

| 应用 | 平台 |
|-----|-----|
| [Happ](https://happ.su/) | iOS、Android、Windows、macOS、Linux |
| [FlClashX](https://github.com/pluralplay/FlClashX/releases/latest) | Android、Windows、macOS、Linux |
| [Clash Mi](https://clashmi.app/download) | iOS、Android、Windows、macOS、Linux |
| [Throne](https://github.com/throneproj/Throne/releases/latest) | Windows、macOS、Linux |

连接页面会优先列出这些应用，并为每个平台提供更多应用的链接。

随时从你的电脑测试服务器：

```
meridian test 198.51.100.10
```

## 添加更多用户

```
meridian client add alice
```

每个人都会获得独立的密钥、连接页面和订阅 URL。`meridian client list` 列出所有客户端，`meridian client disable alice` 可立即暂停某人的访问权限。详见[客户端管理](/zh/client-management/)。

## 文件与服务位置

| 内容 | 位置 |
|------|------|
| 拓扑、面板访问凭据和密钥 | 你电脑上的 `~/.config/meridian/cluster.yml` |
| 已保存的 SSH 服务器 | 你电脑上的 `~/.config/meridian/servers.json` |
| 代理服务 | VPS 上的 Docker、Xray 和 nginx |

客户端管理命令直接调用 `cluster.yml` 中记录的 Remnawave 面板 API，不需要 SSH。请备份 `~/.config/meridian/`——Meridian 依靠它管理你的服务器。

## 多台服务器

`meridian deploy` 适用于单台服务器。如果需要配置多个出口、境内中继服务器或路由网关，请使用交互式向导：

```
meridian setup
```

你也可以用 `meridian node add` 和 `meridian relay add` 逐步扩展集群。每次变更都会经过同一套已审核拓扑：`meridian plan` 显示将要发生的变更，`meridian apply` 执行变更。

## 后续步骤

- [部署指南](/zh/deploy/) — 所有部署选项、伪装目标、重新部署
- [域名模式](/zh/domain-mode/) — 使用你自己的域名添加 Cloudflare CDN 备用线路
- [中继节点](/zh/relay/) — 为用户提供境内入口
- [IP 被封锁？](/zh/recovery/) — 服务器 IP 被封锁时的应对方法
- [故障排除](/zh/troubleshooting/) — 常见问题与排查方法
