/* PWA — Connection Setup */
(function() {
'use strict';

/* -----------------------------------------------------------------------
 * i18n translations (English is the markup default)
 * ----------------------------------------------------------------------- */
var T = {
  en: {
    'conns.show': 'Show all {n}',
    'conns.less': 'Show fewer',
    'install.other': 'Other install options',
    'install.appstore': 'App Store',
    'install.play': 'Google Play',
    'install.apk': 'Official APK',
    'install.exe': 'Windows installer',
    'install.dmg': 'macOS installer',
    'install.deb': 'DEB package',
    'install.rpm': 'RPM package',
    'install.appimage': 'AppImage',
    'install.releases': 'Release downloads',
    'arch.x64': 'x64 (Intel / AMD)',
    'arch.arm64': 'ARM64',
    'arch.universal': 'Universal',
    'arch.apple': 'Apple Silicon',
    'arch.intel': 'Intel',
    'store.ru': 'Russia',
    'platform.choice': 'Platform',
    'sub.label': 'Subscription URL',
    'app.choice': 'App',
    'platform.ios': 'iOS',
    'platform.android': 'Android',
    'platform.windows': 'Windows',
    'platform.macos': 'macOS',
    'platform.linux': 'Linux',
    'manual.import': 'Import manually',
    'manual.desc': 'Copy the URL, then use this menu in {name}:',
    'manual.generic': 'Add a subscription or remote profile from a URL.',
    'manual.copy': 'Copy URL',
    'step.connect': 'Connect',
    'connect.permission': 'Allow VPN permission if your device asks.',
    'connect.profile': 'Select the imported profile, then choose a server.',
    'connect.start': 'Tap the connect or power button.',
    'apps.none': 'No apps for this platform. Choose another platform above.',
    'copy.error': 'Could not copy automatically. Select the text below and copy it.',
    'copy.text': 'Text to copy',
    'apps.desktop': 'Desktop',
    'apps.android.desktop': 'Android, desktop',
    'apps.ios.android': 'iOS, Android',
    'apps.ios.android.macos': 'iOS, Android, macOS',
    language: 'Language', close: 'Close', copy: 'Copy', 'qr.code': 'QR code',
  },
  ru: {
    'conns.show': 'Показать все ({n})',
    'conns.less': 'Показать меньше',
    'install.other': 'Другие способы установки',
    'install.appstore': 'App Store',
    'install.play': 'Google Play',
    'install.apk': 'Официальный APK',
    'install.exe': 'Установщик для Windows',
    'install.dmg': 'Установщик для macOS',
    'install.deb': 'Пакет DEB',
    'install.rpm': 'Пакет RPM',
    'install.appimage': 'AppImage',
    'install.releases': 'Выпуски приложения',
    'arch.x64': 'x64 (Intel / AMD)',
    'arch.arm64': 'ARM64',
    'arch.universal': 'Универсальный',
    'arch.apple': 'Apple Silicon',
    'arch.intel': 'Intel',
    'store.ru': 'Россия',
    'platform.choice': 'Платформа',
    'app.choice': 'Приложение',
    'platform.ios': 'iOS',
    'platform.android': 'Android',
    'platform.windows': 'Windows',
    'platform.macos': 'macOS',
    'platform.linux': 'Linux',
    'manual.import': 'Добавить вручную',
    'manual.desc': 'Скопируйте URL, затем откройте это меню в {name}:',
    'manual.generic': 'Добавьте подписку или удалённый профиль по URL.',
    'manual.copy': 'Скопировать URL',
    'step.connect': 'Подключитесь',
    'connect.permission': 'Если устройство запросит разрешение на VPN, разрешите.',
    'connect.profile': 'Выберите добавленный профиль, затем сервер.',
    'connect.start': 'Нажмите кнопку подключения или включения.',
    'apps.none': 'Для этой платформы нет приложений. Выберите другую платформу выше.',
    'copy.error': 'Не удалось скопировать автоматически. Выделите текст ниже и скопируйте его.',
    'copy.text': 'Текст для копирования',
    'apps.desktop': 'Для компьютера',
    'apps.android.desktop': 'Android и компьютеры',
    'apps.ios.android': 'iOS и Android',
    'apps.ios.android.macos': 'iOS, Android и macOS',
    language: 'Язык', close: 'Закрыть', copy: 'Скопировать', 'qr.code': 'QR-код',
    'primary.rec': 'Рекомендовано',
    clock: 'Синхронизация часов',
    'clock.desc': 'Часы устройства отстают. При неверных часах подключение может не работать. Откройте Настройки > Дата и время > включите «Автоматически».',
    ping: 'Не подключается?',
    'ping.desc': 'Проверьте автоматическую синхронизацию времени и попробуйте другой вариант подключения выше.',
    'ping.link': 'Попросите владельца сервера запустить meridian test.',
    'install.title': 'Установите приложение',
    'install.btn': 'Установить',
    'sub.label': 'Подписка (автообновление)',
    'sub.desc': 'Добавьте этот URL как подписку в приложении для автоматических обновлений.',
    'import.add': 'Добавить в {name}',
    'import.manual': 'Скопировать URL подписки',
    'install.offline': 'Доступ к подключению офлайн',
    'apps.all': 'Все платформы',
    'apps.more': 'Другие платформы',
    'apps.other': 'Другие приложения',
    'apps.update': 'Обновляйте приложение',
    'apps.update.desc': 'В новых версиях исправляют проблемы с подключением и учитывают новые блокировки. Включите автообновление в магазине приложений или время от времени заглядывайте на страницу загрузки.',
    'sub.refresh': 'Приложение само обновляет подписку. Чтобы обновить её сейчас, откройте в приложении список подписок (или профилей) и нажмите «Обновить».',
    'tip.webrtc': 'Если приложение работает только как прокси, сайты в браузере иногда могут узнать ваш настоящий IP-адрес (утечка WebRTC). Чтобы этого избежать, включите в приложении режим VPN (TUN): тогда весь трафик идёт через подключение. Проверить можно любым сервисом проверки утечек: адрес должен отличаться от вашего обычного.',
    router: 'Роутер',
    'router.desc': 'Подключите всю домашнюю сеть: инструкция по настройке',
    selfcheck: 'Проверить подключение',
    'selfcheck.desc': 'Три быстрые проверки с этого устройства и из этой сети: часы, передача данных и подписка. Сначала выключите VPN, чтобы проверка видела вашу настоящую сеть.',
    'selfcheck.run': 'Запустить проверку',
    'show.qr': 'Показать QR-код',
    'share.page': 'Поделиться этой страницей',
    copied: 'Скопировано',
    'page_title': 'Настройка подключения',
    'error.title': 'Настройка подключения',
    'error.msg': 'Не удалось загрузить конфигурацию. Попробуйте перезагрузить страницу.',
    'error.retry': 'Повторить',
    'error.slow': 'Загрузка занимает больше времени, чем обычно\u2026',
    'hero.title': 'Ваше подключение',
    'hero.for': 'Для кого: {name}',
    'hero.sub': 'Три шага, около минуты.',
    'step.install': 'Установите приложение',
    'app.get': 'Скачать',
    'step.add': 'Добавьте подключение',
    'step.add.desc': 'Одно нажатие добавляет все серверы, а приложение само следит за обновлениями.',
    'import.more': 'Другое приложение?',
    'qr.title': 'Настраиваете телефон?',
    'qr.desc': 'В приложении на телефоне добавьте подписку по QR-коду и отсканируйте этот.',
    conns: 'Все подключения',
    'conns.desc': 'Они уже в вашем приложении. Используйте отдельное подключение, только если это нужно.',
    'proto.reality': 'Самое быстрое — пробуйте первым',
    'proto.xhttp': 'Труднее обнаружить',
    'proto.hysteria2': 'UDP — для медленных или нестабильных сетей',
    'proto.wss': 'Через CDN — на крайний случай',
    'open.short': 'Открыть',
  },
  fa: {
    'conns.show': 'نمایش همهٔ {n} اتصال',
    'conns.less': 'نمایش کمتر',
    'install.other': 'روش‌های دیگر نصب',
    'install.appstore': 'اپ استور',
    'install.play': 'گوگل پلی',
    'install.apk': 'APK رسمی',
    'install.exe': 'نصب‌کنندهٔ ویندوز',
    'install.dmg': 'نصب‌کنندهٔ macOS',
    'install.deb': 'بستهٔ DEB',
    'install.rpm': 'بستهٔ RPM',
    'install.appimage': 'AppImage',
    'install.releases': 'نسخه‌های منتشرشده',
    'arch.x64': 'x64 (اینتل / AMD)',
    'arch.arm64': 'ARM64',
    'arch.universal': 'همهٔ معماری‌ها',
    'arch.apple': 'اپل سیلیکون',
    'arch.intel': 'اینتل',
    'store.ru': 'روسیه',
    'platform.choice': 'پلتفرم',
    'app.choice': 'برنامه',
    'platform.ios': 'iOS',
    'platform.android': 'اندروید',
    'platform.windows': 'ویندوز',
    'platform.macos': 'macOS',
    'platform.linux': 'لینوکس',
    'manual.import': 'افزودن دستی',
    'manual.desc': 'نشانی را کپی کنید، سپس این منو را در {name} باز کنید:',
    'manual.generic': 'اشتراک یا پروفایل راه دور را از طریق نشانی اضافه کنید.',
    'manual.copy': 'کپی نشانی',
    'step.connect': 'وصل شوید',
    'connect.permission': 'اگر دستگاه اجازهٔ VPN خواست، آن را تأیید کنید.',
    'connect.profile': 'پروفایل اضافه‌شده و سپس یک سرور را انتخاب کنید.',
    'connect.start': 'دکمهٔ اتصال یا روشن‌کردن را بزنید.',
    'apps.none': 'برای این پلتفرم برنامه‌ای نیست. از بالا پلتفرم دیگری را انتخاب کنید.',
    'copy.error': 'کپی خودکار ممکن نشد. متن زیر را انتخاب و کپی کنید.',
    'copy.text': 'متن برای کپی',
    'apps.desktop': 'رایانه',
    'apps.android.desktop': 'اندروید و رایانه',
    'apps.ios.android': 'iOS و اندروید',
    'apps.ios.android.macos': 'iOS، اندروید و macOS',
    language: 'زبان', close: 'بستن', copy: 'کپی', 'qr.code': 'کد QR',
    'primary.rec': 'پیشنهادی',
    clock: 'همگام\u200Cسازی ساعت',
    'clock.desc': 'ساعت دستگاه عقب است. با ساعت نادرست ممکن است اتصال برقرار نشود. به تنظیمات > تاریخ و ساعت > \u00ABتنظیم خودکار\u00BB بروید.',
    ping: '\u0645\u062A\u0635\u0644 \u0646\u0645\u06CC\u200C\u0634\u0648\u06CC\u062F\u061F',
    'ping.desc': 'همگام‌سازی خودکار ساعت را بررسی کنید و یکی از گزینه‌های اتصال بالا را امتحان کنید.',
    'ping.link': 'از صاحب سرور بخواهید meridian test را اجرا کند.',
    'install.title': 'برنامه را نصب کنید',
    'install.btn': 'نصب',
    'sub.label': 'اشتراک (به‌روزرسانی خودکار)',
    'sub.desc': 'این URL را به عنوان اشتراک در برنامه اضافه کنید.',
    'import.add': 'افزودن به {name}',
    'import.manual': 'کپی URL اشتراک',
    'install.offline': 'دسترسی آفلاین به اتصال',
    'apps.all': 'همهٔ پلتفرم\u200Cها',
    'apps.more': 'پلتفرم\u200Cهای دیگر',
    'apps.other': 'برنامه‌های دیگر',
    'apps.update': 'برنامه را به‌روز نگه دارید',
    'apps.update.desc': 'نسخه‌های جدید مشکلات اتصال را برطرف می‌کنند و با روش‌های تازهٔ مسدودسازی همگام می‌مانند. به‌روزرسانی خودکار را در فروشگاه برنامه روشن کنید یا هر از گاهی به صفحهٔ دانلود سر بزنید.',
    'sub.refresh': 'برنامه اشتراک را خودش به‌روز می‌کند. برای به‌روزرسانی فوری، فهرست اشتراک‌ها (یا پروفایل‌ها) را در برنامه باز کنید و «به‌روزرسانی» را بزنید.',
    'tip.webrtc': 'اگر برنامه فقط به‌صورت پروکسی کار کند، گاهی وب‌سایت‌ها در مرورگر می‌توانند نشانی IP واقعی شما را ببینند (نشت WebRTC). برای جلوگیری از این، حالت VPN (یا TUN) را در برنامه روشن کنید تا همهٔ ترافیک از مسیر اتصال عبور کند. می‌توانید با هر سرویس بررسی نشت، تأیید کنید که نشانی نمایش‌داده‌شده با نشانی همیشگی شما فرق دارد.',
    router: 'روتر',
    'router.desc': 'کل شبکهٔ خانگی را متصل کنید: راهنمای راه‌اندازی',
    selfcheck: 'بررسی اتصال من',
    'selfcheck.desc': 'سه آزمون سریع از همین دستگاه و همین شبکه: ساعت، جریان داده و اشتراک. ابتدا VPN را خاموش کنید تا آزمون شبکهٔ واقعی شما را ببیند.',
    'selfcheck.run': 'شروع بررسی',
    'show.qr': 'نمایش کد QR',
    'share.page': 'اشتراک\u200Cگذاری این صفحه',
    copied: '\u06A9\u067E\u06CC \u0634\u062F',
    'page_title': 'تنظیم اتصال',
    'error.title': 'تنظیم اتصال',
    'error.msg': '\u0628\u0627\u0631\u06AF\u0630\u0627\u0631\u06CC \u067E\u06CC\u06A9\u0631\u0628\u0646\u062F\u06CC \u0627\u0646\u062C\u0627\u0645 \u0646\u0634\u062F. \u0644\u0637\u0641\u0627\u064B \u0635\u0641\u062D\u0647 \u0631\u0627 \u062F\u0648\u0628\u0627\u0631\u0647 \u0628\u0627\u0631\u06AF\u0630\u0627\u0631\u06CC \u06A9\u0646\u06CC\u062F.',
    'error.retry': '\u062A\u0644\u0627\u0634 \u0645\u062C\u062F\u062F',
    'error.slow': '\u0628\u0627\u0631\u06AF\u0630\u0627\u0631\u06CC \u0628\u06CC\u0634 \u0627\u0632 \u062D\u062F \u0645\u0639\u0645\u0648\u0644 \u0637\u0648\u0644 \u0645\u06CC\u200C\u06A9\u0634\u062F\u2026',
    'hero.title': 'اتصال شما',
    'hero.for': 'تنظیم‌شده برای {name}',
    'hero.sub': 'سه مرحله، حدود یک دقیقه.',
    'step.install': 'یک برنامه نصب کنید',
    'app.get': 'دریافت',
    'step.add': 'اتصال خود را اضافه کنید',
    'step.add.desc': 'با یک ضربه همهٔ سرورها اضافه می‌شوند و برنامه آن‌ها را به‌روز نگه می‌دارد.',
    'import.more': 'برنامهٔ دیگری دارید؟',
    'qr.title': 'گوشی را تنظیم می‌کنید؟',
    'qr.desc': 'در برنامهٔ گوشی، افزودن اشتراک با کد QR را بزنید و این کد را اسکن کنید.',
    conns: 'همهٔ اتصال‌ها',
    'conns.desc': 'این‌ها از قبل در برنامهٔ شما هستند. فقط در صورت نیاز از یکی از آن‌ها جداگانه استفاده کنید.',
    'proto.reality': 'سریع‌ترین — اول این را امتحان کنید',
    'proto.xhttp': 'تشخیصش سخت‌تر است',
    'proto.hysteria2': 'UDP — برای شبکه‌های کند یا ناپایدار',
    'proto.wss': 'از طریق CDN — آخرین راه',
    'open.short': 'باز کردن',
  },
  zh: {
    'conns.show': '显示全部 {n} 个连接',
    'conns.less': '收起部分连接',
    'install.other': '其他安装方式',
    'install.appstore': 'App Store',
    'install.play': 'Google Play',
    'install.apk': '官方 APK',
    'install.exe': 'Windows 安装程序',
    'install.dmg': 'macOS 安装程序',
    'install.deb': 'DEB 软件包',
    'install.rpm': 'RPM 软件包',
    'install.appimage': 'AppImage',
    'install.releases': '应用发布页',
    'arch.x64': 'x64（Intel / AMD）',
    'arch.arm64': 'ARM64',
    'arch.universal': '通用版',
    'arch.apple': 'Apple 芯片',
    'arch.intel': 'Intel',
    'store.ru': '俄罗斯',
    'platform.choice': '平台',
    'app.choice': '应用',
    'platform.ios': 'iOS',
    'platform.android': 'Android',
    'platform.windows': 'Windows',
    'platform.macos': 'macOS',
    'platform.linux': 'Linux',
    'manual.import': '手动导入',
    'manual.desc': '复制 URL，然后在 {name} 中打开以下菜单：',
    'manual.generic': '通过 URL 添加订阅或远程配置。',
    'manual.copy': '复制 URL',
    'step.connect': '连接',
    'connect.permission': '如果设备请求 VPN 权限，请允许。',
    'connect.profile': '选择导入的配置，再选择一个服务器。',
    'connect.start': '点击连接或电源按钮。',
    'apps.none': '此平台暂无应用，请在上方选择其他平台。',
    'copy.error': '无法自动复制。请选择下方文本并手动复制。',
    'copy.text': '待复制文本',
    'apps.desktop': '电脑',
    'apps.android.desktop': 'Android 和电脑',
    'apps.ios.android': 'iOS 和 Android',
    'apps.ios.android.macos': 'iOS、Android 和 macOS',
    language: '语言', close: '关闭', copy: '复制', 'qr.code': '二维码',
    'primary.rec': '推荐',
    clock: '时钟同步',
    'clock.desc': '设备时钟慢了。时钟不准可能导致连接失败。前往设置 > 日期与时间 > 启用“自动设置”。',
    ping: '无法连接？',
    'ping.desc': '请确认设备已开启自动校时，然后尝试上面的其他连接方式。',
    'ping.link': '请让服务器所有者运行 meridian test。',
    'install.title': '安装应用',
    'install.btn': '安装',
    'sub.label': '订阅（自动更新）',
    'sub.desc': '将此 URL 作为订阅添加到应用中以自动更新。',
    'import.add': '添加到 {name}',
    'import.manual': '复制订阅链接',
    'install.offline': '离线访问连接',
    'apps.all': '全平台',
    'apps.more': '其他平台',
    'apps.other': '更多应用',
    'apps.update': '保持应用为最新版本',
    'apps.update.desc': '新版本会修复连接问题，并应对新的封锁手段。请在应用商店中开启自动更新，或不时查看下载页面。',
    'sub.refresh': '应用会自动刷新订阅。如需立即刷新，请在应用中打开订阅（或配置）列表并点击“更新”。',
    'tip.webrtc': '如果应用只以代理模式运行，浏览器中的网站有时可能获知您的真实 IP 地址（WebRTC 泄漏）。要避免这种情况，请在应用中开启 VPN（TUN）模式，让所有流量都经过连接。您可以用任意泄漏检测服务确认：显示的地址应与您平时的地址不同。',
    router: '路由器',
    'router.desc': '让整个家庭网络都能连接：设置指南',
    selfcheck: '检查我的连接',
    'selfcheck.desc': '从此设备和网络进行三项快速检查：时钟、数据传输和订阅。请先关闭 VPN，让检查看到您真实的网络环境。',
    'selfcheck.run': '开始检查',
    'show.qr': '显示二维码',
    'share.page': '分享此页面',
    copied: '已复制',
    'page_title': '连接设置',
    'error.title': '连接设置',
    'error.msg': '无法加载配置。请重新加载页面。',
    'error.retry': '重试',
    'error.slow': '加载时间比预期更长\u2026',
    'hero.title': '您的连接',
    'hero.for': '已为 {name} 设置好',
    'hero.sub': '只需三步，大约一分钟。',
    'step.install': '安装应用',
    'app.get': '获取',
    'step.add': '添加您的连接',
    'step.add.desc': '轻点一下即可添加所有服务器，应用会自动保持更新。',
    'import.more': '使用其他应用？',
    'qr.title': '要设置手机吗？',
    'qr.desc': '在手机应用中选择通过二维码添加订阅，然后扫描这个二维码。',
    conns: '所有连接',
    'conns.desc': '它们已经在您的应用里了。只有需要时才单独使用某一个。',
    'proto.reality': '最快 — 请先试这个',
    'proto.xhttp': '更难被发现',
    'proto.hysteria2': 'UDP — 适合缓慢或丢包的网络',
    'proto.wss': '经由 CDN — 最后的选择',
    'open.short': '打开',
  },
};

/* -----------------------------------------------------------------------
 * Platform detection
 * ----------------------------------------------------------------------- */
function detectPlatform() {
  var ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/i.test(ua)) return 'ios';
  if (/Mac/i.test(ua) && navigator.maxTouchPoints > 1) return 'ios';
  if (/Android/i.test(ua)) return 'android';
  if (/Win/i.test(ua)) return 'windows';
  if (/Mac/i.test(ua)) return 'macos';
  return 'linux';
}

/* -----------------------------------------------------------------------
 * App catalog — where each app runs and where the page recommends it
 * ----------------------------------------------------------------------- */
var PLATFORM_KEYS = { ios: 'iOS', android: 'Android', windows: 'Windows', macos: 'macOS', linux: 'Linux' };
var PLATFORM_ORDER = ['android', 'ios', 'windows', 'macos', 'linux'];
var ALL_PLATFORMS = ['iOS', 'Android', 'Windows', 'macOS', 'Linux'];
var selectedPlatform = detectPlatform();
var selectedApp = '';
var showAllConnections = false;
var CONNECTION_PREVIEW = 3;
var ROUTER_DOCS = 'https://getmeridian.org/{lang}/routers/';

function appPlatforms(a) {
  if (a.platforms) return a.platforms;
  /* Older config.json entries carry only a display label */
  var legacy = { 'All platforms': ALL_PLATFORMS, 'Desktop': ['Windows', 'macOS', 'Linux'],
    'Android, desktop': ['Android', 'Windows', 'macOS', 'Linux'],
    'iOS, Android': ['iOS', 'Android'], 'iOS, Android, macOS': ['iOS', 'Android', 'macOS'] };
  return Object.prototype.hasOwnProperty.call(legacy, a.platform) ? legacy[a.platform] : [a.platform];
}

function appUrl(a, key) {
  return (a.urls && a.urls[key]) || a.url;
}

/* Apps that run on `key`: recommended ones in catalog order, then the rest.
   Catalogs without recommendations put every app in `rec`. */
function appsFor(apps, key, needDeeplink) {
  var rec = [], more = [], other = [];
  for (var i = 0; apps && i < apps.length; i++) {
    var a = apps[i];
    if (needDeeplink && !a.deeplink) continue;
    if (appPlatforms(a).indexOf(key) === -1) other.push(a);
    else if (a.recommended && a.recommended.indexOf(key) !== -1) rec.push(a);
    else more.push(a);
  }
  if (!rec.length) { rec = more; more = []; }
  return { rec: rec, more: more, other: other };
}

/* -----------------------------------------------------------------------
 * Deep link construction
 * ----------------------------------------------------------------------- */
var iosFallbackUrl = '';

function tryOpenIOS(index) {
  var el = document.querySelector('[data-ios-idx="' + index + '"]');
  if (!el) return;
  var url = el.getAttribute('data-url');
  if (!url) return;
  var start = Date.now();
  window.location.href = url;
  setTimeout(function() {
    if (iosFallbackUrl && Date.now() - start < 2000) {
      window.location.href = iosFallbackUrl;
    }
  }, 1500);
}

/* -----------------------------------------------------------------------
 * Clipboard + toast
 * ----------------------------------------------------------------------- */
function copyToClipboard(value) {
  function failed() { showCopyFallback(value); return false; }
  if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') return Promise.resolve(failed());
  try {
    return Promise.resolve(navigator.clipboard.writeText(value)).then(function() {
      var old = document.getElementById('copy-fallback');
      if (old) old.remove();
      showToast();
      try { if (navigator.vibrate) navigator.vibrate(30); } catch (e) {}
      return true;
    }, failed);
  } catch (e) { return Promise.resolve(failed()); }
}

var copyFallbackFocus = null;
function showCopyFallback(value) {
  var old = document.getElementById('copy-fallback');
  if (old) old.remove();
  copyFallbackFocus = document.activeElement;
  var box = document.createElement('section');
  box.id = 'copy-fallback'; box.className = 'copy-fallback';
  var message = document.createElement('p');
  message.setAttribute('role', 'alert'); message.setAttribute('data-t', 'copy.error');
  message.textContent = text('copy.error'); box.appendChild(message);
  var field = document.createElement('textarea');
  field.value = value; field.setAttribute('readonly', ''); field.setAttribute('dir', 'ltr');
  field.setAttribute('aria-label', text('copy.text')); field.setAttribute('data-t-aria', 'copy.text');
  field.rows = value.indexOf('\n') !== -1 ? 8 : 3;
  box.appendChild(field);
  var close = document.createElement('button');
  close.className = 'btn btn-ghost btn-sm'; close.setAttribute('data-action', 'dismiss-copy');
  close.setAttribute('data-t', 'close'); close.textContent = text('close'); box.appendChild(close);
  document.body.appendChild(box);
  field.focus(); field.select();
}

function dismissCopyFallback() {
  var box = document.getElementById('copy-fallback');
  if (box) box.remove();
  if (copyFallbackFocus && document.documentElement.contains(copyFallbackFocus)) copyFallbackFocus.focus();
  copyFallbackFocus = null;
}

function showToast() {
  var t = document.getElementById('toast');
  if (!t) return;
  /* Update toast text to current language */
  var span = t.querySelector('[data-t="copied"]');
  if (span) {
    var dict = currentLang && T[currentLang];
    if (dict && dict.copied) span.textContent = dict.copied;
    else span.textContent = 'Copied';
  }
  t.classList.add('show');
  setTimeout(function() { t.classList.remove('show'); }, 1200);
}

/* -----------------------------------------------------------------------
 * Screen Wake Lock
 * ----------------------------------------------------------------------- */
var wakeLock = null;

function requestWakeLock() {
  if (!('wakeLock' in navigator)) return;
  navigator.wakeLock.request('screen').then(function(wl) {
    wakeLock = wl;
    wl.addEventListener('release', function() { wakeLock = null; });
  }).catch(function() {});
}

document.addEventListener('visibilitychange', function() {
  if (wakeLock === null && document.visibilityState === 'visible') {
    requestWakeLock();
  }
});

/* -----------------------------------------------------------------------
 * PWA install prompt
 * ----------------------------------------------------------------------- */
var deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', function(e) {
  e.preventDefault();
  if (window.matchMedia('(display-mode: standalone)').matches || navigator.standalone) return;
  deferredInstallPrompt = e;
  var banner = document.getElementById('install-banner');
  if (banner) banner.classList.add('show');
});

function handleInstallClick() {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  deferredInstallPrompt.userChoice.then(function() {
    deferredInstallPrompt = null;
    var banner = document.getElementById('install-banner');
    if (banner) banner.classList.remove('show');
  });
}

function dismissInstallBanner() {
  deferredInstallPrompt = null;
  var banner = document.getElementById('install-banner');
  if (banner) banner.classList.remove('show');
}

/* Placeholders: {url_raw} as-is, {url_b64} standard Base64, {url} and {name}
   percent-encoded. Function replacements keep `$` in values literal. */
function buildDeepLink(template, subUrl, name) {
  var values = {};
  return template.replace(/\{(url_raw|url_b64|url|name)\}/g, function(m, key) {
    if (Object.prototype.hasOwnProperty.call(values, key)) return values[key];
    if (key === 'url_raw') values[key] = subUrl;
    else if (key === 'url') values[key] = encodeURIComponent(subUrl);
    else if (key === 'name') values[key] = encodeURIComponent(name || 'Meridian');
    /* Handlers require standard Base64 of an ASCII URL, not UTF-16 bytes.
       Keep ASCII subscriptions verbatim; URL serializes Unicode when needed. */
    else values[key] = btoa(/[^\x00-\x7f]/.test(subUrl) ? new URL(subUrl).href : subUrl);
    return values[key];
  });
}

function sharePageUrl() {
  if (navigator.share) {
    navigator.share({ title: '', url: location.href }).catch(function() {});
  } else {
    copyToClipboard(location.href);
  }
}

/* -----------------------------------------------------------------------
 * Clock skew detection
 * ----------------------------------------------------------------------- */
/* Same threshold as selfcheck.js and the CLI (core/defaults.py). A config
   generated in the device's future means the device clock is behind; a clock
   ahead looks like an old config here, so selfcheck.js reads the server's
   Date header for both directions. */
var CLOCK_WARN_MS = 60000;

function checkClockSkew(generatedAt) {
  if (!generatedAt) return 'ok';
  try {
    var serverTime = new Date(generatedAt).getTime();
    var age = Date.now() - serverTime;
    /* If page was generated more than 1h ago, can't reliably detect skew */
    if (Math.abs(age) > 3600000) return 'ok';
    return age < -CLOCK_WARN_MS ? 'bad' : 'ok';
  } catch (e) { return 'ok'; }
}

/* -----------------------------------------------------------------------
 * Validation helpers
 * ----------------------------------------------------------------------- */
var SKIP_NAMES = ['default', 'demo', 'test', 'client'];

function isPersonalName(name) {
  return name && SKIP_NAMES.indexOf(name.toLowerCase()) === -1;
}

/* Defense-in-depth for QR data injected into <img src> */
var B64_RE = /^[A-Za-z0-9+/=]+$/;
function isValidBase64(s) { return s && B64_RE.test(s); }

function object(value) { return !!value && typeof value === 'object' && !Array.isArray(value); }
function string(value, max, empty) { return typeof value === 'string' && (empty || value.length > 0) && value.length <= max; }
function webUrl(value, httpsOnly) {
  if (!string(value, 16384) || /[\u0000-\u001f\u007f]/.test(value)) return false;
  try {
    var url = new URL(value);
    return !url.username && !url.password && (url.protocol === 'https:' || (!httpsOnly && url.protocol === 'http:'));
  } catch (e) { return false; }
}
function imageSource(value) {
  if (!string(value, 2 * 1024 * 1024)) return false;
  if (/^data:image\/(png|jpeg|webp|gif|svg\+xml);base64,[A-Za-z0-9+/]+={0,2}$/.test(value)) return true;
  try {
    var url = new URL(value, location.href);
    return url.origin === location.origin && !url.username && !url.password && !url.search && !url.hash &&
      /\/pwa\/[a-z0-9-]+\.(svg|webp|png)$/.test(url.pathname);
  } catch (e) { return false; }
}
function platformList(list) {
  return Array.isArray(list) && list.length > 0 && list.length <= 5 && list.every(function(key, i) {
    return ALL_PLATFORMS.indexOf(key) !== -1 && list.indexOf(key) === i;
  });
}
function importTemplate(value) {
  if (!string(value, 512) || /[\u0000-\u001f\u007f]/.test(value)) return false;
  var schemes = ['happ', 'flclashx', 'clash', 'throne', 'sub', 'streisand', 'v2raytun', 'v2rayng',
    'sn', 'flclash', 'sing-box', 'hiddify', 'karing', 'v2box', 'stash'];
  var scheme = /^([a-z][a-z0-9+.-]*):\/\//.exec(value);
  return !!scheme && schemes.indexOf(scheme[1]) !== -1 && /\{url(?:_raw|_b64)?\}/.test(value) &&
    !/[{}]/.test(value.replace(/\{(?:url_raw|url_b64|url|name)\}/g, ''));
}
function localizedRecord(value, steps) {
  if (!object(value)) return false;
  return ['en', 'ru', 'fa', 'zh'].every(function(lang) {
    var entry = value[lang];
    return steps ? Array.isArray(entry) && entry.length >= 3 && entry.length <= 8 &&
      entry.every(function(line) { return string(line, 360); }) : string(entry, 360);
  });
}
function validInstallers(routes, platforms) {
  return Array.isArray(routes) && routes.length <= 24 && routes.every(function(route) {
    return object(route) && platforms.indexOf(route.platform) !== -1 && webUrl(route.url, true) &&
      ['appstore', 'play', 'apk', 'exe', 'dmg', 'deb', 'rpm', 'appimage', 'releases'].indexOf(route.kind) !== -1 &&
      (route.arch === undefined || ['x64', 'arm64', 'universal'].indexOf(route.arch) !== -1) &&
      (route.region === undefined || route.region === 'ru');
  });
}
function validApps(apps) {
  if (!Array.isArray(apps) || !apps.length || apps.length > 64) return false;
  var names = [];
  return apps.every(function(app) {
    if (!object(app) || !string(app.name, 80) || names.indexOf(app.name) !== -1 || !string(app.platform, 80) || !webUrl(app.url, true)) return false;
    names.push(app.name);
    var platforms = appPlatforms(app);
    if (!platformList(platforms)) return false;
    if (app.recommended !== undefined && (!platformList(app.recommended) || !app.recommended.every(function(key) { return platforms.indexOf(key) !== -1; }))) return false;
    if (app.urls !== undefined && (!object(app.urls) || !Object.keys(app.urls).every(function(key) {
      return platforms.indexOf(key) !== -1 && webUrl(app.urls[key], true);
    }))) return false;
    if (app.deeplink !== undefined && !importTemplate(app.deeplink)) return false;
    if (app.manual !== undefined && !localizedRecord(app.manual, false)) return false;
    if (app.connect !== undefined && !localizedRecord(app.connect, true)) return false;
    if (app.tun !== undefined && !localizedRecord(app.tun, false)) return false;
    if (app.installers !== undefined && !validInstallers(app.installers, platforms)) return false;
    return app.icon === undefined || imageSource(app.icon);
  });
}
function validCatalog(value) { return object(value) && value.version === 1 && validApps(value.apps); }
function validConfig(config) {
  if (!object(config) || config.version !== 1 || !Array.isArray(config.protocols) || config.protocols.length > 2048 || !validApps(config.apps)) return false;
  var labels = ['client_name', 'server_name', 'server_ip', 'domain'];
  if (!labels.every(function(key) { return config[key] === undefined || string(config[key], 256, true); })) return false;
  if (config.generated_at !== undefined && (!string(config.generated_at, 40) || !isFinite(Date.parse(config.generated_at)))) return false;
  if (config.subscription_url !== undefined && config.subscription_url !== '' && !webUrl(config.subscription_url, false)) return false;
  if (config.server_icon !== undefined && (!string(config.server_icon, 2 * 1024 * 1024, true) ||
      (config.server_icon.indexOf('data:') === 0 ? !imageSource(config.server_icon) : config.server_icon.length > 64))) return false;
  if (config.color !== undefined && ['ocean', 'sunset', 'forest', 'lavender', 'rose', 'slate', ''].indexOf(config.color) === -1) return false;
  if (config.subscription_qr_b64 !== undefined && !string(config.subscription_qr_b64, 1024 * 1024, true)) return false;
  return config.protocols.every(function(proto) {
    return object(proto) && string(proto.key, 40) && string(proto.label, 512, true) && string(proto.url, 16384) &&
      /^(vless|hysteria2|hy2|vmess|ss|trojan):\/\//.test(proto.url) &&
      (proto.qr_b64 === undefined || string(proto.qr_b64, 1024 * 1024, true)) &&
      (proto.recommended === undefined || typeof proto.recommended === 'boolean');
  });
}

var iosButtonIndex = 0;

/* -----------------------------------------------------------------------
 * SVG icons (inline, no external requests)
 * ----------------------------------------------------------------------- */
function svg(body, size) {
  return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
}
var ICON_SHIELD = svg('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>', 26);
var ICON_COPY = svg('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>', 15);
var ICON_QR = svg('<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20v.01M17 20h4v-3"/>', 15);
var ICON_PLUS = svg('<path d="M12 5v14M5 12h14"/>', 16);
var ICON_LIST = svg('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>', 18);
var ICON_HELP = svg('<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>', 18);
var ICON_ROUTER = svg('<rect x="2" y="14" width="20" height="7" rx="2"/><path d="M6 17.5h.01M10 17.5h.01M15 14V9M8.5 8.5a6 6 0 0 1 13 0M11.5 10.5a3 3 0 0 1 7 0"/>', 18);
var ICON_SHARE = svg('<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="m16 6-4-4-4 4M12 2v13"/>', 15);
var ICON_CHEVRON = svg('<path d="m9 18 6-6-6-6"/>', 16);

/* -----------------------------------------------------------------------
 * Color palettes — curated presets with dark + light mode variants
 * ----------------------------------------------------------------------- */
var PALETTES = {
  ocean:    { dark: '#5b9cf5', light: '#2b7de9' },
  sunset:   { dark: '#e57c4e', light: '#c4602a' },
  forest:   { dark: '#4CD68A', light: '#1E8C52' },
  lavender: { dark: '#9b8cf5', light: '#6b5de9' },
  rose:     { dark: '#f56b8a', light: '#d94468' },
  slate:    { dark: '#8B8FA2', light: '#646880' }
};

function applyPalette(paletteName) {
  var p = PALETTES[paletteName];
  if (!p) return;
  var isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var accent = isDark ? p.dark : p.light;
  var root = document.documentElement;
  root.style.setProperty('--accent', accent);
  root.style.setProperty('--accent-bg', accent + (isDark ? '1f' : '14'));
  root.style.setProperty('--accent-br', accent + '38');
}

/* Listen for system theme changes to swap palette variant */
var _activePalette = '';
try {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
    if (_activePalette) applyPalette(_activePalette);
  });
} catch(e) { /* old browsers */ }

/* -----------------------------------------------------------------------
 * Page rendering from config.json
 * ----------------------------------------------------------------------- */
function renderPage(config) {
  var app = document.getElementById('app');
  if (!app) return;
  var platform = selectedPlatform;
  iosButtonIndex = 0;
  var html = '';

  if (config.color && PALETTES[config.color]) {
    _activePalette = config.color;
    applyPalette(config.color);
  }

  html += renderTopBar();

  if (checkClockSkew(config.generated_at) === 'bad') {
    html += '<div class="notice notice-bad">';
    html += '<strong data-t="clock">Clock Sync Required</strong>';
    html += '<span data-t="clock.desc">Your device clock is behind. Connections can fail when the clock is wrong. Go to Settings &gt; Date &amp; Time &gt; enable "Set Automatically".</span>';
    html += '</div>';
  }

  html += renderHero(config);
  html += '<section class="card account" id="account-panel" aria-live="polite" hidden></section>';

  html += '<div class="install-banner" id="install-banner">';
  html += '<div class="install-banner-text"><strong data-t="install.title">Install this app</strong><span data-t="install.offline">Access your connection offline</span></div>';
  html += '<button class="btn btn-primary btn-sm" data-action="install" data-t="install.btn">Install</button>';
  html += '<button class="install-dismiss" data-action="dismiss" data-t-aria="close" aria-label="Close">&times;</button>';
  html += '</div>';

  /* iOS "Open" falls back to the first recommended iOS app */
  var iosApps = appsFor(config.apps, 'iOS', false).rec;
  iosFallbackUrl = iosApps.length ? appUrl(iosApps[0], 'iOS') : '';

  var subUrl = config.subscription_url || '';
  html += renderSetup(config.apps, platform, subUrl, config.server_name || 'Meridian', config.subscription_qr_b64);

  html += '<div class="panels">';
  html += renderConnections(config, platform, !subUrl);
  html += renderTrouble();
  var routerHref = ROUTER_DOCS.replace('{lang}', currentLang);
  html += '<a class="panel panel-link" href="' + escapeHtml(routerHref) + '" target="_blank" rel="noopener">';
  html += '<span class="panel-icon">' + ICON_ROUTER + '</span>';
  html += '<span class="panel-text"><strong data-t="router">Router</strong>';
  html += '<span data-t="router.desc">Connect your whole home network: setup guide</span></span>';
  html += '<span class="panel-chevron">' + ICON_CHEVRON + '</span>';
  html += '</a>';
  html += '</div>';

  if (navigator.share) {
    html += '<div class="foot">';
    html += '<button class="btn btn-ghost btn-sm" data-action="share-page">' + ICON_SHARE;
    html += '<span data-t="share.page">Share this page</span></button>';
    html += '</div>';
  }

  app.innerHTML = html;

  loadSelfCheck();
  requestWakeLock();
  applyI18n(config.client_name, config.server_name);
  highlightActiveLang();
  loadAccountPanel();
  document.dispatchEvent(new Event('meridian-render'));
}

function renderTopBar() {
  var html = '<div class="topbar"><div class="lang-bar" role="group" data-t-aria="language" aria-label="Language">';
  var langs = [['en', 'EN'], ['ru', 'RU'], ['fa', 'FA'], ['zh', '中文']];
  var titles = { en: 'English', ru: 'Русский', fa: 'فارسی', zh: '中文' };
  for (var i = 0; i < langs.length; i++) {
    html += '<button class="lang-btn" data-lang="' + langs[i][0] + '" data-action="lang" title="' + titles[langs[i][0]] + '" lang="' + langs[i][0] + '">' + langs[i][1] + '</button>';
  }
  return html + '</div></div>';
}

function renderHero(config) {
  var html = '<header class="hero"><div class="hero-mark">';
  var icon = config.server_icon;
  if (icon && icon.indexOf('data:image/') === 0) {
    html += '<img src="' + escapeHtml(icon) + '" alt="" width="56" height="56">';
  } else if (icon) {
    html += '<span class="hero-emoji">' + escapeHtml(icon) + '</span>';
  } else {
    html += ICON_SHIELD;
  }
  html += '</div><div class="hero-text">';
  if (config.server_name) {
    html += '<h1>' + escapeHtml(config.server_name) + '</h1>';
  } else {
    html += '<h1 data-t="hero.title">Your connection</h1>';
    var codes = validCountries(config.exit_countries);
    if (codes.length) html += '<p class="hero-place" data-countries="' + codes.join(',') + '"></p>';
  }
  if (isPersonalName(config.client_name)) {
    html += '<p data-t="hero.for">Set up for ' + escapeHtml(capitalize(config.client_name)) + '</p>';
  } else {
    html += '<p data-t="hero.sub">Three steps, about a minute.</p>';
  }
  return html + '</div></header>';
}

function validCountries(list) {
  var codes = [];
  for (var i = 0; list && i < list.length; i++) {
    if (typeof list[i] === 'string' && /^[A-Z]{2}$/.test(list[i])) codes.push(list[i]);
  }
  return codes;
}

function countryFlag(code) {
  return String.fromCharCode(0xD83C, 0xDDE6 + code.charCodeAt(0) - 65, 0xD83C, 0xDDE6 + code.charCodeAt(1) - 65);
}

function countryName(code, lang) {
  try {
    if (window.Intl && Intl.DisplayNames) return new Intl.DisplayNames([lang], { type: 'region' }).of(code) || code;
  } catch (e) { /* unknown locale or region: keep the code */ }
  return code;
}

/* Exit countries are data: named in the page language, never via data-t */
function fillPlaces(lang) {
  document.querySelectorAll('[data-countries]').forEach(function(el) {
    var codes = el.dataset.countries.split(',');
    var parts = [];
    for (var i = 0; i < codes.length; i++) parts.push(countryFlag(codes[i]) + ' ' + countryName(codes[i], lang));
    el.textContent = parts.join(' · ');
  });
}

/* -----------------------------------------------------------------------
 * Setup card: 1. install an app, 2. add the subscription to it
 * ----------------------------------------------------------------------- */
function renderAppMark(a, size) {
  if (a.icon) {
    return '<img class="app-mark" src="' + escapeHtml(a.icon) + '" alt="" width="' + size + '" height="' + size + '">';
  }
  return '<span class="app-mark app-mark-letter">' + escapeHtml(a.name.charAt(0)) + '</span>';
}

function renderAppRow(a, key) {
  var html = '<a class="app-row" data-ui-key="install-' + escapeHtml(a.name + '-' + key) + '" href="' + escapeHtml(appUrl(a, key)) + '" target="_blank" rel="noopener">';
  html += renderAppMark(a, 40);
  html += '<span class="app-row-text"><strong>' + escapeHtml(a.name) + '</strong>';
  var labels = { 'All platforms': 'apps.all', 'Desktop': 'apps.desktop',
    'iOS': 'platform.ios', 'Android': 'platform.android', 'Windows': 'platform.windows',
    'macOS': 'platform.macos', 'Linux': 'platform.linux',
    'Android, desktop': 'apps.android.desktop', 'iOS, Android': 'apps.ios.android',
    'iOS, Android, macOS': 'apps.ios.android.macos' };
  html += '<span>' + escapeHtml(labels[a.platform] ? text(labels[a.platform], a.platform) : a.platform) + '</span></span>';
  var kind = 'app.get';
  var host = new URL(appUrl(a, key)).hostname;
  if (host === 'play.google.com') kind = 'install.play';
  else if (host === 'apps.apple.com') kind = 'install.appstore';
  html += '<span class="app-row-get" data-t="' + kind + '">' + escapeHtml(text(kind, 'Get')) + '</span>';
  return html + '</a>';
}

function installerLabel(route) {
  var label = text('install.' + route.kind);
  if (route.arch) {
    var arch = route.arch;
    if (route.platform === 'macOS') arch = arch === 'arm64' ? 'apple' : 'intel';
    label += ' · ' + text('arch.' + arch);
  }
  if (route.region === 'ru') label += ' · ' + text('store.ru');
  return label;
}

function renderInstallAlternatives(app, platform) {
  var routes = app.installers || [];
  var links = '';
  for (var i = 0; i < routes.length; i++) {
    var route = routes[i];
    if (route.platform !== platform || route.url === appUrl(app, platform)) continue;
    links += '<li><a href="' + escapeHtml(route.url) + '" target="_blank" rel="noopener">' + escapeHtml(installerLabel(route)) + '</a></li>';
  }
  return links ? renderDisclosure('<span data-t="install.other">Other install options</span>',
    '<ul class="install-routes">' + links + '</ul>', 'install-alternatives') : '';
}

function renderImportButton(a, subUrl, serverName, primary) {
  var href;
  try { href = buildDeepLink(a.deeplink, subUrl, serverName); }
  catch (e) { return ''; }
  var html = '<a data-import data-ui-key="import-' + escapeHtml(a.name) + '" class="btn btn-block ' + (primary ? 'btn-primary' : 'btn-soft') + '" href="' + escapeHtml(href) + '">';
  html += a.icon ? renderAppMark(a, 22) : ICON_PLUS;
  html += '<span data-t="import.add" data-t-name="' + escapeHtml(a.name) + '">Add to ' + escapeHtml(a.name) + '</span>';
  return html + '</a>';
}

function renderDisclosure(label, body, key, cls) {
  return '<details class="disclose' + (cls ? ' ' + cls : '') + '"' + (key ? ' data-disclosure="' + key + '"' : '') + '><summary>' + label + ICON_CHEVRON + '</summary>' +
    '<div class="disclose-body">' + body + '</div></details>';
}

function renderSubUrl(subUrl, compact) {
  if (compact) {
    /* Beside a visible URL, copying is an action, not a second field. */
    return '<button class="btn btn-soft btn-block" data-ui-key="subscription-copy" data-action="copy" data-url="' + escapeHtml(subUrl) + '">' +
      ICON_COPY + '<span data-t="manual.copy">Copy URL</span></button>';
  }
  return '<button class="sub-url" data-ui-key="subscription-copy" data-action="copy" data-url="' + escapeHtml(subUrl) + '">' +
    '<span class="sub-url-value">' + escapeHtml(subUrl) + '</span>' + ICON_COPY + '</button>';
}

function renderQr(b64, cls) {
  return '<div class="qr ' + cls + '"><img src="data:image/png;base64,' + b64 + '" data-t-alt="qr.code" alt="QR code" width="200" height="200"></div>';
}

function chooseApp(apps, key) {
  var groups = appsFor(apps, key, false);
  var available = groups.rec.concat(groups.more);
  for (var i = 0; i < available.length; i++) if (available[i].name === selectedApp) return available[i];
  var app = available[0] || null;
  selectedApp = app ? app.name : '';
  return app;
}

function renderPickers(apps, platform, chosen) {
  var html = '<div class="setup-pickers"><label for="platform-choice"><span data-t="platform.choice">Platform</span>';
  html += '<select id="platform-choice" data-ui-key="platform-choice">';
  for (var i = 0; i < PLATFORM_ORDER.length; i++) {
    var key = PLATFORM_ORDER[i];
    html += '<option value="' + key + '"' + (key === platform ? ' selected' : '') + '>' +
      escapeHtml(text('platform.' + key, PLATFORM_KEYS[key])) + '</option>';
  }
  html += '</select></label><label for="app-choice"><span data-t="app.choice">App</span>';
  html += '<select id="app-choice" data-ui-key="app-choice"' + (!chosen ? ' disabled' : '') + '>';
  var groups = appsFor(apps, PLATFORM_KEYS[platform], false);
  var lists = [groups.rec, groups.more];
  for (var j = 0; j < lists.length; j++) {
    if (!lists[j].length) continue;
    html += '<optgroup label="' + escapeHtml(j ? text('apps.other', 'More apps') : text('primary.rec', 'Recommended')) + '">';
    for (var k = 0; k < lists[j].length; k++) {
      var app = lists[j][k];
      html += '<option value="' + escapeHtml(app.name) + '"' + (chosen && app.name === chosen.name ? ' selected' : '') + '>' + escapeHtml(app.name) + '</option>';
    }
    html += '</optgroup>';
  }
  return html + '</select></label></div>';
}

function renderManualImport(app, subUrl, direct) {
  var menu = app && app.manual && app.manual[currentLang];
  var body = '<p class="muted">' + escapeHtml(text('manual.desc').replace('{name}', app ? app.name : text('app.choice'))) + '</p>';
  body += '<p class="manual-path">' + escapeHtml(menu || text('manual.generic')) + '</p>';
  body += '<textarea class="manual-url" data-ui-key="manual-url" data-action="select-text" data-t-aria="sub.label" aria-label="Subscription URL" dir="ltr" readonly rows="2">' + escapeHtml(subUrl) + '</textarea>';
  body += renderSubUrl(subUrl, true);
  var label = '<span data-t="manual.import">Import manually</span>';
  return direct ? '<div class="manual-import manual-direct"><strong>' + label + '</strong>' + body + '</div>' :
    renderDisclosure(label, body, 'manual-import', 'manual-import');
}

function renderConnectSteps(app, mobile) {
  var steps = app && app.connect && app.connect[currentLang];
  steps = steps ? steps.slice() : [text('connect.permission'), text('connect.profile'), text('connect.start')];
  if (!mobile && app && app.tun && app.tun[currentLang]) steps.splice(steps.length - 1, 0, app.tun[currentLang]);
  var html = '<div class="step"><span class="step-num">3</span><div class="step-body">';
  html += '<h2 data-t="step.connect">Connect</h2><ol class="connect-steps">';
  for (var i = 0; i < steps.length; i++) html += '<li>' + escapeHtml(steps[i]) + '</li>';
  return html + '</ol></div></div>';
}

function renderSetup(apps, platform, subUrl, serverName, subQrB64) {
  var key = PLATFORM_KEYS[platform] || 'Linux';
  var mobile = platform === 'ios' || platform === 'android';
  var chosen = chooseApp(apps, key);
  var hasQr = subUrl && isValidBase64(subQrB64);
  var aside = hasQr && !mobile;
  var html = '<section id="setup-card" class="card setup' + (aside ? ' setup-wide' : '') + '"><div class="setup-steps">';
  html += '<div class="step"><span class="step-num">1</span><div class="step-body">';
  html += '<h2 data-t="step.install">Install an app</h2>' + renderPickers(apps, platform, chosen);
  html += chosen ? '<div class="app-list">' + renderAppRow(chosen, key) + '</div>' :
    '<p class="muted" data-t="apps.none">No apps for this platform. Choose another platform above.</p>';
  if (chosen) html += renderInstallAlternatives(chosen, key);
  html += '</div></div>';
  if (subUrl) {
    html += '<div class="step"><span class="step-num">2</span><div class="step-body">';
    html += '<h2 data-t="step.add">Add your connection</h2>';
    html += '<p class="muted" data-t="step.add.desc">One tap adds every server, and the app keeps them up to date.</p>';
    html += '<p class="muted import-status" id="import-status" role="status" hidden></p>';
    var button = chosen && chosen.deeplink ? renderImportButton(chosen, subUrl, serverName, true) : '';
    html += '<div class="import-actions">' + button + renderManualImport(chosen, subUrl, !button) + '</div>';
    if (hasQr && mobile) html += renderDisclosure('<span data-t="show.qr">Show QR code</span>', renderQr(subQrB64, 'qr-inline'), 'subscription-qr');
    html += '</div></div>';
    html += renderConnectSteps(chosen, mobile);
  }
  html += '</div>';
  if (aside) {
    html += '<aside class="setup-qr">' + renderQr(subQrB64, 'qr-aside');
    html += '<strong data-t="qr.title">Setting up a phone?</strong>';
    html += '<span data-t="qr.desc">In the app on your phone, add a subscription from a QR code and scan this one.</span></aside>';
  }
  return html + '</section>';
}

/* Refresh only setup when the optional catalog arrives: a self-check may be
   running, and the visitor may have opened instructions or focused an action. */
function updateSetup(config) {
  var old = document.getElementById('setup-card');
  if (!old) return;
  var open = {};
  old.querySelectorAll('[data-disclosure]').forEach(function(el) { open[el.dataset.disclosure] = el.open; });
  var active = document.activeElement;
  var focusKey = active && old.contains(active) && active.getAttribute('data-ui-key');
  var selection = focusKey && typeof active.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
  var holder = document.createElement('div');
  holder.innerHTML = renderSetup(config.apps, selectedPlatform, config.subscription_url || '', config.server_name || 'Meridian', config.subscription_qr_b64);
  var fresh = holder.firstElementChild;
  old.parentNode.replaceChild(fresh, old);
  fresh.querySelectorAll('[data-disclosure]').forEach(function(el) { el.open = !!open[el.dataset.disclosure]; });
  applyI18n(config.client_name, config.server_name);
  if (focusKey) fresh.querySelectorAll('[data-ui-key]').forEach(function(el) {
    if (el.dataset.uiKey !== focusKey) return;
    el.focus();
    if (selection && el.setSelectionRange) el.setSelectionRange(selection[0], selection[1]);
  });
  document.dispatchEvent(new Event('meridian-setup'));
}

/* -----------------------------------------------------------------------
 * Secondary panels: individual links, troubleshooting
 * ----------------------------------------------------------------------- */
var PROTOCOL_HINTS = {
  reality: '<span data-t="proto.reality">Fastest — try first</span>',
  xhttp: '<span data-t="proto.xhttp">Harder to detect</span>',
  hysteria2: '<span data-t="proto.hysteria2">UDP — for slow or lossy networks</span>',
  wss: '<span data-t="proto.wss">Through a CDN — last resort</span>'
};

function renderConnection(proto, platform) {
  var url = escapeHtml(proto.url);
  var hasQr = isValidBase64(proto.qr_b64);
  var html = '<div class="conn"><div class="conn-main"><div class="conn-text">';
  html += '<span class="conn-name">' + escapeHtml(proto.label) + '</span>';
  html += '<span class="conn-meta">';
  if (proto.recommended) html += '<span class="badge" data-t="primary.rec">Recommended</span>';
  if (PROTOCOL_HINTS.hasOwnProperty(proto.key)) html += PROTOCOL_HINTS[proto.key];
  html += '</span></div><div class="conn-actions">';
  if (platform === 'ios') {
    html += '<a data-import href="#" class="btn btn-soft btn-sm" data-ios-idx="' + (iosButtonIndex++) + '" data-url="' + url + '" data-action="open-ios" data-t="open.short">Open</a>';
  } else {
    html += '<a data-import href="' + url + '" class="btn btn-soft btn-sm" data-t="open.short">Open</a>';
  }
  html += '<button class="btn btn-ghost btn-icon" data-action="copy" data-url="' + url + '" data-t-aria="copy" aria-label="Copy">' + ICON_COPY + '</button>';
  if (hasQr) html += '<button class="btn btn-ghost btn-icon" data-action="toggle-qr" data-t-aria="qr.code" aria-expanded="false" aria-label="QR code">' + ICON_QR + '</button>';
  html += '</div></div>';
  if (hasQr) html += '<div class="conn-qr" hidden>' + renderQr(proto.qr_b64, 'qr-inline') + '</div>';
  return html + '</div>';
}

function renderConnections(config, platform, open) {
  var rows = '';
  var protocols = config.protocols || [];
  var count = protocols.length;
  var preview = Math.min(count, CONNECTION_PREVIEW);
  for (var i = 0; i < preview; i++) rows += renderConnection(protocols[i], platform);
  if (count > preview) {
    var rest = '';
    if (showAllConnections) for (var j = preview; j < count; j++) rest += renderConnection(protocols[j], platform);
    rows += '<div class="conn-extra" id="more-connections"' + (showAllConnections ? '' : ' hidden') + '>' + rest + '</div>';
    rows += '<button class="btn btn-ghost btn-sm" data-action="show-connections" aria-controls="more-connections" aria-expanded="' +
      (showAllConnections ? 'true' : 'false') + '" data-t="' + (showAllConnections ? 'conns.less' : 'conns.show') + '" data-t-count="' + count + '">' +
      escapeHtml(showAllConnections ? text('conns.less') : text('conns.show').replace('{n}', formatCount(count))) + '</button>';
  }
  if (!count) return '';
  var html = '<details id="connections-panel" class="panel"' + (open ? ' open' : '') + '><summary>';
  html += '<span class="panel-icon">' + ICON_LIST + '</span>';
  html += '<span class="panel-text"><strong data-t="conns">All connections</strong>';
  html += '<span data-t="conns.desc">Already in your app. Use one directly only if you need to.</span></span>';
  html += '<span class="count">' + formatCount(count) + '</span><span class="panel-chevron">' + ICON_CHEVRON + '</span>';
  html += '</summary><div class="panel-body conn-list">' + rows + '</div></details>';
  return html;
}

function formatCount(count) {
  try { return new Intl.NumberFormat(currentLang).format(count); } catch (e) { return String(count); }
}

function toggleConnections(button) {
  var extra = document.getElementById('more-connections');
  var config = window._meridianConfig;
  if (!extra || !config) return;
  showAllConnections = !showAllConnections;
  if (showAllConnections && !extra.firstElementChild) {
    var html = '';
    for (var i = CONNECTION_PREVIEW; i < config.protocols.length; i++) html += renderConnection(config.protocols[i], selectedPlatform);
    extra.innerHTML = html;
  }
  extra.hidden = !showAllConnections;
  button.setAttribute('aria-expanded', showAllConnections ? 'true' : 'false');
  button.setAttribute('data-t', showAllConnections ? 'conns.less' : 'conns.show');
  applyI18n(config.client_name, config.server_name);
  document.dispatchEvent(new Event('meridian-setup'));
}

function renderTrouble() {
  var html = '<details class="panel"><summary>';
  html += '<span class="panel-icon">' + ICON_HELP + '</span>';
  html += '<span class="panel-text"><strong data-t="ping">Not connecting?</strong>';
  html += '<span data-t="selfcheck">Check my connection</span></span>';
  html += '<span class="panel-chevron">' + ICON_CHEVRON + '</span>';
  html += '</summary><div class="panel-body">';
  html += '<ul class="tips">';
  html += '<li data-t="ping.desc">Check automatic time sync on your device, then try another connection option above.</li>';
  html += '<li data-t="sub.refresh">Your app refreshes the subscription on its own. To refresh it now, open the subscription (or profile) list in the app and tap Update.</li>';
  html += '<li data-t="tip.webrtc">Websites in your browser can sometimes see your real IP address (a WebRTC leak) when your app runs as a proxy only. To avoid it, turn on the app\'s VPN (TUN) mode so all traffic goes through the connection. You can confirm with any leak-check service: the address it shows should differ from your usual one.</li>';
  html += '<li><strong data-t="apps.update">Keep your app updated</strong> ';
  html += '<span data-t="apps.update.desc">New versions fix connection problems and keep up with new blocking. Turn on automatic updates in your app store, or check the download page now and then.</span></li>';
  html += '<li data-t="ping.link">Ask the server owner to run meridian test.</li>';
  html += '</ul>';
  html += '<div class="selfcheck">';
  html += '<p class="muted" data-t="selfcheck.desc">Runs three quick tests from this device and network: clock, data flow, and subscription. Turn your VPN off first so the test sees your real network.</p>';
  html += '<button class="btn btn-primary btn-block" data-action="selfcheck-run" data-t="selfcheck.run">Run check</button>';
  html += '<div class="selfcheck-results" id="selfcheck-results" aria-live="polite"></div>';
  html += '</div></div></details>';
  return html;
}

function toggleQr(button) {
  var row = button.closest('.conn');
  var qr = row && row.querySelector('.conn-qr');
  if (!qr) return;
  qr.hidden = !qr.hidden;
  button.setAttribute('aria-expanded', qr.hidden ? 'false' : 'true');
  button.classList.toggle('active', !qr.hidden);
}

/* -----------------------------------------------------------------------
 * i18n
 * ----------------------------------------------------------------------- */
var currentLang = null;

function switchLang(lang) {
  if (lang !== 'en' && !T[lang]) return;
  currentLang = lang;
  setDocumentLanguage();
  try { localStorage.setItem('meridian-lang', lang); } catch (e) {}
  /* Re-render the entire page — English text is baked into renderPage(),
     so we can't just swap data-t values; we need a full rebuild. */
  if (window._meridianConfig) {
    renderPage(window._meridianConfig);
  } else {
    init();
  }
}

function text(key, fallback) {
  return (T[currentLang] && T[currentLang][key]) || T.en[key] || fallback || key;
}

function setDocumentLanguage() {
  document.documentElement.lang = currentLang || 'en';
  document.documentElement.dir = currentLang === 'fa' ? 'rtl' : 'ltr';
}

function detectLang() {
  try {
    var saved = localStorage.getItem('meridian-lang');
    if (saved && (saved === 'en' || T[saved])) return saved;
  } catch (e) {}
  var bl = (navigator.language || '').slice(0, 2);
  return T[bl] ? bl : null;
}

function highlightActiveLang() {
  var lang = currentLang || 'en';
  document.querySelectorAll('.lang-btn').forEach(function(btn) {
    var active = btn.getAttribute('data-lang') === lang;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function applyI18n(clientName, serverName) {
  setDocumentLanguage();
  fillPlaces(currentLang);
  var dict = T[currentLang] || {};
  document.querySelectorAll('[data-t]').forEach(function(el) {
    var k = el.dataset.t;
    var v = dict[k];
    if (!v) return;
    if (k === 'hero.for' && isPersonalName(clientName)) {
      el.textContent = v.replace('{name}', capitalize(clientName));
    } else if (k === 'import.add' && el.dataset.tName) {
      el.textContent = v.replace('{name}', el.dataset.tName);
    } else if (el.dataset.tCount) {
      el.textContent = v.replace('{n}', formatCount(Number(el.dataset.tCount)));
    } else {
      el.textContent = v;
    }
  });
  document.querySelectorAll('[data-t-aria]').forEach(function(el) {
    el.setAttribute('aria-label', text(el.dataset.tAria));
  });
  document.querySelectorAll('[data-t-alt]').forEach(function(el) {
    el.setAttribute('alt', text(el.dataset.tAlt));
  });
  highlightActiveLang();
  updatePageMeta(clientName, serverName);
}

function updatePageMeta(clientName, serverName) {
  /* Update <title> and <html lang> to match current language */
  var dict = currentLang && T[currentLang];
  if (serverName) {
    document.title = serverName;
  } else {
    var titleText = (dict && dict['page_title']) || 'Connection Setup';
    if (isPersonalName(clientName)) {
      titleText += ' — ' + capitalize(clientName);
    }
    document.title = titleText;
  }

  /* Map internal lang codes to BCP 47 */
  var langMap = { en: 'en', ru: 'ru', fa: 'fa', zh: 'zh' };
  document.documentElement.lang = langMap[currentLang] || 'en';
}

/* -----------------------------------------------------------------------
 * Utilities
 * ----------------------------------------------------------------------- */
function escapeHtml(s) {
  /* Safe in both text nodes and quoted attributes. A text-node serializer
     leaves quotes intact, which once allowed data-url to become attributes. */
  return s === undefined || s === null ? '' : String(s).replace(/&/g, '&amp;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function capitalize(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

/* -----------------------------------------------------------------------
 * Service Worker + Persistent storage
 * ----------------------------------------------------------------------- */
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('../pwa/sw.js', { scope: '../' }).catch(function() {});
}

/* Loaded from here, not the shell, so pages rendered before it existed get it too */
function loadSelfCheck() {
  if (document.getElementById('selfcheck-js')) return;
  var s = document.createElement('script');
  s.id = 'selfcheck-js';
  s.src = '../pwa/selfcheck.js';
  document.body.appendChild(s);
}

/* Injected here so existing per-client shells receive the account panel too. */
function loadAccountPanel() {
  if (document.getElementById('account-js')) return;
  var script = document.createElement('script');
  script.id = 'account-js';
  script.src = '../pwa/account.js';
  document.body.appendChild(script);
}

function requestPersistentStorage() {
  if (navigator.storage && navigator.storage.persist) {
    navigator.storage.persist().catch(function() {});
  }
}

/* -----------------------------------------------------------------------
 * Event delegation — replaces all inline onclick handlers
 * ----------------------------------------------------------------------- */
document.addEventListener('click', function(e) {
  var disabled = e.target.closest('[data-import][aria-disabled="true"]');
  if (disabled) { e.preventDefault(); return; }
  var target = e.target.closest('[data-action]');
  if (!target) return;
  var action = target.getAttribute('data-action');

  if (action === 'copy') {
    var url = target.getAttribute('data-url');
    if (url) copyToClipboard(url);
  } else if (action === 'show-connections') {
    toggleConnections(target);
  } else if (action === 'select-text') {
    target.select();
  } else if (action === 'dismiss-copy') {
    dismissCopyFallback();
  } else if (action === 'toggle-qr') {
    toggleQr(target);
  } else if (action === 'share-page') {
    sharePageUrl();
  } else if (action === 'open-ios') {
    e.preventDefault();
    var iosIdx = target.getAttribute('data-ios-idx');
    if (iosIdx !== null) tryOpenIOS(parseInt(iosIdx, 10));
  } else if (action === 'lang') {
    var lang = target.getAttribute('data-lang');
    if (lang) switchLang(lang);
  } else if (action === 'install') {
    handleInstallClick();
  } else if (action === 'dismiss') {
    dismissInstallBanner();
  } else if (action === 'retry') {
    init();
  }
});

document.addEventListener('change', function(event) {
  var config = window._meridianConfig;
  if (!config) return;
  if (event.target.id === 'platform-choice' && Object.prototype.hasOwnProperty.call(PLATFORM_KEYS, event.target.value)) {
    selectedPlatform = event.target.value; selectedApp = '';
    updateSetup(config);
    var old = document.getElementById('connections-panel');
    if (old) {
      var holder = document.createElement('div');
      iosButtonIndex = 0;
      holder.innerHTML = renderConnections(config, selectedPlatform, old.open);
      old.parentNode.replaceChild(holder.firstElementChild, old);
      applyI18n(config.client_name, config.server_name);
      document.dispatchEvent(new Event('meridian-setup'));
    }
  } else if (event.target.id === 'app-choice') {
    selectedApp = event.target.value; updateSetup(config);
  }
});

/* -----------------------------------------------------------------------
 * Expose globals (minimal — selfcheck.js copies its report through this)
 * ----------------------------------------------------------------------- */
window.copyToClipboard = copyToClipboard;

/* -----------------------------------------------------------------------
 * Init
 * ----------------------------------------------------------------------- */
var initId = 0;
var CATALOG_TIMEOUT_MS = 5000;
function init() {
  var id = ++initId;
  registerSW();
  requestPersistentStorage();

  var app = document.getElementById('app');
  var slowTimer = setTimeout(function() {
    if (id !== initId) return;
    if (!app || app.querySelector('.load-slow')) return;
    var dict = currentLang && T[currentLang];
    var el = document.createElement('p');
    el.className = 'load-slow';
    el.textContent = (dict && dict['error.slow']) || 'Taking longer than expected…';
    app.appendChild(el);
  }, 10000);

  var catalog = null, catalogDone = false, configLoaded = false;
  var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  var catalogTimer = setTimeout(function() {
    catalogDone = true;
    if (controller) controller.abort();
  }, CATALOG_TIMEOUT_MS);
  function applyCatalog() {
    if (!configLoaded || !catalog || id !== initId) return;
    window._meridianConfig.apps = catalog.apps;
    updateSetup(window._meridianConfig);
  }
  fetch('../pwa/apps.json', { credentials: 'omit', redirect: 'error', signal: controller ? controller.signal : undefined })
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(value) {
      if (catalogDone || id !== initId) return;
      catalogDone = true; clearTimeout(catalogTimer);
      if (validCatalog(value)) { catalog = value; applyCatalog(); }
    }, function() { catalogDone = true; clearTimeout(catalogTimer); });

  fetch('config.json', { credentials: 'omit', redirect: 'error' })
    .then(function(r) {
      if (!r.ok) throw new Error('config.json fetch failed');
      return r.json();
    })
    .then(function(config) {
      clearTimeout(slowTimer);
      if (id !== initId) return;
      if (!validConfig(config)) throw new Error('config');
      window._meridianConfig = config;
      renderPage(config);
      configLoaded = true;
      applyCatalog();
    })
    .catch(function() {
      clearTimeout(slowTimer);
      if (id !== initId) return;
      if (app) {
        var dict = currentLang && T[currentLang];
        var errTitle = (dict && dict['error.title']) || 'Connection Setup';
        document.title = errTitle;
        var errMsg = (dict && dict['error.msg']) || 'Could not load configuration. Please reload the page.';
        var errRetry = (dict && dict['error.retry']) || 'Retry';
        app.innerHTML = '<div class="load-error"><h1>' + escapeHtml(errTitle) + '</h1>' +
          '<p>' + escapeHtml(errMsg) + '</p>' +
          '<button class="btn btn-primary" data-action="retry">' + escapeHtml(errRetry) + '</button></div>';
      }
    });
}

/* Choose the locale before a slow-load message or a fetch error can render. */
currentLang = detectLang() || 'en';
setDocumentLanguage();
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

})();
