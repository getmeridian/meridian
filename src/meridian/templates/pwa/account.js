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
    checked: 'Checked {time}', saved: 'Last saved {time}. These details are unverified.',
    failed: 'Could not check your account. Try refreshing when you are online.',
    elsewhere: 'This page cannot check an account hosted at another address.',
    'days.one': '{n} day left', 'days.other': '{n} days left',
    units: ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'],
  },
  ru: {
    title: 'Ваш аккаунт', refresh: 'Обновить', checking: 'Проверяем аккаунт…',
    status: 'Статус', active: 'Активен', disabled: 'Отключён', expired: 'Истёк', limited: 'Лимит исчерпан',
    'active.desc': 'Аккаунт готов к подключению.',
    'disabled.desc': 'Владелец подключения отключил ваш аккаунт. Попросите его включить аккаунт.',
    'expired.desc': 'Срок действия аккаунта истёк. Попросите того, кто поделился подключением, продлить его.',
    'limited.desc': 'Лимит трафика исчерпан. Уточните у того, кто поделился подключением, когда появится новый лимит.',
    missing: 'Эта подписка больше недоступна. Обратитесь к тому, кто поделился с вами подключением.',
    period: 'Трафик за текущий период', lifetime: 'Трафик за всё время', expiry: 'Срок действия', unlimited: 'Без лимита', never: 'Без срока',
    checked: 'Проверено: {time}', saved: 'Сохранено: {time}. Эти данные не подтверждены.',
    failed: 'Не удалось проверить аккаунт. Попробуйте обновить данные, когда появится интернет.',
    elsewhere: 'Аккаунт размещён по другому адресу; с этой страницы его не проверить.',
    'days.one': 'Остался {n} день', 'days.few': 'Осталось {n} дня', 'days.many': 'Осталось {n} дней', 'days.other': 'Осталось {n} дня',
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
    checked: 'بررسی‌شده در {time}', saved: 'آخرین ذخیره در {time}. این اطلاعات تأیید نشده‌اند.',
    failed: 'بررسی حساب ممکن نشد. وقتی به اینترنت وصل شدید، دوباره به‌روزرسانی کنید.',
    elsewhere: 'حساب در نشانی دیگری میزبانی می‌شود و از این صفحه نمی‌توان آن را بررسی کرد.',
    'days.one': '{n} روز باقی مانده', 'days.other': '{n} روز باقی مانده',
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
    checked: '检查时间：{time}', saved: '上次保存于 {time}。这些信息尚未核实。',
    failed: '无法检查账户。请联网后重试刷新。',
    elsewhere: '账户托管在其他地址，此页面无法检查。',
    'days.one': '剩余 {n} 天', 'days.other': '剩余 {n} 天',
    units: ['字节', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'],
  },
};
var INFO_TIMEOUT_MS = 10000;
var snapshot = null, verified = false, checking = false, failed = false;
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
function days(value) {
  var plural = value === 1 ? 'one' : 'other';
  try { if (Intl.PluralRules) plural = new Intl.PluralRules(lang()).select(value); } catch (e) {}
  return t('days.' + (TEXT[lang()]['days.' + plural] ? plural : 'other'), { n: number(value) });
}
function decimal(value) { return typeof value === 'string' && /^(0|[1-9][0-9]{0,19})$/.test(value); }
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
  if (!validSnapshot(answer)) throw new Error('account');
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
  panel.hidden = !window._meridianConfig || !window._meridianConfig.subscription_url;
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
    var never = /^2099-12-31T/.test(snapshot.expiresAt);
    metric(list, t('expiry'), never ? t('never') : date(snapshot.expiresAt), never ? '' : days(snapshot.daysLeft));
    panel.appendChild(list);
  } else if (snapshot) panel.appendChild(element('p', 'muted', t('missing')));
  if (checking) panel.appendChild(element('p', 'account-freshness', t('checking')));
  if (snapshot) panel.appendChild(element('p', 'account-freshness', t(verified ? 'checked' : 'saved', { time: date(snapshot.checkedAt, true) })));
  if (failed) panel.appendChild(element('p', 'account-freshness account-unverified', t(infoUrl ? 'failed' : 'elsewhere')));
  applyImportState();
}
function refresh() {
  if (checking || !infoUrl) { if (!infoUrl) { failed = true; render(); } return; }
  checking = true; failed = false; verified = false; render();
  var id = ++requestId, done = false;
  var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  function finish(answer) {
    if (done || id !== requestId) return;
    done = true; clearTimeout(timer); checking = false; failed = !answer;
    if (answer) {
      snapshot = answer; verified = true;
      /* Only this projection is saved: no links, IPs, username, or device data. */
      try { localStorage.setItem(storageKey, JSON.stringify(snapshot)); } catch (e) {}
    }
    render();
  }
  var timer = setTimeout(function() { if (controller) controller.abort(); finish(null); }, INFO_TIMEOUT_MS);
  fetch(infoUrl, { method: 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller ? controller.signal : undefined })
    .then(function(response) { return response.json().then(function(body) { return readInfo(body, response.status); }); })
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
    requestId++; checking = false; snapshot = null; verified = false; failed = false; infoUrl = target;
    /* The path scopes the snapshot to this subscription without saving its host. */
    storageKey = target ? 'meridian-account:' + new URL(target).pathname : '';
    try {
      var saved = JSON.parse(localStorage.getItem(storageKey));
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
