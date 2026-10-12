---
title: Справочник CLI
description: Полный справочник всех команд и флагов Meridian CLI.
order: 10
section: reference
---

## Команды

### meridian deploy

Развернуть прокси-сервер на VPS.

```
meridian deploy [IP] [flags]
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--sni HOST` | www.cloudflare.com | Сайт, который Reality маскирует |
| `--domain DOMAIN` | (нет) | Включить режим домена с резервом CDN |
| `--client-name NAME` | default | Имя для первого клиента |
| `--display-name NAME` | (нет) | Отображаемое имя на страницах подключения |
| `--icon EMOJI_OR_URL` | (нет) | Значок сервера — эмодзи или URL изображения |
| `--color PALETTE` | ocean | Цветовая палитра (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH пользователь |
| `--harden / --no-harden` | включен | Защита SSH + файрвол |
| `--pq / --no-pq` | выключен | Постквантовое шифрование — ML-KEM-768 гибридное (экспериментально) |
| `--server NAME` | | Целевой сервер (имя или IP) |
| `--decoy MODE` | none | Ответ-обманка для неизвестных путей (`none` / `403`) |
| `--yes` | | Пропустить подтверждающие диалоги |

### meridian client

Управление ключами доступа клиента.

```
meridian client add NAME [--server NAME]
meridian client show NAME [--server NAME]
meridian client list [--server NAME]
meridian client remove NAME [--server NAME]
```

### meridian server

Управление известными серверами.

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

### meridian relay

Управление узлами ретранслятора — легковесные TCP-маршрутизаторы, направляющие трафик через внутренний сервер на выходной сервер за границей.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--exit/-e EXIT` | (требуется для deploy) | IP или имя выходного сервера |
| `--name NAME` | (автоматически) | Дружественное имя для ретранслятора (например, "ru-moscow") |
| `--port/-p PORT` | 443 | Порт прослушивания на сервере ретранслятора |
| `--user/-u USER` | root | SSH пользователь на ретрансляторе |
| `--yes/-y` | | Пропустить подтверждающие диалоги |

**Как работают ретрансляторы**: клиент подключается к внутреннему IP-адресу ретранслятора. Ретранслятор пересылает необработанный TCP на выходной сервер за границей. Всё шифрование осуществляется от конца до конца между клиентом и выходом — ретранслятор никогда не видит открытый текст. Все протоколы (Reality, XHTTP, WSS) работают через ретранслятор.

### meridian preflight

Предварительная проверка сервера. Тестирует SNI, порты, DNS, ОС, диск, ASN без установки.

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

Найти оптимальные цели SNI на сети сервера используя RealiTLScanner.

```
meridian scan [IP] [--server NAME]
```

### meridian test

Протестируйте доступность прокси и проверьте фактические подключения с устройства клиента. SSH не требуется.

Сначала проверяет базовую доступность (TCP, TLS handshake, domain HTTPS). Затем загружает локальный xray клиент (кэшируется после первого использования), подключается через прокси для каждого активного протокола (Reality, XHTTP, WSS) и подтверждает, что трафик проходит end-to-end.

```
meridian test [IP] [--server NAME]
```

### meridian doctor

Собрать диагностику системы для отладки. Альтернатива: `meridian rage`.

```
meridian doctor [IP] [--ai] [--server NAME]
```

### meridian teardown

Удалить прокси с сервера.

```
meridian teardown [IP] [--server NAME] [--yes]
```

### meridian update

Обновить CLI на последнюю версию.

```
meridian update
```

### meridian --version

Показать версию CLI.

```
meridian --version
meridian -v
```

## Глобальные флаги

| Флаг | Описание |
|------|---------|
| `--server NAME` | Выбрать конкретный именованный сервер |

## Разрешение сервера

Команды, которым нужен сервер, следуют этому приоритету:
1. Явный аргумент IP
2. Флаг `--server NAME`
3. Определение локального режима (запуск на самом сервере)
4. Автоматический выбор одного сервера (если сохранён только один)
5. Интерактивный диалог
