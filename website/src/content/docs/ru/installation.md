---
title: Установка
description: Установите Meridian CLI на вашу локальную машину.
---

## Быстрая установка

```
curl -sSf https://getmeridian.org/install.sh | bash
```

Этот скрипт:
1. Устанавливает [uv](https://docs.astral.sh/uv/) если его нет, с pipx или pip как возврат. Установщик uv закреплён по версии и проверяется по SHA-256 перед запуском.
2. Устанавливает `meridian-vpn` из PyPI (задайте `MERIDIAN_VERSION=4.0.0`, чтобы установить точный выпуск)
3. Добавляет директорий инструмента в ваш shell PATH при необходимости
4. Создаёт `/usr/local/bin/meridian` только когда доступен passwordless sudo

## Проверенная установка

К каждому [выпуску на GitHub](https://github.com/getmeridian/meridian/releases) приложены `install.sh` и его `SHA256SUMS`. Чтобы проверить скрипт перед запуском, скачайте оба файла из выпуска и сверьте их:

```
VERSION=4.0.0
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/install.sh
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/SHA256SUMS
sha256sum -c SHA256SUMS          # macOS: shasum -a 256 -c SHA256SUMS
less install.sh                  # optional: read it
MERIDIAN_VERSION=$VERSION bash install.sh
```

Чтобы проверить копию с getmeridian.org, сравните `curl -sSf https://getmeridian.org/install.sh | sha256sum` с `SHA256SUMS` выпуска; они совпадают, если скрипт не менялся после этого выпуска. Если новейший выпуск является бета-версией, копия на сайте по умолчанию устанавливает именно её, и из-за этой одной строки её контрольная сумма отличается от суммы файла из выпуска.

## Ручная установка

С uv (рекомендуется):
```
uv tool install meridian-vpn
```

С pipx:
```
pipx install meridian-vpn
```

## Обновление

```
meridian update
```

Meridian проверяет PyPI сейчас и потом перед интерактивными командами и выводит уведомление когда существует более новая версия. Она никогда не устанавливает обновления самостоятельно. `meridian update` обновляет через какой-либо менеджер пакетов, который установил его, и подтверждает, что активная команда `meridian` сообщает новую версию. Установите `MERIDIAN_DISABLE_UPDATE_CHECK=1` для отключения фоновой проверки в автоматизации.

Обновление CLI ничего не меняет на ваших серверах. Если сервер работает, нет спешки; прочитайте [release notes](https://github.com/getmeridian/meridian/releases), затем запустите ту же команду `meridian deploy` (или `meridian apply`) когда вы готовы применить новую версию.

## Обновление с 3.x

Meridian 4 **не является обновлением на месте**. Он заменяет панель 3x-ui на Remnawave, и ссылки с сервера 3.x не могут передаваться: каждый пользователь нуждается в новой ссылке.

Ваши серверы 3.x продолжают работать. У вас есть два выбора:

- **Продолжайте управлять ими с 3.x.** Переустановите CLI 3.x:
  ```
  uv tool install 'meridian-vpn<4'
  ```
  (или `pipx install 'meridian-vpn<4'`). Meridian 3.17.3 и позже остаются на 3.x когда вы запускаете `meridian update`, если вы не передаёте `--major`.
- **Переходите на Meridian 4.**
  1. Разверните новый сервер с Meridian 4: `meridian deploy NEW_IP`
  2. Дайте вашим пользователям их новые страницы подключения (`meridian client add NAME` для каждого человека)
  3. Как только все переключились, разберите старый сервер с `meridian teardown OLD_IP` — teardown Meridian 4 также удаляет 3x-ui

Meridian 4 хранит своё состояние в `~/.config/meridian/` и никогда не читает и не меняет состояние 3.x в `~/.meridian/`, поэтому обе версии могут жить на одной машине; команды флота, которые находят только состояние 3.x, объясняют эти варианты.

Эта документация описывает Meridian 4. [Документация Meridian 3.17.3](https://github.com/getmeridian/meridian/tree/v3.17.3/website/src/content/docs/ru) остаётся на GitHub для серверов, которые вы оставляете на 3.x.

## Требования

- **Python 3.11+** (uv устанавливает и управляет им для вас)
- **SSH доступ** к вашему серверу, как root или пользователь с passwordless sudo. Лучше всего вход по ключу; если сервер принимает только пароль, `meridian deploy` и `meridian setup` предложат создать ключ для Meridian и один раз установить его с помощью этого пароля (см. [Серверы только с паролем](/ru/deploy/#серверы-только-с-паролем))

Терминальные QR коды отрисовываются встроенным пакетом `segno`; нет системной зависимости.

## Проверить установку

```
meridian --version
```
