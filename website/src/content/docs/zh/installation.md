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
1. 如果不存在，安装 [uv](https://docs.astral.sh/uv/)（或使用 pipx 作为备选）
2. 从 PyPI 安装 `meridian-vpn`
3. 在 `/usr/local/bin/meridian` 创建符号链接以进行系统范围的访问
4. 如果存在，从旧的基于 bash 的 CLI 迁移

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

Meridian 会自动检查更新：
- **补丁版本**（错误修复）— 静默安装
- **次要版本**（新功能）— 会提示您更新
- **主要版本**（破坏性更改）— 会提示您更新

## 要求

- **Python 3.11+**（由 uv/pipx 自动安装）
- **SSH 密钥访问** 到您的目标服务器
终端 QR 码由内置的 Python `segno` 包生成，无需安装系统依赖。

## 验证安装

```
meridian --version
```
