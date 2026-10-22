---
title: Участие в разработке
description: Настройка окружения для разработки, правила оформления PR и тестирование.
---

## Сообщения о проблемах

- **Нашли ошибку?** Используйте [шаблон отчёта об ошибке](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) — сначала запустите `meridian doctor`
- **Не работает подключение?** Используйте [шаблон проблемы с подключением](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) — сначала запустите `meridian test` и `meridian check`
- **Есть идея новой функции?** Используйте [шаблон предложения функции](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **Нашли уязвимость безопасности?** См. [Безопасность](/ru/security/) — НЕ создавайте публичный issue

## Настройка окружения

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# Установить CLI в режиме редактирования со всеми зависимостями для разработки
make install

# Установить хук pre-push (быстрые проверки перед каждым git push)
make hooks

# Запустить полный прогон CI локально
make ci

# Отдельные проверки:
make test              # pytest
make lint              # ruff check
make format-check      # ruff format --check
make typecheck         # mypy
make templates         # Jinja2 template validation
```

## Структура проекта

CLI представляет собой пакет Python (`src/meridian/`), публикуемый в PyPI под именем `meridian-vpn`.

Основные модули:
- `cli.py` — приложение Typer, регистрация подкоманд
- `commands/` — по одному модулю на каждую подкоманду
- `cluster.py` + `cluster_persistence.py` — типизированная модель состояния `cluster.yml` и её сохранение на диск
- `servers.py` — `ServerRegistry` для сохранённых серверов
- `setup/` — состояние мастера `meridian setup` и общая среда выполнения для `deploy`, `setup` и `apply`
- `compiler/` — компилирует топологию в проверяемый список ресурсов с хешем плана (без ввода-вывода)
- `reconciler/` — проверяет и применяет эти ресурсы с сохранением контрольных точек
- `provision/` — шаги базовой подготовки сервера (пакеты, защита ОС и SSH, Docker, Realm)

## Pull requests

1. Сделайте форк репозитория и создайте ветку от `main`
2. Вносите небольшие, сфокусированные изменения
3. Убедитесь, что все проверки проходят: `make ci`
4. По возможности проверьте изменения на реальном сервере
5. Откройте PR с понятным описанием того, что и зачем изменено

## Основные правила

- **Экранируйте аргументы командной строки через `shlex.quote()`** — никогда не подставляйте неэкранированные значения в shell-команды
- **Поддерживайте синхронизацию ресурсов PWA** (`templates/pwa/` и каталог приложений)
- **Конфигурация nginx** записывается отдельно для каждого ресурса в файлы `meridian-v4-*.conf` в `/etc/nginx/stream.d/` и `/etc/nginx/sites-available/`
- **Шаги provisioner** возвращают `StepResult` (`ok`/`changed`/`skipped`/`failed`)

## Тестирование

В CI проверяются: тесты Python, линтер `ruff`, типы `mypy`, рендеринг шаблонов и синтаксис shell-скриптов. Для полной проверки развёртывания нужен реальный VPS.
