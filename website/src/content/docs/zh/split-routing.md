---
title: 分流（俄罗斯）
description: 让俄罗斯流量直连，其他流量走代理——既保护 VPS IP，又确保俄罗斯网站正常访问。
order: 6.5
section: guides
---

## 问题

Meridian 默认启用**服务器端地理封锁**：Xray 服务器会丢弃发往俄罗斯域名和 IP 的流量（通过 `geosite:category-ru` + `geoip:ru` 路由规则）。这样可以防止您的 VPS IP 出现在俄罗斯服务的日志中。

但这也意味着俄罗斯网站无法通过 VPN 访问。用户必须断开 VPN，才能访问 Yandex、VK、Gosuslugi 及其他俄罗斯本地服务。

## 解决方案：客户端分流

分流会让俄罗斯流量从客户端**直接连接**（绕过代理），其他所有流量则通过 VPN：

```
Russian sites  → Client → Direct → Internet (via local ISP)
Everything else → Client → Proxy → VPS → Internet
```

这样可以兼得：
- **VPS 保护**——服务器端地理封锁继续作为安全网
- **正常访问俄罗斯网站**——客户端让这些流量直连，完全不经过代理

## 配置方法

Meridian 使用 Remnawave 的订阅模板系统。订阅模板决定客户端导入连接时会收到哪些路由规则。

### 步骤 1：打开 Remnawave 面板

前往您的面板 URL（`meridian deploy` 完成后会显示）并登录。

### 步骤 2：编辑订阅模板

1. 前往**订阅设置** → **模板**
2. 找到 **Xray JSON** 模板（或新建一个）
3. 用下方分流配置替换其中的路由/DNS 部分

### Xray JSON 模板

将以下部分添加到 Xray JSON 订阅模板中。`dns` 块确保俄罗斯域名通过本地 DNS 服务器解析，`routing` 块则让俄罗斯流量直连：

```json
{
    "log": {
        "loglevel": "warning"
    },
    "dns": {
        "servers": [
            {
                "address": "https://dns.google/dns-query",
                "domains": ["geosite:geolocation-!cn"]
            },
            "8.8.8.8",
            {
                "address": "localhost",
                "domains": [
                    "geosite:category-ru",
                    "domain:.ru",
                    "domain:.su",
                    "domain:.рф"
                ],
                "expectIPs": ["geoip:ru"]
            }
        ]
    },
    "routing": {
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {
                "type": "field",
                "domain": ["geosite:private"],
                "outboundTag": "direct"
            },
            {
                "type": "field",
                "ip": ["geoip:private"],
                "outboundTag": "direct"
            },
            {
                "type": "field",
                "domain": [
                    "geosite:category-ru",
                    "domain:.ru",
                    "domain:.su",
                    "domain:.рф"
                ],
                "outboundTag": "direct"
            },
            {
                "type": "field",
                "ip": ["geoip:ru"],
                "outboundTag": "direct"
            }
        ]
    }
}
```

此模板的参考副本已包含在 Meridian 源代码中：`src/meridian/data/subscription-templates/xray-split-ru.json`。

### Mihomo / Clash 模板

对于使用 Mihomo（Clash Meta）的客户端，请将以下规则添加到订阅模板：

```yaml
rules:
  - DOMAIN-SUFFIX,.ru,DIRECT
  - DOMAIN-SUFFIX,.su,DIRECT
  - DOMAIN-SUFFIX,.рф,DIRECT
  - GEOSITE,category-ru,DIRECT
  - GEOIP,RU,DIRECT
  - MATCH,PROXY
```

参考副本：`src/meridian/data/subscription-templates/mihomo-split-ru.yaml`。

## 协同工作原理

这两层机制相互补充：

| 层级 | 作用 | 防范风险 |
|-------|-------------|-----------------|
| **服务器端地理封锁**（Xray 路由） | 在服务器端丢弃发往俄罗斯的流量 | VPS IP 出现在俄罗斯服务日志中 |
| **客户端分流**（订阅模板） | 让俄罗斯流量直连并绕过代理 | 俄罗斯网站无法访问、不必要的代理负载 |

如果已配置分流，俄罗斯流量不会到达服务器——客户端会将其直接发送到目的地。服务器端地理封锁充当安全网：如果客户端没有应用模板（例如旧版应用或手动配置），服务器仍会阻止访问俄罗斯目的地。

## 客户端兼容性

分流要求客户端支持 Xray 路由规则：

| 客户端 | 平台 | 分流支持 |
|--------|----------|----------------------|
| v2rayNG | Android | 支持（Xray JSON） |
| Hiddify | Android, iOS | 支持（Xray JSON） |
| Streisand | iOS | 支持（Xray JSON） |
| V2Box | iOS | 支持（Xray JSON） |
| Nekoray / Nekobox | 桌面端 | 支持（Xray JSON） |
| Clash Meta / Mihomo | 全平台 | 支持（Mihomo YAML，格式不同） |

不解析路由规则的客户端会代理所有流量。俄罗斯网站无法通过代理访问（服务器端地理封锁会丢弃这些流量），但 VPS IP 仍受保护。

## DNS 泄漏注意事项

一些客户端默认不会通过代理发送 DNS 查询。启用分流时，这对俄罗斯域名反而正合适——它们应通过本地 DNS 解析。但非俄罗斯域名的 DNS 查询应通过代理侧的 DNS，以避免泄漏。

上面的模板通过 `dns` 部分实现这一点：俄罗斯域名在本地解析（`address: "localhost"`），其他域名全部使用 DNS-over-HTTPS（`dns.google`）。

如果您的客户端忽略 `dns` 部分（某些精简客户端确实如此），建议为非俄罗斯流量启用客户端内置的 DNS 防泄漏功能。
