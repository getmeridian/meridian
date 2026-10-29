/* Support link — apps open support.html#<subscription id> (the panel's support-url header).
   Pages live under the VLESS UUID, which the header cannot carry, so read it from the
   person's own subscription and go to their page. The id stays in the fragment: it never
   reaches the server, a CDN, or a log. */
(function() {
  'use strict';

  var TEXT = {
    en: ['Connection Setup', 'Opening your connection page…',
      'This connection page isn’t available. Ask the person who shared this connection with you.'],
    ru: ['Настройка подключения', 'Открываем вашу страницу подключения…',
      'Страница подключения недоступна. Спросите того, кто поделился с вами этим подключением.'],
    fa: ['تنظیم اتصال', 'در حال باز کردن صفحهٔ اتصال شما…',
      'صفحهٔ اتصال در دسترس نیست. از کسی که این اتصال را با شما به اشتراک گذاشته بپرسید.'],
    zh: ['连接设置', '正在打开您的连接页面…', '连接页面不可用。请联系向您分享此连接的人。']
  };
  var lang = (navigator.language || '').slice(0, 2);
  try {
    var saved = localStorage.getItem('meridian-lang');
    if (TEXT[saved]) lang = saved;
  } catch (e) {}
  if (!TEXT[lang]) lang = 'en';
  var text = TEXT[lang] || TEXT.en;
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === 'fa' ? 'rtl' : 'ltr';
  document.title = text[0];
  document.getElementById('title').textContent = text[0];
  var status = document.getElementById('status');
  status.textContent = text[1];

  function unavailable() { status.textContent = text[2]; }

  /* Hysteria2's password is the same UUID, so a fleet without VLESS still resolves */
  var UUID = '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})';
  var LINK = new RegExp('^(?:vless|hysteria2)://' + UUID + '@', 'im');
  /* An app's in-app browser may be served the Xray JSON instead of links */
  var JSON_ID = new RegExp('"(?:id|password)"\\s*:\\s*"' + UUID + '"', 'i');
  var id = location.hash.slice(1);
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) { unavailable(); return; }

  fetch('../sub/' + id, { cache: 'no-store' })
    .then(function(r) {
      if (!r.ok) throw new Error('subscription');
      return r.text();
    })
    .then(function(body) {
      var decoded = body;
      try { decoded = atob(body.replace(/\s+/g, '')); } catch (e) { /* already plain links */ }
      var match = LINK.exec(decoded) || JSON_ID.exec(body);
      if (!match) throw new Error('no link');
      var page = '../' + match[1].toLowerCase() + '/';
      /* A disabled or removed person's subscription names no page; say so instead of a 404 */
      return fetch(page + 'config.json', { cache: 'no-store' }).then(function(r) {
        if (!r.ok) throw new Error('no page');
        location.replace(page);
      });
    })
    .catch(unavailable);
})();
