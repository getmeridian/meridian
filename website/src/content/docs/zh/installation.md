---
title: 安装
description: 在您的本地计算机上安装 Meridian CLI。
order: 2
section: guides
---

## 快速安装

```
curl -sSf https://getmeridian.org/install.sh | bash
```

此脚本：
1. 如果没有 [uv](https://docs.astral.sh/uv/)，则安装它，pipx 或 pip 作为后备方案
2. 从 PyPI 安装 `meridian-vpn`
3. 必要时将工具目录加入您的 shell PATH
4. 仅在无密码 sudo 可用时创建 `/usr/local/bin/meridian`

## 手动安装

使用 uv（推荐）：
```
uv tool install meridian-vpn
```

使用 pipx：
```
pipx install meridian-vpn
```

## 更新

```
meridian update
```

Meridian 会在交互式命令前定期检查 PyPI，并在有新版本时打印通知。它从不自动安装更新。`meridian update` 通过安装它的任何包管理器升级，并确认活跃的 `meridian` 命令报告新版本。设置 `MERIDIAN_DISABLE_UPDATE_CHECK=1` 在自动化中关闭后台检查。

更新 CLI 对您的服务器上的任何东西都无改变。如果服务器正常工作，没有急迫性；阅读 [release notes](https://github.com/getmeridian/meridian/releases)，然后在准备好时运行相同的 `meridian deploy` 命令（或 `meridian apply`）来应用新版本。

## 从 3.x 升级

Meridian 4.0 **不是就地升级**。它用 Remnawave 替换 3x-ui 面板，来自 3.x 服务器的链接无法转移：每个用户需要新链接。

您的 3.x 服务器继续工作。您有两个选择：

- **继续用 3.x 管理它们。** 重新安装 3.x CLI：
  ```
  uv tool install 'meridian-vpn<4'
  ```
  （或 `pipx install 'meridian-vpn<4'`）。Meridian 3.17.3 及更高版本在您运行 `meridian update` 时保持在 3.x，除非您传递 `--major`。
- **迁移到 4.0。**
  1. 用 4.0 部署一个新服务器：`meridian deploy NEW_IP`
  2. 为您的用户提供他们的新连接页面（为每个人运行 `meridian client add NAME`）
  3. 一旦每个人都切换了，用 `meridian teardown OLD_IP` 卸载旧服务器 — 4.0 的卸载也会移除 3x-ui

Meridian 4.0 不读取或改变 `~/.meridian/` 中的 3.x 状态；找到它的集群命令改为解释这些选项。

## 需求

- **Python 3.11+**（uv 为您安装和管理）
- 对您的服务器的 **SSH 密钥访问**，以 root 身份或拥有无密码 sudo 的用户身份

终端 QR 码由捆绑的 `segno` 包渲染；不需要系统依赖。

## 验证安装

```
meridian --version
```
