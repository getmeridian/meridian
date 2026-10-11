---
title: 域名模式
description: 通过 Cloudflare 添加 CDN 回退以增强对 IP 阻止的抗性。
order: 4
section: guides
---

## 域名模式添加的内容

```
meridian deploy 198.51.100.10 --domain vpn.example.com
```

拥有自己的域名，出口在 Reality 旁边提供两个额外的方式：

- **XHTTP** 和 **WSS** 在您的域名上，带 Meridian 颁发和续期的 Let's Encrypt 证书（acme.sh）
- 一旦域名通过 Cloudflare 代理，两条路径都通过 Cloudflare 的 CDN 传输 — 所以即使您的服务器 IP 被阻止它们也继续工作

面板和连接页面保持在服务器的 IP 证书上；域名仅承载代理路径。

添加 `--hysteria2` 也在 UDP/443 上提供 Hysteria2，带域名的证书（见 [Hysteria2 和 Cloudflare](#hysteria2-and-cloudflare)）。

## Cloudflare 设置

**按此顺序**避免证书问题：

1. 在 Cloudflare 中添加您的域并创建指向您的服务器 IP 的 **A 记录**
2. 保持云图标**灰色**（"仅 DNS"）
3. 运行 `meridian deploy 198.51.100.10 --domain vpn.example.com` — Meridian 获取证书
4. 将记录切换到**橙色云**（代理）
5. 将 SSL/TLS 设置为 **Full (Strict)** 并打开 **WebSockets**（网络设置）

部署完成时再打印这些步骤。

> **重要：** 证书使用端口 80 上的 HTTP 挑战颁发。Cloudflare 的 "Always Use HTTPS" 破坏该挑战 — 关闭它，或豁免 `/.well-known/acme-challenge/*`。

## 连接链接

每个客户端的订阅和连接页面列出每条路径：

| 路径 | 路由 | 何时有帮助 |
|------|-------|---------------|
| Reality | 直接到服务器 IP | 主要 — 最快 |
| XHTTP | 您的域名（代理时通过 Cloudflare） | Reality 被阻止或节流 |
| WSS | 您的域名（代理时通过 Cloudflare） | 服务器 IP 被阻止 |
| Hysteria2（可选） | 您的域名 UDP/443 上 | 丢包或高延迟网络 |

导入 Xray JSON 或 Mihomo 订阅的应用在一条路径停止工作时自动在路径间切换。其他应用将每条路径显示为单独条目。

## Hysteria2 和 Cloudflare

Hysteria2 在 UDP 上使用 QUIC，Cloudflare 的代理不承载。使用橙色云记录，Hysteria2 客户端到达 Cloudflare 而不是您的服务器且不会连接；TCP 路径不受影响。QUIC 在某些地区也被节流。仅在您的用户需要它并可以通过 UDP 到达服务器时启用 `--hysteria2`。

## 稍后添加域名

使用添加的 `--domain` 再次运行您的部署命令。现有 Reality 链接保持工作。一旦部署了域名，在以后的重新部署中保持传递相同的 `--domain` — 从运行中的服务器移除路径尚不支持。
