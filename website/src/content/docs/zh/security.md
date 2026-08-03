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

- **凭证**：以 `0600` 权限存储在 `~/.meridian/cluster.yml` 中，秘密信息从不通过未使用 `shlex.quote()` 的 shell 命令传递，并从 `meridian doctor` 输出中删除。目录权限为 `0700`（仅所有者）。以您的用户身份运行的任何应用程序都可以读取这些文件 — 这是所有在本地存储凭证的 CLI 工具的固有特性（SSH 密钥、云 CLI 令牌等）。如果您的本地计算机在用户级别被入侵，凭证加密也无济于事：恶意软件可以记录密码或拦截 SSH 会话。保护好您的本地计算机。
- **面板访问**：Remnawave 管理 UI 在所有模式下都由 nginx 通过秘密 HTTPS 路径反向代理 — 不需要 SSH 隧道。URL 和管理员凭据位于 `~/.meridian/cluster.yml` 的 `panel` 部分
- **SSH**：默认禁用密码认证，并启用 fail2ban 防止暴力破解
- **防火墙**：UFW 默认拒绝入站流量，仅允许检测到的 SSH TCP 端口、TCP/443 上的 HTTPS、UDP/443 上的 Hysteria2，以及 ACME 或 Web 服务需要时的 TCP/80
- **Docker 镜像**：Remnawave 后端、节点和订阅页面固定为 `src/meridian/config.py` 中经过测试的版本
- **TLS**：[acme.sh](https://github.com/acmesh-official/acme.sh) 通过 Let's Encrypt 处理证书，并由 nginx 提供服务

## 范围

Meridian 配置代理服务器——它**不**实现加密协议。基础安全取决于：

- [Xray-core](https://github.com/XTLS/Xray-core)——VLESS+Reality 协议
- [Remnawave](https://remna.st/)——面板和节点管理栈
- [nginx](https://nginx.org/)——SNI 路由和 TLS 终止
