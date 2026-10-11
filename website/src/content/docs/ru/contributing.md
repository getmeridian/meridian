---
title: Способствование
description: Настройка разработки, руководства PR и подход к тестированию.
order: 12
section: reference
---

## Сообщение о проблемах

- **Ошибка?** Используйте [шаблон отчёта об ошибке](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) — сначала запустите `meridian doctor`
- **Проблема подключения?** Используйте [шаблон проблемы подключения](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) — сначала запустите `meridian test` и `meridian preflight`
- **Идея функции?** Используйте [шаблон запроса функции](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **Уязвимость безопасности?** См. [Безопасность](/docs/ru/security/) — НЕ открывайте публичную проблему

## Настройка разработки

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# Установить CLI в редактируемом режиме с зависимостями разработки
make install

# Установить крючок pre-push (быстрые проверки перед каждым push)
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

CLI — это Python пакет (`src/meridian/`) распределённый через PyPI как `meridian-vpn`.

Ключевые модули:
- `cli.py` — Typer приложение, регистрация подкоманды
- `commands/` — один модуль на подкоманду
- `cluster.py` + `cluster_persistence.py` — типизированное состояние `cluster.yml` и persisance
- `servers.py` — `ServerRegistry` для известных серверов
- `setup/` — состояние мастера `meridian setup` и общий runtime позади deploy, setup и apply
- `compiler/` — преобразует топологию в рассмотренный, хеш-идентифицированный список ресурсов (нет I/O)
- `reconciler/` — наблюдает и применяет эти ресурсы с контрольными точками
- `provision/` — шаги подготовки сервера (пакеты, затвердевание, Docker, Realm)

## Pull requests

1. Раздвойте репо и создайте ветку из `main`
2. Сделайте сфокусированные, минимальные изменения
3. Убедитесь, что CI прошёл: `make ci`
4. Протестируйте на реальном сервере, если возможно
5. Откройте PR с чётким описанием

## Ключевые соглашения

- **Значения оболочки используют `shlex.quote()`** — никогда не интерполируйте неизатёртые значения
- **PWA ресурсы должны оставаться синхронизированными** (`templates/pwa/` и каталог приложения)
- **конфигурация nginx** записывается для каждого ресурса как файлы `meridian-v4-*.conf` под `/etc/nginx/stream.d/` и `/etc/nginx/sites-available/`
- **Шаги provisioner** возвращают `StepResult` (ok/changed/skipped/failed)

## Тестирование

CI проверяет: Python тесты, ruff lint, mypy типы, рендеринг шаблонов и синтаксис shell скрипта. Полное тестирование развёртывания требует реального VPS.
