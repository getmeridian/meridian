---
title: 域名模式
description: 通过 Cloudflare 添加 CDN 备用线路，应对服务器 IP 封锁。
---

## 域名模式带来了什么

```
meridian deploy 198.51.100.10 --domain vpn.example.com
```

没有域名时，出口已经提供 Reality，以及服务器 IP 上的 XHTTP 和 Hysteria2。绑定你自己的域名后会有两点变化：

- **XHTTP** 迁移到你的域名上，并新增 **WSS**，使用由 Meridian 自动申请和续期的 Let's Encrypt 证书（通过 acme.sh）
- 当域名开启 Cloudflare 代理后，这两条路径都会经由 Cloudflare CDN 传输——即使你的服务器 IP 被封锁，它们也能继续工作

管理面板和连接页面仍使用服务器的 IP 证书；域名仅承载代理流量路径。

UDP/443 上的 Hysteria2 也会改用域名的证书（见 [Hysteria2 和 Cloudflare](#hysteria2-和-cloudflare)）；`--no-hysteria2` 可将其关闭。

## Cloudflare 设置

**请按以下顺序操作**，以避免证书签发失败：

1. 在 Cloudflare 中添加你的域名，并创建指向服务器 IP 的 **A 记录**
2. 将云朵图标保持为**灰色**（“仅 DNS” / DNS only）
3. 运行 `meridian deploy 198.51.100.10 --domain vpn.example.com`——Meridian 会完成证书申请
4. 将该记录切换为**橙色云朵**（已代理 / Proxied）
5. 将 SSL/TLS 模式设为 **Full (Strict)**，并在 Network 设置中开启 **WebSockets**

部署完成时，终端还会再次打印这些步骤提醒你。

> **重要：** 证书通过端口 80 上的 HTTP-01 验证签发。Cloudflare 的“始终使用 HTTPS”（Always Use HTTPS）会打断该验证——请将其关闭，或为 `/.well-known/acme-challenge/*` 配置豁免规则。

## 连接链接

每个客户端的订阅和连接页面都会列出所有路径：

| 路径 | 走向 | 适用场景 |
|------|-------|---------------|
| Reality | 直连服务器 IP | 主路径 — 速度最快 |
| XHTTP | 你的域名（开启代理时经由 Cloudflare） | Reality 被封锁或限速时 |
| WSS | 你的域名（开启代理时经由 Cloudflare） | 服务器 IP 被封锁时 |
| Hysteria2 | 通过 UDP/443 直连服务器 IP，使用域名证书 | 丢包或高延迟网络，或所有 TCP 路径都被封锁时 |

支持导入 Xray JSON、Mihomo 或 sing-box 订阅的应用会在某条路径失效时自动切换。其他应用会将每条路径显示为单独的节点条目。

## Hysteria2 和 Cloudflare

Hysteria2 通过 UDP 直接连接服务器 IP，域名仅用于其 TLS 证书，因此即使记录经由 Cloudflare 代理（Cloudflare 无法承载这类流量）也能正常工作。Salamander 混淆让它的数据包看起来是随机的，而不像 QUIC。在 UDP 被封锁的地方，应用会回退到上面列出的 TCP 路径。

## 稍后添加域名

加上 `--domain` 重新运行部署命令即可。原有的 Reality 链接会继续有效。如果以后想移除域名，不带 `--domain` 重新部署即可：WSS 会被移除，XHTTP 和 Hysteria2 会回到服务器的 IP 证书上，订阅也会随之更新。
