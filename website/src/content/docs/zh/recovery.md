---
title: IP 封锁恢复
description: 服务器 IP 被封锁后怎么办 — 诊断和恢复方案。
---

## 诊断

从您自己的机器运行（无需 SSH）：

```
meridian test 198.51.100.10
```

如果 TCP 检查在端口 443 从您的网络失败，但从其他地方正常（另一网络或 VPN），IP 最可能被阻止了。TCP 在任何地方都失败通常意味着云防火墙或服务器已关闭。

要查看究竟什么被阻止了，请在受审查的网络内部运行 [`meridian test 198.51.100.10 --probe`](/zh/cli-reference/#审查者的视角meridian-test---probe)：它会报告是服务器 IP 还是伪装 SNI 看起来被阻止。没有 CLI 的用户可以打开自己的连接页面；如果它在他们的网络上能加载，说明控制服务器从那里可以访问。

## 立即缓解

如果您用**域名**部署（`--domain`）且记录通过 Cloudflare 代理，XHTTP 和 WSS 路径仍然工作 — 它们到达 Cloudflare，不是您被阻止的 IP。使用 Xray JSON 或 Mihomo 订阅的应用自动切换；其他应用可以手动选择 XHTTP 或 WSS 条目。

如果您有**中继**，通过它连接的客户端不受影响，只要中继本身仍能到达出口。

## 恢复选项

### 添加替代出口

最快的路径，只要您的控制服务器（运行面板的那个）仍可从您的机器到达：

```bash
# 1. 从您的提供商获得新 VPS（新 IP）
# 2. 将其添加到集群
meridian node add 198.51.100.20 --name replacement
```

客户端保留他们的账户。他们在下次订阅刷新时收到新出口 — 如果没有自动发生，请要求他们在应用中刷新。

### 或将被阻止的出口移到新服务器

`node replace` 将出口移到新服务器，保留其 ID、密钥、协议路径和中继，然后清理旧服务器：

```bash
meridian node replace exit-1 198.51.100.20
```

客户端在下次订阅刷新时获取新地址。控制服务器无法以这种方式移动：其地址是每个订阅 URL 的一部分。

### 添加域名用于 CDN 回退

如果您之前未使用域名，添加一个到新出口：

```bash
meridian node add 198.51.100.20 --domain vpn.example.com
```

Cloudflare 配置步骤见[域名模式](/zh/domain-mode/)。

### 添加中继

中继在国内服务器上为用户提供一个入口点，在外国 IP 被阻止时保持可到达：

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.20
```

要让中继指向不同的出口，请先用 `meridian relay remove` 移除它，再用 `meridian relay add` 重新添加，并将 `--exit` 设为新出口。

### 如果控制服务器丢失

`node add` 和 `relay add` 需要面板。如果托管它的服务器消失且无法恢复，Meridian 无法迁移其客户端：用 `meridian deploy` 部署新服务器，然后用 `meridian client add` 重新创建每个客户端。每个人获得新链接。

## 从多台机器管理

Meridian 了解的关于集群的所有内容都保存在部署时所用机器的 `~/.config/meridian/` 中。要从第二台机器管理集群，请将整个目录复制过去，并在其他机器上每次做出更改后同步更新该目录。

您不会意外覆盖另一台机器所做的更改。控制服务器会记录最近一次应用的变更（`/var/lib/meridian/fence.json`）。在修改任何内容前，Meridian 会将本地副本与该记录比对；如果 `apply` 会被拒绝，`meridian plan` 会提前给出警告：

- **"Another machine changed this fleet"（另一台机器修改了此集群）**：您的本地副本已过期。请从最后执行 `apply` 的机器复制 `~/.config/meridian/`，然后重新运行命令。
- **"Another apply is running"（另一个 apply 正在运行）**：当前有人正在修改集群。请等待其完成。如果那次运行意外中断，锁会在 30 分钟内自动过期；本机自己中断的运行不会阻塞您。
- **"This control server belongs to a different copy of the fleet"（此控制服务器属于另一份集群配置）**：当前机器上的 `~/.config/meridian/` 不是从管理该集群的机器复制过来的。

## 封锁前的准备

1. **在境内服务器上添加中继**：`meridian relay add RELAY_IP --exit EXIT_IP`
2. **使用域名**配置 CDN 备用线路：带 `--domain vpn.example.com` 部署
3. **两者兼备** — 这样客户端就有三条入口：中继（境内）、CDN（Cloudflare）和直连
4. **备份 `~/.config/meridian/`** — 有了它才能从另一台电脑接管集群

## 保留旧服务器

不要立即销毁被封锁的服务器——有些封锁会在几天或几周后解除。可以不时检查一下：

```bash
meridian test 198.51.100.10
```

当确认不再需要时，再用 `meridian node remove` 将其移除。
