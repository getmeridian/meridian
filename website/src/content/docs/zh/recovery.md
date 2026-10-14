---
title: IP 封锁恢复
description: 服务器 IP 被封锁后怎么办 — 诊断和恢复方案。
order: 7
section: guides
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

最快的路径，只要您的控制平面服务器（运行面板的那个）仍可从您的机器到达：

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

客户端在下次订阅刷新时获取新地址。控制平面服务器无法以这种方式移动：其地址是每个订阅 URL 的一部分。

### 添加域名用于 CDN 回退

如果您之前未使用域名，添加一个到新出口：

```bash
meridian node add 198.51.100.20 --domain vpn.example.com
```

见 [Domain mode](/en/domain-mode/) 的 Cloudflare 步骤。

### 添加中继

中继在国内服务器上为用户提供一个入口点，在外国 IP 被阻止时保持可到达：

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.20
```

要让中继指向不同的出口，请先用 `meridian relay remove` 移除它，再用 `meridian relay add` 重新添加，并将 `--exit` 设为新出口。

### 如果控制平面服务器丢失

`node add` 和 `relay add` 需要面板。如果托管它的服务器消失且无法恢复，Meridian 无法迁移其客户端：用 `meridian deploy` 部署新服务器，然后用 `meridian client add` 重新创建每个客户端。每个人获得新链接。

## 从多台机器管理

您 Meridian 了解的关于集群的所有内容都位于您部署的机器上的 `~/.config/meridian/` 中。要从第二台机器管理集群，将整个目录复制到该机器，并在您在其他地方进行每次更改后再复制一次。

您无法意外撤销另一台机器的工作。控制服务器保留最后应用变更的小记录（`/var/lib/meridian/fence.json`）。在改变任何事情之前，Meridian 比较您的副本与该记录，`meridian plan` 提前警告如果应用会被拒绝：

- **"另一台机器改变了这个集群"**：您的副本过期了。从最后应用的机器复制 `~/.config/meridian/`，然后再次运行命令。
- **"另一个应用正在运行"**：有人现在正在改变集群。等他们完成。如果该运行崩溃，其声明在 30 分钟内过期；您自己机器上的崩溃运行根本不会阻止您。
- **"此控制服务器属于集群的不同副本"**：这个 `~/.config/meridian/` 从未从管理集群的机器复制过。

## 块前准备

1. **添加中继**在国内服务器上：`meridian relay add RELAY_IP --exit EXIT_IP`
2. **使用域名** CDN 回退：用 `--domain vpn.example.com` 部署
3. **两者** — 客户端然后有三个进入方式：中继（国内）、CDN（Cloudflare）和直接
4. **备份 `~/.config/meridian/`** — 它是让您从另一台机器管理集群的方式

## 保留旧服务器

不要立即拆卸被阻止的服务器 — 块有时在天数或数周后被解除。检查不时：

```bash
meridian test 198.51.100.10
```

当您不再需要它时，使用 `meridian node remove` 删除它。
