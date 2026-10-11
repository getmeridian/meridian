---
title: 贡献
description: 开发设置、PR 指南和测试方法。
order: 12
section: reference
---

## 报告问题

- **有 Bug？** 使用 [Bug 报告模板](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml)——先运行 `meridian doctor`
- **连接问题？** 使用 [连接问题模板](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml)——先运行 `meridian test` 和 `meridian preflight`
- **有功能想法？** 使用 [功能请求模板](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **安全漏洞？** 见 [安全](/docs/zh/security/)——不要开放公开问题

## 开发设置

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# 在可编辑模式下安装 CLI 和开发依赖
make install

# 安装 pre-push 钩子（每次推送前进行 11 个快速检查）
make hooks

# 在本地运行完整 CI
make ci

# 单独检查：
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
- `provision/` — 服务器准备步骤（包、加固、Docker、Realm）

## Pull Requests

1. Fork 仓库并从 `main` 创建分支
2. 进行有针对性的最小更改
3. 确保 CI 通过：`make ci`
4. 如果可能，在真实服务器上测试
5. 使用清晰的描述开放 PR

## 关键约定

- **Shell 值使用 `shlex.quote()`** — 永不插入未消毒的值
- **PWA 资产必须保持同步**（`templates/pwa/` 和应用目录）
- **nginx 配置**作为每个资源的 `meridian-v4-*.conf` 文件写在 `/etc/nginx/stream.d/` 和 `/etc/nginx/sites-available/` 下
- **配置程序步骤**返回 `StepResult`（ok/changed/skipped/failed）

## 测试

CI 验证：Python 测试、ruff lint、mypy 类型、模板渲染和 shell 脚本语法。完整部署测试需要真实的 VPS。
