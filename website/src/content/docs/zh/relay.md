---
title: 中继节点
description: 通过国内服务器路由流量，增强 IP 封锁抗性。
order: 6
section: guides
---

## 中继节点解决的问题

当出口服务器的 IP 被封锁时，用户失去访问。中继为他们提供一个国内入口点，更难被阻止：

```
Client → Relay (domestic IP) → Exit server (abroad) → Internet
```

审查者看到发往国内 IP 的流量。中继转发原始 TCP 到出口；加密是端到端的客户端和出口之间，所以中继永不见明文。

## 中继如何工作

中继运行 [Realm](https://github.com/zhboner/realm)，一个小零复制 TCP 转发器。它在端口 443（可配置）上监听并转发到出口的 Reality 监听器。中继上没有 Docker 和面板运行。

中继承载 **Reality** 流量。XHTTP、WSS 和 Hysteria2 停留在出口自己的地址和域名上，并在同一订阅中作为替代方案。

## 部署中继

首先部署出口，然后指向它的中继：

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10
```

Meridian 将中继添加到您的拓扑并应用它：
1. 准备中继服务器（包、BBR、SSH 加固、仅打开 SSH 和中继端口的防火墙）
2. 安装 Realm（版本固定、SHA256 验证）作为 systemd 服务
3. 指向中继到出口的 Reality 监听器
4. 在每个客户端的订阅中发布中继

### 标志

| 标志 | 默认 | 说明 |
|------|---------|-------------|
| `--exit/-e EXIT` | （必需） | 出口服务器 IP 或名称 |
| `--name NAME` | （自动） | 中继的友好名称（例如 `ru-moscow`） |
| `--port/-p PORT` | 443 | 中继服务器上的监听端口 |
| `--sni HOST` | 出口的目标 | 此中继的 Reality 伪装目标；省略使用出口的 |
| `--user/-u USER` | root | 中继上的 SSH 用户 |
| `--ssh-port PORT` | 22 | 中继服务器的 SSH 端口 |
| `--yes/-y` | | 跳过确认提示 |

### 包含所有选项的例子

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-moscow \
  --port 443 --sni dl.google.com --user ubuntu --ssh-port 2222 --yes
```

## 客户端如何连接

现有客户端在下次订阅刷新时收到中继，他们的连接页面列出它。新客户端自动获得它：

```bash
meridian client add alice   # 中继包括
```

## 管理中继

```bash
meridian relay list                       # 所有中继
meridian relay list --exit 198.51.100.10  # 一个出口的中继
```

检查中继的跳跃 — 每个 Realm 服务正在运行，每个跳跃到达下一个，公共入口接受连接 — 然后通过整条路由测试真实流量：

```bash
meridian relay check 203.0.113.10
meridian test
```

要停用中继，删除通过它的链。Meridian 显示它将删除什么，请求确认并清理 Realm 服务、防火墙规则和已发布的入口点；现有客户端在下次订阅刷新时丢失中继：

```bash
meridian relay remove 203.0.113.10
```

## 多个中继

将多个中继连接到一个出口，例如在不同的城市或网络：

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-moscow
meridian relay add 203.0.113.11 --exit 198.51.100.10 --name ru-spb
```

客户端在他们的订阅中收到每个中继。对于超过一个跳的中继链，使用 `meridian setup`。

## 故障排查

### 端口冲突

另一服务正在中继上使用监听端口。用 `ss -tlnp sport = :443` 检查并停止它，或用 `--port 8443` 选择另一端口。

### 防火墙阻止

确保中继端口在您的云提供商防火墙或安全组上打开，不仅仅在 UFW。

### 出口无法到达

中继必须到达出口的 Reality 端口。运行 `meridian test` 以检查端到端的路由，并运行 `meridian node check EXIT` 以检查出口本身。

### 中继服务不运行

在中继服务器上，列出 Meridian 的 Realm 服务并阅读其日志：

```bash
systemctl list-units 'meridian-realm-*'
journalctl -u 'meridian-realm-*' --no-pager -n 20
```
