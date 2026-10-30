---
title: 故障排除
description: 常见问题、解决方案和诊断工具。
---

## 使用哪种工具

```
安装前                   → meridian check IP
  "这台服务器适合部署 Meridian 吗？"
  检查：SNI 可达性、端口 443、DNS、操作系统、磁盘空间。

安装后，运行状态是否正常 → meridian check
  "控制服务、agent、中继、端口和证书都正常吗？"
  通过 SSH 按角色检查集群中的每台服务器。

安装后，无法连接         → meridian test IP
  "从我现在所在的网络能连上 VPN 吗？"
  检查：已配置的监听端口、TLS/SNI、下发的订阅以及真实 VPN 流量。
  无需 SSH — 在客户端设备上运行。

安装后，检查暴露面       → meridian test IP --probe
  "这次部署是否暴露了 VPN 特征或管理后台？"
  检查：公开端口、TLS/HTTP 行为、路径、SNI、DNS 和拓扑策略。
  无需 SSH — 在客户端设备上运行。

安装后，出现故障         → meridian doctor IP
  "收集用于排查故障的全部信息。"
  收集：服务器系统、Meridian 的服务、agent 与 nginx 日志、端口、防火墙、SNI、DNS。
```

在 `check` 或 `doctor` 后加上 `--ai` 可生成直接发给 AI 助手的诊断提示词。

## 完全无法连接

### 端口 443 无法连接

**原因：**
1. 云厂商防火墙或安全组拦截了端口 443 入站流量
2. ISP 或所在网络完全封锁了服务器 IP
3. 服务器宕机或 VPN 服务未运行
4. 服务器上的 UFW 未放行端口 443

**修复：**
1. 检查云厂商控制台——确保已放行 TCP 443 入站
2. 换一个网络尝试（移动数据、另一个 Wi-Fi）
3. SSH 登录服务器检查：`systemctl status ctld netagent netcore nginx`（它们是否都处于运行状态？）、`ss -tlnp sport = :443`
4. 检查 UFW：`ufw status`——应显示 `443/tcp ALLOW`

### TLS 握手失败

**原因：**
1. Xray（`netcore`）未在运行
2. 端口 443 被其他服务占用
3. 服务器无法访问 Reality 伪装目标站点

**修复：**
1. 检查 Xray：`meridian logs IP -c node -n 20`
2. 检查端口：`ss -tlnp sport = :443`——监听进程应为 nginx
3. 测试 SNI：`meridian check IP --sni HOST`

### 域名无法连接

**原因：**
1. DNS 未指向服务器 IP
2. nginx 未运行或未能成功获取 TLS 证书
3. nginx 未能正确路由该域名的 SNI

**修复：**
1. 检查 DNS：`dig +short yourdomain.com @8.8.8.8`
2. 检查 nginx：`systemctl status nginx`
3. 检查 nginx 路由文件：`ls /etc/nginx/stream.d/ /etc/nginx/sites-enabled/`（Meridian 的配置文件以 `meridian-v4-` 开头）并运行 `nginx -t`

## 连接几秒后断开

**原因：**
1. 网络路径上存在 MTU 问题
2. ISP 重置了长时间保持的 TLS 会话

**修复：**
1. 换一个网络尝试
2. 使用 XHTTP 或 WSS 路径（域名模式）

## 部署失败

### "No traffic passes through it"（没有流量通过它）

服务器已部署完成，但真实流量无法通过你用 `--sni` 指定的伪装目标。请换一个伪装目标——`meridian scan IP` 可以在服务器同网段内寻找可用站点——然后带上新的 `--sni` 重新运行部署命令。不带 `--sni` 时，Meridian 会依次尝试同网段站点以及 `www.google.com`、`dl.google.com` 和 `www.cloudflare.com`。

### "The server cannot reach any Reality camouflage target"（服务器无法访问任何 Reality 伪装目标）

服务器无法通过出站 HTTPS 访问任何默认伪装目标。请检查云厂商的出站防火墙，或用 `--sni` 指定一个服务器可访问的目标。

### 部署中断或失败

部署和 `apply` 均带有检查点。排除报错原因后再次运行同一条命令即可，已完成的步骤会自动跳过。

### 端口 443 冲突

其他服务（Apache、nginx）占用了端口 443。请停止该服务或使用干净的服务器。`meridian check IP` 会显示占用该端口的进程。

### 从 Remnawave 迁移中止

`meridian migrate` 在每一步之后都会保存进度。修复报告的原因后再次运行即可：它会从中断处继续，已完成的迁移不会再做任何事。在任何改动之前，面板数据库都会先备份到 `~/.config/meridian/backups/` 和控制服务器上的 `/var/backups/meridian-migration/`。如果应用之后链接检查失败，Remnawave 会保留在原处（处于停止状态），命令也会说明这一点：这是一个 bug，请附上 `meridian doctor` 的输出[提交 issue](https://github.com/getmeridian/meridian/issues)。

### SSH 连接错误

手动测试 SSH：`ssh root@SERVER_IP`。请确保已配置密钥登录。如果不是 `root` 用户，请加上 `--user` 标志。如果服务器只接受密码，请在终端中不带 `--yes` 运行 `meridian deploy` 让它自动安装密钥（见[仅密码登录的服务器](/zh/deploy/#仅密码登录的服务器)），或者先运行一次 `ssh-copy-id root@SERVER_IP`。

### 节点 agent 离线或未应用配置

查看日志：`meridian logs IP -c node -n 50` 会显示 agent（`netagent`）和 Xray（`netcore`）。常见原因包括宿主机端口冲突（Xray 直接绑定拓扑中的端口，这些端口必须空闲），或出口无法通过 TCP 443 访问控制服务器（agent 会主动连接它；请检查服务商的出站防火墙）。

**修复：** 运行 `meridian check IP` 并按提示修复，然后运行 `meridian apply`：它会为丢失身份的 agent 重新注册，并升级运行旧构建的 agent。`meridian fleet status` 会显示每个 agent 是否在线、是否已应用当前配置；网页面板的 **Servers** 页面也显示同样的信息。

### 磁盘空间不足

可用空间不足 2GB。请清理空间：`journalctl --vacuum-time=1d`、`apt-get clean`，并检查 `/var/log/`。

### DNS 解析失败（域名模式）

只有当域名的 A 记录直接指向服务器（部署期间保持灰色云朵）时，才能为该域名签发证书。请更新 DNS 记录，等待解析生效（通常 5–15 分钟），然后再次运行相同的部署命令。部署前可用 `meridian check IP --domain DOMAIN` 检查解析状态。

## 之前正常，现在无法连接

**最常见的原因：** 服务器 IP 被封锁。运行 `meridian test IP`，其检查结果会区分网络连通性、TLS、订阅和隧道故障。TCP 失败也可能是云防火墙拦截或服务器停机所致。

恢复方案（添加替代出口、配置中继、启用 CDN 备用线路）见 [IP 封锁恢复](/zh/recovery/)。

其他原因：
- 服务器重启后服务未随之启动 → `meridian plan` 会显示未运行的组件，`meridian apply` 会将其修复。也可手动恢复：`systemctl restart ctld netagent nginx`
- 磁盘已满 → `df -h /`、`journalctl --vacuum-time=1d`
- 控制服务器永久丢失 → 用 `meridian restore` 从快照重建它（[控制服务器丢失](/zh/recovery/#控制服务器丢失)）
- 已连接，但按域名无法打开网站（按 IP 地址可以）→ 出口服务器无法连接任何加密 DNS 解析器。`meridian check` 会在 **Encrypted DNS** 下列出它们；请在服务商防火墙中放行到这些地址的出站 TCP 443

## 速度慢

1. 选择离用户网络路由更近的服务器（面向欧洲/中东可选芬兰、荷兰、瑞典）
2. 检查服务器负载：`htop` 或 `uptime`
3. 尝试 XHTTP 或 WSS 路径（域名模式）——经由 Cloudflare 的路由有时更快
4. 确认已启用 BBR：`sysctl net.ipv4.tcp_congestion_control`

**请勿**在同一台服务器上混跑其他协议（OpenVPN、WireGuard）——这很容易导致该 IP 被标记。

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
| Camouflage target | 服务器能否访问伪装网站？ | 服务器出站受限。用 `--sni` 换一个 SNI |
| Camouflage target DNS | 伪装网站能否通过服务器 DNS 解析？ | 修复服务器的 DNS 解析器或换一个伪装网站 |
| Port 443 | 端口 443 是否空闲或由 Meridian 占用？ | 其他服务占用了 443。停止该服务或使用干净的服务器 |
| Port 443 from outside | 外部网络能否连上端口 443？ | 云防火墙拦截了入站连接。放行 TCP 443 入站 |
| Domain DNS | 域名是否解析到服务器 IP？ | 更新 DNS A 记录 |
| Server OS | 是否为 Ubuntu/Debian？ | 无 apt 的系统（CentOS、Fedora、Alpine）会失败；请重装为 Ubuntu 或 Debian |
| Disk space | 是否有至少 2GB 可用空间？ | 清理磁盘空间 |
| Clock sync | 客户端与服务器时间差是否小于 1 分钟？ | 有一方未同步时间。在两端开启 NTP 和自动设置时间 |

## 解读 doctor 输出

| 部分 | 关注重点 |
|---------|-----------------|
| Local Machine | 本地系统兼容性 |
| Server | 系统版本、运行时长（是否刚重启？）、磁盘与内存占用 |
| Services | `ctld`（控制服务）、`netagent`（agent）和 `netcore`（Xray）是否处于运行状态？ |
| Exit node Logs | agent 和 Xray 的启动报错、"failed to start" 记录、证书问题 |
| Listening Ports | 端口 443 应显示 nginx。若缺失，说明 nginx 未运行 |
| Firewall (UFW) | 端口 443/tcp 应为 ALLOW。若未列出，说明被防火墙拦截 |
| Camouflage Target | 应显示 CONNECTED 及证书链 |
| Domain DNS | 应解析为服务器 IP |

## 解读 test 输出

| 检查项 | 通过 | 失败或跳过 |
|-------|------|-----------------|
| TCP reachability | 所有已配置的公开 TCP 监听端口均可连接 | 防火墙、ISP 封锁、服务器宕机或拓扑有误 |
| TLS/SNI camouflage | 目标返回预期的伪装站点证书与身份 | TLS 路由、证书、DNS 或 SNI 不匹配 |
| Domain root | 访问根路径返回加固后的 `403`/`404` | DNS/nginx 故障或暴露了非预期的公开内容 |
| Subscription | 控制服务为活跃客户端返回了有效的 Xray JSON | 客户端缺失或未启用，或控制服务无法访问 |
| Automatic fallback | 下发的自动回退策略成功承载真实流量 | 负载均衡、路由、凭据或节点端点故障 |
| Protocol checks | 每个目标出站均成功承载真实流量 | 特定协议的监听端口、防火墙或下发配置故障 |

完整测试会轮换多个独立的公网 IP 观测服务，同时进行直连比对。如果 VPN 隧道和直连通道都无法获取 IP，结果会判定为“不确定”（inconclusive），而不会误报为协议故障。仅在你自己运行已知观测服务时才需设置 `MERIDIAN_CONNECT_TEST_URL`。

首次运行完整测试时，可能需要最多 120 秒从 GitHub 下载并校验固定版本的 Xray。初始化完成后，`--timeout`（默认 `5`，范围 `1`–`30`）会限制单次网络操作的超时时间。退出码 `0` 表示全部通过，`4` 表示测试完成但发现问题，`3` 表示缺少必要证据而无法得出结论。`--basic` 模式无法验证仅使用 UDP 的 Hysteria2 流量。

如果所有检查均通过但 VPN 客户端仍无法连接：请重新扫描二维码、确认设备已开启自动设置日期和时间，或换用其他推荐应用（Happ、FlClashX、Clash Mi，或桌面端的 Throne）。
