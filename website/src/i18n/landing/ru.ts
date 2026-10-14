import type { LandingStrings } from './en';

export const ru: LandingStrings = {
  // Page metadata
  'page.title': 'Meridian — свой VPN против цензуры, настроенный как надо',
  'page.description':
    'Одна команда разворачивает защищённый VPN-сервер на VLESS+Reality и создаёт ссылку, которой можно поделиться. Открытый код.',
  'page.ogAlt': 'Meridian: делитесь безопасным интернетом с теми, кто вам дорог',

  // Hero
  'hero.kicker': 'Открытый инструмент для свободного интернета',
  'hero.title': 'Поделитесь <em>безопасным интернетом</em> с&nbsp;теми, кто&nbsp;вам&nbsp;дорог',
  'hero.sub':
    'Одна команда поднимает VPN-сервер, который трудно отличить от обычного HTTPS: файрвол, TLS и маршрутизацию Meridian настроит за вас. Делитесь доступом по ссылке или QR-коду.',
  'hero.cta': 'Начать',
  'hero.docs': 'Читать документацию',
  'hero.previewAlt': 'Страница подключения — то, что получают ваши друзья',
  'hero.caption': 'Что получают ваши друзья — <a href="/demo/">смотреть демо</a>',

  // How it works
  'setup.label': 'Как это работает',
  'setup.title': 'Две команды — и VPN работает',
  'setup.desc':
    'Понадобится VPS на Debian или Ubuntu (хватит и самого дешёвого, за $5 в месяц) и терминал на вашем компьютере. Остальное Meridian настроит сам.',
  'setup.step1.label': 'Шаг 1 — Установка',
  'setup.step2.label': 'Шаг 2 — Настройка',
  'setup.then': 'затем',
  'setup.hint':
    'Мастер задаст несколько простых вопросов, покажет сводку и настроит ваш сервер. Для одного сервера без вопросов выполните <code>meridian deploy IP</code> — см. <a href="{base}/cli-reference/">справочник CLI</a>.',
  'setup.terminalAlt': 'Вывод Meridian в терминале',
  'setup.details': 'Что происходит за кулисами',
  'setup.idempotent':
    'Команду можно спокойно запускать повторно: учётные данные и ключи сохранятся, прерванное развёртывание продолжится, а готовое обновится на месте.',

  // Behind-the-scenes timeline (title, then the text that follows it)
  'step.1.title': 'Устанавливает Docker',
  'step.1.text': ' и разворачивает <strong>Xray</strong> под управлением панели <strong>Remnawave</strong>',
  'step.2.title': 'Генерирует пару ключей x25519',
  'step.2.text': ' — уникальные ключи шифрования для аутентификации Reality',
  'step.3.title': 'Защищает сервер',
  'step.3.text':
    ' — UFW открывает только ваш порт SSH, 443 (TCP и UDP) и 80 для сертификатов; вход по SSH только по ключу, fail2ban, управление перегрузкой <strong>BBR</strong>',
  'step.4.title': 'Настраивает VLESS+Reality',
  'step.4.text': ' на TCP 443: любой, кто проверяет сервер, видит сертификат настоящего сайта',
  'step.5.title': 'Добавляет XHTTP и Hysteria2',
  'step.5.text': ' — XHTTP через nginx на том же порту, Hysteria2 на UDP 443 с обфускацией salamander',
  'step.6.title': 'Выводит QR-коды',
  'step.6.text': ' и страницу подключения — после того, как через новую подписку прошёл реальный трафик',

  // Command builder section
  'builder.label': 'Конструктор команд',
  'builder.title': 'Соберите свою команду',
  'builder.desc':
    'Выберите параметры и скопируйте готовую команду: развёртывание, промежуточные серверы, проверки, поиск сайтов для маскировки, диагностика и удаление.',

  // Command builder widget
  'builder.tab.deploy': 'Развёртывание',
  'builder.tab.relay': 'Промежуточный сервер',
  'builder.tab.check': 'Проверка',
  'builder.tab.scan': 'Поиск SNI',
  'builder.tab.doctor': 'Диагностика',
  'builder.tab.teardown': 'Удаление',
  'builder.desc.deploy':
    'Развёртывание Reality, XHTTP и Hysteria2 на одном сервере. Настраивает Docker, панель Remnawave, nginx, файрвол и TLS.',
  'builder.desc.relay':
    'Добавляет промежуточный сервер внутри страны, который пересылает TCP-трафик на выходной сервер за границей. Выходной сервер нужно развернуть заранее.',
  'builder.desc.check':
    'Проверяет, подойдёт ли новый сервер для Meridian (SNI, порты, DNS, ОС, диск) или здоров ли уже развёрнутый.',
  'builder.desc.scan':
    'Ищет сайты для маскировки (SNI) в сети вашего сервера с помощью RealiTLScanner, затем вы выбираете один из них.',
  'builder.desc.doctor': 'Собирает диагностический отчёт для сообщения об ошибке. Секреты скрываются.',
  'builder.desc.teardown':
    'Удаляет Meridian с сервера. Развёртывание на одном сервере удаляется полностью, включая локальную конфигурацию.',
  'builder.field.server': 'Сервер',
  'builder.field.server.placeholder': '198.51.100.10 или local',
  'builder.field.server.hint':
    'IP сервера, имя сохранённого сервера или «local» для запуска на самом сервере. Оставьте пустым, чтобы команда спросила сама.',
  'builder.field.relayServer': 'Промежуточный сервер',
  'builder.field.relayServer.hint': 'IP сервера внутри страны, который будет пересылать трафик.',
  'builder.field.user': 'Пользователь SSH',
  'builder.field.user.hint':
    'По умолчанию — сохранённый пользователь, иначе root. Пользователям не root нужен sudo без пароля.',
  'builder.field.sshPort': 'Порт SSH',
  'builder.field.sshPort.hint': 'По умолчанию — сохранённый порт, иначе 22.',
  'builder.field.exit': 'Выходной сервер',
  'builder.field.exit.placeholder': '198.51.100.10 или имя сервера',
  'builder.field.exit.hint': 'IP или имя развёрнутого выходного сервера, куда промежуточный сервер пересылает трафик.',
  'builder.field.domain': 'Домен',
  'builder.field.domain.hint':
    'Необязательно. Переносит XHTTP на ваш домен и добавляет WSS; оба способа могут идти через CDN Cloudflare.',
  'builder.field.sni': 'Сайт для маскировки (SNI)',
  'builder.field.sni.placeholder': 'авто',
  'builder.field.sni.hint':
    'Сайт, под который маскируется Reality. Оставьте «авто», и deploy выберет тот, что работает с вашего сервера, или подберите его на вкладке «Поиск SNI».',
  'builder.field.relaySni.hint': 'Оставьте «авто», чтобы взять сайт для маскировки выходного сервера.',
  'builder.field.clientName': 'Первый клиент',
  'builder.field.clientName.hint': 'Имя первого, кто подключится. По умолчанию: «default».',
  'builder.field.displayName': 'Заголовок страницы',
  'builder.field.displayName.placeholder': 'VPN Алисы',
  'builder.field.displayName.hint': 'Надпись на страницах подключения.',
  'builder.field.icon': 'Значок страницы',
  'builder.field.icon.hint': 'Эмодзи или URL изображения для страницы подключения.',
  'builder.field.color': 'Цвет страницы',
  'builder.field.color.hint': 'Цветовая тема страницы подключения.',
  'builder.field.color.default': 'По умолчанию (океан)',
  'builder.palette.ocean': 'Океан',
  'builder.palette.sunset': 'Закат',
  'builder.palette.forest': 'Лес',
  'builder.palette.lavender': 'Лаванда',
  'builder.palette.rose': 'Роза',
  'builder.palette.slate': 'Графит',
  'builder.field.relayName': 'Имя промежуточного сервера',
  'builder.field.relayName.hint': 'Необязательное понятное имя, например «ru-moscow».',
  'builder.field.port': 'Порт прослушивания',
  'builder.field.port.hint': 'Порт, который слушает промежуточный сервер. По умолчанию: 443.',
  'builder.field.harden': 'Не трогать SSH и файрвол',
  'builder.field.harden.hint':
    'Только если на сервере работают другие службы: по умолчанию Meridian защищает сервер.',
  'builder.field.ai': 'Вывод для ИИ',
  'builder.field.ai.hint': 'Копирует в буфер обмена промпт для ChatGPT или Claude.',
  'builder.field.yes': 'Без подтверждения',
  'builder.field.yes.hint': 'Для скриптов и неинтерактивных сеансов SSH.',
  'builder.command': 'Команда',
  'builder.copy': 'Копировать',
  'builder.copied': 'Скопировано',
  'builder.wizard':
    '<strong>Впервые?</strong> Просто запустите <code>meridian setup</code> — мастер спросит всё, что нужно.',
  'builder.status.invalid': 'Укажите IP-адрес, имя сохранённого сервера или «local».',
  'builder.status.local': 'Работает на самом сервере без SSH — от root или через sudo без пароля.',
  'builder.status.domainMissing': 'Введите домен или снимите галочку.',
  'builder.status.deploy': 'Разворачивает Reality, XHTTP и Hysteria2.',
  'builder.status.deployDomain': 'Разворачивает Reality, XHTTP и Hysteria2, а также WSS на вашем домене.',
  'builder.status.relayNeed': 'Введите промежуточный сервер и выходной сервер, куда он пересылает трафик.',
  'builder.status.relayOk':
    'Добавляет подключения Reality через промежуточный сервер; остальные способы по-прежнему идут напрямую к выходному серверу.',
  'builder.status.checkNeed': 'Без сервера проверяет все сохранённые серверы.',
  'builder.status.checkOk': 'Запускает проверки, ничего не меняя.',
  'builder.status.scanNeed': 'Введите ваш сервер, чтобы найти рядом с ним сайты для маскировки.',
  'builder.status.scanOk':
    'Сканирует сеть самого сервера, даёт выбрать сайт и выводит команду deploy.',
  'builder.status.doctorNeed': 'Без сервера составляет отчёт по сохранённому серверу.',
  'builder.status.doctorOk':
    'Собирает отчёт со скрытыми секретами. Включите вывод для ИИ, чтобы получить готовый промпт.',
  'builder.status.doctorAi':
    'Собирает отчёт со скрытыми секретами и копирует в буфер обмена готовый промпт для ИИ.',
  'builder.status.teardownNeed': 'Без сервера берёт сохранённый, если он один.',
  'builder.status.teardownOk': 'Если серверов несколько, сначала удалите роль сервера командой meridian node remove.',

  // How it avoids detection
  'tech.label': 'Технология',
  'tech.title': 'Почему Meridian трудно заметить',
  'tech.desc':
    'У OpenVPN и WireGuard узнаваемый трафик. Соединение VLESS+Reality трудно отличить от обычного HTTPS-запроса к настоящему сайту.',
  'tech.dpi': 'Глубокая инспекция пакетов (DPI)',
  'tech.dpi.body':
    '<p>DPI анализирует характер трафика, чтобы распознавать прокси-протоколы. У OpenVPN и WireGuard рукопожатия легко узнать и заблокировать.</p><p><strong>VLESS+Reality</strong> работает внутри обычного соединения TLS 1.3 на порту 443, поэтому DPI видит то, что выглядит как обычный HTTPS к сайту для маскировки. Каждый сервер также предлагает XHTTP и Hysteria2, так что блокировка одного способа не мешает работать остальным.</p>',
  'tech.probe': 'Активное зондирование',
  'tech.probe.body':
    '<p>Цензоры подключаются к подозрительным серверам и пытаются снять с них отпечаток. Если сервер отвечает не так, как сайт, за который себя выдаёт, его блокируют.</p><p><strong>Reality</strong> заимствует TLS-идентичность настоящего сайта: по умолчанию это сайт, размещённый рядом с вашим сервером, или <code>www.google.com</code>. Любой, кто подключается без вашего ключа, попадает на этот сайт и видит его подлинный сертификат. Туннель получают только клиенты с вашим ключом.</p>',
  'tech.fp': 'Отпечатки TLS',
  'tech.fp.body':
    '<p>Каждый TLS-клиент оставляет узнаваемый отпечаток в сообщении Client Hello. Цензоры помечают соединения, у которых отпечаток не соответствует заявленному приложению.</p><p>Клиентские приложения используют <strong>uTLS</strong>, чтобы отправлять тот же Client Hello, что и Chrome. Meridian также публикует копию каждой записи Reality и XHTTP для Firefox, так что если цензоры пометят один отпечаток, другой продолжит работать.</p>',

  // Connect
  'connect.label': 'Подключение',
  'connect.title': 'Отсканируйте и подключитесь',
  'connect.desc':
    'После развёртывания вы получаете страницу подключения с QR-кодами. Отправьте её тому, кому нужен доступ: одно нажатие — и всё работает.',
  'connect.apps.more':
    'Страницы подключения сначала показывают эти приложения, а для каждой платформы дают ссылки на другие. <a href="/demo/">Смотреть демо</a>.',
  'connect.clock':
    'Включите автоматическую установку даты и времени на каждом устройстве. Если часы на устройстве неверны, соединение не работает.',

  // Architecture
  'arch.label': 'Архитектура',
  'arch.title': 'Под капотом',
  'arch.alt':
    'Архитектура Meridian: nginx по SNI направляет TCP 443 на Xray Reality, XHTTP и страницы подключения; Hysteria2 слушает UDP 443',
  'arch.standalone': 'Без домена',
  'arch.standalone.body':
    "<p><strong>nginx</strong> занимает TCP-порт 443 и модулем stream распределяет соединения по TLS SNI. Соединения Reality идут в <strong>Xray</strong>. Всё остальное попадает в модуль http nginx, который отдаёт страницы подключения, подписки и XHTTP по HTTPS с короткоживущим сертификатом Let's Encrypt для IP сервера (его выпускает acme.sh). <strong>Hysteria2</strong> слушает UDP 443 с обфускацией salamander.</p><p>Панель администратора Remnawave проксируется nginx по секретному пути — доступна по HTTPS, SSH-туннель не нужен.</p>",
  'arch.domain': 'Доменный режим (запасной путь через CDN)',
  'arch.domain.body':
    "<p>С <code>--domain</code> nginx получает сертификат Let's Encrypt для вашего домена и направляет соединения с этим SNI на способы подключения через домен. XHTTP переезжает на домен, и добавляется <strong>VLESS+WSS</strong>. Если Cloudflare проксирует домен, оба способа идут через CDN и продолжают работать, даже если IP вашего сервера заблокирован. Панель и страницы подключения остаются на сертификате для IP.</p>",
  'arch.relay': 'Промежуточный сервер (точка входа внутри страны)',
  'arch.relay.body':
    '<p>TCP-форвардер <strong>Realm</strong> на сервере внутри вашей страны передаёт соединения на выходной сервер за границей. Через него идёт только Reality; XHTTP, WSS и Hysteria2 по-прежнему подключаются к выходному серверу напрямую. Шифрование сквозное, от приложения до выходного сервера, поэтому промежуточный сервер никогда не видит ваш трафик в открытом виде.</p><p>Добавьте его командой <code>meridian relay add RELAY_IP --exit EXIT</code>. Подписки и страницы подключения подхватывают его автоматически.</p>',

  // Closing call to action
  'cta.title': 'Готовы начать?',
  'cta.desc':
    'Установите Meridian, разверните его на своём сервере и делитесь доступом с теми, кому он нужен. Развёртывание обычно занимает от 3 до 10 минут.',
  'cta.install': 'Установить Meridian',
};
