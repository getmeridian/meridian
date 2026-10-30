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

When the deploy finishes, `meridian4 panel` prints the address of the web panel, where you manage people and API tokens from a browser, and a one-time link to set its password. Treat both like passwords.

Prefer questions to flags? `meridian4 setup` walks you through one server or several (exits in other countries, relays) in English, Russian, Persian, or Chinese. The alias always runs the newest beta; after a new one comes out, `meridian4 apply` brings your servers up to date.

**Already running an earlier 4.0 beta?** Those deployed Remnawave; b9 replaces it with Meridian's own control service. Run `meridian4 migrate` once: it keeps every client link, key, limit, expiry, and traffic count, and checks the links are unchanged before it removes Remnawave. It first backs up the panel's database to `~/.config/meridian/backups/` on your computer and to `/var/backups/meridian-migration/` on the control server; there is no rollback, the backup is the way back. It resumes if interrupted. Panel-only data is not carried over (the panel's admin account, per-day usage history, HWID device lists, plugins, and client IP forwarding), and the review lists it.

**Keep `~/.config/meridian`.** It is how Meridian 4 manages your servers. After every change, each computer that manages the fleet saves an encrypted snapshot of the control service, and your first deploy shows a recovery key once: write it down and keep it offline. If the control server is lost or seized, `meridian4 restore --to NEW-SERVER` rebuilds it from the newest snapshot (on a bare IP, every link changes and is printed; with a domain they stay). See the [recovery guide](https://getmeridian.org/en/recovery/). When 4.0 is released, install `meridian` the usual way and it manages the same servers; nothing to move or redeploy. Set up with an earlier alias that pointed at `~/.meridian-v4`? Drop that part; Meridian finds the folder on its own.

Then, **from a device inside the censored network**, run:

```bash
meridian4 test 198.51.100.10          # does real traffic pass from here?
meridian4 test --probe 198.51.100.10  # does the server look like a VPN server from here?
```

Known gaps: the control server cannot be moved with `node replace`, only rebuilt with `restore`; client IP forwarding and per-user IP limits are not available (the control service stores no client addresses); and a server you remove should still answer, or its cleanup cannot finish: if it is gone, add `--forget-unreachable` to let Meridian forget it.

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

Когда развёртывание закончится, `meridian4 panel` покажет адрес веб-панели, где в браузере управляют людьми и API-токенами, и одноразовую ссылку для задания пароля. Относитесь к обоим как к паролям.

Удобнее отвечать на вопросы, чем писать флаги? `meridian4 setup` проведёт через настройку одного сервера или нескольких (выходы в других странах, реле) на русском, английском, персидском или китайском. Алиас всегда запускает самую свежую бету; после выхода новой `meridian4 apply` обновит ваши серверы.

**Уже запускали раннюю бету 4.0?** Она разворачивала Remnawave, а b9 заменяет его собственным сервисом управления Meridian. Выполните один раз `meridian4 migrate`: он сохраняет все ссылки клиентов, ключи, лимиты, сроки и счётчики трафика и проверяет, что ссылки не изменились, прежде чем удалить Remnawave. Сначала он делает резервную копию базы панели в `~/.config/meridian/backups/` на вашем компьютере и в `/var/backups/meridian-migration/` на управляющем сервере; отката нет, вернуться можно только из копии. После прерывания команда продолжает с того же места. Данные, которые есть только в панели (учётная запись администратора, посуточная история трафика, списки устройств HWID, плагины и передача IP клиентов), не переносятся, и обзор их перечисляет.

**Не удаляйте `~/.config/meridian`.** Через эту папку Meridian 4 управляет вашими серверами. После каждого изменения каждый компьютер, управляющий флотом, сохраняет зашифрованный снимок сервиса управления, а первое развёртывание один раз показывает ключ восстановления: запишите его и храните офлайн. Если управляющий сервер потерян или изъят, `meridian4 restore --to НОВЫЙ-СЕРВЕР` восстановит его из свежего снимка (при адресе-IP все ссылки меняются и выводятся на экран; с доменом остаются прежними). См. [руководство по восстановлению](https://getmeridian.org/ru/recovery/). Когда выйдет 4.0, установите `meridian` обычным способом, и он будет управлять теми же серверами: ничего переносить и разворачивать заново не нужно. Настраивали раньше с алиасом, указывавшим на `~/.meridian-v4`? Уберите эту часть: Meridian найдёт папку сам.

Затем **с устройства внутри сети с блокировками** выполните:

```bash
meridian4 test 198.51.100.10          # проходит ли реальный трафик отсюда?
meridian4 test --probe 198.51.100.10  # похож ли сервер на VPN-сервер, если смотреть отсюда?
```

Известные ограничения: управляющий сервер нельзя перенести командой `node replace`, только восстановить через `restore`; передача IP клиентов и лимиты по IP недоступны (сервис управления не хранит адреса клиентов); удаляемый сервер должен отвечать, иначе его очистка не завершится: если сервера больше нет, добавьте `--forget-unreachable`, и Meridian его забудет.

### Расскажите, что получилось

Откройте [отчёт о бете Meridian 4](https://github.com/getmeridian/meridian/issues/new?template=v4_beta.yml). Важнее всего: страна и тип сети (домашний провайдер или мобильный оператор), подключается ли клиентское приложение, и вывод `meridian4 test`; при проблемах с развёртыванием добавьте `meridian4 doctor`. Те, с кем вы поделились страницей подключения, могут открыть на ней «Не подключается?», запустить проверку и прислать вам скопированный отчёт: в нём нет ссылок и адресов. Секреты в выводе скрыты, но перед публикацией замените IP-адреса, домены и ссылки.
