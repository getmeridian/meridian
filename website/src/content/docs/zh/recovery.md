---
title: IP 封锁恢复
description: 服务器 IP 被封锁后怎么办 — 诊断和恢复方案。
---

## 诊断

从你自己的机器运行（无需 SSH）：

```
meridian test 198.51.100.10
```

如果端口 443 的 TCP 检查在你的网络下失败，但在其他网络（另一条宽带或开启其他 VPN）下正常，说明该 IP 很可能已被封锁。如果在所有网络下 TCP 都失败，通常意味着云防火墙拦截或服务器已宕机。

当出口在你的网络下完全无响应时，`meridian test` 会在最后给出该出口的 `node replace` 命令（见下文）；使用 `--json` 时它位于 `data.recovery`。

要查看究竟是什么被封锁了，请在受审查的网络内部运行 [`meridian test 198.51.100.10 --probe`](/zh/cli-reference/#审查者的视角meridian-test---probe)：它会报告是服务器 IP 还是伪装 SNI 看起来被封锁。没有 CLI 的用户可以打开自己的连接页面；如果它在他们的网络上能加载，说明控制服务器从那里可以访问。

## 立即缓解

如果你使用**域名**部署（`--domain`）且该记录开启了 Cloudflare 代理，XHTTP 和 WSS 路径仍然可用——它们连接的是 Cloudflare，而不是你被封锁的 IP。使用 Xray JSON 或 Mihomo 订阅的应用会自动切换；其他应用可以手动选择 XHTTP 或 WSS 节点条目。

如果你配置了**中继**，只要中继本身仍能连上出口，通过中继连接的客户端就不受影响。

## 恢复方案

### 添加替代出口

只要你的控制服务器（运行控制服务的那台）仍可从你的机器连接，这是最快的方法：

```bash
# 1. 从你的提供商获取一台新 VPS（新 IP）
# 2. 将其添加到集群
meridian node add 198.51.100.20 --name replacement
```

客户端保留原有账户。他们会在下次订阅刷新时收到新出口——如果没有自动刷新，请让他们在应用中手动刷新订阅。

### 或将被封锁的出口迁移到新服务器

`node replace` 会将出口迁移到一台新服务器，保留其 ID、密钥、协议路径和中继，然后清理旧服务器：

```
meridian node replace exit-1 198.51.100.20
```

客户端会在下次订阅刷新时获取新地址。控制服务器无法以这种方式迁移，因为它的地址包含在每个订阅 URL 中。

如果旧服务器在你自己的网络下也被封锁，迁移仍会先部署新服务器，客户端随之切换过去；只有旧服务器的清理会失败。等封锁解除后，或在能连到旧服务器的网络下，运行 `meridian apply` 完成清理。如果永远做不到（你已在服务商处删除了它，或它一直被封锁），可以让 Meridian 忘记它：

```bash
meridian apply --forget-unreachable
```

这样 Meridian 会跳过这部分清理；控制服务仍会删除它自己保存的记录。在你于服务商处删除或清空旧服务器之前，它上面运行的服务会继续为原有用户提供连接，包括你之后删除的客户端。若想一步完成，可直接给 `node replace` 加上 `--forget-unreachable`。

### 添加域名用于 CDN 回退

如果你之前未使用域名，可以为新出口添加一个：

```bash
meridian node add 198.51.100.20 --domain vpn.example.com
```

Cloudflare 配置步骤见[域名模式](/zh/domain-mode/)。

### 添加中继

境内服务器上的中继可为用户提供一个在境外 IP 被封锁时仍可访问的入口点：

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.20
```

要让中继指向不同的出口，请先用 `meridian relay remove` 移除它，再用 `meridian relay add` 重新添加，并将 `--exit` 设为新出口。

### 控制服务器丢失

如果控制服务器已经没了（被删除、被查扣或被永久封锁），可以从快照在一台新服务器上重建它：

```bash
meridian restore --to 198.51.100.30
```

每台管理集群的机器在每次变更后都会保存一份控制服务的加密快照，所以最新的快照已经在你的机器上。restore 会先审核它要做的事，然后用这份快照在新服务器上安装控制服务，并应用集群：每个出口的 agent 都会向新服务器注册，客户端保留各自的账户、限额和流量。

- **链接**：使用裸 IP 时，每个订阅 URL 都包含控制服务器的地址，因此所有链接都会改变；restore 会打印每个客户端的新链接，供你发送。使用域名时，只要把域名的 DNS 记录指向新服务器，链接就保持不变；审核时会列出这些记录。
- **旧服务器**：只要旧控制服务器还在响应，restore 就会拒绝执行，因为两个控制服务会让集群分裂。如果它还在响应但你已不再信任它（例如已被查扣），请加上 `--force`。
- **密钥**：新服务器会得到新的证书颁发机构和新的签名密钥，因此被查扣的服务器无法签发你的节点会接受的任何内容。控制台所有者需要通过 `meridian panel` 打印的链接设置新密码。
- **另一台机器**：在没有该集群的机器上，把快照文件复制过去，运行 `meridian restore --to NEW_IP --snapshot FILE --recovery-key`，在提示时粘贴恢复密钥。快照中带有集群自己的文件；那台机器仍需要能通过 SSH 访问新服务器和你的出口。

恢复密钥只在首次部署结束时显示一次；`meridian backup recovery-key` 可以生成新的。请离线保存，远离管理集群的机器。参见 [`meridian restore`](/zh/cli-reference/#meridian-restore)。

## 从多台机器管理

Meridian 了解的关于集群的所有内容都保存在部署时所用机器的 `~/.config/meridian/` 中。要从第二台机器管理集群，请将该目录复制过去，但不要复制 `device.key` 和 `snapshots/`，这样第二台机器会在第一次变更时得到自己的备份密钥。在其他机器上每次做出更改后，再同步更新该目录。`meridian backup devices` 列出接收快照的机器，`meridian backup revoke` 可以移除你不再使用的机器。

你不会意外覆盖另一台机器所做的更改。控制服务会记录最近一次应用的变更。在修改任何内容前，Meridian 会将本地副本与该记录比对；如果 `apply` 会被拒绝，`meridian plan` 会提前给出警告：

- **"Another machine changed this fleet"（另一台机器修改了此集群）**：你的本地副本已过期。请从最后执行 `apply` 的机器复制 `~/.config/meridian/`，然后重新运行命令。
- **"Another apply is running"（另一个 apply 正在运行）**：当前有人正在修改集群。请等待其完成。如果那次运行意外中断，锁会在 30 分钟内自动过期；本机自己中断的运行不会阻塞你。
- **"This control server belongs to a different copy of the fleet"（此控制服务器属于另一份集群配置）**：当前机器上的 `~/.config/meridian/` 不是从管理该集群的机器复制过来的。

## 封锁前的准备

1. **在境内服务器上添加中继**：`meridian relay add RELAY_IP --exit EXIT_IP`
2. **使用域名**配置 CDN 备用线路：带 `--domain vpn.example.com` 部署
3. **两者兼备** — 这样客户端就有三条入口：中继（境内）、CDN（Cloudflare）和直连
4. **离线保存恢复密钥** — 有了它和一份快照，`meridian restore` 就能重建丢失的控制服务器
5. **备份 `~/.config/meridian/`** — 有了它才能从另一台电脑接管集群

## 保留旧服务器

不要立即销毁被封锁的服务器——有些封锁会在几天或几周后解除。可以不时检查一下：

```bash
meridian test 198.51.100.10
```

当确认不再需要时，再用 `meridian node remove` 将其移除。
