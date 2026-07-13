---
title: 域名模式
description: 通过 Cloudflare 添加 CDN 回退以实现 IP 阻断弹性。
order: 4
section: guides
---

## 域名模式添加的内容

域名模式用三个组件扩展独立设置：

1. **nginx stream SNI 路由**——将域名流量路由到 nginx http，将 Reality 流量路由到 Xray
2. **nginx TLS**——证书由 acme.sh (Let's Encrypt) 管理
3. **VLESS+WSS 入站**——通过 Cloudflare 的旧版 CDN 回退（次要传输方式优先使用 XHTTP）

WSS 连接通过 Cloudflare 的 CDN 路由，即使您的服务器 IP 被阻断也能工作——Cloudflare 的 IP 范围用途广泛，难以整体封锁。WSS 仅为向后兼容而保留；新部署应优先使用 XHTTP 作为备用传输方式。

## 使用域名部署

```
meridian deploy 198.51.100.10 --domain proxy.example.com
```

## Cloudflare 设置

**按照此确切顺序**以避免 TLS 证书问题：

1. 在 Cloudflare 中添加您的域名，创建指向您的服务器 IP 的 **A 记录**
2. 保持云图标为**灰色**（"仅 DNS"）——暂时不启用代理
3. 运行 `meridian deploy`——acme.sh 自动获取 TLS 证书
4. 切换为**橙色云**（已代理）
5. 配置 SSL/TLS → **完整（严格）**和网络 → **启用 WebSocket**

> **重要：** acme.sh 通过端口 80 上的 HTTP-01 挑战获取证书。如果启用了 Cloudflare 的"始终使用 HTTPS"，会破坏挑战。禁用它或为 `/.well-known/acme-challenge/*` 添加页面规则。

> **还要注意：** 在域名模式下，托管的连接页面、隐藏的 Remnawave 管理 UI 和 subscription-page 路径都使用这个 hostname。把记录切换到橙云后，这些页面也会经过 Cloudflare。请在这个 hostname 上关闭会注入脚本或修改 HTML 的 Cloudflare 功能（例如 Website Analytics / RUM），因为 Meridian 的连接页面刻意使用严格的 self-hosted CSP。如果页面在开启代理后开始失败，先临时切回 DNS only，以确认问题是否来自 Cloudflare 这一侧。

## 连接链接

使用域名模式，用户获得三个连接选项：

| 协议 | 优先级 | 路由 |
|----------|----------|-------|
| Reality | 主要 | 直接到服务器 IP |
| XHTTP | 替代 | 通过端口 443 上的 nginx |
| WSS | 旧版后备 | 通过 Cloudflare CDN |

用户应首先尝试 Reality（最快），其次是 XHTTP，仅当两者都失败时才把 WSS 作为最后手段（例如 IP 被阻止）。
