---
title: 故障排除
description: 常见问题、解决方案和诊断工具。
order: 8
section: guides
---

## 使用哪种工具

```
安装前           → meridian check IP
  "这个服务器是否适合 Meridian？"
  测试：SNI 可达性、端口 443、DNS、操作系统、磁盘空间。

安装后，是否健康？ → meridian check
  "面板、节点、中继、端口和证书都好吗？"
  按角色测试舰队中的每个服务器，通过 SSH。

安装后，无法连接 → meridian test IP
  "代理从我现在的地方是否可达？"
  测试：已配置的监听器、TLS/SNI、交付的订阅和真实代理流量。
  无需 SSH — 从客户端设备运行。

安装后，检查暴露 → meridian test IP --probe
  "此部署是否暴露代理或管理表面？"
  测试：公开端口、TLS/HTTP 行为、路径、SNI、DNS 和拓扑策略。
  无需 SSH — 从客户端设备运行。

安装后，出了问题 → meridian doctor IP
  "收集一切用于调试。"
  收集：服务器 OS、Docker、Remnawave 节点和 nginx 日志、端口、防火墙、SNI、DNS。
```

添加 `--ai` 到 check 或 doctor 以获得 AI 就绪的诊断提示。

## 完全无法连接

### 端口 443 无法到达

**原因：**
1. 云提供商防火墙/安全组阻止了端口 443 入站
2. ISP 或网络完全阻止服务器 IP
3. 服务器宕机或代理未运行
4. 服务器上的 UFW 不允许端口 443

**修复：**
1. 检查云提供商控制台 — 确保端口 443/TCP 入站被允许
2. 从不同网络尝试（移动数据、另一个 Wi-Fi）
3. SSH 进去并检查：`docker ps`（`remnawave` 和 `remnawave-node` 运行吗？）、`systemctl status nginx`、`ss -tlnp sport = :443`
4. 检查 UFW：`ufw status` — 应该显示 443/tcp ALLOW

### TLS 握手失败

**原因：**
1. Xray 未在 Docker 容器内运行
2. 端口 443 被另一服务占用
3. Reality SNI 目标从服务器无法到达

**修复：**
1. 检查 Xray：`meridian logs IP -c node -n 20`
2. 检查端口：`ss -tlnp sport = :443` — 应该是 nginx
3. 测试 SNI：`meridian check IP --sni HOST`

### 域名无法到达

**原因：**
1. DNS 未指向服务器 IP
2. nginx 未运行或未能获取 TLS 证书
3. nginx 未正确路由域 SNI

**修复：**
1. 检查 DNS：`dig +short yourdomain.com @8.8.8.8`
2. 检查 nginx：`systemctl status nginx`
3. 检查 nginx 路由文件：`ls /etc/nginx/stream.d/ /etc/nginx/sites-enabled/`（Meridian 的文件以 `meridian-v4-` 开头）并运行 `nginx -t`

## 连接在数秒后断开

**原因：**
1. 网络路径上的 MTU 问题
2. ISP 重置长期 TLS 会话

**修复：**
1. 尝试不同的网络
2. 使用 XHTTP 或 WSS 路径（域名模式）

## 部署失败

### "没有流量通过它"

服务器部署了，但真实流量未通过您用 `--sni` 选择的伪装目标。选择另一个目标 — `meridian scan IP` 在服务器自己的网络上找到可用的 — 并用新的 `--sni` 再次运行部署命令。没有 `--sni`，Meridian 已经依次尝试 `www.google.com`、`dl.google.com` 和 `www.cloudflare.com`。

### "服务器无法到达任何 Reality 伪装目标"

服务器对任何默认目标没有工作的出站 HTTPS。检查提供商的出站防火墙，或用 `--sni` 传递可到达的目标。

### 中断或失败的部署

部署和应用有检查点。修复报告的原因并再次运行相同的命令；完成的步骤被跳过。

### 端口 443 冲突

另一服务（Apache、Nginx）正在使用端口 443。停止它或使用干净的服务器。`meridian check IP` 会告诉您什么在使用该端口。

### Docker 安装失败

来自 distro repos 的冲突 Docker 包。Meridian 自动删除它们，但如果 Docker 已经运行有容器，它会跳过以避免中断。

### SSH 连接错误

手动测试 SSH：`ssh root@SERVER_IP`。确保您有基于密钥的访问。如果不是 root，使用 `--user` 标志。

### Xray 无法在节点容器中启动

检查容器：`meridian logs IP -c node -n 50`。常见原因是主机上的端口冲突（节点运行在 `network_mode: host`，所以来自 `cluster.yml` 的端口必须空闲）、无法到达的面板（节点启动需要面板的 `node_secret_key`）或缺失 `NET_ADMIN` 能力。

**修复：** 运行 `meridian check IP` 并在重新部署前遵循其补救。要验证 Remnawave 面板状态，登录管理 UI 在 `https://<IP>/<secret_path>/` 并检查 **Nodes** — 节点应该是 `connected`；`meridian fleet status` 从 CLI 表面相同信息。

### 磁盘空间不足

少于 2GB 空闲。释放空间：`docker system prune -af`、`journalctl --vacuum-time=1d`、检查 `/var/log/`。

### DNS 解析失败（域名模式）

您的域名的证书只能在域名的 A 记录指向服务器后发行（部署时灰色云）。更新记录，等待它传播（通常 5-15 分钟），然后再次运行相同的部署命令。`meridian check IP --domain DOMAIN` 在您部署前检查这个。

## 曾经工作，现在停止了

**最常见的原因：** 服务器 IP 被阻止。运行 `meridian test IP`；其发现区分网络可达性、TLS、订阅和代理执行失败。TCP 失败也可能意味着云防火墙或离线服务器。

见 [IP 被阻止恢复指南](/zh/recovery/) 了解恢复选项（替代出口、中继、CDN 回退）。

其他原因：
- 服务器重启且服务未启动 → `meridian plan` 显示什么在关闭，`meridian apply` 修复它。手工操作：在 `/opt/remnawave` 和 `/opt/remnanode` 中运行 `docker compose up -d`，然后 `systemctl restart nginx`
- 磁盘满 → `df -h /`、`docker system prune -af`

## 速度慢

1. 选择地理位置更近的服务器（欧洲/中东的芬兰、荷兰、瑞典）
2. 检查服务器负载：`htop` 或 `uptime`
3. 尝试 XHTTP 或 WSS 路径（域名模式）— 通过 Cloudflare 路由有时更好
4. 验证 BBR 已启用：`sysctl net.ipv4.tcp_congestion_control`

**不要**在同一服务器上运行其他协议（OpenVPN、WireGuard）— 它会标记 IP。

## AI 供电的帮助

```
meridian doctor --ai
```

复制诊断提示到您的剪贴板用于任何 AI 助手。

或为 [GitHub issue](https://github.com/getmeridian/meridian/issues) 收集诊断：

```
meridian doctor
```

## 中继不工作

见 [Relay 指南 — 故障排除](/zh/relay/#故障排查) 部分了解中继特定的问题（端口冲突、防火墙、出口无法到达、服务未启动）。

## 解释预检输出

| 检查 | 它测试什么 | 如果失败 |
|-------|--------------|-------------|
| SNI 目标可达性 | 服务器是否能到达伪装网站？ | 服务器出站受限。用 `--sni` 尝试不同的 SNI |
| 端口 443 可用性 | 端口 443 是空闲的还是被 Meridian 使用？ | 另一服务在 443 上。停止它或使用干净的服务器 |
| 端口 443 外部可达性 | 外部世界是否能到达端口 443？ | 云防火墙阻止它。打开端口 443/TCP 入站 |
| 域 DNS | 域是否解析到服务器 IP？ | 更新 DNS A 记录 |
| 服务器 OS | 是 Ubuntu/Debian 吗？ | 没有 apt 的系统（CentOS、Fedora、Alpine）会失败；请重装 Ubuntu 或 Debian |
| 磁盘空间 | 至少 2GB 空闲？ | 释放空间 |
| 时钟同步 | 客户端/服务器漂移是否小于一分钟？ | 有一方没有同步时间。在两个设备上启用 NTP 和自动时间 |

## 解释医生输出

| 部分 | 要查找什么 |
|---------|-----------------|
| 本地机器 | OS 兼容性 |
| 服务器 | OS 版本、正常运行时间（最近重启？）、磁盘/内存使用 |
| Docker | `remnawave` 和 `remnawave-node` 容器是否运行？ 状态应该是"Up" |
| Remnawave 节点日志 | 节点启动错误、"failed to start" 条目、证书问题 |
| 监听端口 | 端口 443 应该显示 nginx。如果缺失，代理未运行 |
| 防火墙 (UFW) | 端口 443/tcp 应该 ALLOW。如果未列出，它被阻止 |
| SNI 目标 | 应该显示 CONNECTED，带证书链 |
| 域 DNS | 应该解析到服务器 IP |

## 解释测试输出

| 检查 | 通过 | 失败或跳过 |
|-------|------|-----------------|
| TCP 可达性 | 每个已配置的公开 TCP 监听器可达 | 防火墙、ISP 阻止、服务器宕机或拓扑错误 |
| TLS/SNI 伪装 | 目标呈现预期的伪装身份 | TLS 路由、证书、DNS 或 SNI 不匹配 |
| 域根 | 加固的 `403`/`404` 响应可达 | DNS/nginx 失败或意外公开内容 |
| 订阅 | Remnawave 为活跃客户端交付有效 Xray JSON | 缺失/无效客户端、面板失败或损坏模板 |
| 自动回退 | 交付的策略承载真实流量 | 平衡器、路由、凭证或端点失败 |
| 协议检查 | 每个目标出站承载真实流量 | 协议特定监听器、防火墙或交付配置失败 |

完整测试轮转独立公开 IP 观察者并直接检查相同观察者。如果代理和控制路径都无法获得 IP，结果是不确定的而不是假协议失败。仅在您运营已知观察者端点时设置 `MERIDIAN_CONNECT_TEST_URL`。

第一个完整测试可能花费最多 120 秒从 GitHub 下载和验证固定 Xray 版本。引导后，`--timeout`（默认 `5`、范围 `1`-`30`）应用于个别网络操作。退出 `0` 通过，`4` 完成有负面发现，`3` 不确定因为必需证据被跳过。基本模式无法证实 UDP 只的 Hysteria 流量。

如果所有检查通过但 VPN 客户端仍无法连接：重新扫描 QR 码、检查设备是否自动设置日期和时间或尝试不同的应用（v2rayNG、Hiddify）。
