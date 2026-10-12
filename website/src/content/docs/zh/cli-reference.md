---
title: CLI 参考
description: Meridian CLI 所有命令和标志的完整参考。
order: 10
section: reference
---

## 命令

### meridian deploy

部署代理服务器到 VPS。

```
meridian deploy [IP] [flags]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--sni HOST` | www.cloudflare.com | TLS 伪装目标 |
| `--domain DOMAIN` | (无) | Cloudflare CDN 回退域名 |
| `--client-name NAME` | default | 第一个客户端的名称 |
| `--display-name NAME` | (无) | 连接页面上的标签 |
| `--icon EMOJI_OR_URL` | (无) | 页面图标 — 表情符号或图片 URL |
| `--color PALETTE` | ocean | 页面颜色主题 (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH 用户 |
| `--harden / --no-harden` | 启用 | SSH + 防火墙加固 |
| `--pq / --no-pq` | 禁用 | 后量子加密 — ML-KEM-768 混合模式（实验性） |
| `--server NAME` | | 目标服务器（名称或 IP） |
| `--decoy MODE` | none | 诱饵响应模式 (`none` / `403`) |
| `--yes` | | 跳过确认提示 |

### meridian client

管理客户端访问密钥。

```
meridian client add NAME [--server NAME]
meridian client show NAME [--server NAME]
meridian client list [--server NAME]
meridian client remove NAME [--server NAME]
```

### meridian server

管理已知服务器。

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

### meridian relay

管理中继节点——轻量级 TCP 转发器，通过国内服务器将流量路由到国外的出口服务器。

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| 标志 | 默认值 | 描述 |
|------|---------|-------------|
| `--exit/-e EXIT` | (deploy 时必需) | 出口服务器 IP 或名称 |
| `--name NAME` | (自动) | 中继的友好名称 (如 "ru-moscow") |
| `--port/-p PORT` | 443 | 中继服务器的监听端口 |
| `--user/-u USER` | root | 中继上的 SSH 用户 |
| `--yes/-y` | | 跳过确认提示 |

**中继如何工作**：客户端连接到中继的国内 IP。中继将原始 TCP 转发到国外的出口服务器。所有加密都是端到端的，在客户端和出口之间——中继永远看不到明文。所有协议（Reality、XHTTP、WSS）都通过中继工作。

### meridian preflight

预检查服务器验证。测试 SNI、端口、DNS、OS、磁盘、ASN，无需安装任何内容。

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

使用 RealiTLScanner 在服务器网络上查找最优 SNI 目标。

```
meridian scan [IP] [--server NAME]
```

### meridian test

测试代理可达性并验证来自客户端设备的实际连接。无需 SSH。

首先检查基本可达性（TCP、TLS 握手、domain HTTPS）。然后下载本地 xray 客户端二进制文件（首次使用后缓存），通过代理为每个活跃协议（Reality、XHTTP、WSS）连接，并确认流量端到端流动。

```
meridian test [IP] [--server NAME]
```

### meridian doctor

收集系统诊断信息以便调试。别名：`meridian rage`。

```
meridian doctor [IP] [--ai] [--server NAME]
```

### meridian teardown

从服务器移除代理。

```
meridian teardown [IP] [--server NAME] [--yes]
```

### meridian update

将 CLI 更新到最新版本。

```
meridian update
```

### meridian --version

显示 CLI 版本。

```
meridian --version
meridian -v
```

## 全局标志

| 标志 | 描述 |
|------|-------------|
| `--server NAME` | 目标特定的已命名服务器 |

## 服务器解析

需要服务器的命令按照此优先级：
1. 显式 IP 参数
2. `--server NAME` 标志
3. 本地模式检测（在服务器本身运行）
4. 单个服务器自动选择（如果只保存了一个）
5. 交互提示
