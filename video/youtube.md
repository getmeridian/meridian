# YouTube upload

Two uploads, one per language. Files come from `pnpm export [ru]`: the 1080p video `out/meridian-setup[.ru].mp4`, the thumbnail `out/thumbnail.<lang>.jpg`, subtitles `out/meridian-setup.<lang>.srt` (the captions are also burned into the picture, so leave the track off by default), and chapters `out/chapters.<lang>.txt`; refresh the chapters here after a re-render.
Settings: not made for kids, no monetization (the ElevenLabs free tier is non-commercial), category Science & Technology, licence Standard YouTube or Creative Commons. Link the two videos to each other in their descriptions once both are up.

# English

## Title

Set up your own VPN in 10 minutes with Meridian (free, open source)

## Description

Your own VPN, on a server you rent, in about ten minutes. Meridian is a free, open-source tool that sets up a hardened VLESS+Reality server, with XHTTP and Hysteria2 as fallbacks, and gives you a connection page and QR code for each person you share it with.

The terminal is a real run of `meridian setup` on a fresh server; waits are shortened, and addresses and secrets are replaced with examples.

Install:
curl -sSf https://getmeridian.org/install.sh | bash
or: uv tool install meridian-vpn

Then: meridian setup

Docs: https://getmeridian.org/docs/
Source: https://github.com/getmeridian/meridian

Chapters
0:00 What Meridian does
0:18 What you need
0:37 Install
0:50 meridian setup: the server
1:10 Defaults and the VPN's name
1:25 Camouflage
1:45 People and review
1:55 Setting up
2:19 Links to share
2:29 On the phone
2:44 Everyday commands
2:59 Why it's hard to block

Narration voice: elevenlabs.io

## Tags

vpn, self-hosted vpn, personal vpn, vless, reality, xray, hysteria2, censorship, open source, meridian

# Русский

Видео: `out/meridian-setup.ru.mp4`. Озвучка сделана локально (Qwen3-TTS), так что ограничение ElevenLabs на монетизацию к этой версии не относится. Также подходит для Rutube и VK Видео.

## Title

Свой VPN за 10 минут с Meridian (бесплатно, открытый код)

## Description

Свой VPN на арендованном сервере примерно за десять минут. Meridian — бесплатный инструмент с открытым исходным кодом: он настраивает защищённый сервер VLESS+Reality, с XHTTP и Hysteria2 про запас, и выдаёт страницу подключения с QR-кодом для каждого, с кем вы делитесь VPN.

В терминале настоящий запуск `meridian setup` на чистом сервере; ожидание сокращено, а адреса и секреты заменены примерами.

Установка:
curl -sSf https://getmeridian.org/install.sh | bash
или: uv tool install meridian-vpn

Затем: meridian setup

Документация: https://getmeridian.org/docs/ru/
Исходный код: https://github.com/getmeridian/meridian

Главы
0:00 Что делает Meridian
0:22 Что понадобится
0:45 Установка
0:59 meridian setup: сервер
1:24 Настройки по умолчанию и название VPN
1:43 Маскировка
2:04 Люди и проверка плана
2:14 Установка на сервер
2:42 Ссылки для отправки
2:52 На телефоне
3:12 Команды на каждый день
3:28 Почему это сложно заблокировать

Озвучка: Qwen3-TTS.

## Tags

vpn, свой vpn, личный vpn, vless, reality, xray, hysteria2, обход блокировок, открытый код, meridian
