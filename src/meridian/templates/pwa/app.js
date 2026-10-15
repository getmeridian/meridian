/* PWA — Connection Setup */
(function() {
'use strict';

/* -----------------------------------------------------------------------
 * i18n translations (English is the markup default)
 * ----------------------------------------------------------------------- */
var T = {
  ru: {
    'primary.rec': 'Рекомендовано',
    clock: 'Синхронизация часов',
    'clock.desc': 'Часы устройства отстают. При неверных часах подключение может не работать. Откройте Настройки > Дата и время > включите «Автоматически».',
    ping: 'Не подключается?',
    'ping.desc': 'Проверьте автоматическую синхронизацию времени и попробуйте другой вариант подключения выше.',
    'ping.link': 'Попросите владельца сервера запустить meridian test',
    'install.title': 'Установите приложение',
    'install.btn': 'Установить',
    'sub.label': 'Подписка (автообновление)',
    'sub.desc': 'Добавьте этот URL как подписку в приложении для автоматических обновлений.',
    'import.add': 'Добавить в {name}',
    'import.manual': 'Скопировать URL подписки',
    'install.offline': 'Доступ к подключению офлайн',
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
    'hero.sub': 'Два шага, около минуты.',
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
    'primary.rec': 'پیشنهادی',
    clock: 'همگام\u200Cسازی ساعت',
    'clock.desc': 'ساعت دستگاه عقب است. با ساعت نادرست ممکن است اتصال برقرار نشود. به تنظیمات > تاریخ و ساعت > \u00ABتنظیم خودکار\u00BB بروید.',
    ping: '\u0645\u062A\u0635\u0644 \u0646\u0645\u06CC\u200C\u0634\u0648\u06CC\u062F\u061F',
    'ping.desc': 'همگام‌سازی خودکار ساعت را بررسی کنید و یکی از گزینه‌های اتصال بالا را امتحان کنید.',
    'ping.link': 'از صاحب سرور بخواهید meridian test را اجرا کند',
    'install.title': 'برنامه را نصب کنید',
    'install.btn': 'نصب',
    'sub.label': 'اشتراک (بروزرسانی خودکار)',
    'sub.desc': 'این URL را به عنوان اشتراک در برنامه اضافه کنید.',
    'import.add': 'افزودن به {name}',
    'import.manual': 'کپی URL اشتراک',
    'install.offline': 'دسترسی آفلاین به اتصال',
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
    'hero.sub': 'دو مرحله، حدود یک دقیقه.',
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
    'primary.rec': '推荐',
    clock: '时钟同步',
    'clock.desc': '设备时钟慢了。时钟不准可能导致连接失败。前往设置 > 日期与时间 > 启用"自动设置"。',
    ping: '无法连接？',
    'ping.desc': '请确认设备已开启自动校时，然后尝试上面的其他连接方式。',
    'ping.link': '请让服务器所有者运行 meridian test',
    'install.title': '安装应用',
    'install.btn': '安装',
    'sub.label': '订阅（自动更新）',
    'sub.desc': '将此 URL 作为订阅添加到应用中以自动更新。',
    'import.add': '添加到 {name}',
    'import.manual': '复制订阅链接',
    'install.offline': '离线访问连接',
    'apps.more': '其他平台',
    'apps.other': '更多应用',
    'apps.update': '保持应用为最新版本',
    'apps.update.desc': '新版本会修复连接问题，并应对新的封锁手段。请在应用商店中开启自动更新，或不时查看下载页面。',
    'sub.refresh': '应用会自动刷新订阅。如需立即刷新，请在应用中打开订阅（或配置）列表并点击"更新"。',
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
    'hero.sub': '只需两步，大约一分钟。',
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
var ROUTER_DOCS = 'https://getmeridian.org/{lang}/routers/';

function appPlatforms(a) {
  if (a.platforms) return a.platforms;
  /* Older config.json entries carry only a display label */
  return a.platform === 'All platforms' ? ALL_PLATFORMS : [a.platform];
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
function copyToClipboard(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(showToast);
  }
  if ('vibrate' in navigator) navigator.vibrate(30);
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
  var values = {
    '{url_raw}': subUrl,
    '{url_b64}': btoa(subUrl),
    '{url}': encodeURIComponent(subUrl),
    '{name}': encodeURIComponent(name || 'Meridian'),
  };
  return template.replace(/\{(url_raw|url_b64|url|name)\}/g, function(m) { return values[m]; });
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
  var platform = detectPlatform();
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

  html += '<div class="install-banner" id="install-banner">';
  html += '<div class="install-banner-text"><strong data-t="install.title">Install this app</strong><span data-t="install.offline">Access your connection offline</span></div>';
  html += '<button class="btn btn-primary btn-sm" data-action="install" data-t="install.btn">Install</button>';
  html += '<button class="install-dismiss" data-action="dismiss" aria-label="Close">&times;</button>';
  html += '</div>';

  /* iOS "Open" falls back to the first recommended iOS app */
  var iosApps = appsFor(config.apps, 'iOS', false).rec;
  iosFallbackUrl = iosApps.length ? appUrl(iosApps[0], 'iOS') : '';

  var subUrl = config.subscription_url || '';
  html += renderSetup(config.apps, platform, subUrl, config.server_name || 'Meridian', config.subscription_qr_b64);

  html += '<div class="panels">';
  html += renderConnections(config, platform, !subUrl);
  html += renderTrouble();
  var routerHref = ROUTER_DOCS.replace('{lang}', detectLang() || 'en');
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
}

function renderTopBar() {
  var html = '<div class="topbar"><div class="lang-bar" role="group" aria-label="Language">';
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
    html += '<p data-t="hero.sub">Two steps, about a minute.</p>';
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
  var html = '<a class="app-row" href="' + escapeHtml(appUrl(a, key)) + '" target="_blank" rel="noopener">';
  html += renderAppMark(a, 40);
  html += '<span class="app-row-text"><strong>' + escapeHtml(a.name) + '</strong>';
  html += '<span>' + escapeHtml(a.platform) + '</span></span>';
  html += '<span class="app-row-get" data-t="app.get">Get</span>';
  return html + '</a>';
}

function renderImportButton(a, subUrl, serverName, primary) {
  var href = buildDeepLink(a.deeplink, subUrl, serverName);
  var html = '<a class="btn btn-block ' + (primary ? 'btn-primary' : 'btn-soft') + '" href="' + escapeHtml(href) + '">';
  html += a.icon ? renderAppMark(a, 22) : ICON_PLUS;
  html += '<span data-t="import.add" data-t-name="' + escapeHtml(a.name) + '">Add to ' + escapeHtml(a.name) + '</span>';
  return html + '</a>';
}

function renderDisclosure(label, body) {
  return '<details class="disclose"><summary>' + label + ICON_CHEVRON + '</summary>' +
    '<div class="disclose-body">' + body + '</div></details>';
}

function renderSubUrl(subUrl) {
  return '<button class="sub-url" data-action="copy" data-url="' + escapeHtml(subUrl) + '">' +
    '<span class="sub-url-value">' + escapeHtml(subUrl) + '</span>' + ICON_COPY + '</button>';
}

function renderQr(b64, cls) {
  return '<div class="qr ' + cls + '"><img src="data:image/png;base64,' + b64 + '" alt="QR code" width="200" height="200"></div>';
}

function renderSetup(apps, platform, subUrl, serverName, subQrB64) {
  var key = PLATFORM_KEYS[platform] || 'Linux';
  var mobile = platform === 'ios' || platform === 'android';
  var install = appsFor(apps, key, false);
  var hasQr = subUrl && isValidBase64(subQrB64);
  var aside = hasQr && !mobile;
  var html = '<section class="card setup' + (aside ? ' setup-wide' : '') + '"><div class="setup-steps">';

  /* Step 1 — install */
  html += '<div class="step"><span class="step-num">1</span><div class="step-body">';
  html += '<h2 data-t="step.install">Install an app</h2>';
  html += '<div class="app-list">';
  for (var i = 0; i < install.rec.length; i++) html += renderAppRow(install.rec[i], key);
  html += '</div>';
  if (install.more.length) {
    var more = '<div class="app-list">';
    for (var j = 0; j < install.more.length; j++) more += renderAppRow(install.more[j], key);
    html += renderDisclosure('<span data-t="apps.other">More apps</span>', more + '</div>');
  }
  var others = '';
  for (var p = 0; p < PLATFORM_ORDER.length; p++) {
    var otherKey = PLATFORM_KEYS[PLATFORM_ORDER[p]];
    if (otherKey === key) continue;
    var otherApps = appsFor(apps, otherKey, false).rec;
    if (!otherApps.length) continue;
    others += '<div class="list-heading">' + escapeHtml(otherKey) + '</div><div class="app-list">';
    for (var q = 0; q < otherApps.length; q++) others += renderAppRow(otherApps[q], otherKey);
    others += '</div>';
  }
  if (others) html += renderDisclosure('<span data-t="apps.more">Other platforms</span>', others);
  html += '</div></div>';

  /* Step 2 — add the subscription (pages without one go straight to the links) */
  if (subUrl) {
    var imports = appsFor(apps, key, true);
    html += '<div class="step"><span class="step-num">2</span><div class="step-body">';
    html += '<h2 data-t="step.add">Add your connection</h2>';
    html += '<p class="muted" data-t="step.add.desc">One tap adds every server, and the app keeps them up to date.</p>';
    if (imports.rec.length) {
      html += '<div class="import-list">';
      for (var k = 0; k < imports.rec.length; k++) html += renderImportButton(imports.rec[k], subUrl, serverName, k === 0);
      html += '</div>';
    } else {
      html += '<p class="muted" data-t="sub.desc">Add this URL as a subscription in your app for automatic updates.</p>';
      html += renderSubUrl(subUrl);
    }
    if (hasQr && mobile) html += renderDisclosure('<span data-t="show.qr">Show QR code</span>', renderQr(subQrB64, 'qr-inline'));
    if (imports.rec.length) {
      var rest = imports.more.concat(imports.other);
      var body = '';
      if (rest.length) {
        body += '<div class="import-list">';
        for (var m = 0; m < rest.length; m++) body += renderImportButton(rest[m], subUrl, serverName, false);
        body += '</div>';
      }
      body += '<div class="list-heading" data-t="import.manual">Copy subscription URL</div>' + renderSubUrl(subUrl);
      html += renderDisclosure('<span data-t="import.more">Using another app?</span>', body);
    }
    html += '</div></div>';
  }
  html += '</div>';

  if (aside) {
    html += '<aside class="setup-qr">' + renderQr(subQrB64, 'qr-aside');
    html += '<strong data-t="qr.title">Setting up a phone?</strong>';
    html += '<span data-t="qr.desc">In the app on your phone, add a subscription from a QR code and scan this one.</span>';
    html += '</aside>';
  }
  return html + '</section>';
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
    html += '<a href="#" class="btn btn-soft btn-sm" data-ios-idx="' + (iosButtonIndex++) + '" data-url="' + url + '" data-action="open-ios" data-t="open.short">Open</a>';
  } else {
    html += '<a href="' + url + '" class="btn btn-soft btn-sm" data-t="open.short">Open</a>';
  }
  html += '<button class="btn btn-ghost btn-icon" data-action="copy" data-url="' + url + '" aria-label="Copy">' + ICON_COPY + '</button>';
  if (hasQr) html += '<button class="btn btn-ghost btn-icon" data-action="toggle-qr" aria-expanded="false" aria-label="QR">' + ICON_QR + '</button>';
  html += '</div></div>';
  if (hasQr) html += '<div class="conn-qr" hidden>' + renderQr(proto.qr_b64, 'qr-inline') + '</div>';
  return html + '</div>';
}

function renderConnections(config, platform, open) {
  var rows = '';
  var protocols = config.protocols || [];
  var count = protocols.length;
  for (var i = 0; i < count; i++) rows += renderConnection(protocols[i], platform);
  if (!count) return '';
  var html = '<details class="panel"' + (open ? ' open' : '') + '><summary>';
  html += '<span class="panel-icon">' + ICON_LIST + '</span>';
  html += '<span class="panel-text"><strong data-t="conns">All connections</strong>';
  html += '<span data-t="conns.desc">Already in your app. Use one directly only if you need to.</span></span>';
  html += '<span class="count">' + count + '</span><span class="panel-chevron">' + ICON_CHEVRON + '</span>';
  html += '</summary><div class="panel-body conn-list">' + rows + '</div></details>';
  return html;
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
  currentLang = lang;
  try { localStorage.setItem('meridian-lang', lang); } catch (e) {}
  /* Re-render the entire page — English text is baked into renderPage(),
     so we can't just swap data-t values; we need a full rebuild. */
  if (window._meridianConfig) {
    renderPage(window._meridianConfig);
  }
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
  currentLang = detectLang();
  fillPlaces(currentLang || 'en');
  if (!currentLang) { currentLang = 'en'; highlightActiveLang(); updatePageMeta(clientName, serverName); return; }
  var dict = T[currentLang];
  if (!dict) { highlightActiveLang(); updatePageMeta(clientName, serverName); return; }
  document.querySelectorAll('[data-t]').forEach(function(el) {
    var k = el.dataset.t;
    var v = dict[k];
    if (!v) return;
    if (k === 'hero.for' && isPersonalName(clientName)) {
      el.textContent = v.replace('{name}', capitalize(clientName));
    } else if (k === 'import.add' && el.dataset.tName) {
      el.textContent = v.replace('{name}', el.dataset.tName);
    } else {
      el.textContent = v;
    }
  });
  document.documentElement.dir = currentLang === 'fa' ? 'rtl' : 'ltr';
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
  if (!s) return '';
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(s));
  return div.innerHTML;
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

function requestPersistentStorage() {
  if (navigator.storage && navigator.storage.persist) {
    navigator.storage.persist().catch(function() {});
  }
}

/* -----------------------------------------------------------------------
 * Event delegation — replaces all inline onclick handlers
 * ----------------------------------------------------------------------- */
document.addEventListener('click', function(e) {
  var target = e.target.closest('[data-action]');
  if (!target) return;
  var action = target.getAttribute('data-action');

  if (action === 'copy') {
    var url = target.getAttribute('data-url');
    if (url) copyToClipboard(url);
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

/* -----------------------------------------------------------------------
 * Expose globals (minimal — selfcheck.js copies its report through this)
 * ----------------------------------------------------------------------- */
window.copyToClipboard = copyToClipboard;

/* -----------------------------------------------------------------------
 * Init
 * ----------------------------------------------------------------------- */
function init() {
  registerSW();
  requestPersistentStorage();

  var app = document.getElementById('app');
  var slowTimer = setTimeout(function() {
    if (!app || app.querySelector('.load-slow')) return;
    var dict = currentLang && T[currentLang];
    var el = document.createElement('p');
    el.className = 'load-slow';
    el.textContent = (dict && dict['error.slow']) || 'Taking longer than expected…';
    app.appendChild(el);
  }, 10000);

  /* The shared catalog (with icons) wins; config.apps is the offline fallback */
  var catalog = fetch('../pwa/apps.json')
    .then(function(r) { return r.ok ? r.json() : null; })
    .catch(function() { return null; });

  fetch('config.json')
    .then(function(r) {
      if (!r.ok) throw new Error('config.json fetch failed');
      return r.json();
    })
    .then(function(config) {
      return catalog.then(function(c) {
        if (c && c.apps && c.apps.length) config.apps = c.apps;
        return config;
      });
    })
    .then(function(config) {
      clearTimeout(slowTimer);
      window._meridianConfig = config;
      renderPage(config);
    })
    .catch(function() {
      clearTimeout(slowTimer);
      if (app) {
        var dict = currentLang && T[currentLang];
        var errTitle = (dict && dict['error.title']) || 'Connection Setup';
        var errMsg = (dict && dict['error.msg']) || 'Could not load configuration. Please reload the page.';
        var errRetry = (dict && dict['error.retry']) || 'Retry';
        app.innerHTML = '<div class="load-error"><h1>' + escapeHtml(errTitle) + '</h1>' +
          '<p>' + escapeHtml(errMsg) + '</p>' +
          '<button class="btn btn-primary" data-action="retry">' + escapeHtml(errRetry) + '</button></div>';
      }
    });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

})();
