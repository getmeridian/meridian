---
title: 贡献
description: 开发设置、PR 指南和测试方法。
---

## 报告问题

- **发现 Bug？** 使用 [Bug 报告模板](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml)——提交前请先运行 `meridian doctor`
- **遇到连接问题？** 使用 [连接问题模板](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml)——提交前请先运行 `meridian test` 和 `meridian check`
- **有新功能建议？** 使用 [功能请求模板](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **发现安全漏洞？** 参见[安全](/zh/security/)——请勿提交公开 issue

## 开发环境设置

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# 在可编辑模式下安装 CLI 和开发依赖
make install

# 安装 pre-push 钩子（每次推送前运行快速检查）
make hooks

# 在本地运行完整 CI
make ci

# 单独运行各项检查：
make test              # pytest
make lint              # ruff check
make format-check      # ruff format --check
make typecheck         # mypy
make templates         # Jinja2 模板验证
```

## 项目结构

CLI 是一个 Python 包（`src/meridian/`），通过 PyPI 分发为 `meridian-vpn`。

关键模块：
- `cli.py` — Typer 应用、子命令注册
- `commands/` — 每个子命令一个模块
- `cluster.py` + `cluster_persistence.py` — 类型化的 `cluster.yml` 状态和持久化
- `servers.py` — `ServerRegistry` 用于已知服务器
- `setup/` — `meridian setup` 向导状态和部署、设置和应用背后的共享运行时
- `compiler/` — 将拓扑变成已审核、哈希标识的资源列表（无 I/O）
- `reconciler/` — 带检查点观察和应用这些资源
- `provision/` — 服务器准备步骤（包、加固、控制服务、Realm）
- `control/` — 控制服务（`meridian serve`）：用户、订阅、签名的节点文档、备份和网页面板
- `backup/` — 快照、设备密钥和 `meridian restore`

节点 agent 是仓库根目录下 `agent/` 中的 Rust crate；它的构建产物随 Python 软件包一起发布。

## Pull Requests

1. Fork 仓库并从 `main` 创建分支
2. 进行有针对性的最小更改
3. 确保 CI 通过：`make ci`
4. 如果可能，在真实服务器上测试
5. 使用清晰的描述开放 PR

## 关键约定

- **Shell 参数必须使用 `shlex.quote()`** — 绝不能将未转义的值直接拼接到 shell 命令中
- **PWA 资源必须保持同步**（`templates/pwa/` 与应用清单）
- **nginx 配置**按资源拆分为独立的 `meridian-v4-*.conf` 文件，位于 `/etc/nginx/stream.d/` 和 `/etc/nginx/sites-available/`
- **Provisioner 步骤**返回 `StepResult`（`ok`/`changed`/`skipped`/`failed`）

## 测试

CI 会验证：Python 测试、ruff 代码检查、mypy 类型检查、模板渲染以及 shell 脚本语法。如需完整测试部署流程，可运行 `make system-lab`（在 Docker 容器中部署多节点集群并验证真实流量）或在真实 VPS 上测试（`make real-lab`）。
