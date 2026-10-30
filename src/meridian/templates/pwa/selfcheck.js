/* PWA — Connection self-check, run from the visitor's own network */
(function() {
'use strict';

/* Mirrors meridian/diagnostics/blocking.py: TSPU-style throttling freezes a
   flagged flow after roughly 14-24 KB, so a freeze between THROTTLE_LOW_BYTES
   and STALL_CLEARANCE_BYTES is the throttling signature. */
var STALL_CLEARANCE_BYTES = 32 * 1024;
var THROTTLE_LOW_BYTES = 8 * 1024;
var STALL_TIMEOUT_MS = 5000;
var HEADERS_TIMEOUT_MS = 10000;
var FLOW_LIMIT_MS = 30000;
var SUB_TIMEOUT_MS = 10000;
var CLOCK_WARN_MS = 60000;
var PROBE_PATH = (window.meridianPwaAssetPath || '../pwa') + '/probe.bin';
/* Replaced with the installed asset hash, like the SW cache version */
var PAGE_VERSION = 'pwa-v1';

var S = {
  en: {
    'clock.label': 'Device clock',
    'clock.ok': 'Correct (off by {t})',
    'clock.ahead': 'Ahead by {t}. Turn on automatic date and time in your settings; connections can fail when the clock is wrong.',
    'clock.behind': 'Behind by {t}. Turn on automatic date and time in your settings; connections can fail when the clock is wrong.',
    'clock.skip': 'Could not read the server’s time',
    'flow.label': 'Data flow',
    'flow.ok': 'Received {kb} KB without a freeze, first byte after {ms} ms',
    'flow.throttled': 'Froze after {kb} KB, the signature of throttling. Tell whoever runs your server; it may need a new IP address.',
    'flow.stopped': 'Stopped after {kb} KB',
    'flow.slow': 'Very slow: {kb} KB in 30 s, but no freeze',
    'flow.small': 'The server sent too little to judge ({kb} KB)',
    'flow.missing': 'This server does not have the test file yet; ask whoever runs it to update Meridian',
    'flow.unreachable': 'Could not start a transfer from this network',
    'flow.unsupported': 'This browser cannot run this test',
    'sub.label': 'Subscription',
    'sub.ok': 'Answers (HTTP {code} in {ms} ms)',
    'sub.status': 'Answered with HTTP {code}',
    'sub.timeout': 'No answer within {s} s',
    'sub.error': 'Not reachable from this network',
    'sub.none': 'No subscription link on this page',
    'sub.elsewhere': 'Hosted at another address, so this page cannot check it',
    running: 'Checking…',
    copy: 'Copy report',
    'report.hint': 'Send the report to whoever runs your server. It contains no links or keys.',
    'unit.s': 's', 'unit.min': 'min', 'unit.h': 'h',
  },
  ru: {
    'clock.label': 'Часы устройства',
    'clock.ok': 'Идут верно (расхождение {t})',
    'clock.ahead': 'Спешат на {t}. Включите автоматическую установку даты и времени в настройках: при неверных часах подключение может не работать.',
    'clock.behind': 'Отстают на {t}. Включите автоматическую установку даты и времени в настройках: при неверных часах подключение может не работать.',
    'clock.skip': 'Не удалось узнать время сервера',
    'flow.label': 'Передача данных',
    'flow.ok': 'Получено {kb} КБ без зависаний, первый байт через {ms} мс',
    'flow.throttled': 'Передача зависла после {kb} КБ — так выглядит замедление. Сообщите тому, кто обслуживает ваш сервер: возможно, нужен новый IP-адрес.',
    'flow.stopped': 'Передача оборвалась после {kb} КБ',
    'flow.slow': 'Очень медленно: {kb} КБ за 30 с, но без зависаний',
    'flow.small': 'Сервер прислал слишком мало данных для вывода ({kb} КБ)',
    'flow.missing': 'На сервере пока нет тестового файла: попросите того, кто его обслуживает, обновить Meridian',
    'flow.unreachable': 'Не удалось начать передачу из этой сети',
    'flow.unsupported': 'Этот браузер не умеет выполнять такую проверку',
    'sub.label': 'Подписка',
    'sub.ok': 'Отвечает (HTTP {code} за {ms} мс)',
    'sub.status': 'Ответила кодом HTTP {code}',
    'sub.timeout': 'Нет ответа за {s} с',
    'sub.error': 'Недоступна из этой сети',
    'sub.none': 'На этой странице нет ссылки на подписку',
    'sub.elsewhere': 'Подписка размещена по другому адресу, отсюда её не проверить',
    running: 'Проверяем…',
    copy: 'Скопировать отчёт',
    'report.hint': 'Отправьте отчёт тому, кто обслуживает ваш сервер. В нём нет ссылок и ключей.',
    'unit.s': 'с', 'unit.min': 'мин', 'unit.h': 'ч',
  },
  fa: {
    'clock.label': 'ساعت دستگاه',
    'clock.ok': 'درست است (اختلاف {t})',
    'clock.ahead': '{t} جلوتر است. تاریخ و ساعت خودکار را در تنظیمات روشن کنید؛ با ساعت نادرست ممکن است اتصال برقرار نشود.',
    'clock.behind': '{t} عقب‌تر است. تاریخ و ساعت خودکار را در تنظیمات روشن کنید؛ با ساعت نادرست ممکن است اتصال برقرار نشود.',
    'clock.skip': 'زمان سرور خوانده نشد',
    'flow.label': 'جریان داده',
    'flow.ok': '{kb} کیلوبایت بدون توقف دریافت شد، اولین بایت پس از {ms} میلی‌ثانیه',
    'flow.throttled': 'پس از {kb} کیلوبایت متوقف شد؛ این نشانهٔ محدودسازی سرعت است. به مدیر سرور خبر دهید؛ شاید به نشانی IP تازه نیاز باشد.',
    'flow.stopped': 'پس از {kb} کیلوبایت قطع شد',
    'flow.slow': 'بسیار کند: {kb} کیلوبایت در ۳۰ ثانیه، ولی بدون توقف',
    'flow.small': 'سرور دادهٔ کافی برای داوری نفرستاد ({kb} کیلوبایت)',
    'flow.missing': 'این سرور هنوز فایل آزمایشی را ندارد؛ از مدیر سرور بخواهید Meridian را به‌روز کند',
    'flow.unreachable': 'شروع انتقال از این شبکه ممکن نشد',
    'flow.unsupported': 'این مرورگر نمی‌تواند این آزمون را اجرا کند',
    'sub.label': 'اشتراک',
    'sub.ok': 'پاسخ می‌دهد (HTTP {code} در {ms} میلی‌ثانیه)',
    'sub.status': 'با HTTP {code} پاسخ داد',
    'sub.timeout': 'در {s} ثانیه پاسخی نیامد',
    'sub.error': 'از این شبکه در دسترس نیست',
    'sub.none': 'این صفحه پیوند اشتراک ندارد',
    'sub.elsewhere': 'اشتراک در نشانی دیگری است و این صفحه نمی‌تواند آن را بررسی کند',
    running: 'در حال بررسی…',
    copy: 'کپی گزارش',
    'report.hint': 'گزارش را برای مدیر سرور خود بفرستید. هیچ پیوند یا کلیدی در آن نیست.',
    'unit.s': 'ثانیه', 'unit.min': 'دقیقه', 'unit.h': 'ساعت',
  },
  zh: {
    'clock.label': '设备时钟',
    'clock.ok': '正常（相差 {t}）',
    'clock.ahead': '快了 {t}。请在设置中开启自动设置日期和时间；时钟不准可能导致连接失败。',
    'clock.behind': '慢了 {t}。请在设置中开启自动设置日期和时间；时钟不准可能导致连接失败。',
    'clock.skip': '无法读取服务器时间',
    'flow.label': '数据传输',
    'flow.ok': '已接收 {kb} KB，没有卡住，首字节用时 {ms} 毫秒',
    'flow.throttled': '在 {kb} KB 后卡住，这是限速的典型特征。请告知服务器管理员，可能需要更换 IP 地址。',
    'flow.stopped': '在 {kb} KB 后中断',
    'flow.slow': '非常慢：30 秒内仅 {kb} KB，但没有卡住',
    'flow.small': '服务器发送的数据太少，无法判断（{kb} KB）',
    'flow.missing': '此服务器还没有测试文件；请让服务器管理员更新 Meridian',
    'flow.unreachable': '无法从此网络开始传输',
    'flow.unsupported': '此浏览器无法运行该检查',
    'sub.label': '订阅',
    'sub.ok': '可以访问（HTTP {code}，{ms} 毫秒）',
    'sub.status': '返回 HTTP {code}',
    'sub.timeout': '{s} 秒内没有响应',
    'sub.error': '无法从此网络访问',
    'sub.none': '此页面没有订阅链接',
    'sub.elsewhere': '订阅托管在其他地址，此页面无法检查',
    running: '正在检查…',
    copy: '复制报告',
    'report.hint': '请把报告发给服务器管理员。报告中不含任何链接或密钥。',
    'unit.s': '秒', 'unit.min': '分钟', 'unit.h': '小时',
  },
};

var STATUS_WORDS = { ok: 'OK', warn: 'WARNING', bad: 'PROBLEM', skip: 'SKIPPED' };
var running = false;
var lastReport = '';

function lang() {
  var l = (document.documentElement.lang || 'en').slice(0, 2);
  return S[l] ? l : 'en';
}

function t(key, vars, l) {
  var s = S[l || lang()][key] || S.en[key];
  for (var k in vars) {
    if (Object.prototype.hasOwnProperty.call(vars, k)) s = s.split('{' + k + '}').join(String(vars[k]));
  }
  return s;
}

function now() {
  return window.performance && performance.now ? performance.now() : Date.now();
}

function escapeHtml(s) {
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(s));
  return div.innerHTML;
}

/* A finished check: localized `key`/`vars` for the page, English `facts` for the report */
function result(id, status, key, vars, facts) {
  return { id: id, status: status, key: key, vars: vars || {}, facts: facts };
}

function duration(ms, l) {
  var s = Math.round(ms / 1000);
  if (s < 120) return s + ' ' + t('unit.s', null, l);
  if (s < 7200) return Math.round(s / 60) + ' ' + t('unit.min', null, l);
  return Math.round(s / 3600) + ' ' + t('unit.h', null, l);
}

function kb(bytes) {
  return (bytes / 1024).toFixed(1);
}

/* (a) Device clock vs the server's Date header. A wrong clock breaks certificate
   checks; CLOCK_WARN_MS matches the CLI (core/defaults.py). */
function checkClock() {
  var t0 = Date.now();
  return fetch(PROBE_PATH + '?clock=' + t0, { method: 'HEAD', cache: 'no-store' }).then(function(r) {
    var t1 = Date.now();
    var date = Date.parse(r.headers.get('Date') || '');
    if (isNaN(date)) return result('clock', 'skip', 'clock.skip', null, 'server sent no Date header');
    /* Date has whole-second precision: compare its midpoint with the request's */
    var skew = (t0 + t1) / 2 - (date + 500);
    var abs = Math.abs(skew);
    var facts = 'device ' + Math.round(abs / 1000) + ' s ' + (skew > 0 ? 'ahead of' : 'behind') + ' server';
    if (abs <= CLOCK_WARN_MS) return result('clock', 'ok', 'clock.ok', { ms: abs }, facts);
    return result('clock', 'warn', skew > 0 ? 'clock.ahead' : 'clock.behind', { ms: abs }, facts);
  }, function() {
    return result('clock', 'skip', 'clock.skip', null, 'server time unavailable');
  });
}

/* (b) Stream a ≥64 KB same-server asset and watch for a freeze in the throttling window */
function checkFlow() {
  if (typeof AbortController === 'undefined' || typeof ReadableStream === 'undefined') {
    return Promise.resolve(result('flow', 'skip', 'flow.unsupported', null, 'browser lacks streaming fetch'));
  }
  var ctrl = new AbortController();
  var start = now();
  var bytes = 0;
  var firstByte = null;
  var stalled = false;
  var capped = false;
  var idle = null;
  var cap = setTimeout(function() { capped = true; ctrl.abort(); }, FLOW_LIMIT_MS);
  function arm(ms) {
    clearTimeout(idle);
    idle = setTimeout(function() { stalled = true; ctrl.abort(); }, ms);
  }
  function done() {
    clearTimeout(idle);
    clearTimeout(cap);
  }
  function timing() {
    return firstByte === null ? 'no body bytes' : 'first byte ' + Math.round(firstByte) + ' ms';
  }
  function classify(errored) {
    var vars = { kb: kb(bytes), ms: firstByte === null ? '-' : Math.round(firstByte) };
    if (stalled || (errored && !capped)) {
      var how = stalled ? 'froze' : 'reset';
      if (bytes >= THROTTLE_LOW_BYTES && bytes <= STALL_CLEARANCE_BYTES) {
        return result('flow', 'bad', 'flow.throttled', vars, how + ' after ' + vars.kb + ' KB (throttling window 8-32 KB), ' + timing());
      }
      return result('flow', 'bad', 'flow.stopped', vars, how + ' after ' + vars.kb + ' KB, ' + timing());
    }
    if (bytes < STALL_CLEARANCE_BYTES) {
      if (capped) return result('flow', 'warn', 'flow.slow', vars, 'slow: ' + vars.kb + ' KB in 30 s, ' + timing());
      return result('flow', 'skip', 'flow.small', vars, 'only ' + vars.kb + ' KB returned');
    }
    return result('flow', 'ok', 'flow.ok', vars, vars.kb + ' KB without a freeze, ' + timing());
  }

  arm(HEADERS_TIMEOUT_MS);
  return fetch(PROBE_PATH + '?t=' + Date.now(), { cache: 'no-store', signal: ctrl.signal }).then(function(r) {
    if (r.status === 404) {
      done();
      return result('flow', 'skip', 'flow.missing', null, 'probe.bin missing (HTTP 404)');
    }
    if (!r.ok || !r.body) {
      done();
      return result('flow', 'bad', 'flow.unreachable', null, 'HTTP ' + r.status + ' for probe.bin');
    }
    var reader = r.body.getReader();
    arm(STALL_TIMEOUT_MS);
    function pump() {
      return reader.read().then(function(chunk) {
        if (chunk.done) return;
        if (firstByte === null) firstByte = now() - start;
        bytes += chunk.value.byteLength;
        arm(STALL_TIMEOUT_MS);
        return pump();
      });
    }
    return pump().then(function() { done(); return classify(false); },
                       function() { done(); return classify(true); });
  }, function() {
    done();
    var why = stalled ? 'no response within ' + HEADERS_TIMEOUT_MS / 1000 + ' s' : 'network error';
    return result('flow', 'bad', 'flow.unreachable', null, why);
  });
}

/* (c) Does the subscription URL answer? Status only; the body is never read. */
function checkSubscription() {
  var config = window._meridianConfig;
  var url = config && config.subscription_url;
  if (!url) return Promise.resolve(result('sub', 'skip', 'sub.none', null, 'no subscription link'));
  var target;
  try { target = new URL(url, location.href); } catch (e) {
    return Promise.resolve(result('sub', 'skip', 'sub.none', null, 'unparsable subscription link'));
  }
  /* connect-src 'self': another origin would look like a network failure */
  if (target.origin !== location.origin) {
    return Promise.resolve(result('sub', 'skip', 'sub.elsewhere', null, 'subscription on another origin'));
  }
  var start = now();
  var timedOut = false;
  function attempt(method) {
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = setTimeout(function() { timedOut = true; if (ctrl) ctrl.abort(); }, SUB_TIMEOUT_MS);
    return fetch(target.href, { method: method, cache: 'no-store', signal: ctrl ? ctrl.signal : undefined }).then(function(r) {
      clearTimeout(timer);
      if (r.body && r.body.cancel) r.body.cancel().catch(function() {});
      return r;
    }, function(e) {
      clearTimeout(timer);
      throw e;
    });
  }
  return attempt('HEAD').then(function(r) {
    return (r.status === 405 || r.status === 501) ? attempt('GET') : r;
  }).then(function(r) {
    var vars = { code: r.status, ms: Math.round(now() - start) };
    var facts = 'HTTP ' + r.status + ' in ' + vars.ms + ' ms';
    return result('sub', r.ok ? 'ok' : 'bad', r.ok ? 'sub.ok' : 'sub.status', vars, facts);
  }, function() {
    var vars = { s: SUB_TIMEOUT_MS / 1000 };
    if (timedOut) return result('sub', 'bad', 'sub.timeout', vars, 'no answer within ' + vars.s + ' s');
    return result('sub', 'bad', 'sub.error', vars, 'network error');
  });
}

function message(res, l) {
  var vars = res.vars;
  if (res.id === 'clock' && vars.ms !== undefined) vars = { t: duration(vars.ms, l) };
  return t(res.key, vars, l);
}

function renderRow(res) {
  return '<div class="selfcheck-row is-' + res.status + '"><span class="selfcheck-dot"></span>' +
    '<div><strong>' + escapeHtml(t(res.id + '.label')) + '</strong>' +
    '<span>' + escapeHtml(message(res)) + '</span></div></div>';
}

/* Plain English for whoever runs the server: never links, hostnames, or UUIDs */
function buildReport(results) {
  var lines = [
    'Meridian connection self-check',
    'Time: ' + new Date().toISOString(),
    'Page: ' + PAGE_VERSION,
    'Language: ' + lang(),
  ];
  for (var i = 0; i < results.length; i++) {
    var r = results[i];
    lines.push(t(r.id + '.label', null, 'en') + ': ' + STATUS_WORDS[r.status] + ' - ' + r.facts);
  }
  return lines.join('\n') + '\n';
}

function run(button) {
  var out = document.getElementById('selfcheck-results');
  if (running || !out) return;
  running = true;
  lastReport = '';
  var label = button.textContent;
  button.disabled = true;
  button.textContent = t('running');
  out.innerHTML = '';
  var results = [];
  var checks = [checkClock, checkFlow, checkSubscription];
  function next(i) {
    if (i === checks.length) return Promise.resolve();
    return checks[i]().then(function(res) {
      results.push(res);
      out.innerHTML += renderRow(res);
      return next(i + 1);
    });
  }
  next(0).catch(function() {}).then(function() {
    if (results.length) {
      lastReport = buildReport(results);
      out.innerHTML += '<button class="copy-link-btn selfcheck-copy" data-action="selfcheck-copy">' + escapeHtml(t('copy')) + '</button>' +
        '<p class="card-hint">' + escapeHtml(t('report.hint')) + '</p>' +
        '<pre class="selfcheck-report" id="selfcheck-report" hidden>' + escapeHtml(lastReport) + '</pre>';
    }
    running = false;
    button.disabled = false;
    button.textContent = label;
  });
}

function copyReport() {
  if (!lastReport) return;
  if (window.copyToClipboard) window.copyToClipboard(lastReport);
  /* Always show what is shared; it is also the fallback where the clipboard API is missing */
  var pre = document.getElementById('selfcheck-report');
  if (pre) pre.hidden = false;
}

document.addEventListener('click', function(e) {
  var target = e.target.closest('[data-action]');
  if (!target) return;
  var action = target.getAttribute('data-action');
  if (action === 'selfcheck-run') run(target);
  else if (action === 'selfcheck-copy') copyReport();
});

})();
