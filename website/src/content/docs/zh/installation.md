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
1. 如果没有 [uv](https://docs.astral.sh/uv/)，则安装它；pipx 或 pip 作为后备方案
2. 从 PyPI 安装 `meridian-vpn`
3. 必要时将工具目录加入 shell PATH
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

Meridian 会在交互式命令前定期检查 PyPI，并在有新版本时提示；不会自动安装。`meridian update` 会依次尝试发现的 uv、pipx 和 pip 安装，并在每次之后验证当前实际执行的 `meridian` 版本；只更新到闲置环境不算成功。退出码 `0` 表示已经最新或升级已验证，`3` 表示版本发现、安装或验证失败。自动化环境可设置 `MERIDIAN_DISABLE_UPDATE_CHECK=1` 关闭后台检查。

## 要求

- **Python 3.11+**（项目最低版本；uv 可自动安装和管理）
- 修改或诊断主机的命令需要目标服务器的 **SSH 访问**。`test` 和 `probe` 从客户端机器运行，不需要 SSH；Studio 可使用一次密码安装密钥

首次完整运行 `meridian test` 时，会在独立的 120 秒 deadline 内从 GitHub 下载固定 Xray 并验证发布的 checksum。之后使用完整本地缓存；`--timeout` 控制网络检查，不控制该首次下载。
终端 QR 码由内置的 Python `segno` 包生成，无需安装系统依赖。

## 验证安装

```
meridian --version
```
