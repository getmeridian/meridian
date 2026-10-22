---
title: 安装
description: 在你的本地计算机上安装 Meridian CLI。
---

## 快速安装

```
curl -sSf https://getmeridian.org/install.sh | bash
```

此脚本：
1. 如果没有 [uv](https://docs.astral.sh/uv/)，则安装它，pipx 或 pip 作为后备方案。uv 安装程序按版本固定，并在运行前用 SHA-256 校验。
2. 从 PyPI 安装 `meridian-vpn`（设置 `MERIDIAN_VERSION=4.0.0` 可安装某个确切版本）
3. 必要时将工具目录加入你的 shell PATH
4. 仅在无密码 sudo 可用时创建 `/usr/local/bin/meridian`

## 验证安装

每个 [GitHub 发布版本](https://github.com/getmeridian/meridian/releases)都附带 `install.sh` 及其 `SHA256SUMS`。要在运行脚本前检查它，请从发布页下载两者并校验：

```
VERSION=4.0.0
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/install.sh
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/SHA256SUMS
sha256sum -c SHA256SUMS          # macOS: shasum -a 256 -c SHA256SUMS
less install.sh                  # optional: read it
MERIDIAN_VERSION=$VERSION bash install.sh
```

要检查 getmeridian.org 上的副本，请把 `curl -sSf https://getmeridian.org/install.sh | sha256sum` 的结果与该发布版本的 `SHA256SUMS` 对比；只要脚本在该发布之后没有变动，两者就会一致。当最新发布版本是测试版时，网站上的副本默认安装的就是该测试版，仅这一行就会使它的校验和与发布附件不同。

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

更新 CLI 不会改动你服务器上的任何内容。如果服务器运行正常，无需急于更新；先阅读[发行说明](https://github.com/getmeridian/meridian/releases)，准备好后再运行相同的 `meridian deploy` 命令（或 `meridian apply`）以应用新版本。

## 从 3.x 升级

Meridian 4 **不是就地升级**。它用 Remnawave 替换了 3x-ui 面板，3.x 服务器上的链接无法直接继承：每个用户都需要新链接。

你的 3.x 服务器会继续正常运行。你有两个选择：

- **继续用 3.x 管理它们。** 重新安装 3.x CLI：
  ```
  uv tool install 'meridian-vpn<4'
  ```
  （或 `pipx install 'meridian-vpn<4'`）。Meridian 3.17.3 及更高版本在你运行 `meridian update` 时会留在 3.x，除非你传入 `--major`。
- **迁移到 Meridian 4。**
  1. 用 Meridian 4 部署一台新服务器：`meridian deploy NEW_IP`
  2. 把新的连接页面发给你的用户（为每个人运行 `meridian client add NAME`）
  3. 等所有人都切换完成后，用 `meridian teardown OLD_IP` 卸载旧服务器——Meridian 4 的 `teardown` 也会一并移除 3x-ui

Meridian 4 把自己的状态保存在 `~/.config/meridian/`，从不读取或改动 `~/.meridian/` 中的 3.x 状态，因此两个版本可以共存于同一台机器；只找到 3.x 状态的集群命令会改为提示上述选项。

本文档介绍的是 Meridian 4。仍在使用 3.x 的服务器可以参阅保留在 GitHub 上的 [Meridian 3.17.3 文档](https://github.com/getmeridian/meridian/tree/v3.17.3/website/src/content/docs/zh)。

## 系统要求

- **Python 3.11+**（uv 会自动为你安装和管理）
- 对服务器的 **SSH 访问权限**，使用 `root` 或拥有免密 `sudo` 权限的用户。优先使用密钥登录；如果服务器只接受密码，`meridian deploy` 和 `meridian setup` 会提示为 Meridian 创建密钥，并用该密码一次性完成安装（见[仅密码登录的服务器](/zh/deploy/#仅密码登录的服务器)）

终端二维码由内置的 `segno` 包渲染，不需要额外系统依赖。

## 验证安装

```
meridian --version
```
