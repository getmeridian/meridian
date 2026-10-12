---
title: Windows 和 WSL 设置
description: 使用 Windows Subsystem for Linux 在 Windows 上设置 Meridian。
order: 2.5
section: guides
---

Meridian 从类 Unix 终端运行。在 Windows 上，请使用 Windows Subsystem for Linux (WSL)，并在 Linux 环境中运行 Meridian。

原生 PowerShell、Command Prompt、Git Bash 和 Cygwin 不是 Meridian 的推荐路径。WSL 提供与 Linux 相同的 shell、SSH 和文件系统行为，与其余文档保持一致。

## 安装 WSL

以管理员身份打开 PowerShell 并安装 WSL：

```powershell
wsl --install
```

如果系统提示，请重启，然后从 Start 菜单打开已安装的 Linux 发行版。

Ubuntu 是最安全的默认选择，因为 Meridian 面向 Debian 和 Ubuntu 服务器，并且大多数示例使用 `apt` 包名。如果您已经在 WSL 中安装了 Debian，也可以使用。

检查 WSL 是否使用版本 2：

```powershell
wsl --list --verbose
```

如果您的发行版显示版本 1，请转换它：

```powershell
wsl --set-version Ubuntu 2
```

## 准备 WSL shell

在 WSL 终端中，更新包并安装基础工具：

```bash
sudo apt update
sudo apt install -y curl openssh-client ca-certificates
```

安装 Meridian：

```bash
curl -sSf https://getmeridian.org/install.sh | bash
```

如果 `meridian` 没有立即找到，请重启 WSL 终端或重新加载 shell profile：

```bash
exec "$SHELL" -l
```

验证安装：

```bash
meridian --version
```

## 设置 SSH 密钥

Meridian 从 WSL 通过 SSH 连接到您的 VPS。请在 WSL 内生成密钥：

```bash
ssh-keygen -t ed25519 -C "meridian-wsl"
```

复制公钥：

```bash
cat ~/.ssh/id_ed25519.pub
```

将该公钥添加到 VPS 提供商，或添加到服务器用户的 `~/.ssh/authorized_keys`。

运行 Meridian 前先测试 SSH：

```bash
ssh root@198.51.100.10
```

如果您的 VPS 使用非 root 用户，请测试该用户并传给 deploy：

```bash
meridian deploy 198.51.100.10 --user ubuntu
```

## 使用 SSH agent

如果密钥有 passphrase，请在 WSL 内启动 agent：

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

只有在理解安全取舍时，才把这些命令加入 `~/.profile` 或 `~/.bashrc`。对于大多数首次部署，手动启动 agent 就够了。

刚开始时避免混用 Windows 和 WSL 的 SSH agent。先把密钥和 agent 都放在 WSL 内，直到部署正常工作。

## 从 WSL 运行 Meridian

从 WSL 终端运行正常部署流程：

```bash
meridian deploy
```

或者直接提供服务器 IP：

```bash
meridian deploy 198.51.100.10
```

Meridian 会把凭据保存在 WSL 内的 `~/.meridian/`。后续命令也从同一个 WSL 发行版运行，这样它才能找到缓存的服务器凭据：

```bash
meridian client add alice
meridian client list
meridian test 198.51.100.10
```

## VS Code Remote WSL

如果使用 VS Code，请安装 "WSL" 扩展，并从 WSL 打开 Meridian 工作区：

```bash
code .
```

通过 Remote WSL 编辑的文件位于 Linux 文件系统中，可避免路径和权限问题。优先使用 WSL home 下的路径，例如 `~/projects`，不要通过 `/mnt/c/` 编辑 Meridian 状态或 SSH 密钥。

## 常见问题

### 向导似乎跳过了一个提示

某些交互式终端提示在 WSL 下可能表现不同。如果 deploy 向导没有按预期接受输入，请显式提供值：

```bash
meridian deploy 198.51.100.10 --sni www.microsoft.com
```

也可以先运行 preflight 检查：

```bash
meridian preflight 198.51.100.10
```

### SSH 提示 Permission denied

确认密钥存在于 WSL 中，而不仅是在 Windows 中：

```bash
ls -la ~/.ssh
ssh -v root@198.51.100.10
```

私钥应只允许您的 WSL 用户读取：

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
```

### Meridian 已安装但命令缺失

重新加载 shell：

```bash
exec "$SHELL" -l
```

如果仍然找不到，请检查常见的 uv 和本地二进制路径：

```bash
ls ~/.local/bin
```

如有需要，把 `~/.local/bin` 添加到 shell profile：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
exec "$SHELL" -l
```

### Windows 路径导致权限问题

将 SSH 密钥、Meridian 凭据和项目文件保存在 WSL 文件系统中：

```bash
cd ~
mkdir -p projects
```

避免把 `~/.ssh` 或 `~/.meridian` 放在 `/mnt/c/` 下。Windows 挂载路径可能有不同的权限，SSH 会拒绝它们。

## 后续步骤

- [快速开始](/docs/zh/getting-started/) — 部署您的第一台服务器
- [安装](/docs/zh/installation/) — CLI 安装选项
- [故障排除](/docs/zh/troubleshooting/) — 常见部署和连接问题
