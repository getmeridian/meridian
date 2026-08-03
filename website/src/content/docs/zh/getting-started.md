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

新的托管 V4 拓扑请使用 `meridian setup`；intent 会成为服务器、路由和 access users 的权威图。下面的 `meridian deploy` 保留单主机旧版快速路径。

```
meridian deploy
```

交互式向导会询问您的服务器 IP、SSH 用户和伪装目标（SNI）。为所有内容提供智能默认值。

或者预先指定所有内容：

```
meridian deploy 198.51.100.10 --sni www.microsoft.com
```

## 发生了什么

1. **安装 Docker** 并部署由 Remnawave 面板管理的 Xray
2. **生成 x25519 密钥对** — Reality 认证的唯一密钥
3. **加固服务器** — UFW 防火墙、SSH 仅密钥认证、BBR 拥塞控制
4. **配置 VLESS+Reality** 在端口 443 上 — 伪装为真实的 TLS 服务器
5. **启用 XHTTP 传输** — 额外的隐身层，通过 nginx 路由
6. **旧版会部署可共享 PWA**；V4 直接提供规范订阅 URL，不创建独立页面

## 文件位置

Meridian 通过 SSH 连接到 VPS（也可使用 `deploy local` 直接在服务器上运行）。部署后，状态会缓存在本地：

| 内容 | 位置 |
|------|------|
| 拓扑、面板令牌和密钥 | 本机的 `~/.meridian/cluster.yml` |
| SSH 服务器配置 | 本机的 `~/.meridian/servers.json` |
| 代理服务 | VPS 上的 Docker、Xray 和 nginx |

在旧版中，`meridian client add alice` 创建面板用户；在 V4 中，它把用户加入 access intent 并应用计划。大多数操作只使用 Remnawave API；只有旧版 PWA 页面操作需要 SSH。

管理多台服务器时，只有需要连接服务器的命令才使用 `--server NAME` 指定 SSH 主机。

## 连接

旧版 deploy 输出订阅 URL，并在页面上传成功后显示 PWA URL。V4 setup 始终返回规范订阅 URL，不会声称存在旧版 PWA 页面。

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

每个客户端都有独立访问和订阅。V4 的 `client list` 只显示声明的 access users。`client remove alice` 支持旧版；V4 暂不支持安全退役托管用户，需要立即临时撤销时请用 `client disable alice`（只持续到下一次 `meridian apply`）。

## 管理服务器

当您管理多个 VPS 部署时：

```
meridian server list                # 查看所有管理的服务器
meridian server add 198.51.100.11  # 添加现有服务器
meridian server remove finland     # 从注册表中删除
```

`--server` 标志可为需要连接服务器的命令指定目标，例如 `meridian preflight --server finland`。客户端命令直接操作集群面板，不接受 `--server`。

## 声明式工作流

V4 将已审核的舰队模型作为 `topology_intent` 存储在 `cluster.yml` 中。使用 `meridian setup` 创建或修订它；intent 包含控制平面、出口、路由链、协议路径和访问用户。运行 `plan` 预览编译后的资源变更，再用 `apply` 使其收敛。

```
meridian plan                      # 预览编译后的资源变更
meridian apply --yes               # 收敛已审核的拓扑
```

当集群已收敛时，`meridian plan` 退出码为 `0`；存在待应用变更时为 `2`，因此可以用它约束 CI 流程。使用 `meridian plan --json` 查看强类型计划；process 或 UI 客户端需要最终执行结果时，使用 `meridian apply --json --yes`。两者都使用 `meridian.output/v1` 封装。完整选项请参阅 [CLI 参考](/docs/zh/cli-reference/#meridian-plan)。

旧版集群仍支持可选的 `desired_nodes`、`desired_relays` 和 `desired_clients` 协调。这些字段不是 V4 的权威来源。带注释的状态示例请参阅 [`cluster.example.yml`](https://github.com/getmeridian/meridian/blob/v4/cluster.example.yml)。

## 后续步骤

- [部署指南](/docs/zh/deploy/) — 完整的部署演练，包括所有选项
- [中继节点](/docs/zh/relay/) — 通过国内 IP 路由以增强抗 IP 阻挡能力
- [域名模式](/docs/zh/domain-mode/) — 通过 Cloudflare 添加 CDN 回退
- [IP 被阻止了？](/docs/zh/recovery/) — IP 被阻止时的分步恢复指南
- [故障排除](/docs/zh/troubleshooting/) — 常见问题和解决方案
