/* Account information, independent of the visitor's connection self-check. */
(function() {
'use strict';

var TEXT = {
  en: {
    title: 'Your account', refresh: 'Refresh', checking: 'Checking account…',
    status: 'Status', active: 'Active', disabled: 'Disabled', expired: 'Expired', limited: 'Limited',
    'active.desc': 'Your account is ready to connect.',
    'disabled.desc': 'The person who manages this connection has disabled your account. Ask them to enable it.',
    'expired.desc': 'Your account has expired. Ask the person who shared this connection to renew it.',
    'limited.desc': 'Your traffic limit has been reached. Ask the person who shared this connection about the next allowance.',
    missing: 'This subscription is no longer available. Ask the person who shared this connection with you.',
    period: 'Traffic this period', lifetime: 'Lifetime traffic', expiry: 'Expiry', unlimited: 'Unlimited', never: 'No expiry',
    slots: 'Device slots', 'slots.hint': 'When slots are full, new devices get a limit notice on subscription refresh. Apps that do not report a device are not counted.',
    checked: 'Checked {time}', saved: 'Last saved {time}. These details are unverified.',
    failed: 'Could not check your account. Try refreshing when you are online.',
    'days.one': '{n} day left', 'days.other': '{n} days left',
    'valid.one': 'Valid for {n} day from first use', 'valid.other': 'Valid for {n} days from first use',
    firstUseNote: 'The duration starts with your first traffic. Opening this page or refreshing the subscription does not start it.',
    nextReset: 'Next reset', nextRenewal: 'Next renewal', renewalsLeft: 'Renewals left',
    waiting: 'Waiting for first use', noRenewals: 'No more renewals', unlimitedRenewals: 'Unlimited renewals',
    units: ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'],
  },
  ru: {
    title: 'Ваш аккаунт', refresh: 'Обновить', checking: 'Проверяем статус…',
    status: 'Статус', active: 'Активен', disabled: 'Отключён', expired: 'Срок истёк', limited: 'Лимит исчерпан',
    'active.desc': 'Всё готово к подключению.',
    'disabled.desc': 'Владелец сервера приостановил ваш доступ. Попросите его снова включить подключение.',
    'expired.desc': 'Срок доступа истёк. Попросите того, кто поделился с вами ссылкой, продлить его.',
    'limited.desc': 'Лимит трафика на этот период исчерпан. Уточните у того, кто поделился с вами подключением, когда появится новый лимит.',
    missing: 'Эта подписка больше не действует. Обратитесь к тому, кто поделился с вами подключением.',
    period: 'Трафик за текущий период', lifetime: 'Трафик за всё время', expiry: 'Срок доступа', unlimited: 'Без лимита', never: 'Бессрочно',
    slots: 'Места для устройств', 'slots.hint': 'Когда все места заняты, новое устройство при обновлении подписки получает сообщение о лимите. Приложения без идентификатора устройства не учитываются.',
    checked: 'Обновлено: {time}', saved: 'Сохранено: {time}. Данные могли устареть.',
    failed: 'Не удалось обновить статус. Попробуйте ещё раз, когда появится интернет.',
    'days.one': 'Остался {n} день', 'days.few': 'Осталось {n} дня', 'days.many': 'Осталось {n} дней', 'days.other': 'Осталось {n} дня',
    'valid.one': 'Доступ на {n} день с первого использования', 'valid.few': 'Доступ на {n} дня с первого использования',
    'valid.many': 'Доступ на {n} дней с первого использования', 'valid.other': 'Доступ на {n} дня с первого использования',
    firstUseNote: 'Отсчёт начнётся с первого трафика. Открытие страницы и обновление подписки его не запускают.',
    nextReset: 'Следующий сброс трафика', nextRenewal: 'Следующее продление', renewalsLeft: 'Осталось продлений',
    waiting: 'Ожидается первое использование', noRenewals: 'Продлений больше нет', unlimitedRenewals: 'Без ограничения числа продлений',
    units: ['Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ', 'ПиБ'],
  },
  fa: {
    title: 'حساب شما', refresh: 'به‌روزرسانی', checking: 'در حال بررسی حساب…',
    status: 'وضعیت', active: 'فعال', disabled: 'غیرفعال', expired: 'منقضی‌شده', limited: 'سهمیه تمام شده',
    'active.desc': 'حساب شما آمادهٔ اتصال است.',
    'disabled.desc': 'مدیر این اتصال، حساب شما را غیرفعال کرده است. از او بخواهید حساب را فعال کند.',
    'expired.desc': 'اعتبار حساب شما تمام شده است. از کسی که اتصال را با شما به اشتراک گذاشته بخواهید آن را تمدید کند.',
    'limited.desc': 'سهمیهٔ ترافیک شما تمام شده است. دربارهٔ سهمیهٔ بعدی از کسی که اتصال را با شما به اشتراک گذاشته بپرسید.',
    missing: 'این اشتراک دیگر در دسترس نیست. از کسی که اتصال را با شما به اشتراک گذاشته بپرسید.',
    period: 'ترافیک این دوره', lifetime: 'کل ترافیک مصرف‌شده', expiry: 'پایان اعتبار', unlimited: 'نامحدود', never: 'بدون تاریخ انقضا',
    slots: 'ظرفیت دستگاه‌ها', 'slots.hint': 'وقتی ظرفیت پر باشد، دستگاه جدید هنگام تازه‌سازی اشتراک پیام محدودیت می‌گیرد. برنامه‌هایی که شناسهٔ دستگاه نمی‌فرستند شمرده نمی‌شوند.',
    checked: 'بررسی‌شده در {time}', saved: 'آخرین ذخیره در {time}. این اطلاعات تأیید نشده‌اند.',
    failed: 'بررسی حساب ممکن نشد. وقتی به اینترنت وصل شدید، دوباره به‌روزرسانی کنید.',
    'days.one': '{n} روز باقی مانده', 'days.other': '{n} روز باقی مانده',
    'valid.one': 'از اولین استفاده به‌مدت {n} روز معتبر است', 'valid.other': 'از اولین استفاده به‌مدت {n} روز معتبر است',
    firstUseNote: 'مدت اعتبار با اولین ترافیک شما آغاز می‌شود. باز کردن این صفحه یا به‌روزرسانی اشتراک آن را آغاز نمی‌کند.',
    nextReset: 'بازنشانی بعدی', nextRenewal: 'تمدید بعدی', renewalsLeft: 'تمدیدهای باقی‌مانده',
    waiting: 'در انتظار اولین استفاده', noRenewals: 'تمدید دیگری باقی نمانده است', unlimitedRenewals: 'تمدید نامحدود',
    units: ['بایت', 'کیبی‌بایت', 'مبی‌بایت', 'گیبی‌بایت', 'تبی‌بایت', 'پبی‌بایت'],
  },
  zh: {
    title: '您的账户', refresh: '刷新', checking: '正在检查账户…',
    status: '状态', active: '有效', disabled: '已停用', expired: '已到期', limited: '流量已用完',
    'active.desc': '账户可正常使用，您可以连接。',
    'disabled.desc': '此连接的管理者已停用您的账户。请联系对方重新启用。',
    'expired.desc': '您的账户已到期。请联系向您分享此连接的人续期。',
    'limited.desc': '您的流量额度已用完。请联系向您分享此连接的人，了解下一次额度何时可用。',
    missing: '此订阅已不可用。请联系向您分享此连接的人。',
    period: '本期流量', lifetime: '累计流量', expiry: '到期时间', unlimited: '不限流量', never: '永久有效',
    slots: '设备名额', 'slots.hint': '名额用满后，新设备刷新订阅时会收到上限提示。不报告设备标识的应用不计数。',
    checked: '检查时间：{time}', saved: '上次保存于 {time}。这些信息尚未核实。',
    failed: '无法检查账户。请联网后重试刷新。',
    'days.one': '剩余 {n} 天', 'days.other': '剩余 {n} 天',
    'valid.one': '首次使用后有效 {n} 天', 'valid.other': '首次使用后有效 {n} 天',
    firstUseNote: '首次产生流量时开始计时。打开此页面或刷新订阅不会开始计时。',
    nextReset: '下次流量重置', nextRenewal: '下次续期', renewalsLeft: '剩余续期次数',
    waiting: '等待首次使用', noRenewals: '不再续期', unlimitedRenewals: '不限续期次数',
    units: ['字节', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'],
  },
};
var INFO_TIMEOUT_MS = 10000;
var snapshot = null, verified = false, checking = false, failed = false;
var deviceSlots = null;
var infoUrl = '', storageKey = '', requestId = 0;

function lang() { return TEXT[document.documentElement.lang] ? document.documentElement.lang : 'en'; }
function t(key, vars) {
  var text = TEXT[lang()][key] || TEXT.en[key];
  return String(text).replace(/\{(\w+)\}/g, function(m, name) { return vars && vars[name] !== undefined ? vars[name] : m; });
}
function number(value, decimals) {
  try { return new Intl.NumberFormat(lang(), { maximumFractionDigits: decimals || 0 }).format(value); }
  catch (e) { return String(value); }
}
function date(value, withTime) {
  var options = { year: 'numeric', month: 'long', day: 'numeric' };
  if (withTime) { options.hour = '2-digit'; options.minute = '2-digit'; }
  /* Intl's fa locale selects the Persian calendar and digits. */
  try { return new Intl.DateTimeFormat(lang() === 'fa' ? 'fa-IR-u-ca-persian' : lang(), options).format(new Date(value)); }
  catch (e) { return new Date(value).toISOString().slice(0, 10); }
}
function bytes(value) {
  var count = Number(value), unit = 0;
  while (count >= 1024 && unit < TEXT.en.units.length - 1) { count /= 1024; unit++; }
  return number(count, 2) + ' ' + TEXT[lang()].units[unit];
}
function pluralText(prefix, value) {
  var plural = value === 1 ? 'one' : 'other';
  try { if (Intl.PluralRules) plural = new Intl.PluralRules(lang()).select(value); } catch (e) {}
  return t(prefix + '.' + (TEXT[lang()][prefix + '.' + plural] ? plural : 'other'), { n: number(value) });
}
function days(value) { return pluralText('days', value); }
function decimal(value) { return typeof value === 'string' && /^(0|[1-9][0-9]{0,19})$/.test(value); }
function readSlots(value) {
  if (!value || typeof value !== 'object' || typeof value.used !== 'number' || typeof value.allowed !== 'number' ||
      !isFinite(value.used) || Math.floor(value.used) !== value.used || value.used < 0 || value.used > 1000000 ||
      Math.floor(value.allowed) !== value.allowed || value.allowed < 1 || value.allowed > 100) return null;
  return { used: value.used, allowed: value.allowed };
}
function utcTime(value) {
  return value === null || typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value) && isFinite(Date.parse(value));
}
function whole(value, minimum, maximum) {
  return typeof value === 'number' && Math.floor(value) === value && value >= minimum && value <= maximum;
}
function validSchedule(value) {
  return value === undefined || value === null || value && typeof value === 'object' &&
    typeof value.pending === 'boolean' && typeof value.automatic === 'boolean' &&
    (value.duration === null || whole(value.duration, 1, 3650)) && (!value.pending || value.duration !== null) &&
    utcTime(value.nextResetAt) && utcTime(value.nextRenewalAt) &&
    (value.renewalsLeft === null || whole(value.renewalsLeft, 0, 10000));
}
function readSchedule(value) {
  if (!value || typeof value !== 'object') return null;
  var schedule = {
    pending: value.activation_pending, duration: value.activation_duration_days, automatic: value.renewal_enabled,
    nextResetAt: value.next_reset_at, nextRenewalAt: value.next_renewal_at, renewalsLeft: value.renewals_left,
  };
  return validSchedule(schedule) ? schedule : null;
}
function validSnapshot(value) {
  if (!value || typeof value !== 'object' || typeof value.found !== 'boolean' || !isFinite(value.checkedAt) || value.checkedAt <= 0) return false;
  if (!value.found) return true;
  return ['active', 'disabled', 'expired', 'limited'].indexOf(value.status) !== -1 &&
    decimal(value.used) && decimal(value.limit) && decimal(value.lifetime) &&
    typeof value.expiresAt === 'string' && isFinite(Date.parse(value.expiresAt)) &&
    typeof value.daysLeft === 'number' && value.daysLeft >= 0 && value.daysLeft <= 365250 && Math.floor(value.daysLeft) === value.daysLeft;
}
function readInfo(body, status) {
  if (status === 404 && body && body.isFound === false) return { found: false, checkedAt: Date.now() };
  if (status !== 200 || !body || body.isFound !== true || !body.user) throw new Error('account');
  var user = body.user;
  var answer = {
    found: true, status: typeof user.userStatus === 'string' ? user.userStatus.toLowerCase() : '',
    used: user.trafficUsedBytes, limit: user.trafficLimitBytes, lifetime: user.lifetimeTrafficUsedBytes,
    expiresAt: user.expiresAt, daysLeft: user.daysLeft, checkedAt: Date.now(),
  };
  var schedule = readSchedule(user.accountSchedule);
  if (schedule) answer.schedule = schedule;
  if (!validSnapshot(answer)) throw new Error('account');
  answer.deviceSlots = readSlots(user.deviceSlots);
  return answer;
}
function element(tag, cls, text) {
  var el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
function metric(list, label, value, note) {
  var row = element('div', 'account-metric');
  row.appendChild(element('dt', '', label));
  row.appendChild(element('dd', '', value));
  if (note) row.appendChild(element('dd', 'account-note', note));
  list.appendChild(row);
}
function suspension() {
  if (!snapshot) return '';
  if (!snapshot.found) return t('missing');
  return snapshot.status === 'active' ? '' : t(snapshot.status + '.desc');
}
function applyImportState() {
  var reason = suspension();
  document.querySelectorAll('[data-import]').forEach(function(link) {
    if (reason) link.setAttribute('aria-disabled', 'true');
    else link.removeAttribute('aria-disabled');
  });
  var notice = document.getElementById('import-status');
  if (notice) { notice.textContent = reason; notice.hidden = !reason; }
}
function render() {
  var panel = document.getElementById('account-panel');
  if (!panel) return;
  panel.innerHTML = '';
  /* A subscription on another origin cannot be read from here; say nothing rather than alarm. */
  panel.hidden = !infoUrl;
  if (panel.hidden) return;
  panel.setAttribute('aria-busy', checking ? 'true' : 'false');
  var heading = element('div', 'account-heading');
  heading.appendChild(element('h2', '', t('title')));
  var button = element('button', 'btn btn-ghost btn-sm', t('refresh'));
  button.setAttribute('data-action', 'account-refresh'); button.disabled = checking;
  heading.appendChild(button); panel.appendChild(heading);
  if (snapshot && snapshot.found) {
    panel.appendChild(element('strong', 'account-status is-' + snapshot.status, t(snapshot.status)));
    panel.appendChild(element('p', 'muted', t(snapshot.status + '.desc')));
    var list = element('dl', 'account-metrics');
    metric(list, t('period'), bytes(snapshot.used) + ' / ' + (snapshot.limit === '0' ? t('unlimited') : bytes(snapshot.limit)));
    metric(list, t('lifetime'), bytes(snapshot.lifetime));
    if (verified && deviceSlots) {
      metric(list, t('slots'), number(deviceSlots.used) + ' / ' + number(deviceSlots.allowed), t('slots.hint'));
    }
    var never = /^2099-12-31T/.test(snapshot.expiresAt);
    var schedule = snapshot.schedule;
    if (schedule && schedule.pending) {
      metric(list, t('expiry'), pluralText('valid', schedule.duration), t('firstUseNote'));
    } else {
      metric(list, t('expiry'), never ? t('never') : date(snapshot.expiresAt, true),
        never ? '' : days(snapshot.daysLeft) + ' · ' + snapshot.expiresAt + ' UTC');
    }
    if (schedule && schedule.nextResetAt) metric(list, t('nextReset'), date(schedule.nextResetAt, true), schedule.nextResetAt + ' UTC');
    if (schedule && schedule.automatic) {
      metric(list, t('nextRenewal'), schedule.nextRenewalAt ? date(schedule.nextRenewalAt, true) :
        t(schedule.pending ? 'waiting' : 'noRenewals'), schedule.nextRenewalAt ? schedule.nextRenewalAt + ' UTC' : '');
      metric(list, t('renewalsLeft'), schedule.renewalsLeft === null ? t('unlimitedRenewals') : number(schedule.renewalsLeft));
    }
    panel.appendChild(list);
  } else if (snapshot) panel.appendChild(element('p', 'muted', t('missing')));
  if (checking) panel.appendChild(element('p', 'account-freshness', t('checking')));
  if (snapshot) panel.appendChild(element('p', 'account-freshness', t(verified ? 'checked' : 'saved', { time: date(snapshot.checkedAt, true) })));
  if (failed) panel.appendChild(element('p', 'account-freshness account-unverified', t('failed')));
  applyImportState();
}
function refresh() {
  if (checking || !infoUrl) { if (!infoUrl) render(); return; }
  checking = true; failed = false; verified = false; render();
  var id = ++requestId, done = false, notice = null;
  var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  function finish(answer) {
    if (done || id !== requestId) return;
    done = true; clearTimeout(timer); checking = false; failed = !answer;
    if (answer) {
      deviceSlots = answer.deviceSlots || null;
      delete answer.deviceSlots;
      snapshot = answer; verified = true;
      if (window._meridianConfig) window._meridianConfig.notice = notice;
      if (window.MeridianNotice) window.MeridianNotice.mount(document.getElementById('owner-notice'), notice, lang());
      /* Only this projection is saved: no links, IPs, username, or device data. */
      try { localStorage.setItem(storageKey, JSON.stringify(snapshot)); } catch (e) {}
    }
    render();
  }
  var timer = setTimeout(function() { if (controller) controller.abort(); finish(null); }, INFO_TIMEOUT_MS);
  fetch(infoUrl, { method: 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller ? controller.signal : undefined })
    .then(function(response) { return response.json().then(function(body) {
      var answer = readInfo(body, response.status);
      notice = body.notice || null;
      return answer;
    }); })
    .then(finish, function() { finish(null); });
}
function bindPage() {
  var config = window._meridianConfig;
  var target = '';
  try {
    if (config && config.subscription_url) {
      var url = new URL(config.subscription_url, location.href);
      if (url.origin === location.origin && !url.username && !url.password) {
        url.hash = ''; url.pathname = url.pathname.replace(/\/+$/, '') + '/info'; target = url.href;
      }
    }
  } catch (e) {}
  if (target !== infoUrl || !storageKey) {
    requestId++; checking = false; snapshot = null; deviceSlots = null; verified = false; failed = false; infoUrl = target;
    /* The path scopes the snapshot to this subscription without saving its host. */
    storageKey = target ? 'meridian-account:' + new URL(target).pathname : '';
    try {
      var saved = JSON.parse(localStorage.getItem(storageKey));
      if (saved && !validSchedule(saved.schedule)) delete saved.schedule;
      if (validSnapshot(saved)) snapshot = saved;
    } catch (e) {}
    refresh();
  } else render();
}

document.addEventListener('meridian-render', bindPage);
document.addEventListener('meridian-setup', applyImportState);
document.addEventListener('visibilitychange', function() { if (document.visibilityState === 'visible') refresh(); });
window.addEventListener('pageshow', function(event) { if (event.persisted) refresh(); });
document.addEventListener('click', function(event) {
  var link = event.target.closest('[data-import]');
  if (link && link.getAttribute('aria-disabled') === 'true') event.preventDefault();
  var button = event.target.closest('[data-action="account-refresh"]');
  if (button) refresh();
});
bindPage();
})();
