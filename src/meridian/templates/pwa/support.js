/* Support links carry a subscription token in the fragment and open its connection page. */
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

  var id = location.hash.slice(1);
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) { unavailable(); return; }
  var path = location.pathname;
  var marker = path.indexOf('/sub/~pwa/');
  var base;
  if (marker !== -1) base = path.slice(0, marker);
  else {
    marker = path.indexOf('/pwa/');
    if (marker === -1) { unavailable(); return; }
    base = path.slice(0, marker);
  }
  location.replace(base + '/sub/' + id);
})();
