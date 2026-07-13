/**
 * Meridian translations — Russian, Farsi, Chinese.
 *
 * Keys match data-t="..." attributes in Astro components.
 * English is the build-time default (not in this file).
 *
 * Migrated from docs/i18n.js with keys adapted for the new landing page structure.
 */

export const translations: Record<string, Record<string, string>> = {
  ru: {
    // Page title
    'page.title': 'Meridian — Антицензурный прокси, развёрнутый правильно',

    // Nav / global
    'nav.docs': 'Документация',
    'nav.blog': 'Блог',
    'nav.demo': 'Демо',
    'nav.ping': 'Пинг',
    'toc.label': 'На этой странице',
    'docs.viewMd': 'Открыть Markdown',
    'docs.search': 'Поиск по документации...',

    // Hero
    'hero.kicker': 'Инструмент для свободы интернета',
    'hero.title': 'Поделитесь <em>безопасным интернетом</em> с&nbsp;близкими',
    'hero.sub': 'Одна команда разворачивает невидимый прокси-сервер — файрвол, TLS, маршрутизация, всё настроено безопасно по умолчанию. Делитесь доступом через QR-коды.',
    'hero.cta': 'Начать',
    'hero.docs': 'Документация',
    'hero.caption': 'Что получают ваши друзья — <a href="/demo/">смотреть демо</a>',

    // Setup
    'setup.label': 'Как это работает',
    'setup.title': 'Три шага к безопасному интернету',
    'setup.desc': 'Вам нужен VPS (любой сервер за $5/мес) и терминал на вашем ноутбуке. Meridian сделает всё остальное.',
    'setup.step1.label': 'Шаг 1 — Установка',
    'setup.step2.label': 'Шаг 2 — Развёртывание',
    'setup.then': 'затем',
    'setup.hint': 'Мастер спросит IP сервера и настроит всё. Или передайте флаги напрямую — смотрите <a href="/docs/ru/cli-reference/">справочник CLI</a>.',
    'setup.details': 'Что происходит за кулисами',
    'setup.idempotent': 'Безопасно перезапускать. Полная идемпотентность — данные сохраняются, настройки обновляются на месте.',

    // Behind the scenes timeline
    'step.1.title': 'Устанавливает Docker',
    'step.1.text': ' и разворачивает <strong>Xray</strong> через панель управления <strong>Remnawave</strong>',
    'step.2.title': 'Генерирует ключевую пару x25519',
    'step.2.text': ' — уникальные ключи шифрования для аутентификации Reality',
    'step.3.title': 'Защищает сервер',
    'step.3.text': ' — фаервол UFW (порты 22 + 443), только SSH-ключи, <strong>BBR</strong> для ускорения',
    'step.4.title': 'Настраивает VLESS+Reality',
    'step.4.text': ' на порту 443 — прокси, имитирующий настоящий TLS-сервер',
    'step.5.title': 'Включает XHTTP',
    'step.5.text': ' — дополнительная скрытность через nginx, без лишних портов',
    'step.6.title': 'Выводит QR-коды',
    'step.6.text': ' и создаёт HTML-страницу с инструкциями для подключения',

    // Technology
    'tech.label': 'Технология',
    'tech.title': 'Почему цензоры не могут обнаружить',
    'tech.desc': 'Традиционные VPN имеют характерные сигнатуры трафика. VLESS+Reality неотличим от обычного веб-серфинга.',
    'tech.dpi': 'Глубокая инспекция пакетов (DPI)',
    'tech.probe': 'Активное зондирование',
    'tech.fp': 'TLS-фингерпринтинг',

    // Connect
    'connect.label': 'Подключение',
    'connect.title': 'Сканируй и подключайся',
    'connect.desc': 'После развёртывания вы получаете страницу с QR-кодами. Отправьте её кому нужно — одно нажатие и подключение готово.',
    'connect.clock': 'Часы устройства должны быть точными с отклонением не более 30 секунд. Включите автоматическую установку даты/времени.',

    // Architecture
    'arch.label': 'Архитектура',
    'arch.title': 'Под капотом',
    'arch.standalone': 'Автономный режим (без домена)',
    'arch.domain': 'Доменный режим (CDN-фолбэк)',
    'arch.relay': 'Режим реле (внутренняя точка входа)',

    // Reference
    // (removed — content moved to docs pages)

    // Trust bar
    'trust.mit': 'MIT Лицензия',
    'trust.languages': '4 Языка',
    'trust.oss': '100% Открытый код',

    // CTA
    'cta.title': 'Готовы начать?',
    'cta.desc': 'Установите Meridian, разверните на вашем сервере и делитесь доступом с теми, кому он нужен. Весь процесс занимает около пяти минут.',
    'cta.install': 'Установить Meridian',

    // Command builder
    'builder.label': 'Конструктор команд',
    'builder.title': 'Соберите команду',
    'builder.desc': 'Настройте флаги интерактивно и скопируйте полную команду. Поддерживает все операции CLI.',
    'builder.tab.deploy': 'Развёртывание',
    'builder.tab.preflight': 'Проверка',
    'builder.tab.scan': 'Поиск SNI',
    'builder.tab.doctor': 'Диагностика',
    'builder.tab.relay': 'Ретранслятор',
    'builder.tab.teardown': 'Удаление',
    'builder.desc.deploy': 'Развёртывание VLESS+Reality. Настраивает Docker, Xray, фаервол и TLS автоматически.',
    'builder.desc.relay': 'Развёртывание TCP-ретранслятора — перенаправляет трафик через внутренний сервер к вашему выходному серверу.',
    'builder.desc.preflight': 'Проверка совместимости сервера — SNI, порты, DNS, ОС, диск, ASN.',
    'builder.desc.scan': 'Поиск оптимальных целей SNI в сети сервера через RealiTLScanner.',
    'builder.desc.doctor': 'Сбор диагностики для отчётов об ошибках. Секреты автоматически скрываются.',
    'builder.desc.teardown': 'Удаление контейнера, конфигов и правил фаервола. Docker и системные пакеты сохраняются.',
    'builder.field.ip': 'IP сервера',
    'builder.field.ip.hint': 'Публичный IPv4 адрес вашего VPS или "local" для развертывания на этом сервере. Оставьте пустым для интерактивного режима.',
    'builder.field.user': 'SSH пользователь',
    'builder.field.user.hint': 'По умолчанию: root. Не-root получает sudo автоматически.',
    'builder.field.domain': 'Домен',
    'builder.field.domain.hint': 'Опционально. Добавляет CDN-фолбэк через Cloudflare и веб-панель.',
    'builder.field.sni': 'Свой SNI',
    'builder.field.sni.hint': 'Сайт, который Reality имитирует. По умолчанию: www.microsoft.com. Используйте meridian scan для оптимальных целей.',
    'builder.field.name': 'Имя клиента',
    'builder.field.name.hint': 'Имя первого клиента. По умолчанию: "default".',
    'builder.field.icon': 'Значок',
    'builder.field.icon.hint': 'Эмодзи или URL изображения для страницы подключения.',
    'builder.field.color': 'Цвет',
    'builder.field.color.hint': 'Цветовая палитра страницы подключения.',
    'builder.field.exit': 'Выходной сервер',
    'builder.field.exit.hint': 'IP или имя выходного сервера, к которому подключается ретранслятор. Должен быть развёрнут первым.',
    'builder.field.relay-name': 'Имя ретранслятора',
    'builder.field.relay-name.hint': 'Необязательное имя (напр. "ru-moscow").',
    'builder.field.port': 'Порт',
    'builder.field.port.hint': 'Порт на сервере ретранслятора. По умолчанию: 443.',
    'builder.field.ai': 'AI-диагностика',
    'builder.field.ai.hint': 'Копирует AI-промпт для ChatGPT/Claude в буфер обмена.',
    'builder.field.yes': 'Без подтверждений',
    'builder.field.yes.hint': 'Флаг --yes. Для CI/автоматизации или неинтерактивных SSH-сессий.',
    'builder.wizard': '<strong>Впервые?</strong> Просто запустите <code>meridian deploy</code> — интерактивный мастер проведёт вас через все шаги.',

    // Footer
    'footer.tagline': 'Открытый код. Без трекинга. Без аккаунтов.',
  },

  fa: {
    'page.title': 'Meridian — پراکسی ضد سانسور، راه‌اندازی بی‌نقص',

    'nav.docs': 'مستندات',
    'nav.blog': 'بلاگ',
    'nav.demo': 'نمونه',
    'nav.ping': 'پینگ',
    'toc.label': 'در این صفحه',
    'docs.viewMd': 'مشاهده Markdown',
    'docs.search': 'جستجوی مستندات...',

    'hero.kicker': 'ابزار آزادی اینترنت',
    'hero.title': 'اینترنت <em>امن</em> را با عزیزانتان به&nbsp;اشتراک&nbsp;بگذارید',
    'hero.sub': 'یک دستور یک سرور پراکسی غیرقابل شناسایی راه‌اندازی می‌کند — فایروال، TLS، مسیریابی، همه به‌صورت پیش‌فرض امن. دسترسی را از طریق کدهای QR به اشتراک بگذارید.',
    'hero.cta': 'شروع',
    'hero.docs': 'مستندات',
    'hero.caption': 'آنچه دوستان شما دریافت می‌کنند — <a href="/demo/">نمونه را ببینید</a>',

    'setup.label': 'نحوه کار',
    'setup.title': 'سه مرحله تا اینترنت امن',
    'setup.desc': 'یک VPS (هر سروری با ۵ دلار/ماه) و یک ترمینال روی لپ‌تاپ خود نیاز دارید. Meridian بقیه کارها را انجام می‌دهد.',
    'setup.step1.label': 'مرحله ۱ — نصب',
    'setup.step2.label': 'مرحله ۲ — راه‌اندازی',
    'setup.then': 'سپس',
    'setup.hint': 'ویزارد IP سرور را می‌پرسد و بقیه را انجام می‌دهد. یا فلگ‌ها را مستقیم وارد کنید — <a href="/docs/fa/cli-reference/">مرجع CLI</a> را ببینید.',
    'setup.details': 'آنچه پشت صحنه اتفاق می‌افتد',
    'setup.idempotent': 'می‌توان هر زمان دوباره اجرا کرد. کاملاً idempotent — اعتبارنامه‌ها حفظ و تنظیمات در جا به‌روز می‌شوند.',

    'step.1.title': 'نصب Docker',
    'step.1.text': ' و استقرار <strong>Xray</strong> از طریق پنل <strong>Remnawave</strong>',
    'step.2.title': 'تولید کلید x25519',
    'step.2.text': ' — کلیدهای رمزنگاری برای احراز هویت Reality',
    'step.3.title': 'امن‌سازی سرور',
    'step.3.text': ' — فایروال UFW (پورت‌های ۲۲ + ۴۴۳)، فقط کلید SSH، <strong>BBR</strong>',
    'step.4.title': 'پیکربندی VLESS+Reality',
    'step.4.text': ' روی پورت ۴۴۳ — پراکسی جعل‌کننده سرور TLS واقعی',
    'step.5.title': 'فعال‌سازی XHTTP',
    'step.5.text': ' — پنهان‌کاری بیشتر از طریق nginx، بدون پورت اضافی',
    'step.6.title': 'خروجی QR',
    'step.6.text': ' و ذخیره صفحه HTML با لینک‌ها و دستورالعمل‌ها',

    'tech.label': 'فناوری',
    'tech.title': 'چرا سانسورچی‌ها نمی‌توانند شناسایی کنند',
    'tech.desc': 'VPN‌های سنتی امضای ترافیک متمایزی دارند. VLESS+Reality از مرور عادی وب قابل تشخیص نیست.',
    'tech.dpi': 'بازرسی عمیق بسته (DPI)',
    'tech.probe': 'کاوش فعال',
    'tech.fp': 'اثرانگشت TLS',

    'connect.label': 'اتصال',
    'connect.title': 'اسکن کن و وصل شو',
    'connect.desc': 'پس از راه‌اندازی، صفحه‌ای با کدهای QR دریافت می‌کنید. به هر کسی که نیاز دارد بفرستید — یک ضربه و متصل می‌شوند.',
    'connect.clock': 'ساعت دستگاه باید با دقت ۳۰ ثانیه تنظیم باشد. تاریخ/ساعت خودکار را فعال کنید.',

    'arch.label': 'معماری',
    'arch.title': 'زیر کاپوت',
    'arch.standalone': 'حالت مستقل (بدون دامنه)',
    'arch.domain': 'حالت دامنه (CDN)',
    'arch.relay': 'حالت رله (نقطه ورود داخلی)',

    // Reference
    // (removed — content moved to docs pages)

    // Trust bar
    'trust.mit': 'MIT مجاز',
    'trust.languages': '۴ زبان',
    'trust.oss': '۱۰۰٪ متن‌باز',

    // CTA
    'cta.title': 'آماده برای شروع؟',
    'cta.desc': 'Meridian را نصب کنید، روی سرور خود راه‌اندازی کنید و دسترسی را با کسانی که نیاز دارند به اشتراک بگذارید. کل فرآیند حدود پنج دقیقه طول می‌کشد.',
    'cta.install': 'نصب Meridian',

    // Command builder
    'builder.label': 'سازنده دستور',
    'builder.title': 'دستور خود را بسازید',
    'builder.desc': 'فلگ‌ها را به صورت تعاملی تنظیم کنید و دستور کامل را کپی کنید.',
    'builder.tab.deploy': 'راه‌اندازی',
    'builder.tab.preflight': 'بررسی',
    'builder.tab.scan': 'اسکن SNI',
    'builder.tab.doctor': 'تشخیص',
    'builder.tab.relay': 'رله',
    'builder.tab.teardown': 'حذف',
    'builder.desc.deploy': 'راه‌اندازی VLESS+Reality. Docker، Xray، فایروال و TLS را خودکار پیکربندی می‌کند.',
    'builder.desc.relay': 'راه‌اندازی رله TCP — ترافیک را از طریق سرور داخلی به سرور خروجی هدایت می‌کند.',
    'builder.desc.preflight': 'بررسی سازگاری سرور — SNI، پورت‌ها، DNS، سیستم‌عامل.',
    'builder.desc.scan': 'یافتن اهداف SNI بهینه در شبکه سرور.',
    'builder.desc.doctor': 'جمع‌آوری اطلاعات تشخیصی. اطلاعات حساس خودکار حذف می‌شوند.',
    'builder.desc.teardown': 'حذف کانتینر پراکسی و تنظیمات. Docker و بسته‌های سیستم حفظ می‌شوند.',
    'builder.field.ip': 'IP سرور',
    'builder.field.ip.hint': 'آدرس IPv4 عمومی VPS یا "local" برای استقرار روی این سرور. برای حالت تعاملی خالی بگذارید.',
    'builder.field.user': 'کاربر SSH',
    'builder.field.user.hint': 'پیش‌فرض: root. کاربران غیر root خودکار sudo می‌گیرند.',
    'builder.field.domain': 'دامنه',
    'builder.field.domain.hint': 'اختیاری. CDN از طریق Cloudflare اضافه می‌کند.',
    'builder.field.sni': 'SNI سفارشی',
    'builder.field.sni.hint': 'سایتی که Reality جعل می‌کند. پیش‌فرض: www.microsoft.com.',
    'builder.field.name': 'نام کلاینت',
    'builder.field.name.hint': 'نام اولین کلاینت. پیش‌فرض: "default".',
    'builder.field.icon': 'آیکون',
    'builder.field.icon.hint': 'اموجی یا URL تصویر برای صفحه اتصال.',
    'builder.field.color': 'رنگ',
    'builder.field.color.hint': 'پالت رنگی صفحه اتصال.',
    'builder.field.exit': 'سرور خروجی',
    'builder.field.exit.hint': 'IP یا نام سرور خروجی. باید قبلاً راه‌اندازی شده باشد.',
    'builder.field.relay-name': 'نام رله',
    'builder.field.relay-name.hint': 'نام اختیاری (مثلاً "ir-tehran").',
    'builder.field.port': 'پورت',
    'builder.field.port.hint': 'پورت روی سرور رله. پیش‌فرض: ۴۴۳.',
    'builder.field.ai': 'خروجی AI',
    'builder.field.ai.hint': 'پرامپت AI آماده را برای ChatGPT/Claude کپی می‌کند.',
    'builder.field.yes': 'بدون تأیید',
    'builder.field.yes.hint': 'فلگ --yes. برای CI یا جلسات SSH غیرتعاملی.',
    'builder.wizard': '<strong>اولین بار؟</strong> فقط <code>meridian deploy</code> را اجرا کنید — ویزارد تعاملی شما را قدم به قدم راهنمایی می‌کند.',

    'footer.tagline': 'متن‌باز. بدون ردیابی. بدون حساب کاربری.',
  },

  zh: {
    'page.title': 'Meridian — 抗审查代理，一键正确部署',

    'nav.docs': '文档',
    'nav.blog': '博客',
    'nav.demo': '演示',
    'nav.ping': '测试',
    'toc.label': '本页目录',
    'docs.viewMd': '查看 Markdown',
    'docs.search': '搜索文档...',

    'hero.kicker': '开源互联网自由工具',
    'hero.title': '与关心的人分享<em>安全的互联网</em>',
    'hero.sub': '一条命令部署不可检测的代理服务器——防火墙、TLS、路由，全部默认加固。通过二维码分享访问权限。',
    'hero.cta': '开始使用',
    'hero.docs': '阅读文档',
    'hero.caption': '你的朋友收到的页面 — <a href="/demo/">查看演示</a>',

    'setup.label': '工作原理',
    'setup.title': '三步获得安全互联网',
    'setup.desc': '你需要一台 VPS（每月 $5 的服务器）和笔记本上的终端。Meridian 处理其余一切。',
    'setup.step1.label': '步骤 1 — 安装',
    'setup.step2.label': '步骤 2 — 部署',
    'setup.then': '然后',
    'setup.hint': '向导会询问服务器 IP 并处理其余事项。或直接传递参数 — 查看 <a href="/docs/zh/cli-reference/">CLI 参考</a>。',
    'setup.details': '幕后发生了什么',
    'setup.idempotent': '可随时安全重新运行。完全幂等——凭证保留，设置原地更新。',

    'step.1.title': '安装 Docker',
    'step.1.text': '并通过 <strong>Remnawave</strong> 面板部署 <strong>Xray</strong>',
    'step.2.title': '生成 x25519 密钥对',
    'step.2.text': '——用于 Reality 认证的唯一加密密钥',
    'step.3.title': '加固服务器',
    'step.3.text': '——UFW 防火墙（仅 22 + 443 端口）、仅密钥认证、<strong>BBR</strong>',
    'step.4.title': '配置 VLESS+Reality',
    'step.4.text': '在 443 端口——伪装真实 TLS 服务器的代理',
    'step.5.title': '启用 XHTTP',
    'step.5.text': '——通过 nginx 的额外隐蔽性，无需额外端口',
    'step.6.title': '输出二维码',
    'step.6.text': '并生成包含连接说明的 HTML 页面',

    'tech.label': '技术',
    'tech.title': '为什么审查者无法检测',
    'tech.desc': '传统 VPN 具有独特的流量特征。VLESS+Reality 与正常网页浏览无法区分。',
    'tech.dpi': '深度包检测 (DPI)',
    'tech.probe': '主动探测',
    'tech.fp': 'TLS 指纹识别',

    'connect.label': '连接',
    'connect.title': '扫描即连',
    'connect.desc': '部署后，你会得到一个带二维码的连接页面。发送给需要的人——一键连接。',
    'connect.clock': '设备时钟必须精确到 30 秒以内。请启用自动日期/时间。',

    'arch.label': '架构',
    'arch.title': '工作原理',
    'arch.standalone': '独立模式（无域名）',
    'arch.domain': '域名模式（CDN 回退）',
    'arch.relay': '中继模式（境内入口）',

    // Reference
    // (removed — content moved to docs pages)

    // Trust bar
    'trust.mit': 'MIT 许可证',
    'trust.languages': '4 种语言',
    'trust.oss': '100% 开源',

    // CTA
    'cta.title': '准备好开始了吗？',
    'cta.desc': '安装 Meridian，部署到你的服务器，并与需要的人分享访问权限。整个过程大约需要五分钟。',
    'cta.install': '安装 Meridian',

    // Command builder
    'builder.label': '命令生成器',
    'builder.title': '构建你的命令',
    'builder.desc': '交互式配置参数并复制完整命令。支持所有 Meridian CLI 操作。',
    'builder.tab.deploy': '部署',
    'builder.tab.preflight': '预检',
    'builder.tab.scan': 'SNI 扫描',
    'builder.tab.doctor': '诊断',
    'builder.tab.relay': '中继',
    'builder.tab.teardown': '卸载',
    'builder.desc.deploy': '部署 VLESS+Reality 代理。自动配置 Docker、Xray、防火墙和 TLS。',
    'builder.desc.relay': '部署 TCP 中继——通过国内服务器将流量转发到国外出口服务器。',
    'builder.desc.preflight': '测试服务器兼容性——检查 SNI、端口、DNS、系统。',
    'builder.desc.scan': '在服务器网络中寻找最佳 SNI 目标。',
    'builder.desc.doctor': '收集系统诊断信息。敏感信息自动脱敏。',
    'builder.desc.teardown': '删除代理容器、配置和防火墙规则。Docker 和系统包保留。',
    'builder.field.ip': '服务器 IP',
    'builder.field.ip.hint': 'VPS 的公共 IPv4 地址，或输入 "local" 在此服务器上部署。留空使用交互模式。',
    'builder.field.user': 'SSH 用户',
    'builder.field.user.hint': '默认：root。非 root 用户自动获得 sudo。',
    'builder.field.domain': '域名',
    'builder.field.domain.hint': '可选。通过 Cloudflare 添加 CDN 回退。',
    'builder.field.sni': '自定义 SNI',
    'builder.field.sni.hint': 'Reality 伪装的网站。默认：www.microsoft.com。使用 meridian scan 寻找最佳目标。',
    'builder.field.name': '客户端名称',
    'builder.field.name.hint': '第一个客户端的名称。默认："default"。',
    'builder.field.icon': '图标',
    'builder.field.icon.hint': '连接页面的表情符号或图片 URL。',
    'builder.field.color': '配色',
    'builder.field.color.hint': '连接页面的颜色方案。',
    'builder.field.exit': '出口服务器',
    'builder.field.exit.hint': '中继转发到的出口服务器 IP 或名称。必须先部署。',
    'builder.field.relay-name': '中继名称',
    'builder.field.relay-name.hint': '可选的友好名称（如 "cn-shanghai"）。',
    'builder.field.port': '端口',
    'builder.field.port.hint': '中继服务器的监听端口。默认：443。',
    'builder.field.ai': 'AI 诊断',
    'builder.field.ai.hint': '将 AI 提示复制到剪贴板，用于 ChatGPT/Claude 排障。',
    'builder.field.yes': '跳过确认',
    'builder.field.yes.hint': '添加 --yes 标志。用于 CI/自动化或非交互式 SSH 会话。',
    'builder.wizard': '<strong>第一次使用？</strong>直接运行 <code>meridian deploy</code> — 交互式向导会引导你完成每一步。',

    'footer.tagline': '开源。无追踪。无账户。',
  },
};
