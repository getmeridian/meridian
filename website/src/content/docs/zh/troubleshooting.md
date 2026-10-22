---
title: 故障排除
description: 常见问题、解决方案和诊断工具。
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
3. SSH 进去并检查：`docker ps`（`ctlsvc-api` 和 `netagent` 运行吗？）、`systemctl status nginx`、`ss -tlnp sport = :443`
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

### 升级期间面板重命名中止

`meridian apply` 会把由 4.0 测试版部署的面板从 `/opt/remnawave` 移到 `/opt/ctlsvc`。在停止任何东西之前，如果两个目录都存在，或 `/opt/remnawave` 是挂载点，它会拒绝继续。请把存放面板 `data/` 和 `.env` 的那个目录保留为 `/opt/ctlsvc`（或把卷挂载到该处），把另一个移开，然后再次运行 `meridian apply`。重命名中途被打断时，下次 apply 会接着继续。

### SSH 连接错误

手动测试 SSH：`ssh root@SERVER_IP`。确保您有基于密钥的访问。如果不是 root，使用 `--user` 标志。如果服务器只接受密码，请在终端中不带 `--yes` 运行 `meridian deploy` 让它安装密钥（见[仅密码登录的服务器](/zh/deploy/#仅密码登录的服务器)），或者运行一次 `ssh-copy-id root@SERVER_IP`。

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
- 服务器重启且服务未启动 → `meridian plan` 显示什么在关闭，`meridian apply` 修复它。手工操作：在 `/opt/ctlsvc` 和 `/opt/netagent` 中运行 `docker compose up -d`，然后 `systemctl restart nginx`
- 磁盘满 → `df -h /`、`docker system prune -af`

## 速度慢

1. 选择地理位置更近的服务器（欧洲/中东的芬兰、荷兰、瑞典）
2. 检查服务器负载：`htop` 或 `uptime`
3. 尝试 XHTTP 或 WSS 路径（域名模式）— 通过 Cloudflare 路由有时更好
4. 验证 BBR 已启用：`sysctl net.ipv4.tcp_congestion_control`

**不要**在同一服务器上运行其他协议（OpenVPN、WireGuard）— 它会标记 IP。

## AI 辅助诊断

```
meridian doctor --ai
```

将诊断提示词复制到剪贴板，可直接粘贴给任何 AI 助手。

或者收集诊断信息用于提交 [GitHub issue](https://github.com/getmeridian/meridian/issues)：

```
meridian doctor
```

## 中继无法工作

中继相关问题（端口冲突、防火墙、无法连接出口、服务未启动）请参见[中继节点 — 故障排查](/zh/relay/#故障排查)。

## 解读预检输出

| 检查项 | 检查内容 | 失败时如何处理 |
|-------|--------------|-------------|
| SNI target reachability | 服务器能否访问伪装网站？ | 服务器出站受限。用 `--sni` 换一个 SNI |
| Port 443 availability | 端口 443 是否空闲或由 Meridian 占用？ | 其他服务占用了 443。停止该服务或使用干净的服务器 |
| Port 443 external reachability | 外部网络能否连上端口 443？ | 云防火墙拦截了入站连接。放行 TCP 443 入站 |
| Domain DNS | 域名是否解析到服务器 IP？ | 更新 DNS A 记录 |
| Server OS | 是否为 Ubuntu/Debian？ | 无 apt 的系统（CentOS、Fedora、Alpine）会失败；请重装为 Ubuntu 或 Debian |
| Disk space | 是否有至少 2GB 可用空间？ | 清理磁盘空间 |
| Clock sync | 客户端与服务器时间差是否小于 1 分钟？ | 有一方未同步时间。在两端开启 NTP 和自动设置时间 |

## 解读 doctor 输出

| 部分 | 关注重点 |
|---------|-----------------|
| Local Machine | 本地系统兼容性 |
| Server | 系统版本、运行时长（是否刚重启？）、磁盘与内存占用 |
| Docker | `ctlsvc-api`（面板）和 `netagent`（节点）容器是否在运行？状态应为 "Up" |
| Remnawave Node Logs | 节点启动报错、"failed to start" 记录、证书问题 |
| Listening Ports | 端口 443 应显示 nginx。若缺失，说明代理未运行 |
| Firewall (UFW) | 端口 443/tcp 应为 ALLOW。若未列出，说明被防火墙拦截 |
| SNI Target | 应显示 CONNECTED 及证书链 |
| Domain DNS | 应解析为服务器 IP |

## 解读 test 输出

| 检查项 | 通过 | 失败或跳过 |
|-------|------|-----------------|
| TCP reachability | 所有已配置的公开 TCP 监听端口均可连接 | 防火墙、ISP 封锁、服务器宕机或拓扑有误 |
| TLS/SNI camouflage | 目标返回预期的伪装站点证书与身份 | TLS 路由、证书、DNS 或 SNI 不匹配 |
| Domain root | 访问根路径返回加固后的 `403`/`404` | DNS/nginx 故障或暴露了非预期的公开内容 |
| Subscription | Remnawave 为活跃客户端返回了有效的 Xray JSON | 客户端缺失/未启用、面板故障或订阅模板损坏 |
| Automatic fallback | 下发的自动回退策略成功承载真实流量 | 负载均衡、路由、凭据或节点端点故障 |
| Protocol checks | 每个目标出站均成功承载真实流量 | 特定协议的监听端口、防火墙或下发配置故障 |

完整测试会轮换多个独立的公网 IP 观测服务，同时进行直连比对。如果代理通道和直连通道都无法获取 IP，结果会判定为“不确定”（inconclusive），而不会误报为协议故障。仅在你自己运行已知观测服务时才需设置 `MERIDIAN_CONNECT_TEST_URL`。

首次运行完整测试时，可能需要最多 120 秒从 GitHub 下载并校验固定版本的 Xray。初始化完成后，`--timeout`（默认 `5`，范围 `1`–`30`）会限制单次网络操作的超时时间。退出码 `0` 表示全部通过，`4` 表示测试完成但发现问题，`3` 表示缺少必要证据而无法得出结论。`--basic` 模式无法验证仅使用 UDP 的 Hysteria2 流量。

如果所有检查均通过但 VPN 客户端仍无法连接：请重新扫描二维码、确认设备已开启自动设置日期和时间，或换用其他推荐应用（Happ、FlClashX、Clash Mi，或桌面端的 Throne）。
