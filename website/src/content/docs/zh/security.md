---
title: 安全
description: 安全设计、漏洞报告和范围。
order: 11
section: reference
---

## 报告漏洞

如果您在 Meridian 中发现安全漏洞：

1. **不要开放公开问题**
2. 给维护者发电子邮件或使用 [GitHub 安全公告](https://github.com/getmeridian/meridian/security/advisories/new)
3. 包含复现步骤和潜在影响

我们的目标是在 48 小时内响应，并将在修复中感谢报告者。

## 安全设计

- **凭证**：以 `0600` 权限存储在 `~/.config/meridian/cluster.yml` 中，秘密永不通过无 `shlex.quote()` 的 shell 命令传递，从 `meridian doctor` 输出中编辑。目录权限为 `0700`（仅所有者）。任何作为你用户运行的应用都能读这些文件 — 这是所有本地存储凭证的 CLI 工具的固有特性（SSH 密钥、云 CLI 令牌等）。如果你的本地机器在用户级别被破解，凭证加密无法帮助：恶意软件可以键盘记录密码或拦截 SSH 会话。保护你的本地机器。
- **面板访问**：Remnawave 管理 UI 由 nginx 在秘密 HTTPS 路径反向代理 — 不需要 SSH 隧道。连接页面和订阅链接使用单独的路径并永不显示面板的。URL 和管理员凭证在 `~/.config/meridian/cluster.yml` 的 `panel` 部分
- **SSH**：默认禁用密码认证，并启用 fail2ban（`--no-harden` 跳过两者加防火墙，用于与其他服务共享的服务器）
- **防火墙**：UFW 拒绝入站流量除了服务器的 SSH 端口和其角色需要的端口 — TCP/443 用于代理流量、TCP/80 用于证书签发、UDP/443 仅当启用 Hysteria2、以及中继上的监听端口
- **固定软件**：Remnawave 镜像固定到已测试版本；Realm、Xray（用于 `meridian test`）和 RealiTLScanner 下载是版本固定和校验和验证的
- **TLS**：[acme.sh](https://github.com/acmesh-official/acme.sh) 通过 Let's Encrypt 处理证书，由 nginx 提供

## 范围

Meridian 配置代理服务器 — 它**不**实现加密协议。底层安全取决于：

- [Xray-core](https://github.com/XTLS/Xray-core) — VLESS+Reality 协议
- [Remnawave](https://remna.st/) — 面板 + 节点管理栈
- [nginx](https://nginx.org/) — SNI 路由和 TLS 终止
