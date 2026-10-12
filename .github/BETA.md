> [!WARNING]
> **This is a beta of Meridian 4.** Nobody gets it automatically: `pip`, `uv`, the install script, and `meridian update` on 3.x all skip it. Meridian 3.x keeps working.

**Use a fresh server you can wipe.** Meridian 4 cannot manage servers deployed by 3.x, and every user of a moved server needs a new link.

### Try it next to 3.x

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Your 3.x command and `~/.meridian` stay untouched:

```bash
alias meridian4='MERIDIAN_HOME="$HOME/.meridian-v4" uvx --prerelease allow --from meridian-vpn@latest meridian'
meridian4 deploy 198.51.100.10        # your server's IP
```

The alias always runs the newest beta. Then, **from a device inside the censored network**, run:

```bash
meridian4 test 198.51.100.10          # does real traffic pass from here?
meridian4 probe 198.51.100.10         # does the server look like a proxy from here?
```

Known gaps: a server you remove must still be reachable, or the change cannot finish; removing a node leaves Docker installed on it.

### Tell us what you see

Open a [Meridian 4 beta report](https://github.com/getmeridian/meridian/issues/new?template=v4_beta.yml). The most useful details: your country and network type (home ISP or mobile operator), whether your client app connects, and the output of `meridian4 test`. Outputs hide secrets, but replace IPs, domains, and links before posting.

---

> [!WARNING]
> **Это бета-версия Meridian 4.** Она не устанавливается сама: `pip`, `uv`, установочный скрипт и `meridian update` в 3.x её пропускают. Meridian 3.x продолжает работать.

**Используйте новый сервер, который не жалко переустановить.** Meridian 4 не управляет серверами, развёрнутыми через 3.x, а после переезда сервера всем его пользователям нужна новая ссылка.

### Как попробовать рядом с 3.x

Нужен [uv](https://docs.astral.sh/uv/getting-started/installation/). Команда 3.x и папка `~/.meridian` останутся как есть:

```bash
alias meridian4='MERIDIAN_HOME="$HOME/.meridian-v4" uvx --prerelease allow --from meridian-vpn@latest meridian'
meridian4 deploy 198.51.100.10        # IP вашего сервера
```

Алиас всегда запускает самую свежую бету. Затем **с устройства внутри сети с блокировками** выполните:

```bash
meridian4 test 198.51.100.10          # проходит ли реальный трафик отсюда?
meridian4 probe 198.51.100.10         # похож ли сервер на прокси, если смотреть отсюда?
```

Известные ограничения: удаляемый сервер должен быть доступен, иначе изменение не завершится; после удаления узла на сервере остаётся Docker.

### Расскажите, что получилось

Откройте [отчёт о бете Meridian 4](https://github.com/getmeridian/meridian/issues/new?template=v4_beta.yml). Важнее всего: страна и тип сети (домашний провайдер или мобильный оператор), подключается ли клиентское приложение, и вывод `meridian4 test`. Секреты в выводе скрыты, но перед публикацией замените IP-адреса, домены и ссылки.
