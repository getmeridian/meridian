---
title: 客户端管理
description: 添加用户、分享连接信息和管理访问密钥。
order: 5
section: guides
---

## 添加客户端

```
meridian client add alice
```

这会为"alice"创建一个唯一的连接密钥并显示：
- **终端中的二维码** — 扫描即可打开连接页面或导入订阅
- **订阅 URL** — 供兼容的 VPN 应用导入
- **可共享的 PWA URL** — 托管在您的服务器上，可通过任何通信应用发送

一次传递多个名称以同时添加多个客户端：

```
meridian client add alice bob charlie
```

每个客户端获得自己的密钥和连接页面。失败按客户端报告 — 成功创建的客户端即使某些失败也会被保留。

### 收件人看到的内容

可共享的 URL 打开一个连接页面，包括：
- 安装 VPN 应用（v2RayTun、v2rayNG、Hiddify 或 v2rayN）的分步说明
- 每个连接协议的二维码
- 一键"在应用中打开"的深度链接
- 连接状态和使用统计

通过电子邮件、iMessage、Telegram 或任何信使发送该 URL。收件人打开它、安装应用、扫描二维码并连接。无需任何技术知识。

## 显示连接详情

要在任何时候重新显示现有客户端的连接信息：

```
meridian client show alice
```

这会输出相同的二维码、订阅 URL 和可共享的页面链接——不会创建新密钥。在以下情况下使用：
- 您需要与某人重新分享连接页面
- 您丢失了原始二维码或订阅 URL
- 您想验证客户端的连接看起来如何

## 列出客户端

```
meridian client list
```

显示所有客户端及其协议连接（Reality、XHTTP、WSS）。

## 删除客户端

```
meridian client remove alice
```

立即撤销访问权限。客户端的 UUID 将从服务器上的所有入站中删除。

## 暂停客户端

```
meridian client disable alice
```

临时阻止客户端连接。他们的配置保持完整 — 没有删除密钥，他们的订阅 URL 保持有效。使用此功能可以在不失去客户端设置的情况下暂停访问。

要重新启用：`meridian client enable alice`

## 重新启用客户端

```
meridian client enable alice
```

恢复之前暂停的客户端。他们可以使用现有密钥和订阅 URL 立即再次连接。

## 凭证存储位置

Meridian 在本地将舰队拓扑存储在 `~/.meridian/cluster.yml` — 面板 URL、API 令牌、管理员凭证、节点和中继。客户端状态（用户、UUID、流量）位于 Remnawave 面板的 PostgreSQL 数据库中，这是事实的来源。

```
~/.meridian/cluster.yml                 # 舰队拓扑 + 面板访问
```

客户端命令直接使用存储的 API 令牌与面板的 REST API 对话。客户端操作无需 SSH。

如果您需要在丢失本地文件后恢复，`meridian fleet recover` 从实时面板 API 重建 `cluster.yml`。

## Web 面板

Meridian 部署[Remnawave](https://remna.st/) 管理面板用于流量监控、用户管理和高级配置。它由 nginx 反向代理在随机化的 HTTPS 路径 — 不需要 SSH 隧道。在 `~/.meridian/cluster.yml` 中找到 URL 和管理员凭证：

```
grep -A6 "^panel:" ~/.meridian/cluster.yml
```

相关字段：

```yaml
panel:
  url: https://<your-server-ip>/<secret_path>/
  admin_user: admin
  admin_pass: <generated>
  api_token: <JWT used by Meridian CLI>
  secret_path: <random>
  sub_path: <random>   # subscription page path
```

在浏览器中打开 `url` 并使用 `admin_user` / `admin_pass` 登录。

面板端编辑（例如重命名用户、禁用主机）会在 Meridian 中浮现为漂移 — 下次 `meridian plan` 显示面板实际状态与您的 `cluster.yml` 所需状态之间的差异。使用 `meridian apply` 以任何方式收敛。

## 工作原理

每个 Meridian 客户端是一个单一的 Remnawave 用户（`users` 表中的一个 UUID）。该用户被分配给 Meridian 的默认内部小组，这授予对面板知道的每个入站的可见性（`vless-reality`、`vless-xhttp` 和域名模式下的 `vless-xhttp-ws`）。订阅 URL — `https://<ip>/<sub_path>/<short_uuid>` — 由 Remnawave 订阅页容器提供，包含客户端可以使用的所有入站端点。

客户端应用（v2rayNG、Streisand、Hiddify、sing-box）将订阅 URL 视为单个事实来源：刷新它会在您部署新出口、添加中继或轮换 Reality 密钥时拉取新入站。

## 声明性客户端列表

对于舰队范围的设置，您可以以声明的方式而不是命令方式管理客户端。将 `desired_clients` 列表添加到 `~/.meridian/cluster.yml`：

```yaml
desired_clients:
  - alice
  - bob
  - charlie
```

然后 `meridian plan` 显示与面板实际用户列表的差异，`meridian apply` 收敛 — 添加任何缺失的客户端，删除任何额外的。`meridian client add/remove` 仍然与此并排工作；两种方法共存。参见[声明性工作流](/docs/zh/getting-started/#declarative-workflow)了解完整故事。
