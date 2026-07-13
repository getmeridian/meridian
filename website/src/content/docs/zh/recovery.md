---
title: IP 封锁恢复
description: 服务器 IP 被封锁后怎么办 — 诊断和恢复方案。
order: 7
section: guides
---

## 诊断

从本地机器运行（无需 SSH）：

```
meridian test IP
```

如果 TCP 端口 443 检查失败，IP 可能被您的 ISP 或政府封锁。这是受审查地区最常见的问题。

## 紧急缓解

如果您使用**域名模式**（`--domain`）部署，您的 WSS/CDN 连接仍然有效——它通过 Cloudflare 的 CDN 路由，完全绕过 IP 封锁。告诉用户切换到其连接页面上的 WSS 连接链接。

如果您已部署**中继**，通过中继连接的客户端不受影响——他们连接到中继的本地 IP，而不是被封锁的出站 IP。

## 恢复选项

### 选项 A：添加替代出口

当前面板仍可访问时，这是最快的方式：

```bash
# 1. 从您的提供商获得新 VPS（新 IP）
# 2. 将其添加到现有舰队
meridian node add NEW_IP --name replacement
```

现有客户端会在下次刷新订阅时收到替代出口，无需重新创建账户。

### 选项 B：新出站服务器 + 现有中继

如果您已部署中继，这是最佳方案——您的客户端保留中继连接，同时交换其后的出站服务器：

```bash
# 1. 将新出站节点添加到现有集群
meridian node add NEW_EXIT_IP --name replacement-exit

# 2. 将中继切换到新出站
meridian relay remove RELAY_IP --exit OLD_EXIT_IP
meridian relay deploy RELAY_IP --exit NEW_EXIT_IP

# 客户端刷新订阅后自动获得新路径——中继 IP 不变
```

### 选项 C：添加域名模式以实现 CDN 回退

如果您之前未使用域名模式，现在添加它以防止未来中断：

```bash
meridian node add NEW_IP --domain proxy.example.com
```

使用域名模式，即使服务器 IP 被封锁，WSS/CDN 连接也能工作——流量通过 Cloudflare 路由。有关 Cloudflare 设置的详细信息，请参阅[域名模式指南](/docs/zh/domain-mode/)。

`meridian node add` 需要当前 Remnawave 面板保持可访问。如果丢失的服务器同时托管面板，请先恢复该主机；目前尚不支持自动迁移面板。

## 主动防御

在 IP 被封锁**之前**设置弹性：

1. **部署中继**——为客户端提供本地入口点。当出站 IP 被封锁时，交换中继后的出站，无需触及客户端：
   ```bash
   meridian relay deploy RELAY_IP --exit EXIT_IP
   ```

2. **启用域名模式**——添加即使 IP 被封锁也能工作的 WSS/CDN 回退：
   ```bash
   meridian deploy EXIT_IP --domain proxy.example.com
   ```

3. **两者都使用**——最大弹性。客户端有三条路径：中继（本地）、CDN（Cloudflare）和直接（如果未被封锁）。

## 客户端迁移

如果要替换整个面板主机，每个客户端都必须在新面板上重新创建——目前没有跨面板自动迁移工具。仅在现有集群中添加或替换出口节点时无需重新添加客户端；Remnawave 面板仍是客户端状态的事实来源。

替换整个面板时的工作流程：

1. 部署新服务器
2. 为每个客户端运行 `meridian client add NAME`
3. 与用户共享新的连接页面（二维码、可共享 PWA URL 或订阅 URL）

连接页面会自动提供所有可用的连接选项（直接、中继、CDN），可共享的 PWA URL 会随订阅状态更新。

## 保留旧服务器

不要立即关闭旧服务器——它可能在数天或数周后解封。您可以定期检查：

```bash
meridian test OLD_IP
```

如果恢复了，您有一个备用出站服务器准备就绪。
