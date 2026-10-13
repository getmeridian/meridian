> [!WARNING]
> **This is a beta of Meridian 4.** Nobody gets it automatically: `pip`, `uv`, the install script, and `meridian update` on 3.x all skip it. Your 3.x servers keep working.

**Use a separate, fresh server you can wipe.** Meridian 3.x and 4 cannot share a server: both need port 443. Meridian 4 refuses to deploy onto a server that still runs 3.x; replacing a 3.x server means tearing it down first, and every one of its users needs a new link.

### Try it next to 3.x

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (macOS or Linux; on Windows use WSL). On your computer Meridian 4 keeps its state in `~/.config/meridian`, so your 3.x command and `~/.meridian` stay untouched:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv and uvx; open a new terminal afterwards
alias meridian4='uvx --prerelease allow --from meridian-vpn@latest meridian'
meridian4 deploy 198.51.100.10        # IP of the separate server
```

Prefer questions to flags? `meridian4 setup` walks you through one server or several (exits in other countries, relays) in English, Russian, Persian, or Chinese. The alias always runs the newest beta; after a new one comes out, `meridian4 apply` brings your servers up to date.

**Keep `~/.config/meridian`.** It is how Meridian 4 manages your servers, and there is no recovery command yet: lose it and you have to tear down and deploy again. When 4.0 is released, install `meridian` the usual way and it manages the same servers; nothing to move or redeploy. Set up with an earlier alias that pointed at `~/.meridian-v4`? Drop that part; Meridian finds the folder on its own.

Then, **from a device inside the censored network**, run:

```bash
meridian4 test 198.51.100.10          # does real traffic pass from here?
meridian4 test --probe 198.51.100.10  # does the server look like a proxy from here?
```

Known gaps: a server you remove must still be reachable, or the change cannot finish; removing a node leaves Docker installed on it.

### Tell us what you see

Open a [Meridian 4 beta report](https://github.com/getmeridian/meridian/issues/new?template=v4_beta.yml). The most useful details: your country and network type (home ISP or mobile operator), whether your client app connects, and the output of `meridian4 test`; for deploy problems add `meridian4 doctor`. People you shared a connection page with can open "Not connecting?" on it, run the check, and send you the copied report: it contains no links or addresses. Outputs hide secrets, but replace IPs, domains, and links before posting.

---

> [!WARNING]
> **Это бета-версия Meridian 4.** Она не устанавливается сама: `pip`, `uv`, установочный скрипт и `meridian update` в 3.x её пропускают. Ваши серверы на 3.x продолжают работать.

**Используйте отдельный новый сервер, который не жалко переустановить.** Meridian 3.x и 4 не могут работать на одном сервере: обоим нужен порт 443. Meridian 4 откажется разворачиваться на сервере, где ещё работает 3.x; чтобы заменить такой сервер, его сначала нужно очистить, и всем его пользователям понадобится новая ссылка.

### Как попробовать рядом с 3.x

Нужен [uv](https://docs.astral.sh/uv/getting-started/installation/) (macOS или Linux; на Windows через WSL). На вашем компьютере Meridian 4 хранит своё состояние в `~/.config/meridian`, поэтому команда 3.x и папка `~/.meridian` останутся как есть:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # ставит uv и uvx; затем откройте новый терминал
alias meridian4='uvx --prerelease allow --from meridian-vpn@latest meridian'
meridian4 deploy 198.51.100.10        # IP отдельного сервера
```

Удобнее отвечать на вопросы, чем писать флаги? `meridian4 setup` проведёт через настройку одного сервера или нескольких (выходы в других странах, реле) на русском, английском, персидском или китайском. Алиас всегда запускает самую свежую бету; после выхода новой `meridian4 apply` обновит ваши серверы.

**Не удаляйте `~/.config/meridian`.** Через эту папку Meridian 4 управляет вашими серверами, а команды восстановления пока нет: без неё придётся всё удалить и развернуть заново. Когда выйдет 4.0, установите `meridian` обычным способом, и он будет управлять теми же серверами: ничего переносить и разворачивать заново не нужно. Настраивали раньше с алиасом, указывавшим на `~/.meridian-v4`? Уберите эту часть: Meridian найдёт папку сам.

Затем **с устройства внутри сети с блокировками** выполните:

```bash
meridian4 test 198.51.100.10          # проходит ли реальный трафик отсюда?
meridian4 test --probe 198.51.100.10  # похож ли сервер на прокси, если смотреть отсюда?
```

Известные ограничения: удаляемый сервер должен быть доступен, иначе изменение не завершится; после удаления узла на сервере остаётся Docker.

### Расскажите, что получилось

Откройте [отчёт о бете Meridian 4](https://github.com/getmeridian/meridian/issues/new?template=v4_beta.yml). Важнее всего: страна и тип сети (домашний провайдер или мобильный оператор), подключается ли клиентское приложение, и вывод `meridian4 test`; при проблемах с развёртыванием добавьте `meridian4 doctor`. Те, с кем вы поделились страницей подключения, могут открыть на ней «Не подключается?», запустить проверку и прислать вам скопированный отчёт: в нём нет ссылок и адресов. Секреты в выводе скрыты, но перед публикацией замените IP-адреса, домены и ссылки.
