/* PWA — Connection Setup */
(function() {
'use strict';

/* -----------------------------------------------------------------------
 * i18n translations
 * ----------------------------------------------------------------------- */
var T = {
  ru: {
    title: 'Настройка подключения',
    subtitle: 'Отсканируйте QR-код или нажмите, чтобы открыть в приложении',
    'subtitle.named': 'Подключение для {name} — отсканируйте QR-код или откройте в приложении',
    trust: 'Это безопасное подключение, настроенное для вас',
    'trust.server': 'Безопасное подключение через {name}',
    primary: 'Основной', backup: 'Резервный',
    'primary.rec': 'Рекомендовано',
    'primary.desc': 'Рекомендуется — самое быстрое и надёжное подключение. Используйте в первую очередь.',
    'backup.desc': 'Устаревший запасной — через CDN. Используйте, только если Основной и XHTTP не работают.',
    'xhttp.desc': 'Альтернатива — используйте, если Основной не работает. Труднее обнаружить.',
    'hy2.desc': 'Резервный UDP — попробуйте, если TCP медленный или заблокирован.',
    open: 'Открыть в приложении',
    share: 'Поделиться',
    'copy.link': 'Скопировать ссылку',
    'show.raw': 'Показать ссылку',
    'copy.hint': 'нажмите для копирования',
    apps: 'Приложения',
    'apps.desc': 'Установите одно из них, затем нажмите «Открыть в приложении» или отсканируйте QR-код.',
    setup: 'Быстрая настройка',
    step1: 'Установите клиентское приложение из списка выше',
    step2: 'Нажмите «Открыть в приложении» — подключение импортируется автоматически',
    step3: 'Или отсканируйте QR-код с другого устройства',
    step4: 'Активируйте подключение в приложении',
    clock: 'Синхронизация часов',
    'clock.desc': 'Часы устройства должны быть точными с отклонением не более 30 секунд. Откройте Настройки > Дата и время > включите «Автоматически».',
    ping: 'Не подключается?',
    'ping.desc': 'Проверьте автоматическую синхронизацию времени и попробуйте другой вариант подключения выше.',
    'ping.link': 'Попросите владельца сервера запустить meridian test',
    stats: 'Использование', 'stats.upload': 'Загрузка', 'stats.download': 'Скачивание',
    'install.title': 'Установите приложение',
    'install.btn': 'Установить',
    'sub.label': 'Подписка (автообновление)',
    'sub.desc': 'Добавьте этот URL как подписку в приложении для автоматических обновлений.',
    'import.label': 'Импорт в одно касание',
    'import.desc': 'Добавьте в приложение одним нажатием. Обновляется автоматически.',
    'import.desc.qr': 'Сканируйте любым V2Ray-приложением или нажмите для добавления. Обновляется автоматически.',
    'import.add': 'Добавить в {name}',
    'import.manual': 'Скопировать URL подписки',
    'install.offline': 'Доступ к подключению офлайн',
    'backup.direct': 'ЗАПАСНОЙ (ПРЯМОЙ)',
    'relay.desc': 'Подключайтесь через реле для лучшей надёжности в вашем регионе.',
    'relay.via': 'через {name}',
    'direct.desc': 'Прямое подключение — используйте, если реле недоступно.',
    'more.options': 'Другие варианты подключения',
    'import.other': 'Другие платформы',
    'apps.more': 'Другие платформы',
    'show.qr': 'Показать QR-код',
    'share.page': 'Поделиться этой страницей',
    copied: 'Скопировано',
    'page_title': 'Настройка подключения',
    'stats.now': 'Активно сейчас',
    'stats.min_ago': 'Активно {n}м назад',
    'stats.hr_ago': 'Активно {n}ч назад',
    'stats.day_ago': 'Активно {n}д назад',
    'error.title': 'Настройка подключения',
    'error.msg': 'Не удалось загрузить конфигурацию. Попробуйте перезагрузить страницу.',
    'error.retry': 'Повторить',
    'error.slow': 'Загрузка занимает больше времени, чем обычно\u2026',
  },
  fa: {
    title: 'تنظیم اتصال',
    subtitle: 'کد QR را اسکن کنید یا برای باز کردن در برنامه ضربه بزنید',
    'subtitle.named': 'اتصال برای {name} — کد QR را اسکن کنید یا در برنامه باز کنید',
    trust: 'این یک اتصال امن است که برای شما تنظیم شده',
    'trust.server': 'اتصال امن از {name}',
    primary: 'اصلی', backup: 'پشتیبان',
    'primary.rec': 'پیشنهادی',
    'primary.desc': 'پیشنهادی \u2014 سریع\u200Cترین و پایدارترین اتصال. ابتدا این را امتحان کنید.',
    'backup.desc': 'جایگزین قدیمی \u2014 از طریق CDN. فقط اگر اصلی و XHTTP کار نکرد استفاده کنید.',
    'xhttp.desc': 'جایگزین \u2014 اگر اصلی کار نمی\u200Cکند استفاده کنید. شناسایی آن دشوارتر است.',
    'hy2.desc': 'پشتیبان UDP — وقتی TCP کند یا مسدود است امتحان کنید.',
    open: 'باز کردن در برنامه',
    share: 'اشتراک\u200Cگذاری',
    'copy.link': 'کپی لینک',
    'show.raw': 'نمایش لینک',
    'copy.hint': 'برای کپی ضربه بزنید',
    apps: 'برنامه\u200Cها',
    'apps.desc': 'یکی را نصب کنید، سپس \u00ABباز کردن در برنامه\u00BB را بزنید یا کد QR را اسکن کنید.',
    setup: 'راه\u200Cاندازی سریع',
    step1: 'یک برنامه کلاینت از لیست بالا نصب کنید',
    step2: '\u00ABباز کردن در برنامه\u00BB را بزنید \u2014 اتصال به\u200Cطور خودکار وارد می\u200Cشود',
    step3: 'یا کد QR را از دستگاه دیگری اسکن کنید',
    step4: 'اتصال را در برنامه فعال کنید',
    clock: 'همگام\u200Cسازی ساعت',
    'clock.desc': 'ساعت دستگاه باید با دقت ۳۰ ثانیه تنظیم باشد. به تنظیمات > تاریخ و ساعت > \u00ABتنظیم خودکار\u00BB بروید.',
    ping: '\u0645\u062A\u0635\u0644 \u0646\u0645\u06CC\u200C\u0634\u0648\u06CC\u062F\u061F',
    'ping.desc': 'همگام‌سازی خودکار ساعت را بررسی کنید و یکی از گزینه‌های اتصال بالا را امتحان کنید.',
    'ping.link': 'از صاحب سرور بخواهید meridian test را اجرا کند',
    stats: 'مصرف', 'stats.upload': 'آپلود', 'stats.download': 'دانلود',
    'install.title': 'برنامه را نصب کنید',
    'install.btn': 'نصب',
    'sub.label': 'اشتراک (بروزرسانی خودکار)',
    'sub.desc': 'این URL را به عنوان اشتراک در برنامه اضافه کنید.',
    'import.label': 'افزودن با یک ضربه',
    'import.desc': 'با یک ضربه به برنامه اضافه کنید. به\u200Cروزرسانی خودکار.',
    'import.desc.qr': 'با هر برنامه V2Ray اسکن کنید یا برای افزودن ضربه بزنید. به\u200Cروزرسانی خودکار.',
    'import.add': 'افزودن به {name}',
    'import.manual': 'کپی URL اشتراک',
    'install.offline': 'دسترسی آفلاین به اتصال',
    'backup.direct': 'پشتیبان (مستقیم)',
    'relay.desc': 'برای بهترین پایداری در منطقه خود از طریق رله متصل شوید.',
    'relay.via': 'از طریق {name}',
    'direct.desc': 'اتصال مستقیم \u2014 اگر رله در دسترس نیست استفاده کنید.',
    'more.options': 'گزینه\u200Cهای دیگر',
    'import.other': 'پلتفرم\u200Cهای دیگر',
    'apps.more': 'پلتفرم\u200Cهای دیگر',
    'show.qr': 'نمایش کد QR',
    'share.page': 'اشتراک\u200Cگذاری این صفحه',
    copied: '\u06A9\u067E\u06CC \u0634\u062F',
    'page_title': 'تنظیم اتصال',
    'stats.now': '\u0641\u0639\u0627\u0644 \u0627\u06A9\u0646\u0648\u0646',
    'stats.min_ago': '\u0641\u0639\u0627\u0644 {n} \u062F\u0642\u06CC\u0642\u0647 \u067E\u06CC\u0634',
    'stats.hr_ago': '\u0641\u0639\u0627\u0644 {n} \u0633\u0627\u0639\u062A \u067E\u06CC\u0634',
    'stats.day_ago': '\u0641\u0639\u0627\u0644 {n} \u0631\u0648\u0632 \u067E\u06CC\u0634',
    'error.title': 'تنظیم اتصال',
    'error.msg': '\u0628\u0627\u0631\u06AF\u0630\u0627\u0631\u06CC \u067E\u06CC\u06A9\u0631\u0628\u0646\u062F\u06CC \u0627\u0646\u062C\u0627\u0645 \u0646\u0634\u062F. \u0644\u0637\u0641\u0627\u064B \u0635\u0641\u062D\u0647 \u0631\u0627 \u062F\u0648\u0628\u0627\u0631\u0647 \u0628\u0627\u0631\u06AF\u0630\u0627\u0631\u06CC \u06A9\u0646\u06CC\u062F.',
    'error.retry': '\u062A\u0644\u0627\u0634 \u0645\u062C\u062F\u062F',
    'error.slow': '\u0628\u0627\u0631\u06AF\u0630\u0627\u0631\u06CC \u0628\u06CC\u0634 \u0627\u0632 \u062D\u062F \u0645\u0639\u0645\u0648\u0644 \u0637\u0648\u0644 \u0645\u06CC\u200C\u06A9\u0634\u062F\u2026',
  },
  zh: {
    title: '连接设置',
    subtitle: '扫描二维码或点击在应用中打开',
    'subtitle.named': '{name} 的连接 — 扫描二维码或点击在应用中打开',
    trust: '这是为您设置的安全连接',
    'trust.server': '来自 {name} 的安全连接',
    primary: '主要', backup: '备用',
    'primary.rec': '推荐',
    'primary.desc': '推荐 — 最快最稳定的连接。请优先使用。',
    'backup.desc': '旧备用通道 — 通过 CDN 路由。仅在主连接和 XHTTP 都失败时使用。',
    'xhttp.desc': '备选 — 主连接不可用时使用。更难被检测。',
    'hy2.desc': 'UDP 备用通道 — TCP 慢或被封锁时尝试。',
    open: '在应用中打开',
    share: '分享',
    'copy.link': '复制链接',
    'show.raw': '显示链接',
    'copy.hint': '点击复制',
    apps: '客户端应用',
    'apps.desc': '安装一个应用，然后点击"在应用中打开"或扫描二维码。',
    setup: '快速设置',
    step1: '从上方列表安装一个客户端应用',
    step2: '点击"在应用中打开"——连接自动导入',
    step3: '或从另一台设备扫描二维码',
    step4: '在应用中激活连接',
    clock: '时钟同步',
    'clock.desc': '设备时钟必须精确到 30 秒以内。前往设置 > 日期与时间 > 启用"自动设置"。',
    ping: '无法连接？',
    'ping.desc': '请确认设备已开启自动校时，然后尝试上面的其他连接方式。',
    'ping.link': '请让服务器所有者运行 meridian test',
    stats: '用量', 'stats.upload': '上传', 'stats.download': '下载',
    'install.title': '安装应用',
    'install.btn': '安装',
    'sub.label': '订阅（自动更新）',
    'sub.desc': '将此 URL 作为订阅添加到应用中以自动更新。',
    'import.label': '一键导入',
    'import.desc': '一键添加到应用，自动更新。',
    'import.desc.qr': '使用任意V2Ray应用扫描，或点击添加。自动更新。',
    'import.add': '添加到 {name}',
    'import.manual': '复制订阅链接',
    'install.offline': '离线访问连接',
    'backup.direct': '备用（直连）',
    'relay.desc': '通过中继连接以获得最佳可靠性。',
    'relay.via': '经由 {name}',
    'direct.desc': '直接连接 — 中继不可用时使用。',
    'more.options': '更多连接选项',
    'import.other': '其他平台',
    'apps.more': '其他平台',
    'show.qr': '显示二维码',
    'share.page': '分享此页面',
    copied: '已复制',
    'page_title': '连接设置',
    'stats.now': '当前在线',
    'stats.min_ago': '{n}分钟前活跃',
    'stats.hr_ago': '{n}小时前活跃',
    'stats.day_ago': '{n}天前活跃',
    'error.title': '连接设置',
    'error.msg': '无法加载配置。请重新加载页面。',
    'error.retry': '重试',
    'error.slow': '加载时间比预期更长\u2026',
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
 * Deep link construction
 * ----------------------------------------------------------------------- */
function tryOpenIOS(index) {
  var el = document.querySelector('[data-ios-idx="' + index + '"]');
  if (!el) return;
  var url = el.getAttribute('data-url');
  if (!url) return;
  var start = Date.now();
  window.location.href = url;
  setTimeout(function() {
    if (Date.now() - start < 2000) {
      window.location.href = 'https://apps.apple.com/app/v2raytun/id6476628951';
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

/* -----------------------------------------------------------------------
 * Stats loading
 * ----------------------------------------------------------------------- */
function ago(ts) {
  if (!ts) return '';
  var s = Math.floor((Date.now() - ts) / 1000);
  var dict = currentLang && T[currentLang];
  if (s < 60) return (dict && dict['stats.now']) || 'Active now';
  if (s < 3600) {
    var m = Math.floor(s / 60);
    return ((dict && dict['stats.min_ago']) || 'Active {n}m ago').replace('{n}', m);
  }
  if (s < 86400) {
    var h = Math.floor(s / 3600);
    return ((dict && dict['stats.hr_ago']) || 'Active {n}h ago').replace('{n}', h);
  }
  var d = Math.floor(s / 86400);
  return ((dict && dict['stats.day_ago']) || 'Active {n}d ago').replace('{n}', d);
}

function loadStats(uuid) {
  if (!uuid) return;
  var base = location.pathname.replace(/\/[^/]*\/?$/, '');
  fetch(base + '/stats/' + uuid + '.json')
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(d) {
      if (!d) return;
      var el = document.getElementById('stats');
      if (!el) return;
      function fmt(b) {
        if (b >= 1073741824) return (b / 1073741824).toFixed(1) + ' GB';
        if (b >= 1048576) return (b / 1048576).toFixed(0) + ' MB';
        if (b >= 1024) return (b / 1024).toFixed(0) + ' KB';
        return b + ' B';
      }
      document.getElementById('s-up').textContent = fmt(d.up);
      document.getElementById('s-down').textContent = fmt(d.down);
      var act = document.getElementById('s-active');
      if (d.lastOnline && act) act.textContent = ago(d.lastOnline);
      el.style.display = 'block';
    })
    .catch(function() {});
}

/* -----------------------------------------------------------------------
 * Subscription URL
 * ----------------------------------------------------------------------- */
function getSubscriptionUrl(config) {
  if (config && config.subscription_url) {
    return config.subscription_url;
  }
  var path = location.pathname.replace(/\/?$/, '');
  return location.origin + path + '/sub.txt';
}

function buildDeepLink(template, subUrl, name) {
  return template
    .replace('{url_raw}', subUrl)
    .replace('{url_b64}', btoa(subUrl))
    .replace('{url}', encodeURIComponent(subUrl))
    .replace('{name}', encodeURIComponent(name || 'Meridian'));
}

/* -----------------------------------------------------------------------
 * Web Share API
 * ----------------------------------------------------------------------- */
function shareUrl(url) {
  if (navigator.share) {
    navigator.share({ title: '', text: url }).catch(function() {});
  } else {
    copyToClipboard(url);
  }
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
function checkClockSkew(generatedAt) {
  if (!generatedAt) return 'ok';
  try {
    var serverTime = new Date(generatedAt).getTime();
    var age = Date.now() - serverTime;
    /* If page was generated more than 1h ago, can't reliably detect skew */
    if (Math.abs(age) > 3600000) return 'ok';
    /* Only warn if clock is off by more than 2 minutes (Reality needs ~30s) */
    return age < -120000 ? 'bad' : 'ok'; /* device clock is BEHIND server */
  } catch (e) { return 'ok'; }
}

/* -----------------------------------------------------------------------
 * Base64 validation (defense-in-depth for QR data)
 * ----------------------------------------------------------------------- */
var SKIP_NAMES = ['default', 'demo', 'test', 'client'];

function isPersonalName(name) {
  return name && SKIP_NAMES.indexOf(name.toLowerCase()) === -1;
}

var B64_RE = /^[A-Za-z0-9+/=]+$/;
function isValidBase64(s) { return s && B64_RE.test(s); }

/* -----------------------------------------------------------------------
 * iOS button index counter
 * ----------------------------------------------------------------------- */
var iosButtonIndex = 0;

/* -----------------------------------------------------------------------
 * SVG icons (inline, no external requests)
 * ----------------------------------------------------------------------- */
var ICON_COPY = '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
var ICON_QR = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="8" height="8" rx="1"/><rect x="14" y="2" width="8" height="8" rx="1"/><rect x="2" y="14" width="8" height="8" rx="1"/><rect x="14" y="14" width="4" height="4" rx="1"/><line x1="22" y1="14" x2="22" y2="14.01"/><line x1="22" y1="22" x2="22" y2="22.01"/><line x1="18" y1="22" x2="18" y2="22.01"/></svg>';

/* -----------------------------------------------------------------------
 * i18n helper for relay "via" label
 * ----------------------------------------------------------------------- */
function relayViaLabel(name) {
  var dict = currentLang && T[currentLang];
  var tpl = (dict && dict['relay.via']) || 'via {name}';
  return tpl.replace('{name}', name);
}

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
  root.style.setProperty('--accent-bg', accent + '10');
  root.style.setProperty('--accent-br', accent + '28');
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
  var hasRelays = config.relays && config.relays.length > 0;
  var clockStatus = checkClockSkew(config.generated_at);

  /* Apply color palette */
  if (config.color && PALETTES[config.color]) {
    _activePalette = config.color;
    applyPalette(config.color);
  }

  /* Clock warning at TOP if skew detected */
  if (clockStatus === 'bad') {
    html += '<div class="warn clock-warn-urgent">';
    html += '<h3 data-t="clock">Clock Sync Required</h3>';
    html += '<p data-t="clock.desc">Your device clock must be accurate within 30 seconds. Go to Settings &gt; Date &amp; Time &gt; enable "Set Automatically".</p>';
    html += '</div>';
  }

  /* Server icon */
  var iconHtml = '';
  if (config.server_icon) {
    if (config.server_icon.indexOf('data:image/') === 0) {
      iconHtml = '<img class="server-icon" src="' + escapeHtml(config.server_icon) + '" alt="" width="48" height="48">';
    } else {
      iconHtml = '<span class="server-icon-emoji">' + escapeHtml(config.server_icon) + '</span>';
    }
  }

  /* Trust bar */
  var serverName = config.server_name ? escapeHtml(config.server_name) : '';
  var clientName = isPersonalName(config.client_name) ? capitalize(config.client_name) : '';
  html += '<div class="trust-bar">';
  html += '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>';
  if (serverName) {
    html += '<span data-t="trust.server">Secure connection via ' + serverName + '</span>';
  } else {
    html += '<span data-t="trust">This is a secure connection set up for you</span>';
  }
  html += '</div>';

  /* Header */
  var pageTitle = config.server_name ? escapeHtml(config.server_name) : 'Connection Setup';
  var titleExtra = '';
  if (!config.server_name && clientName) {
    titleExtra = ' \u2014 ' + escapeHtml(clientName);
  }
  html += '<div class="hdr">';
  if (iconHtml) {
    html += iconHtml;
  }
  if (config.server_name) {
    html += '<h1>' + pageTitle + '</h1>';
  } else {
    html += '<h1 data-t="title">Connection Setup' + titleExtra + '</h1>';
  }
  if (clientName) {
    html += '<p data-t="subtitle.named">Connection for ' + escapeHtml(clientName) + ' \u2014 scan QR code or tap to open in your app</p>';
  } else {
    html += '<p data-t="subtitle">Scan QR code or tap to open in your app</p>';
  }

  /* Language selector */
  html += '<div class="lang-bar">';
  var langs = [['en', 'English'], ['ru', 'Русский'], ['fa', 'فارسی'], ['zh', '中文']];
  for (var li = 0; li < langs.length; li++) {
    html += '<button class="lang-btn" data-lang="' + langs[li][0] + '" data-action="lang">' + langs[li][1] + '</button>';
  }
  html += '</div>';
  html += '</div>';

  /* PWA install banner */
  html += '<div class="install-banner" id="install-banner">';
  html += '<div class="install-banner-text"><strong data-t="install.title">Install this app</strong><span data-t="install.offline">Access your connection offline</span></div>';
  html += '<button class="install-btn" data-action="install" data-t="install.btn">Install</button>';
  html += '<button class="install-dismiss" data-action="dismiss">&times;</button>';
  html += '</div>';

  /* ---- Subscription QR hero ---- */
  var subUrl = getSubscriptionUrl(config);
  var serverLabel = config.server_name || 'Meridian';
  html += renderImportCard(config.apps, subUrl, platform, serverLabel, config.subscription_qr_b64);

  /* ---- Client Apps (open for first-time visitors, collapsed for returning) ---- */
  var isReturning = 'serviceWorker' in navigator && navigator.serviceWorker.controller;
  html += renderAppsCard(config.apps, platform, isReturning);

  /* ---- Quick Setup (collapsed) ---- */
  html += '<details class="more-options">';
  html += '<summary data-t="setup">Quick Setup</summary>';
  html += '<div class="card" style="margin-top:8px">';
  html += '<div class="steps">';
  html += '<div class="step" data-t="step1">Install a client app from the list above</div>';
  html += '<div class="step" data-t="step2">Tap "Open in App" — the connection imports automatically</div>';
  html += '<div class="step" data-t="step3">Or scan the QR code from another device</div>';
  html += '<div class="step" data-t="step4">Activate the connection in the app</div>';
  html += '</div>';
  html += '</div>';
  html += '</details>';

  /* ---- Individual protocol cards (collapsed — advanced) ---- */
  var allProtocolCards = '';

  if (hasRelays) {
    for (var ri = 0; ri < config.relays.length; ri++) {
      var relay = config.relays[ri];
      for (var rui = 0; rui < relay.urls.length; rui++) {
        allProtocolCards += renderProtocolCard(relay.urls[rui], platform, {
          extraLabel: relayViaLabel(escapeHtml(relay.name || relay.ip)),
          isRelay: true,
        });
      }
    }
    if (config.protocols.length > 0) {
      allProtocolCards += '<div class="section-divider" data-t="backup.direct">BACKUP (DIRECT)</div>';
    }
    for (var pi = 0; pi < config.protocols.length; pi++) {
      allProtocolCards += renderProtocolCard(config.protocols[pi], platform, {
        hasRelays: true,
        isFirst: pi === 0,
      });
    }
  } else {
    for (var pi2 = 0; pi2 < config.protocols.length; pi2++) {
      allProtocolCards += renderProtocolCard(config.protocols[pi2], platform, {
        isFirst: pi2 === 0,
      });
    }
  }

  if (allProtocolCards) {
    html += '<details class="more-options">';
    html += '<summary data-t="more.options">Individual connections</summary>';
    html += '<div class="cards-grid" style="margin-top:8px">' + allProtocolCards + '</div>';
    html += '</details>';
  }

  /* Troubleshooting */
  html += '<div class="warn" style="border-color:var(--amber-br);background:var(--amber-bg)">';
  html += '<h3 data-t="ping">Not connecting?</h3>';
  html += '<p data-t="ping.desc">Check automatic time sync on your device, then try another connection option above.</p>';
  html += '<p data-t="ping.link">Ask the server owner to run meridian test.</p>';
  html += '</div>';

  /* Stats (bottom — informational) */
  html += '<div class="stats" id="stats">';
  html += '<div class="stats-title" data-t="stats">Usage</div>';
  html += '<div class="stats-grid">';
  html += '<div class="stats-item"><div class="stats-val" id="s-up">&mdash;</div><div class="stats-label" data-t="stats.upload">Upload</div></div>';
  html += '<div class="stats-item"><div class="stats-val" id="s-down">&mdash;</div><div class="stats-label" data-t="stats.download">Download</div></div>';
  html += '</div>';
  html += '<div class="stats-active" id="s-active"></div>';
  html += '</div>';

  /* Footer */
  html += '<div class="foot">';
  if (navigator.share) {
    html += '<button class="share-page-btn" data-action="share-page" data-t="share.page">';
    html += '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>';
    html += 'Share this page</button>';
  }
  html += '</div>';

  app.innerHTML = html;

  /* Post-render */
  var primaryUuid = extractUuid(config.protocols);
  if (primaryUuid) loadStats(primaryUuid);
  requestWakeLock();
  applyI18n(config.client_name, config.server_name);
  highlightActiveLang();
}

/* -----------------------------------------------------------------------
 * Card rendering
 * ----------------------------------------------------------------------- */
function renderProtocolCard(proto, platform, opts) {
  opts = opts || {};
  var isBlue = proto.key !== 'wss';
  var colorClass = isBlue ? 'card-blue' : 'card-amber';
  var heroClass = opts.isHero ? ' card-hero' : '';
  var html = '<div class="card ' + colorClass + heroClass + '">';

  /* Label */
  html += '<div class="card-label"><i></i> ';
  if (opts.isRelay) {
    html += '<span>&#9733; ' + escapeHtml(proto.label) + '</span>';
    if (opts.extraLabel) {
      html += '<span class="card-rec">' + opts.extraLabel + '</span>';
    }
  } else if (opts.isFirst && !opts.hasRelays) {
    html += '<span data-t="primary">' + escapeHtml(proto.label) + '</span>';
    if (proto.recommended) {
      html += '<span class="card-rec" data-t="primary.rec">Recommended</span>';
    }
  } else if (proto.key === 'wss') {
    html += '<span data-t="backup">Backup</span>';
  } else if (proto.key === 'xhttp') {
    html += '<span>XHTTP</span>';
  } else {
    html += '<span data-t="primary">' + escapeHtml(proto.label) + '</span>';
  }
  html += '</div>';

  /* Description */
  if (opts.isRelay) {
    html += '<p class="card-desc" data-t="relay.desc">Connect via relay for best reliability in your region.</p>';
  } else if (opts.isFirst && !opts.hasRelays) {
    html += '<p class="card-desc" data-t="primary.desc">Recommended — fastest and most reliable connection. Use this first.</p>';
  } else if (opts.hasRelays && opts.isFirst) {
    html += '<p class="card-desc" data-t="direct.desc">Direct connection — use if relay is unavailable.</p>';
  } else if (proto.key === 'xhttp') {
    html += '<p class="card-desc" data-t="xhttp.desc">Alternative — use if Primary doesn\'t work. More hidden from censors.</p>';
  } else if (proto.key === 'wss') {
    html += '<p class="card-desc" data-t="backup.desc">Legacy CDN fallback — use only if Primary and XHTTP are blocked.</p>';
  } else if (proto.key === 'hysteria2') {
    html += '<p class="card-desc" data-t="hy2.desc">UDP fallback — try when TCP is slow or blocked. May not work on all networks.</p>';
  }

  /* SNI indicator — shows what domain the traffic appears as */
  var sniMatch = proto.url && proto.url.match(/[?&]sni=([^&#]+)/);
  if (sniMatch) {
    html += '<p class="card-sni">Appears as: <strong>' + escapeHtml(decodeURIComponent(sniMatch[1])) + '</strong></p>';
  }

  html += '<div class="card-body">';

  /* QR code */
  if (proto.qr_b64 && isValidBase64(proto.qr_b64)) {
    html += '<div class="qr"><img src="data:image/png;base64,' + proto.qr_b64 + '" alt="QR code" loading="lazy"></div>';
  }

  html += '<div class="card-controls">';

  /* Open in App button */
  html += '<div class="card-actions">';
  if (platform === 'ios') {
    var idx = iosButtonIndex++;
    html += '<a href="#" data-ios-idx="' + idx + '" data-url="' + escapeHtml(proto.url) + '" data-action="open-ios" class="open-btn" data-t="open">Open in App</a>';
  } else {
    html += '<a href="' + escapeHtml(proto.url) + '" class="open-btn" data-t="open">Open in App</a>';
  }
  if (navigator.share) {
    html += '<button class="share-btn" data-action="share" data-url="' + escapeHtml(proto.url) + '" data-t="share">Share</button>';
  }
  html += '</div>';

  /* Secondary actions: copy link */
  html += '<div class="card-tools">';
  html += '<button class="copy-link-btn" data-action="copy" data-url="' + escapeHtml(proto.url) + '">';
  html += ICON_COPY;
  html += '<span data-t="copy.link">Copy link</span>';
  html += '</button>';
  html += '</div>';

  /* Show raw link */
  html += '<details class="url-section"><summary data-t="show.raw">Show raw link</summary>';
  html += '<div class="url" tabindex="0" role="button" data-action="copy" data-url="' + escapeHtml(proto.url) + '">';
  html += escapeHtml(proto.url);
  html += '<span class="url-hint"><span data-t="copy.hint">tap to copy</span></span>';
  html += '</div>';
  html += '</details>';

  html += '</div>'; /* card-controls */
  html += '</div>'; /* card-body */

  html += '</div>'; /* card */
  return html;
}

function renderImportCard(apps, subUrl, platform, serverName, subQrB64) {
  var osMap = {
    ios: 'iOS', android: 'Android',
    windows: 'Windows', macos: 'All platforms', linux: 'All platforms',
  };
  var detectedOs = osMap[platform] || 'All platforms';

  /* Filter: only apps with deep links AND relevant to this platform */
  var deepApps = [];
  var otherDeepApps = [];
  if (apps) {
    for (var i = 0; i < apps.length; i++) {
      if (!apps[i].deeplink) continue;
      if (apps[i].platform === detectedOs || apps[i].platform === 'All platforms') {
        deepApps.push(apps[i]);
      } else {
        otherDeepApps.push(apps[i]);
      }
    }
  }
  if (!deepApps.length && !otherDeepApps.length) {
    /* Fallback: show old-style subscription URL if no deep links */
    var html = '<details class="more-options">';
    html += '<summary data-t="sub.label">Subscription (auto-update)</summary>';
    html += '<div class="card" style="margin-top:8px">';
    html += '<p class="card-desc" data-t="sub.desc">Add this URL as a subscription in your app for automatic updates.</p>';
    html += '<div class="sub-url">';
    html += '<div class="sub-url-value" tabindex="0" role="button" data-action="copy-text">' + escapeHtml(subUrl) + '</div>';
    html += '</div></div></details>';
    return html;
  }

  var heroClass = (subQrB64 && isValidBase64(subQrB64)) ? ' card-hero' : '';
  var html = '<div class="card' + heroClass + '">';

  /* Subscription QR hero — scan to import all protocols */
  if (subQrB64 && isValidBase64(subQrB64)) {
    html += '<div class="qr" style="margin:4px auto 12px"><img src="data:image/png;base64,' + subQrB64 + '" alt="QR" loading="lazy"></div>';
    html += '<p class="card-desc" style="text-align:center" data-t="import.desc.qr">Scan with any V2Ray app or tap to add. Updates automatically.</p>';
  } else {
    html += '<div style="font-size:.78rem;font-weight:600;margin-bottom:4px" data-t="import.label">One-Tap Import</div>';
    html += '<p class="card-desc" data-t="import.desc">Add to your app with one tap. Updates automatically.</p>';
  }

  /* Deep link buttons */
  html += '<div class="import-grid">';

  for (var j = 0; j < deepApps.length; j++) {
    var app = deepApps[j];
    var href = buildDeepLink(app.deeplink, subUrl, serverName);
    html += '<a class="import-btn detected" href="' + escapeHtml(href) + '">';
    if (app.icon) {
      html += '<img class="import-btn-icon" src="' + escapeHtml(app.icon) + '" alt="" width="20" height="20">';
    } else {
      html += '<span class="import-btn-add">+</span> ';
    }
    html += '<span data-t="import.add" data-t-name="' + escapeHtml(app.name) + '">Add to ' + escapeHtml(app.name) + '</span>';
    html += '</a>';
  }

  html += '</div>';

  if (otherDeepApps.length) {
    html += '<details class="more-options" style="margin-top:6px">';
    html += '<summary data-t="import.other">Other platforms</summary>';
    html += '<div class="import-grid" style="margin-top:6px">';
    for (var m = 0; m < otherDeepApps.length; m++) {
      var oa = otherDeepApps[m];
      var ohref = buildDeepLink(oa.deeplink, subUrl, serverName);
      html += '<a class="import-btn" href="' + escapeHtml(ohref) + '">';
      if (oa.icon) {
        html += '<img class="import-btn-icon" src="' + escapeHtml(oa.icon) + '" alt="" width="20" height="20">';
      } else {
        html += '<span class="import-btn-add">+</span> ';
      }
      html += '<span data-t="import.add" data-t-name="' + escapeHtml(oa.name) + '">Add to ' + escapeHtml(oa.name) + '</span>';
      html += '</a>';
    }
    html += '</div></details>';
  }

  /* Collapsed subscription URL */
  html += '<details class="more-options" style="margin-top:8px">';
  html += '<summary data-t="import.manual">Copy subscription URL</summary>';
  html += '<div class="sub-url" style="margin-top:6px">';
  html += '<div class="sub-url-value" tabindex="0" role="button" data-action="copy-text">' + escapeHtml(subUrl) + '</div>';
  html += '</div>';
  html += '</details>';

  html += '</div>';
  return html;
}

function renderAppsCard(apps, platform, isReturning) {
  if (!apps || !apps.length) return '';

  var osMap = {
    ios: 'iOS', android: 'Android',
    windows: 'Windows', macos: 'All platforms', linux: 'All platforms',
  };
  var urlKeyMap = {
    ios: 'iOS', android: 'Android',
    windows: 'Windows', macos: 'macOS', linux: 'Linux',
  };
  var detectedOs = osMap[platform] || 'All platforms';
  var urlKey = urlKeyMap[platform] || '';

  /* Split: relevant apps (matching platform) vs others */
  var relevant = [];
  var others = [];
  for (var i = 0; i < apps.length; i++) {
    var a = apps[i];
    if (a.platform === detectedOs || a.platform === 'All platforms') {
      relevant.push(a);
    } else {
      others.push(a);
    }
  }

  function appUrl(a) {
    return (a.urls && a.urls[urlKey]) || a.url;
  }

  var html = '';
  /* Collapsed for returning visitors, open for first-timers */
  var openAttr = isReturning ? '' : ' open';
  html += '<details class="more-options"' + openAttr + '><summary data-t="apps">Client Apps</summary>';
  html += '<div class="card" style="margin-top:8px">';
  html += '<p class="card-desc" data-t="apps.desc">Install one, then tap an import button above.</p>';
  html += '<div class="apps">';

  for (var j = 0; j < relevant.length; j++) {
    var r = relevant[j];
    html += '<a class="app detected" href="' + escapeHtml(appUrl(r)) + '" target="_blank">';
    if (r.icon) html += '<img class="app-icon" src="' + escapeHtml(r.icon) + '" alt="" width="32" height="32">';
    html += escapeHtml(r.name);
    html += '<span>' + escapeHtml(r.platform) + '</span>';
    html += '</a>';
  }
  html += '</div>';

  if (others.length) {
    html += '<details class="more-options" style="margin-top:6px">';
    html += '<summary data-t="apps.more">Other platforms</summary>';
    html += '<div class="apps" style="margin-top:6px">';
    for (var k = 0; k < others.length; k++) {
      var o = others[k];
      html += '<a class="app" href="' + escapeHtml(appUrl(o)) + '" target="_blank">';
      if (o.icon) html += '<img class="app-icon" src="' + escapeHtml(o.icon) + '" alt="" width="32" height="32">';
      html += escapeHtml(o.name);
      html += '<span>' + escapeHtml(o.platform) + '</span>';
      html += '</a>';
    }
    html += '</div></details>';
  }

  html += '</div>';
  html += '</details>';
  return html;
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
    btn.classList.toggle('active', btn.getAttribute('data-lang') === lang);
  });
}

function applyI18n(clientName, serverName) {
  currentLang = detectLang();
  if (!currentLang) { currentLang = 'en'; highlightActiveLang(); updatePageMeta(clientName, serverName); return; }
  var dict = T[currentLang];
  if (!dict) { highlightActiveLang(); updatePageMeta(clientName, serverName); return; }
  document.querySelectorAll('[data-t]').forEach(function(el) {
    var k = el.dataset.t;
    var v = dict[k];
    if (v) {
      if (k === 'trust.server' && serverName) {
        el.textContent = v.replace('{name}', serverName);
      } else if (k === 'subtitle.named' && isPersonalName(clientName)) {
        el.textContent = v.replace('{name}', capitalize(clientName));
      } else if (k === 'import.add' && el.dataset.tName) {
        el.textContent = v.replace('{name}', el.dataset.tName);
      } else {
        el.textContent = v;
      }
      if (k === 'title') {
        if (serverName) {
          el.textContent = serverName;
        } else if (isPersonalName(clientName)) {
          el.textContent += ' \u2014 ' + capitalize(clientName);
        }
      }
    }
  });
  if (currentLang === 'fa') {
    document.documentElement.dir = 'rtl';
  } else {
    document.documentElement.dir = 'ltr';
  }
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
      titleText += ' \u2014 ' + capitalize(clientName);
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

function extractUuid(protocols) {
  if (!protocols || !protocols.length) return '';
  for (var i = 0; i < protocols.length; i++) {
    var m = protocols[i].url.match(/vless:\/\/([0-9a-fA-F-]{36})@/);
    if (m && protocols[i].url.indexOf('reality') > -1) return m[1];
  }
  var fm = protocols[0].url.match(/vless:\/\/([0-9a-fA-F-]{36})@/);
  return fm ? fm[1] : '';
}

/* -----------------------------------------------------------------------
 * Service Worker + Persistent storage
 * ----------------------------------------------------------------------- */
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('../pwa/sw.js', { scope: '../' }).catch(function() {});
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
  } else if (action === 'copy-text') {
    copyToClipboard(target.textContent);
  } else if (action === 'share') {
    var shareUrlVal = target.getAttribute('data-url');
    if (shareUrlVal) shareUrl(shareUrlVal);
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

/* Keyboard support for click-to-copy elements */
document.addEventListener('keydown', function(e) {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  var target = e.target.closest('[data-action]');
  if (!target) return;
  var action = target.getAttribute('data-action');
  if (action === 'copy' || action === 'copy-text') {
    e.preventDefault();
    target.click();
  }
});

/* -----------------------------------------------------------------------
 * Expose globals (minimal — only for SW/PWA install APIs)
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
    if (!app) return;
    var existing = app.querySelector('.load-slow');
    if (existing) return;
    var dict = currentLang && T[currentLang];
    var msg = (dict && dict['error.slow']) || 'Taking longer than expected\u2026';
    var el = document.createElement('div');
    el.className = 'load-slow';
    el.style.cssText = 'text-align:center;color:var(--tx2);font-size:.82rem;padding:12px';
    el.textContent = msg;
    app.appendChild(el);
  }, 10000);

  fetch('config.json')
    .then(function(r) {
      if (!r.ok) throw new Error('config.json fetch failed');
      return r.json();
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
        app.innerHTML = '<div class="hdr"><h1>' + escapeHtml(errTitle) + '</h1>' +
          '<p style="color:var(--tx2)">' + escapeHtml(errMsg) + '</p>' +
          '<button class="open-btn" style="margin-top:12px" data-action="retry">' + escapeHtml(errRetry) + '</button></div>';
      }
    });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

})();
