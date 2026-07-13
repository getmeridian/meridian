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
| `--sni HOST` | www.microsoft.com | Сайт, который Reality маскирует |
| `--domain DOMAIN` | (нет) | Резервный домен Cloudflare CDN |
| `--client-name NAME` | default | Имя для первого клиента |
| `--display-name NAME` | (нет) | Ярлык для страниц подключения |
| `--icon EMOJI_OR_URL` | (нет) | Значок страницы — эмодзи или URL изображения |
| `--color PALETTE` | ocean | Цветовая палитра (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | SSH пользователь |
| `--harden / --no-harden` | включен | Защита SSH + файрвол |
| `--warp / --no-warp` | выключен | Маршрутизировать исходящий трафик через Cloudflare WARP |
| `--server NAME` | | Целевой сервер (имя или IP) |
| `--geo-block` / `--no-geo-block` | включен | Блокировать российские домены и IP (geosite:category-ru + geoip:ru) |
| `--ssh-port PORT` | 22 | SSH порт (если нестандартный) |
| `--yes` | | Пропустить подтверждающие диалоги |
| `--json` | | Выдать окончательный результат развёртывания в виде конверта `meridian.output/v1` |
| `--events=jsonl` | | Потоком выдавать типизированные события прогресса как JSONL на stderr |
| `--request FILE` | | Прочитать JSON-полезную нагрузку `deploy-request` из файла; используйте `-` для stdin |
| `--dry-run` | | Проверить и спланировать развёртывание без SSH или изменения панели |

**Поток машина/UI**: `meridian api workflow deploy --json` возвращает контракт отрисовки мастера. UI собирает эти поля, проверяет их против `deploy-request`, затем запускает `meridian deploy --request deploy.json --json --events=jsonl`. Машинные развёртывания неинтерактивны: запрос должен содержать `yes: true` после подтверждения пользователем. `--dry-run --json` возвращает `deploy-plan` в `data` чтобы UI мог просмотреть режим, порты и сгенерированные пути перед открытием SSH.

### meridian client

Управление ключами доступа клиента и данными подключения.

```
meridian client add NAME [NAME...]  [--json]
meridian client show NAME [--json]
meridian client list [--json]
meridian client remove NAME [--yes] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--json` | | Выдать результат в виде конверта `meridian.output/v1` (для всех команд client) |
| `--yes`, `-y` | | Пропустить подтверждение удаления (применяется к `client remove`) |

**`client add`** — передайте несколько имён для добавления нескольких клиентов сразу (например `meridian client add alice bob charlie`). С флагом `--json` возвращает `data.clients[]` с именем пользователя, UUID и статусом для каждого созданного клиента.

**`client list`** — с флагом `--json` возвращает подсчёты статуса `data.summary` и записи `data.clients[]` с именем пользователя, UUID, статусом, счётчиками трафика, временем создания и последним временем появления.

**`client show`** — с флагом `--json` возвращает одну запись `data.client` плюс метаданные доступности `data.handoff.*`. Человеческая команда по-прежнему выводит используемые URL подписки/обмена.

**`client enable`** — возобновляет ранее приостановленного клиента, чтобы он снова мог подключаться. С флагом `--json` возвращает `data.client` с именем пользователя и статусом.

**`client disable`** — временно приостанавливает клиента. Его конфигурация остаётся целой, но он не может подключаться до повторного включения. С флагом `--json` возвращает `data.client` с именем пользователя и статусом.

### meridian server

Управление известными серверами.

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--name NAME` | (автоматически) | Отображаемое имя сервера |

### meridian node

Управление дополнительными выходными узлами в многоузловом флоте. Первый сервер (хост панели) развёртывается с помощью `meridian deploy`; последующие выходные узлы добавляются с помощью `meridian node add`.

```
meridian node add IP [flags]
meridian node list
meridian --json node list
meridian node remove IP [--yes] [--force]
meridian node check IP
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--user USER` | root | SSH пользователь на узле |
| `--ssh-port PORT` | 22 | SSH порт на узле (если нестандартный) |
| `--name NAME` | (автоматически, из IP) | Дружественное имя отображаемое в панели / подписке |
| `--domain DOMAIN` | (нет) | Домен для узла WSS/CDN резерва |
| `--sni HOST` | www.microsoft.com | Цель маскировки Reality для этого узла |
| `--harden / --no-harden` | включен | Защита ОС + SSH + файрвола для узла |
| `--yes` | | Пропустить подтверждающие диалоги (применяется к `node remove`) |
| `--force` | | При `node remove`, продолжить даже если ретрансляторы ссылаются на этот узел как на выход |
| глобальный `--json` | | Разместите перед `node`, чтобы выдать `node list` в виде конверта `meridian.output/v1` |

**Как это работает**: `meridian node add` подготавливает хост узла (пакеты ОС, Docker, nginx, TLS, контейнер узла Remnawave), регистрирует его через API панели и создаёт записи `reality`, `xhttp` и `hysteria2` (а в режиме домена ещё `wss`). Клиенты получают новый выход при следующем обновлении подписки. Запись добавляется в `nodes[]`; `desired_nodes[]` также обновляется, если этот список не null.

**Проверки здоровья**: `meridian node check` запускает проверки статуса панели, SSH, контейнера, порта и TLS. Когда проверка не пройдена, она выводит подсказку исправления (например `Run: docker compose up -d`).

**Удаление**: `meridian node remove` использует SSH для входа в узел, чтобы остановить контейнеры перед удалением узла из кластера и панели. Он отказывается удалять узел, который всё ещё является `exit_node` одного или нескольких ретрансляторов; передайте `--force` для отмены.

**JSON вывод**: `meridian --json node list` использует конверт `meridian.output/v1` с `data.nodes[]` содержащим ip, name, uuid, status, xray_version и traffic_bytes.

### meridian fleet

Инспектировать и восстанавливать флот из активного REST API панели.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet recover --panel-url URL --api-token TOKEN
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--json` | | Выдать состояние флота как JSON (для скриптинга / CI) |
| `--panel-url URL` | требуется | URL HTTPS панели (для `fleet recover`) |
| `--api-token TOKEN` | требуется | Токен Remnawave API (для `fleet recover`) |

**`fleet status`** — показывает здоровье панели, подключение каждого узла + версию Xray + трафик, вышестоящее каждого ретранслятора и подсчёты пользователей. С флагом `--json` вывод использует конверт `meridian.output/v1`. Стабильный доступ к полям внутри `data`: `data.panel.url`, `data.panel.healthy`, `data.sources.*`, `data.servers[].roles`, `data.nodes[].status` (`"connected"`, `"disconnected"`, `"disabled"`, `"unknown"`), `data.relays[].health` (`"healthy"`, `"unhealthy"`, `"unknown"`) и `data.summary.health/needs_attention/active_users/disabled_users/unknown_nodes/unhealthy_relays`. `data.summary.health` это `"unknown"` когда требуемые живые данные не могли быть собраны. Верхний уровень `status` сообщает о выполнении команды, не о здоровье флота.

**`fleet inventory`** — показывает настроенную панель, узлы, ретрансляторы, желаемую топологию и живой статус узла панели когда он доступен. Он никогда не выводит токен API панели или пути секретных URL. С флагом `--json` вывод использует конверт `meridian.output/v1`. Стабильный доступ к полям внутри `data` включает `data.sources.*`, `data.servers[].roles`, `data.summary.*`, `data.nodes[].desired`, `data.nodes[].protocols`, `data.relays[].exit_node_*` и `data.desired_nodes[].present`. Поля присутствия инвентаря не являются истиной примирения; используйте `plan --json` для решений о дрейфе/применении.

**`fleet recover`** — перестраивает `~/.meridian/cluster.yml` из активной панели. Команда получает профиль конфигурации, узлы и UUID входящих, затем через SSH восстанавливает серверные метаданные и выводит публичный ключ Reality. После этого вручную проверьте SSH-настройки, выбор хоста панели и ретрансляторы.

### meridian api

Инспектировать машинночитаемый контракт meridian-core используемый JSON выводом и будущими UI клиентами.

```
meridian api schemas [--json] [--include-schemas]
meridian api commands [--json] [--include-schemas]
meridian api schema NAME [--envelope|--json]
meridian api workflow NAME [--json]
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--json` | | Выдать каталог схем как конверт `meridian.output/v1` |
| `--include-schemas` | | Включить полные JSON-схемы в вывод `api schemas --json` или `api commands --json` |
| `--envelope`, `--json` | | Оборачивать `api schema NAME` в конверт `meridian.output/v1` вместо выведения сырой JSON-схемы |

**`api schemas`** — выводит стабильные имена схем как `output-envelope`, `apply-envelope`, `client-list-envelope`, `client-show-envelope`, `deploy-envelope`, `deploy-command-data`, `deploy-request`, `deploy-workflow-answers`, `deploy-result`, `deploy-plan`, `workflow-plan`, `input-field`, `remote-target`, `command-spec`, `remote-command-result`, `plan-envelope`, `fleet-status-envelope`, `fleet-inventory-envelope`, `event`, `apply`, `plan-result`, `fleet-status` и `fleet-inventory`. Схемы конверта команды включают запись `commands` в каталог.

**`api commands`** — выводит перенесённые контракты команды с `command`, `argv`, `envelope_schema`, `data_schema`, возможные `statuses`, структурированные `outcomes`, значения выходного кода, машинные флаги и стабильность. Используйте это перед подключением UI чтобы решить какую схему полезной нагрузки команды используйте для проверки заданного конверта. `deploy` объявляет `--json`, `--events=jsonl`, `--request` и `--dry-run`.

**`api schema NAME`** — выводит одну JSON-схему. Пример: `meridian api schema output-envelope`.

**`api workflow NAME`** — выводит отрисовываемый UI план работы. Пример: `meridian api workflow deploy --json` возвращает разделы и поля мастера развёртывания.

### meridian relay

Управление узлами ретранслятора — легковесные TCP-маршрутизаторы, направляющие трафик через внутренний сервер на выходной сервер за границей.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT]
meridian --json relay list [--exit EXIT]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--exit/-e EXIT` | (требуется для deploy) | IP или имя выходного сервера |
| `--name NAME` | (автоматически) | Дружественное имя для ретранслятора (например, "ru-moscow") |
| `--port/-p PORT` | 443 | Порт прослушивания на сервере ретранслятора |
| `--user/-u USER` | root | SSH пользователь на ретрансляторе |
| `--ssh-port PORT` | 22 | SSH порт на сервере ретранслятора (если нестандартный) |
| `--yes/-y` | | Пропустить подтверждающие диалоги |
| глобальный `--json` | | Разместите перед `relay`, чтобы выдать `relay list` в виде конверта `meridian.output/v1` |

**Как работают ретрансляторы**: клиент подключается к внутреннему IP-адресу ретранслятора. Ретранслятор пересылает необработанный TCP на выходной сервер за границей. Всё шифрование осуществляется от конца до конца между клиентом и выходом — ретранслятор никогда не видит открытый текст. Все протоколы (Reality, XHTTP, WSS) работают через ретранслятор.

**JSON вывод**: `meridian --json relay list` использует конверт `meridian.output/v1` с `data.relays[]`.

### meridian plan

Показать план примирения — что `meridian apply` сделает чтобы привести кластер в желаемое состояние объявленное в `cluster.yml`.

Читает `desired_nodes`, `desired_relays`, `desired_clients` и `subscription_page` из `cluster.yml`, получает фактическое состояние из панели и выводит различия в стиле Terraform с `+` для добавлений, `-` для удалений, `~` для обновлений.

```
meridian plan [--json]
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--json` | | Выдать план как JSON конверт `meridian.output/v1` для CI/CD и UI клиентов. Такие же выходные коды; человеческий вывод плана подавляется |

**Выходные коды**:
- `0` — приведён в соответствие (изменений не требуется)
- `2` — изменения ожидаются (запустите `meridian apply` чтобы привести в соответствие)
- ошибки также используют ненулевые выходы; клиенты процесса должны рассматривать JSON `status` и `errors[].category` как авторитетные потому что `2` может также означать ошибку пользователя/конфигурации когда `status` это `failed`

**JSON форма** (режим `--json`):
```json
{
  "schema": "meridian.output/v1",
  "meridian_version": "4.x.x",
  "command": "plan",
  "operation_id": "9d0f...",
  "started_at": "2026-05-04T21:00:00Z",
  "duration_ms": 128,
  "status": "changed",
  "exit_code": 2,
  "summary": {
    "text": "Plan: 1 to add, 1 to remove",
    "changed": true,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1}
  },
  "data": {
    "converged": false,
    "summary": "Plan: 1 to add, 1 to remove",
    "exit_code": 2,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1},
    "actions": [
      {"plan_index": 0, "execution_order": 2, "kind": "add_client", "operation": "add", "resource_type": "client",
       "resource_id": "alice", "target": "alice", "detail": "create client alice",
       "phase": "provision", "requires_confirmation": false,
       "destructive": false, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "", "from_extras": false,
       "change_set": [], "symbol": "+", "can_run_parallel": false},
      {"plan_index": 1, "execution_order": 1, "kind": "remove_client", "operation": "remove", "resource_type": "client",
       "resource_id": "ghost", "target": "ghost", "detail": "delete client ghost",
       "phase": "deprovision", "requires_confirmation": true,
       "destructive": true, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "delete client ghost",
       "from_extras": true, "change_set": [], "symbol": "-", "can_run_parallel": false}
    ]
  },
  "warnings": [],
  "errors": []
}
```

`data.actions[].from_extras: true` отмечает ресурсы которые существуют на панели но отсутствуют в `cluster.yml` — входы на которые работает `meridian apply --prune-extras`. `execution_order` показывает порядок который `apply` будет использовать, что может отличаться от порядка отображения для безопасности замены. `operation: "replace"` отмечает деструктивные замены как ретрансляция переподготовки. `status` это `no_changes` когда приведён в соответствие и `changed` когда apply имеет работу; `data.exit_code` отражает выходной код процесса.

Смотрите [Декларативный рабочий процесс](/docs/ru/getting-started/#declarative-workflow) для того как составлять `cluster.yml`.

### meridian apply

Привести кластер в желаемое состояние объявленное в `cluster.yml`. Запускает `plan` внутренне, показывает различия, запрашивает подтверждение, затем выполняет действия в порядке зависимостей (удаления первы, затем добавления, затем удаления узлов в последнюю очередь).

```
meridian apply [--yes] [--prune-extras=ask|yes|no] [--json]
```

| Флаг | По умолчанию | Описание |
|------|--------------|---------|
| `--yes`, `-y` | | Пропустить подтверждающие диалоги |
| `--parallel N` | 4 | Макс параллельных потоков подготовки узла (каждый узел получает свой SSH сеанс и клиент панели) |
| `--prune-extras` | `ask` | Как справляться с дрейфом — ресурсы присутствующие на панели но отсутствующие в `cluster.yml`. `ask` запрашивает на ресурс (понижен до `no` под `--yes` для безопасности); `yes` автоматическое удаление; `no` пропускает и выводит однострочное резюме |
| `--json` | | Выдать результат `meridian.output/v1` окончательного применения с статусом выполнения каждого действия |

Деструктивные действия (удаления, UPDATE_RELAY переподготовка) выводят предупреждение и требуют отдельное подтверждение. Ошибка в начале плана пропускает оставшиеся деструктивные действия — `cluster.yml` остаётся истинным.

С флагом `--json`, `data.plan` содержит типизированный план и `data.actions[]` содержит результаты выполнения со статусом `"succeeded" | "failed" | "skipped"`. Режим JSON неинтерактивен: если изменения требуют подтверждения и `--yes` отсутствует, Meridian возвращает `MERIDIAN_CONFIRMATION_REQUIRED` с вычисленным планом. Если существует только панель дрейфа и `--prune-extras` оставлен на `ask`, Meridian возвращает `MERIDIAN_DRIFT_DECISION_REQUIRED`; передайте `--prune-extras=no` чтобы сохранить дрейф или `--prune-extras=yes` чтобы удалить его. Контракт JSON сообщает о выполнении; он не делает деструктивные операции транзакционными. UI клиенты должны инспектировать не удавшиеся/пропущенные действия и переустановить идемпотентно после исправления основной проблемы.

**Пример обработки дрейфа:** если `cluster.yml` выводит `desired_clients: ['alice']` но панель также имеет `bob` (например созданного через UI панели), `meridian plan` показывает `- remove client: bob`. С по умолчанию `--prune-extras=ask` вас спросят удалить ли `bob` или держать его. `--yes --prune-extras=yes` запускает удаление молча; `--yes` один (без явного `--prune-extras`) пропускает его.

### meridian preflight

Предварительная проверка валидности сервера. Тестирует SNI, порты, DNS, ОС, диск, ASN без установки.

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

Найти оптимальные цели SNI на сети сервера используя RealiTLScanner.

```
meridian scan [IP] [--server NAME]
```

### meridian test

Протестировать доступность прокси и проверить фактические подключения с устройства клиента. SSH не требуется.

Сначала проверяет базовую доступность (TCP, TLS handshake, domain HTTPS). Затем загружает локальный клиент xray (кэшируется после первого использования), подключается через прокси для каждого активного протокола (Reality, XHTTP, WSS) и подтверждает что трафик проходит end-to-end.

```
meridian test [IP] [--server NAME]
```

### meridian probe

Зондировать сервер как цензор — проверить обнаружимо ли развёртывание. SSH не требуется. Работает на любом сервере, не только на развёртываниях Meridian. Принимает IP адреса или имена доменов.

Запускает 9 проверок: поверхность портов, ответ HTTP, сертификат TLS, согласованность SNI, зондирование пути прокси, обновление WebSocket, обратный DNS, поддержка HTTP/2 и устаревшие версии TLS.

```
meridian probe [IP|DOMAIN] [--server NAME]
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

Эти флаги доступны на командах которые взаимодействуют с сервером через SSH (deploy, node, relay, preflight, test, probe, doctor, teardown):

| Флаг | Описание |
|------|---------|
| `--server NAME` | Выбрать конкретный именованный сервер |
| `--user/-u USER` | SSH пользователь (по умолчанию: root, не-root получает sudo автоматически) |
| `--sni HOST` | Цель маскировки TLS (используется deploy, preflight, test, doctor) |
| `--domain DOMAIN` | Резервный домен Cloudflare CDN (используется deploy, preflight, test) |

Команды клиента (`client add/show/list/remove/enable/disable`) работают с панелью кластера напрямую и не принимают `--server`.

## Разрешение сервера

Команды которым нужен сервер следуют этому приоритету:
1. Явный аргумент IP или ключевое слово `local` (deploy на этом сервере без SSH)
2. Флаг `--server NAME` (также принимает `--server local`)
3. Определение локального режима (запуск на самом сервере)
4. Автоматический выбор одного сервера (если сохранён только один)
5. Интерактивный диалог
