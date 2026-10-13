---
title: 客户端管理
description: 添加用户、分享连接信息和管理访问密钥。
order: 5
section: guides
---

## 添加客户端

```
meridian client add alice
```

这给 "alice" 她自己的密钥并打印两样东西来分享：

- **一个带 QR 码的连接页面链接** — 最容易发送的
- **一个订阅 URL** 用于直接导入到 Xray/V2Ray 应用中

一次添加多个人：

```
meridian client add alice bob charlie
```

客户端属于您的整个集群，不属于一个服务器。您拥有的每个出口和中继都显示在他们的订阅中。

### 收件人看到的内容

连接页面从该人的真实订阅构建，所以它列出的正是他们的应用会收到的内容：直接出口、中继以及每种已启用的方式。页面包含：

- 两个步骤：安装为其平台推荐的应用（首选 Happ，其次是 FlClashX 和 Clash Mi，桌面端还有 Throne），然后轻点一下添加连接
- 订阅的二维码；在电脑上它显示在步骤旁边，方便用来设置手机
- 每个单独的连接，都带有“打开”“复制”和二维码按钮，平时折叠起来，需要时再展开
- “无法连接？”部分，包含一些提示和“检查我的连接”测试，会复制一份不含链接的报告，方便他们发给你

在应用中，条目按出口所在国家和方式命名：“🇩🇪 DE · Reality · Chrome”“🇩🇪 DE · XHTTP · Chrome”“🇩🇪 DE · WebSocket · CDN”“🇩🇪 DE · Hysteria2 · UDP”，中继条目另加“via relay”。同一国家有两个出口时，旗帜后显示它们的名称（“🇩🇪 main”）；国家未知的出口在有多个出口时显示其名称。Reality 和 XHTTP 还提供使用另一种 TLS 指纹的“· Firefox”副本，以防某一种指纹被封锁。会自动切换的应用（Xray JSON 应用如 Happ，以及 Mihomo 和 sing-box）会自行选择可用的条目，但不会在不同指纹之间切换；Happ 为此显示一个单独的“Automatic · Firefox”条目。

通过任何信使发送链接。收件人打开它，安装应用，点击“添加到 …”（或用另一台设备扫描二维码），然后连接。

## 显示连接详情

要再次分享客户端的链接：

```
meridian client show alice
```

这打印连接页面和订阅 URL，加上状态、流量和客户端最后一次见到的时间。如果页面缺失（例如上传失败），重新发布它：

```
meridian client show alice --repair-page
```

## 列出客户端

```
meridian client list
```

显示每个客户端的状态、流量、创建时间和最后一次见到的时间。

## 暂停和恢复客户端

```
meridian client disable alice
meridian client enable alice
```

`disable` 立即切断客户端；`enable` 用相同链接让他们回来。

禁用是临时的：下次应用将每个声明的客户端转回开启。Apply 运行于 `meridian apply`、重新部署以及当您添加客户端、节点或中继时。如果需要，这之后再禁用。

## 删除客户端

`meridian client remove NAME` 永久删除客户端：他们的链接停止工作，他们的连接页面被删除。集群至少保持一个客户端。不要直接在 Remnawave 面板中删除用户 — Meridian 在下次应用时重新创建拓扑中保留的任何客户端。

## 订阅链接

订阅 URL 看起来像 `https://198.51.100.10/<page-path>/sub/<id>`。它们位于连接页面路径下；Remnawave 管理面板位于共享链接永远不会暴露的单独密钥路径上。

应用自己刷新订阅，所以当您添加出口或中继时，客户端没有新链接就能获取它。

## 客户端数据位于何处

客户端账户（密钥、状态、流量）位于您的控制平面服务器上的 Remnawave 面板数据库中。Meridian 管理的客户端列表是您在 `~/.meridian/cluster.yml` 中的拓扑的一部分。客户端命令使用面板 API 并不需要 SSH；发布连接页面使用对控制平面服务器的 SSH。

## Web 面板

[Remnawave](https://remna.st/) 管理面板在浏览器中显示流量、用户和节点，您可以随意使用它。`meridian panel` 打印其 URL 和管理员用户名；`meridian panel --show-password` 也打印密码。

Meridian 和面板分工，所以您的编辑不会被覆盖：

| Meridian 管理（下一次应用恢复其版本） | 您的修改（apply 永远不会改变这些） |
|---|---|
| 节点、配置文件、入站、发布的主机、小队 | 每个客户端的状态：启用/禁用、流量限制、过期、HWID 限制、描述 |
| 订阅模板和 Meridian 设置的设置 | 您在面板中自己创建的用户 |
| 存在哪些客户端、他们的名字和他们的小队 | 通知、插件、IP 控制和其他面板设置 |

`meridian plan` 在任何事情改变之前显示 Meridian 管理内容的任何差异。您在面板中添加的用户从未被 Meridian 列出、改变或删除；要给某人一个 Meridian 管理的链接和连接页面，改为使用 `meridian client add`。
