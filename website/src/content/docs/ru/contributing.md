---
title: Участие в разработке
description: Настройка разработки, рекомендации PR и подход к тестированию.
order: 12
section: reference
---

## Сообщение об ошибках

- **Ошибка?** Используйте [шаблон отчёта об ошибке](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) — сначала запустите `meridian doctor`
- **Проблема с подключением?** Используйте [шаблон проблемы подключения](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) — сначала запустите `meridian test` и `meridian preflight`
- **Идея функции?** Используйте [шаблон запроса функции](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **Уязвимость безопасности?** Смотрите [Security](/docs/ru/security/) — НЕ открывайте публичный issue

## Настройка разработки

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# Установить CLI в режиме редактирования с зависимостями разработки
make install

# Установить pre-push hook (11 быстрых проверок перед каждым push)
make hooks

# Запустить полный CI локально
make ci

# Отдельные проверки:
make test              # pytest
make lint              # ruff check
make format-check      # ruff format --check
make typecheck         # mypy
make templates         # Jinja2 template validation
```

## Структура проекта

CLI — это Python пакет (`src/meridian/`) распространяемый через PyPI как `meridian-vpn`.

Ключевые модули:
- `cli.py` — Typer приложение, регистрация подкоманд
- `commands/` — один модуль на подкоманду
- `cluster.py` + `cluster_persistence.py` — типизированное состояние `cluster.yml` и его сохранение
- `servers.py` — `ServerRegistry` для известных серверов
- `provision/` — идемпотентный конвейер шагов

## Pull requests

1. Форкните репозиторий и создайте ветку из `main`
2. Сделайте сосредоточенные, минимальные изменения
3. Убедитесь что CI проходит: `make ci`
4. Тестируйте на реальном сервере если возможно
5. Откройте PR с чётким описанием

## Ключевые соглашения

- **Shell значения используют `shlex.quote()`** — никогда не интерполируйте несанитизированные значения
- **Ресурсы PWA должны быть синхронизированы** (`templates/pwa/` и каталог приложений)
- **Конфиг nginx** использует `/etc/nginx/stream.d/meridian.conf` и `/etc/nginx/conf.d/meridian-http.conf`
- **Шаги provisioner** возвращают `StepResult` (ok/changed/skipped/failed)

## Тестирование

CI проверяет: Python тесты, ruff lint, mypy типы, рендеринг шаблонов и синтаксис shell скриптов. Полное развертывание тестирование требует реальный VPS.
