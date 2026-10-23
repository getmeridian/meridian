---
title: 安全
description: 安全设计、漏洞报告和范围。
---

## 报告漏洞

如果你在 Meridian 中发现安全漏洞：

1. **请勿提交公开 issue**
2. 请通过 [GitHub 私密漏洞报告](https://github.com/getmeridian/meridian/security/advisories/new)私下提交
3. 附上复现步骤与潜在影响

我们力争在 48 小时内响应，并会在修复说明中致谢报告者。

## 安全设计

- **凭据**：以 `0600` 权限保存在 `~/.config/meridian/cluster.yml` 中；敏感值传入 shell 命令时一律使用 `shlex.quote()` 转义，并在 `meridian doctor` 输出中自动脱敏。配置目录权限为 `0700`（仅所有者可访问）。以当前系统用户身份运行的程序都可以读取这些文件——所有在本地保存凭据的 CLI 工具（SSH 密钥、云厂商 CLI 令牌等）都是如此。如果本地机器在当前用户权限下已遭入侵，对凭据文件加密也无济于事：恶意软件可以通过键盘记录窃取口令，或直接劫持 SSH 会话。请务必保护好本地机器。
- **面板访问**：Remnawave 管理 UI 由 nginx 在秘密 HTTPS 路径反向代理 — 不需要 SSH 隧道。连接页面和订阅链接使用单独的路径并永不显示面板的。URL 和管理员凭证在 `~/.config/meridian/cluster.yml` 的 `panel` 部分
- **SSH**：默认禁用密码认证，并启用 fail2ban（`--no-harden` 跳过两者加防火墙，用于与其他服务共享的服务器）
- **防火墙**：UFW 拒绝入站流量除了服务器的 SSH 端口和其角色需要的端口 — TCP/443 用于代理流量、TCP/80 用于证书签发、UDP/443 仅当启用 Hysteria2、以及中继上的监听端口
- **出口 DNS**：出口服务器通过加密 DNS 解析每个目标（DNS over HTTPS，同时询问 Cloudflare、Quad9 和 Google，均按 IP 地址连接），从不使用托管商的解析器，因此任何目标域名都不会以明文离开服务器。如果三者都没有响应，经该出口按域名访问的网站会失败，而不会退回明文 DNS；`meridian check` 会报告服务器无法连接的解析器
- **固定软件**：Remnawave 镜像固定到已测试版本；Realm、Xray（用于 `meridian test`）和 RealiTLScanner 下载是版本固定和校验和验证的
- **TLS**：[acme.sh](https://github.com/acmesh-official/acme.sh) 通过 Let's Encrypt 处理证书，由 nginx 提供

## 范围

Meridian 配置代理服务器 — 它**不**实现加密协议。底层安全取决于：

- [Xray-core](https://github.com/XTLS/Xray-core) — VLESS+Reality、XHTTP、WSS 和 Hysteria2 协议
- [Remnawave](https://remna.st/) — 面板与节点管理栈
- [nginx](https://nginx.org/) — SNI 路由和 TLS 终止
- [acme.sh](https://github.com/acmesh-official/acme.sh) — Let's Encrypt 证书签发与续期
- [Realm](https://github.com/zhboner/realm) — 中继服务器上的 TCP 转发
