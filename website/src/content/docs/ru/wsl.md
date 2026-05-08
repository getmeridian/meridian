---
title: Настройка Windows и WSL
description: Настройте Meridian в Windows через Windows Subsystem for Linux.
order: 2.5
section: guides
---

Meridian запускается из Unix-подобного терминала. В Windows используйте Windows Subsystem for Linux (WSL) и запускайте Meridian внутри Linux-среды.

Нативные PowerShell, Command Prompt, Git Bash и Cygwin не являются рекомендуемым способом для Meridian. WSL дает такую же оболочку, SSH и поведение файловой системы, как Linux, что соответствует остальной документации.

## Установить WSL

Откройте PowerShell от имени администратора и установите WSL:

```powershell
wsl --install
```

Перезагрузитесь, если появится запрос, затем откройте установленный Linux-дистрибутив из меню Start.

Ubuntu - самый безопасный вариант по умолчанию, потому что Meridian ориентируется на серверы Debian и Ubuntu, а большинство примеров используют названия пакетов `apt`. Если у вас уже установлен Debian в WSL, он тоже подойдет.

Проверьте, что WSL использует версию 2:

```powershell
wsl --list --verbose
```

Если у дистрибутива указана версия 1, преобразуйте его:

```powershell
wsl --set-version Ubuntu 2
```

## Подготовить оболочку WSL

В терминале WSL обновите пакеты и установите базовые инструменты:

```bash
sudo apt update
sudo apt install -y curl openssh-client ca-certificates
```

Установите Meridian:

```bash
curl -sSf https://getmeridian.org/install.sh | bash
```

Перезапустите терминал WSL или перезагрузите профиль оболочки, если `meridian` не найден сразу:

```bash
exec "$SHELL" -l
```

Проверьте установку:

```bash
meridian --version
```

## Настроить SSH-ключи

Meridian подключается из WSL к вашему VPS по SSH. Создайте ключ внутри WSL:

```bash
ssh-keygen -t ed25519 -C "meridian-wsl"
```

Скопируйте публичный ключ:

```bash
cat ~/.ssh/id_ed25519.pub
```

Добавьте этот публичный ключ у VPS-провайдера или в `~/.ssh/authorized_keys` пользователя на сервере.

Проверьте SSH перед запуском Meridian:

```bash
ssh root@198.51.100.10
```

Если ваш VPS использует пользователя не root, проверьте этого пользователя и передайте его в deploy:

```bash
meridian deploy 198.51.100.10 --user ubuntu
```

## Использовать SSH-агент

Если у ключа есть passphrase, запустите агент внутри WSL:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

Добавляйте эти команды в `~/.profile` или `~/.bashrc` только если понимаете компромисс безопасности. Для первых развертываний обычно достаточно запускать агент вручную.

Пока настраиваете Meridian, не смешивайте SSH-агенты Windows и WSL. Держите ключ и агент внутри WSL, пока развертывание не заработает.

## Запускать Meridian из WSL

Запустите обычный процесс развертывания из терминала WSL:

```bash
meridian deploy
```

Или укажите IP сервера сразу:

```bash
meridian deploy 198.51.100.10
```

Meridian хранит учетные данные в `~/.meridian/` внутри WSL. Запускайте последующие команды из того же дистрибутива WSL, чтобы Meridian мог найти кэшированные учетные данные сервера:

```bash
meridian client add alice
meridian client list
meridian test 198.51.100.10
```

## VS Code Remote WSL

Если вы используете VS Code, установите расширение "WSL" и откройте рабочую папку Meridian из WSL:

```bash
code .
```

Файлы, открытые через Remote WSL, находятся в Linux-файловой системе, что помогает избежать проблем с путями и правами. Предпочитайте путь внутри домашней папки WSL, например `~/projects`, вместо редактирования состояния Meridian или SSH-ключей через `/mnt/c/`.

## Частые проблемы

### Мастер как будто пропускает вопрос

Некоторые интерактивные подсказки терминала могут вести себя в WSL иначе. Если мастер deploy не принимает ввод как ожидается, укажите значения явно:

```bash
meridian deploy 198.51.100.10 --sni www.microsoft.com
```

Также можно сначала запустить предварительную проверку:

```bash
meridian preflight 198.51.100.10
```

### Permission denied по SSH

Проверьте, что ключ существует в WSL, а не только в Windows:

```bash
ls -la ~/.ssh
ssh -v root@198.51.100.10
```

Приватный ключ должен читаться только вашим пользователем WSL:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
```

### Meridian установлен, но команда не найдена

Перезагрузите оболочку:

```bash
exec "$SHELL" -l
```

Если команда все еще не найдена, проверьте обычные пути uv и локальных бинарных файлов:

```bash
ls ~/.local/bin
```

При необходимости добавьте `~/.local/bin` в профиль оболочки:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
exec "$SHELL" -l
```

### Пути Windows вызывают проблемы с правами

Держите SSH-ключи, учетные данные Meridian и проектные файлы в файловой системе WSL:

```bash
cd ~
mkdir -p projects
```

Не храните `~/.ssh` или `~/.meridian` под `/mnt/c/`. У путей, смонтированных из Windows, могут быть другие права, и SSH их отклонит.

## Дальше

- [Начало работы](/docs/ru/getting-started/) — разверните первый сервер
- [Установка](/docs/ru/installation/) — варианты установки CLI
- [Устранение неполадок](/docs/ru/troubleshooting/) — частые проблемы развертывания и подключения
