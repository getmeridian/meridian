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

该命令为 `alice` 创建唯一访问，并显示订阅 URL 和二维码。在旧版部署中，Meridian 还会尝试在面板主机上部署独立 PWA 页面。在 V4 中，用户被加入 `topology_intent.access.users`，更新后的 intent 会立即应用，只提供 Remnawave 的规范订阅，不创建独立 PWA 页面。

一次传递多个名称以同时添加多个客户端：

```
meridian client add alice bob charlie
```

旧版模式会逐个报告错误：成功创建的客户端会保留，部分成功或页面上传失败以退出码 `3` 结束。V4 会先把全部名称加入 access intent，再应用生成的计划。

### 收件人看到的内容

在旧版模式中，成功部署的连接页面包括：
- 安装 VPN 应用（v2RayTun、v2rayNG、Hiddify 或 v2rayN）的分步说明
- 每个连接协议的二维码
- 一键"在应用中打开"的深度链接
- 连接状态和使用统计

页面不可用或使用 V4 时，请分享订阅 URL 或其二维码；规范订阅始终是连接配置的权威来源。

## 显示连接详情

要在任何时候重新显示现有客户端的连接信息：

```
meridian client show alice
```

该命令会重新显示二维码和订阅 URL，不创建新密钥。只有存在页面已部署的正面证据时才显示 PWA 链接。要明确修复缺失的旧版页面，请运行：

```
meridian client show alice --repair-page
```

以下情况可使用 `show`：
- 您需要与某人重新分享连接页面
- 您丢失了原始二维码或订阅 URL
- 您需要重新取得规范订阅

在 V4 中，`show` 和 `list` 只显示 access intent 中声明的用户；直接在面板创建的用户不会自动归 Meridian 所有。`--repair-page` 仅适用于旧版页面。

## 列出客户端

```
meridian client list
```

显示客户端状态和统计。在 V4 中，列表仅包含 access intent 用户。

## 删除客户端

```
meridian client remove alice
```

旧版模式会删除面板用户及其已确认的本地页面。V4 会明确拒绝该命令，因为尚未实现安全的托管 access 用户退役。需要立即临时撤销时请使用 `client disable`，不要直接在面板删除用户。

## 暂停客户端

```
meridian client disable alice
```

临时阻止客户端连接，密钥和订阅仍保留。在 V4 中只能操作已声明用户，并且 disable 只持续到下一次 `meridian apply`；apply 会让 intent 中声明的访问重新回到 active。

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

客户端命令使用保存的令牌访问面板 REST API。一般操作不需要 SSH；例外是旧版 PWA 页面在面板主机上的部署、探测、修复和删除。

本地状态丢失后，`meridian fleet recover --legacy --panel-url https://HOST/SECRET_PATH` 可以导入一个无歧义的旧版共享配置文件。它无法重建 V4 topology intent；V4 部署应恢复备份或重新运行 `meridian setup`。

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

面板端对托管资源的编辑会表现为漂移。旧版的所需用户来自 `desired_clients`；V4 的权威来源是 `topology_intent.access.users`。执行 `apply` 前务必审核计划，尤其是对额外资源的删除。

## 工作原理

每个 Meridian 客户端都是一个带 UUID 的 Remnawave 用户。旧版把用户加入共享 Internal Squad；V4 从 access intent 编译权限并通过资源驱动管理。Remnawave 订阅 URL 只包含该用户获准使用的 endpoints，是客户端应用的规范来源。

客户端应用（v2rayNG、Streisand、Hiddify、sing-box）将订阅 URL 视为单个事实来源：刷新它会在您部署新出口、添加中继或轮换 Reality 密钥时拉取新入站。

## 声明式访问

旧版部署可以在 `~/.meridian/cluster.yml` 中配置 `desired_clients`：

```yaml
desired_clients:
  - alice
  - bob
  - charlie
```

随后 `meridian plan` 会与面板用户比较，`meridian apply` 使状态收敛。V4 的对应来源是 `topology_intent.access.users`：通过 `meridian client add` 添加，或在 `meridian setup` 中审核 intent；`desired_clients` 不是 V4 的权威来源。
