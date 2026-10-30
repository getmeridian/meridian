---
title: 在路由器上使用 Meridian
description: 通过 OpenWrt 上的 podkop 或 Keenetic 上的 XKeen 将家庭路由器连接到 Meridian 服务器，这样每台设备都能覆盖，无需安装应用。
---

连接到 Meridian VPN 服务器的路由器可以覆盖家中的每台设备，包括无法运行应用的电视和游戏机。本指南展示两种常见的设置：**podkop** 在 OpenWrt 上和 **XKeen** 在 Keenetic 上。两者都是社区项目，所以开始前请查阅他们的页面了解最新步骤。

## 获取链接

为路由器创建自己的客户端，这样可以在不影响他人的情况下禁用它：

```
meridian client add home-router
meridian client show home-router
```

`client show` 打印连接页面和订阅 URL。打开连接页面：每个路径（Reality、XHTTP、WSS 和每个中继）都有一个**复制链接**按钮，可复制 `vless://` 链接。

粘贴哪一个：

- **优先使用 Reality。** 它是主路径，也是下面每个路由器客户端都能运行的。
- **XHTTP** 需要基于 Xray 的客户端，且客户端版本较新、与服务器端的 Xray 版本相近。原版 sing-box（以及 podkop）不支持 XHTTP 传输；XKeen 运行 Xray，因此可以使用。
- **中继条目**也是 Reality 链接。如果你的路由器在俄罗斯，且出口 IP 被封锁，请粘贴中继的链接。
- **Hysteria2** 基于 UDP，不经过中继。仅当你的路由器客户端支持时才使用。

粘贴的链接是一次性快照。订阅 URL 会自动更新，但 podkop 的常见问题说明它没有内置订阅支持，因此当你以后添加出口或中继时，需要重新粘贴新链接。

### 客户端版本

较新的 Xray 服务器默认拒绝 sing-box（podkop）和 Mihomo 客户端，因为这些客户端上报的版本号较旧。Meridian 的出口明确接受所有支持 Reality 的客户端，因此这不会影响你。如果电脑上运行 `meridian test` 通过，但路由器仍然无法连接，请更新路由器上的代理内核。在 Keenetic 上运行 `xkeen -ux` 可更新 XKeen 的 Xray。

## OpenWrt with podkop

[podkop](https://podkop.net/) 只把你选择的域名和列表通过隧道转发，其余流量全部直连。这很适合俄罗斯用户：境内网站无需额外规则就能走普通宽带直连。

**要求**（来自 [podkop 文档](https://podkop.net/docs/install)）：OpenWrt 24.10（25.12 通过 apk 也可用），以及约 20 MB 的可用闪存。如果 sing-box 尚未安装，podkop 需要 `/overlay` 中有 40 MB 可用空间。当空间不足时，podkop 常见问题建议 `sing-box-tiny`。

**从路由器的 SSH shell 安装：**

```
sh <(wget -O - https://raw.githubusercontent.com/itdoginfo/podkop/refs/heads/main/install.sh)
```

如果无法从路由器访问 GitHub，podkop 在其安装页面上记录了镜像引导。

**在 LuCI 中的 Podkop 页面进行配置：**

1. 在 `Main` 部分，设置**连接类型** (Connection Type) 为 `Proxy`。
2. 设置**配置类型** (Configuration Type) 为 `Connection URL` 并粘贴 Reality `vless://` 链接。
3. 选择你想通过 Meridian 转发的社区规则列表，或添加你自己的域名。
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

在 `05_routing.json` 中设置路由。XKeen 指南描述了两种方式：仅将选定的域名和 IP 列表通过隧道发送，或让 `.ru`、`.su` 和 `.рф` 直连而其余流量通过隧道。然后检查并启动：

```
xkeen -xtest
xkeen -start
```

## 国内网站保持直连

默认情况下，出口拒绝到俄罗斯域名和 IP 范围的流量（`--geo-block`）。通过隧道发送所有内容的路由器会破坏俄罗斯网站。改为从路由器直接发送；podkop 默认这样做，使用 XKeen 可选择上面的第二种路由模式。[分割路由](/zh/split-routing/) 解释了各层。

## 注意事项

- **闪存、RAM 和 CPU。** podkop 需要闪存空间来存放 sing-box，存储空间已满的路由器会安装失败。较弱的路由器 CPU 会将吞吐量限制在远低于宽带速率的水平，因此在依赖它之前请先通过路由器实测速度。
- **时钟不准。** 许多路由器开机时系统时间不正确，直到完成 NTP 同步后才恢复正常。在此之前，订阅页面、XHTTP、WSS 和软件包下载的 TLS 证书校验都会失败。podkop 常见问题建议，如果安装脚本因证书错误失败，请先检查路由器的系统时间。
- **DNS 泄漏。** podkop 按域名分流，因此局域网设备必须将路由器设为 DNS 服务器，且浏览器不能使用自带的安全 DNS（DoH）绕过路由器。使用其他 DNS 服务器的设备会直接解析出真实地址并绕过隧道。根据 podkop 文档，这是“一台设备能用、另一台设备不能用”最常见的原因。
- **设备上的 VPN 应用。** 手机上如果同时开启了自己的 VPN 应用，会绕过路由器的分流规则。
- **被禁用的客户端。** `meridian client disable home-router` 会切断路由器的连接，直到你运行 `meridian client enable home-router`。

## 验证

从连接在该路由器下的设备打开任意 IP 查询页面：显示的应当是你的出口地址。在你自己的电脑上，`meridian test` 会端到端测试下发的订阅，`meridian check` 则会检查服务器端状态。如果结果不一致，请参阅[故障排除](/zh/troubleshooting/)。
