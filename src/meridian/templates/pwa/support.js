/* Support links carry a subscription token in the fragment and open its connection page. */
(function() {
  'use strict';

  var TEXT = {
    en: ['Connection Setup', 'Opening your connection page…',
      'This connection page isn’t available. Ask the person who shared this connection with you.',
      'Open connection page', "Use the owner's instructions below, or open your connection page."],
    ru: ['Настройка подключения', 'Открываем вашу страницу подключения…',
      'Страница подключения недоступна. Спросите того, кто поделился с вами этим подключением.',
      'Открыть страницу подключения', 'Следуйте указаниям владельца ниже или откройте страницу подключения.'],
    fa: ['تنظیم اتصال', 'در حال باز کردن صفحهٔ اتصال شما…',
      'صفحهٔ اتصال در دسترس نیست. از کسی که این اتصال را با شما به اشتراک گذاشته بپرسید.',
      'باز کردن صفحهٔ اتصال', 'راهنمای مالک را در پایین بخوانید یا صفحهٔ اتصال خود را باز کنید.'],
    zh: ['连接设置', '正在打开您的连接页面…', '连接页面不可用。请联系向您分享此连接的人。',
      '打开连接页面', '请按照下方所有者的说明操作，或打开您的连接页面。']
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
  var holder = document.getElementById('owner-contact');
  var contact = holder ? { text: holder.getAttribute('data-owner-text') || '', link: holder.getAttribute('data-owner-link') || '' } : null;
  var hasContact = window.MeridianContact && window.MeridianContact.mount(holder, contact, lang);

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
  if (hasContact) {
    status.textContent = text[4];
    var page = document.getElementById('open-page');
    page.textContent = text[3];
    page.setAttribute('href', base + '/sub/' + id);
    page.hidden = false;
  } else location.replace(base + '/sub/' + id);
})();
