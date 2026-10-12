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

连接页面从该人的真实订阅构建，所以它列出他们的应用接收的确切路径：直接出口、中继和启用时的 Hysteria2。它有：

- 分步说明安装应用（v2RayTun、v2rayNG、Hiddify 或 v2rayN）
- 每个连接路径的二维码
- 一键"在应用中打开"链接

通过任何信使发送链接。收件人打开它，安装应用，扫描二维码，并连接。

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

[Remnawave](https://remna.st/) 管理面板在浏览器中显示流量、用户和节点。其 URL 和管理员登录在 `~/.meridian/cluster.yml` 的 `panel` 部分，并且部署将 URL 打印为 "Remnawave panel (advanced)"。

使用面板来查看；使用 Meridian 来改变事物。Meridian 在下次应用时恢复它管理的用户、主机和模板，并且 `meridian plan` 首先显示任何差异。
