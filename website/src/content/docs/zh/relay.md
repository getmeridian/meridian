---
title: 中继节点
description: 通过国内服务器路由流量，增强 IP 封锁抗性。
order: 6
section: guides
---

## 中继节点解决的问题

当出口服务器的 IP 被封锁时，客户端会失去访问权限。中继节点为他们提供了一个国内入口点，更难被封锁：

```
客户端 → 中继节点（国内 IP）→ 出口服务器（国外）→ 互联网
```

审查机构看到的是发往国内 IP 的流量。中继节点将原始 TCP 转发到出口服务器 — 所有加密都是客户端和出口服务器之间的端到端加密。中继节点永远看不到明文。

## 中继节点如何工作

中继节点运行 [Realm](https://github.com/zhboner/realm)，一个轻量级零复制 TCP 转发器（~5MB Rust 二进制文件）。它在端口 443（可配置）上监听，并将所有流量转发到出口服务器的端口 443。没有 Docker、没有 VPN 软件、没有管理面板。

旧版 Realm 中继只发布 **Reality**。其专用 SNI 路由终止于出口服务器的 Reality 入站，而 HTTP 传输需要独立的 host/path 路由。直接 XHTTP 与 WSS 条目仍在同一订阅中作为后备。

在 V4 中，中继是 `topology_intent` 中的一条链：每个 hop 都引用已保存的服务器身份，入口 hop 可以向客户端发布。入口 listener 可连接并不代表内部 hop 已得到验证。

## 部署中继节点

首先按常规方式部署出口服务器。然后部署指向它的中继节点：

```bash
meridian relay deploy RELAY_IP --exit EXIT_IP
```

在旧版模式中，配置程序：
1. 安装所需的包并启用 BBR
2. 配置 UFW 防火墙（允许 SSH + 中继端口）
3. 下载 Realm 二进制文件（版本固定、SHA256 验证）
4. 写入 Realm 配置并启动 systemd 服务
5. 验证中继 → 出口连接

### 标志

| 标志 | 默认值 | 说明 |
|------|---------|-------------|
| `--exit/-e EXIT` | （必需） | 出口服务器 IP 或名称 |
| `--name NAME` | （自动） | 中继的友好名称（例如 `ru-moscow`） |
| `--port/-p PORT` | 443 | 中继服务器上的监听端口 |
| `--sni HOST` | （自动） | 中继本地的 Reality 伪装目标 |
| `--user/-u USER` | root | 中继服务器上的 SSH 用户 |
| `--ssh-port PORT` | 22 | 中继服务器的 SSH 端口 |
| `--yes/-y` | | 跳过确认提示 |

旧版省略 `--sni` 时，会在中继的 IPv4 网络运行 RealiTLScanner。V4 要求 `--exit` 无歧义地匹配 intent 中已有 exit；命令会加入中继链并立即应用计划，空 `--sni` 则继承出口的 Reality 路由。

### 包含所有选项的示例

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow \
  --port 443 --sni www.microsoft.com --user ubuntu --ssh-port 2222 --yes
```

## 客户端如何连接

部署中继节点后，Meridian 会在 Remnawave 面板中创建中继主机条目。现有客户端下次刷新订阅时会自动获得中继 URL，直接 URL 则作为备用。

添加新客户端时，其订阅也会自动包含中继 URL：

```bash
meridian client add alice   # 订阅中包含中继 URL
```

## 管理中继节点

```bash
meridian relay list                    # 所有出口服务器上的所有中继节点
meridian relay list --exit 198.51.100.10     # 特定出口的中继节点
meridian relay check RELAY_IP          # 中继与面板健康检查
meridian relay remove RELAY_IP         # 停止服务 + 从配置中移除
```

`relay list` 同时显示旧版记录和 V4 投影。`relay check` 与 `relay remove` 只适用于旧版 Realm 中继。V4 会明确拒绝这两条命令：用 `meridian test` 验证完整链路，在 `meridian setup` 中审核链路删除并应用 intent 变更。

### 健康检查

对于旧版中继，`meridian relay check` 测试路径及面板注册：

| 检查 | 测试内容 |
|-------|---------------|
| SSH 到中继 | 能否连接到中继服务器？ |
| Realm 服务 | systemd 服务是否活跃？ |
| 中继 → 出口 TCP | 中继能否在端口 443 上到达出口服务器？ |
| 本地 → 中继 TCP | 本地机器能否在其监听端口上到达中继？ |
| 面板主机 | Remnawave 中的中继主机条目是否存在且已启用？ |

退出码 `0` 表示健康，`4` 表示检查完成但有问题，`3` 表示所需 SSH 或面板证据不可用。

`meridian fleet status` 对公网中继只执行 TCP listener 连接；这不是代理流量端到端成功的证据。V4 内部 hop 保持 `unknown`，因此在单独验证规范路由前，舰队可能返回退出码 `3`。

### 移除中继

```bash
meridian relay remove RELAY_IP [--exit EXIT_IP] [--yes]
```

旧版命令会停止并删除 Realm 服务、binary 和 config、精确 UFW 规则、出口 nginx routing 和 Remnawave Host。全部远端清理成功前，`cluster.yml` 条目会保留以便重试。客户端下次刷新订阅后将不再看到该中继。

## 多个中继节点

您可以将多个中继节点连接到一个出口服务器 — 例如，不同城市或 ISP 中的中继节点：

```bash
meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --name ru-moscow
meridian relay deploy 203.0.113.11 --exit 198.51.100.10 --name ru-spb
```

客户端会在订阅中收到所有中继选项。

## 故障排查

### 端口冲突

另一个服务正在中继上使用端口 443。使用 `ss -tlnp sport = :443` 检查并停止冲突的服务，或使用 `--port 8443` 指定不同的端口。

### 防火墙阻止

确保在中继的云提供商防火墙 / 安全组上打开了端口 443，而不仅仅是 UFW。

### 出口服务器无法到达

中继必须能到达出口服务器的 TCP/443。旧版使用 `meridian relay check`；V4 使用 `meridian test`，因为单独 hop 可达不等于客户端规范路由可用。

### 中继服务未启动

检查 Realm 服务：`systemctl status meridian-relay`。查看日志：`journalctl -u meridian-relay --no-pager -n 20`。
