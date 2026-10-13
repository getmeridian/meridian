> [!WARNING]
> **This is a beta of Meridian 4.** Nobody gets it automatically: `pip`, `uv`, the install script, and `meridian update` on 3.x all skip it. Your 3.x servers keep working.

**Use a separate, fresh server you can wipe.** Meridian 3.x and 4 cannot share a server: both need port 443. Meridian 4 refuses to deploy onto a server that still runs 3.x; replacing a 3.x server means tearing it down first, and every one of its users needs a new link.

### Try it next to 3.x

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). On your computer the beta keeps its own state, so your 3.x command and `~/.meridian` stay untouched:

```bash
alias meridian4='MERIDIAN_HOME="$HOME/.meridian-v4" uvx --prerelease allow --from meridian-vpn@latest meridian'
meridian4 deploy 198.51.100.10        # IP of the separate server
```

Prefer questions to flags? `meridian4 setup` walks you through one server or several (exits in other countries, relays) in English, Russian, Persian, or Chinese. The alias always runs the newest beta; after a new one comes out, `meridian4 apply` brings your servers up to date.

**Keep `~/.meridian-v4`.** It is how the beta manages your servers, and there is no recovery command yet: lose it and you have to tear down and deploy again. Then, **from a device inside the censored network**, run:

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

Нужен [uv](https://docs.astral.sh/uv/getting-started/installation/). На вашем компьютере бета хранит своё состояние отдельно, поэтому команда 3.x и папка `~/.meridian` останутся как есть:

```bash
alias meridian4='MERIDIAN_HOME="$HOME/.meridian-v4" uvx --prerelease allow --from meridian-vpn@latest meridian'
meridian4 deploy 198.51.100.10        # IP отдельного сервера
```

Удобнее отвечать на вопросы, чем писать флаги? `meridian4 setup` проведёт через настройку одного сервера или нескольких (выходы в других странах, реле) на русском, английском, персидском или китайском. Алиас всегда запускает самую свежую бету; после выхода новой `meridian4 apply` обновит ваши серверы.

**Не удаляйте `~/.meridian-v4`.** Через эту папку бета управляет вашими серверами, а команды восстановления пока нет: без неё придётся всё удалить и развернуть заново. Затем **с устройства внутри сети с блокировками** выполните:

```bash
meridian4 test 198.51.100.10          # проходит ли реальный трафик отсюда?
meridian4 test --probe 198.51.100.10  # похож ли сервер на прокси, если смотреть отсюда?
```

Известные ограничения: удаляемый сервер должен быть доступен, иначе изменение не завершится; после удаления узла на сервере остаётся Docker.

### Расскажите, что получилось

Откройте [отчёт о бете Meridian 4](https://github.com/getmeridian/meridian/issues/new?template=v4_beta.yml). Важнее всего: страна и тип сети (домашний провайдер или мобильный оператор), подключается ли клиентское приложение, и вывод `meridian4 test`; при проблемах с развёртыванием добавьте `meridian4 doctor`. Те, с кем вы поделились страницей подключения, могут открыть на ней «Не подключается?», запустить проверку и прислать вам скопированный отчёт: в нём нет ссылок и адресов. Секреты в выводе скрыты, но перед публикацией замените IP-адреса, домены и ссылки.
