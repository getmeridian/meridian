---
title: 在路由器上使用 Meridian
description: 通过 OpenWrt 上的 podkop 或 Keenetic 上的 XKeen 将家庭路由器连接到 Meridian 服务器，这样每台设备都能覆盖，无需安装应用。
order: 6.7
section: guides
---

运行代理客户端的路由器可以覆盖家中的每台设备，包括无法运行应用的电视和游戏机。本指南展示两种常见的设置：**podkop** 在 OpenWrt 上和 **XKeen** 在 Keenetic 上。两者都是社区项目，所以开始前请查阅他们的页面了解最新步骤。

## 获取链接

为路由器创建自己的客户端，这样可以在不影响他人的情况下禁用它：

```
meridian client add home-router
meridian client show home-router
```

`client show` 打印连接页面和订阅 URL。打开连接页面：每个路径（Reality、XHTTP、WSS 和每个中继）都有一个**复制链接**按钮，可复制 `vless://` 链接。

粘贴哪一个：

- **优先使用 Reality。** 它是主路径，也是下面每个路由器客户端都能运行的。
- **XHTTP** 需要一个基于 Xray 的客户端，并且该客户端的版本要最新且接近服务器的 Xray 版本。库存 sing-box 以及 podkop 没有 XHTTP 传输；XKeen 运行 Xray 并可以使用它。
- **中继条目**也是 Reality 链接。如果您的路由器在俄罗斯，出口的 IP 被阻止了，粘贴中继的链接。
- **Hysteria2** 是 UDP，不通过中继。仅在您的路由器客户端支持它时才使用。

粘贴的链接是一个快照。订阅 URL 会自动更新，但 podkop 的常见问题说它没有内置订阅支持，所以当您稍后添加出口或中继时，粘贴新链接。

### 客户端版本

最近的 Xray 服务器默认拒绝 sing-box（podkop）和 Mihomo 客户端，因为这些客户端宣传的版本号较旧。Meridian 的出口明确接受所有支持 Reality 的客户端，所以这不会影响您。如果路由器仍然无法连接而您的笔记本上 `meridian test` 通过了，请更新其代理核心。在 Keenetic 上，`xkeen -ux` 更新 XKeen 的 Xray。

## OpenWrt with podkop

[podkop](https://podkop.net/) 仅路由您选择的域名和列表通过代理，其他所有内容直接发送。这适合俄罗斯用户：国内网站保持在正常连接上，无需额外规则。

**要求**（来自 [podkop 文档](https://podkop.net/docs/install)）：OpenWrt 24.10（25.12 通过 apk 也可用），以及约 20 MB 的可用闪存。如果 sing-box 尚未安装，podkop 需要 `/overlay` 中有 40 MB 可用空间。当空间不足时，podkop 常见问题建议 `sing-box-tiny`。

**从路由器的 SSH shell 安装：**

```
sh <(wget -O - https://raw.githubusercontent.com/itdoginfo/podkop/refs/heads/main/install.sh)
```

如果无法从路由器访问 GitHub，podkop 在其安装页面上记录了镜像引导。

**在 LuCI 中的 Podkop 页面进行配置：**

1. 在 `Main` 部分，设置**连接类型** (Connection Type) 为 `Proxy`。
2. 设置**配置类型** (Configuration Type) 为 `Connection URL` 并粘贴 Reality `vless://` 链接。
3. 选择您想通过 Meridian 发送的社区列表，或添加您自己的域名。
4. **保存并应用** (Save & Apply)，然后打开诊断选项卡。从 LAN 运行，而不是通过远程端口转发。

要在直接 Reality 链接和中继链接之间进行故障转移，请选择 **URLTest** 并添加两个链接。sing-box 然后探测它们并使用一个有效的。

## Keenetic with XKeen

[XKeen](https://github.com/jameszeroX/XKeen) 通过 Entware 在 Keenetic 路由器上安装和管理 Xray。积极维护的分支也支持 Mihomo。

**要求：** 格式化为 EXT4 的 USB 驱动器（带 Entware）和启用路由器的 Netfilter 内核模块组件。XKeen 指南建议在驱动器上使用交换分区并在路由器的系统设置中关闭 RAM 压缩。

**以 root 身份从 Entware SSH shell 安装：**

```
opkg update && opkg upgrade && opkg install curl tar && cd /tmp
sh -c "$(curl -sSL https://raw.githubusercontent.com/jameszeroX/XKeen/main/install.sh)"
```

**在 `/opt/etc/xray/configs/` 中配置 Xray 文件。** 将 Reality 链接的值放入 `04_outbounds.json`：

```json
{
  "outbounds": [
    {
      "tag": "vless-reality",
      "protocol": "vless",
      "settings": {
        "vnext": [{
          "address": "198.51.100.10",
          "port": 443,
          "users": [{ "id": "UUID", "flow": "xtls-rprx-vision", "encryption": "none" }]
        }]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "fingerprint": "chrome",
          "serverName": "www.google.com",
          "publicKey": "PBK",
          "shortId": "SID"
        }
      }
    }
  ]
}
```

链接包含每个值：`@` 之前的部分是 UUID，然后是地址和端口。在查询中，`pbk` 是 `publicKey`，`sid` 是 `shortId`，`sni` 是 `serverName`，`fp` 是 `fingerprint`。当链接包含时，保持 `flow` 为 `xtls-rprx-vision`。

在 `05_routing.json` 中设置路由。XKeen 指南描述了两种方式：仅发送选定的域名和 IP 列表通过代理，或发送 `.ru`、`.su` 和 `.рф` 直接，其他所有内容通过。然后检查并开始：

```
xkeen -xtest
xkeen -start
```

## 国内网站保持直连

默认情况下，出口拒绝到俄罗斯域名和 IP 范围的流量（`--geo-block`）。通过隧道发送所有内容的路由器会破坏俄罗斯网站。改为从路由器直接发送；podkop 默认这样做，使用 XKeen 可选择上面的第二种路由模式。[分割路由](/zh/split-routing/) 解释了各层。

## 注意事项

- **闪存、RAM 和 CPU。** podkop 需要空间在闪存中存放 sing-box，而已经满的路由器会安装失败。CPU 较弱会将吞吐量限制在远低于您的线速，所以在依赖它之前要通过路由器进行测试。
- **错误的时钟。** 许多路由器启动时时间不对，直到 NTP 响应。TLS 证书检查然后对订阅页面、XHTTP、WSS 和包下载失败。当其安装程序因证书错误失败时，podkop 常见问题建议您先检查路由器的日期。
- **DNS 泄漏。** podkop 按域名路由，所以设备必须使用路由器作为 DNS 服务器，浏览器不得使用其自己的安全 DNS 绕过它。设备在另一个 DNS 服务器上会解析为真实地址并跳过隧道。根据 podkop 文档，这是"在一台设备上工作，在另一台上不工作"最常见的原因。
- **设备上的 VPN 应用。** 运行自己的 VPN 应用的手机会跳过路由器的路由。
- **禁用的客户端。** `meridian client disable home-router` 会切断路由器的连接，直到你运行 `meridian client enable home-router`。

## 验证

从路由器后面的设备打开一个 IP 检查页面：它应该显示您出口的地址。从您自己的机器上，`meridian test` 检查端到端传递的订阅，`meridian check` 确认服务器端。如果它们不一致，请参阅[故障排查](/zh/troubleshooting/)。
