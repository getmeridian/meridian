---
title: 故障排除
description: 常见问题、解决方案和诊断工具。
order: 8
section: guides
---

## 使用哪种工具

```
安装前               → meridian preflight IP
  "这个服务器是否适合 Meridian？"
  测试：SNI 可达性、端口 443、DNS、操作系统和磁盘空间。

安装后，无法连接 → meridian test IP
  "从我所在的位置代理是否可达？"
  测试：TCP 端口 443、TLS 握手（Reality）和域名 HTTPS。
  无需 SSH — 在客户端设备上运行。

安装后，出现问题 → meridian doctor IP
  "收集所有内容用于调试。"
  收集：服务器操作系统、Docker、Remnawave 面板和节点日志、端口、防火墙、SNI 与 DNS。
```

添加 `--ai` 到 preflight 或 doctor 以获得 AI 就绪的诊断提示。

## 完全无法连接

### 端口 443 不可达

**原因：**
1. 云提供商防火墙 / 安全组阻止了入站端口 443
2. ISP 或网络完全阻止服务器 IP
3. 服务器已关闭或代理未运行
4. 服务器上的 UFW 不允许端口 443

**修复：**
1. 检查云提供商控制台 — 确保入站端口 443/TCP 被允许
2. 从不同的网络尝试（移动数据、另一个 Wi-Fi）
3. SSH 进入并检查：`docker ps`（`remnawave` 和 `remnawave-node` 是否运行？）、`systemctl status nginx`、`ss -tlnp sport = :443`
4. 检查 UFW：`ufw status` — 应该显示 443/tcp ALLOW

### TLS 握手失败

**原因：**
1. Xray 未在 Docker 容器内运行
2. 端口 443 被另一个服务占用
3. Reality SNI 目标从服务器无法访问

**修复：**
1. 检查 Xray：`docker logs remnawave-node --tail 20`
2. 检查端口：`ss -tlnp sport = :443` — 应该是 nginx
3. 测试 SNI：`meridian preflight IP`

### 域名无法访问

**原因：**
1. DNS 未指向服务器 IP
2. nginx 未运行或未能获取 TLS 证书
3. nginx 未正确路由域 SNI

**修复：**
1. 检查 DNS：`dig +short yourdomain.com @8.8.8.8`
2. 检查 nginx：`systemctl status nginx`
3. 检查 nginx 配置：`/etc/nginx/stream.d/meridian.conf`

## 连接在几秒钟后中断

**原因：**
1. 客户端和服务器之间的系统时钟偏差 >30 秒
2. 网络路径上的 MTU 问题
3. ISP 重置长期 TLS 会话

**修复：**
1. 服务器：`timedatectl set-ntp true`。客户端：启用自动日期/时间
2. 尝试不同的网络
3. 使用 WSS/CDN 连接（域名模式）

## 设置失败

### 端口 443 冲突

另一个服务（Apache、Nginx）正在使用端口 443。停止它或使用干净的服务器。`meridian preflight` 会告诉您什么在使用该端口。

### Docker 安装失败

来自 distro repos 的冲突 Docker 包。Meridian 会自动删除它们，但如果 Docker 已经运行有容器，它会跳过以避免中断。

### SSH 连接错误

手动测试 SSH：`ssh root@SERVER_IP`。确保您有基于密钥的访问。如果不是 root，请使用 `--user` 标志。

### 节点容器中的 Xray 无法启动

检查容器日志：`docker logs remnawave-node --tail 50`。常见原因包括主机端口冲突（节点使用 `network_mode: host`，因此 `cluster.yml` 中的端口必须空闲）、面板不可达（节点启动时需要面板的 `node_secret_key`）或缺少 `NET_ADMIN` 能力。

**修复：** `meridian teardown IP && meridian deploy IP` 会重新构建节点。要验证 Remnawave 面板状态，请登录 `https://<IP>/<secret_path>/` 的管理 UI 并查看 **Nodes**；节点应显示为 `connected`。`meridian fleet status` 可在 CLI 中显示相同信息。

### XHTTP 入站创建失败（端口冲突）

旧版 Meridian（v3.6.0 之前）曾让 Reality 和 XHTTP 同时使用端口 443。v4 会为每个节点分配确定性的 XHTTP、Reality 和 WSS 端口，并通过 nginx 反向代理，因此该冲突不会再次出现。

### 磁盘空间不足

少于 2GB 可用空间。释放空间：`docker system prune -af`、`journalctl --vacuum-time=1d`、检查 `/var/log/`。

### DNS 解析失败（域名模式）

域名尚未指向服务器 IP。更新 DNS A 记录。传播通常需要 5-15 分钟（最多 48 小时）。Meridian 会警告 DNS 未解析但允许继续。

## 曾经可以工作，现在停止了

**最常见的原因：** 服务器 IP 被阻止。运行 `meridian test IP`；如果 TCP 检查失败，IP 很可能已被阻止。

有关分步恢复选项（更换服务器、切换中继、CDN 回退），请参阅 [IP 被阻止恢复指南](/docs/zh/recovery/)。

其他原因：
- 服务器重启且服务未自动启动 → 在 `/opt/remnawave` 和 `/opt/remnanode` 中运行 `docker compose up -d`，然后运行 `systemctl restart nginx`
- 磁盘已满 → `df -h /`、`docker system prune -af`

## 速度缓慢

1. 选择地理位置更近的服务器（芬兰、荷兰、瑞典用于欧洲/中东）
2. 检查服务器负载：`htop` 或 `uptime`
3. 尝试 WSS/CDN 链接 — 通过 Cloudflare 的路由可能更好
4. 验证 BBR 是否启用：`sysctl net.ipv4.tcp_congestion_control`

**不要** 在同一服务器上运行其他协议（OpenVPN、WireGuard）— 这会标记该 IP。

## AI 辅助

```
meridian doctor --ai
```

将诊断提示复制到您的剪贴板以与任何 AI 助手一起使用。

或为 [GitHub issue](https://github.com/getmeridian/meridian/issues) 收集诊断：

```
meridian doctor
```

## 中继节点不工作

有关中继特定的问题，请参阅[中继指南 — 故障排除](/docs/zh/relay/#troubleshooting)部分。

## 解读 preflight 输出

| 检查 | 测试内容 | 如果失败 |
|------|---------|---------|
| SNI 目标可达性 | 服务器能否访问伪装站点？ | 服务器出站受限。用 `--sni` 尝试其他 SNI |
| SNI ASN 匹配 | SNI 目标是否与服务器共享 CDN/ASN？ | 使用全球 CDN 域名。避免 apple.com（Apple 专有 ASN） |
| 端口 443 可用性 | 端口 443 是否空闲或由 Meridian 使用？ | 其他服务占用 443。停止它或使用干净的服务器 |
| 端口 443 外部可达性 | 外部能否访问端口 443？ | 云防火墙阻止。开放入站 443/TCP |
| 域名 DNS | 域名是否解析到服务器 IP？ | 更新 DNS A 记录 |
| 服务器系统 | 是否为 Ubuntu/Debian？ | 其他发行版可能可用但未经测试 |
| 磁盘空间 | 至少 2GB 可用？ | 释放空间 |

## 解读 doctor 输出

| 部分 | 关注内容 |
|------|---------|
| 本地机器 | 系统兼容性 |
| 服务器 | 系统版本、运行时间（最近重启？）、磁盘/内存使用 |
| Docker | `remnawave` 和 `remnawave-node` 容器是否运行？状态应为 "Up" |
| Remnawave 日志 | 面板后端或节点的错误消息、"failed to start" 条目、证书问题 |
| 监听端口 | 端口 443 应显示 nginx。如缺失，代理未运行 |
| 防火墙 (UFW) | 端口 443/tcp 应为 ALLOW。如未列出，已被阻止 |
| SNI 目标 | 应显示 CONNECTED 及证书链 |
| 域名 DNS | 应解析到服务器 IP |

## 解读 test 输出

| 检查 | 通过 | 失败 |
|------|------|------|
| TCP 端口 443 | 服务器网络可达 | 防火墙、ISP 封锁或服务器宕机 |
| TLS 握手 | Reality 协议正常工作 | Xray 未运行、端口冲突或 SNI 问题 |
| Domain HTTPS | nginx 正常工作 | DNS 或 nginx 问题 |

如果所有检查通过但 VPN 客户端仍无法连接：重新扫描 QR 码、检查设备时钟准确性（30 秒内）、或尝试其他应用（v2rayNG、Hiddify）。
