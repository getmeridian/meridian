/* Personal support HTML knows its connection path; old fragment links keep working. */
(function() {
  'use strict';

  var TEXT = {
    en: ['Connection Setup', 'Opening your connection page…',
      'This connection page isn’t available. Ask the person who shared this connection with you.',
      'Open connection page', "Use the owner's instructions below, or open your connection page.", 'Language'],
    ru: ['Настройка подключения', 'Открываем вашу страницу подключения…',
      'Страница подключения недоступна. Обратитесь к тому, кто поделился с вами ссылкой.',
      'Открыть страницу подключения', 'Воспользуйтесь контактами владельца ниже или перейдите на страницу подключения.', 'Язык'],
    fa: ['تنظیم اتصال', 'در حال باز کردن صفحهٔ اتصال شما…',
      'صفحهٔ اتصال در دسترس نیست. از کسی که این اتصال را با شما به اشتراک گذاشته بپرسید.',
      'باز کردن صفحهٔ اتصال', 'راهنمای مالک را در پایین بخوانید یا صفحهٔ اتصال خود را باز کنید.', 'زبان'],
    zh: ['连接设置', '正在打开您的连接页面…', '连接页面不可用。请联系向您分享此连接的人。',
      '打开连接页面', '请按照下方所有者的说明操作，或打开您的连接页面。', '语言']
  };
  var root = document.documentElement;
  var lang = window.MeridianLanguage ? window.MeridianLanguage.current : root.lang;
  if (!Object.prototype.hasOwnProperty.call(TEXT, lang)) lang = 'en';
  var holder = document.getElementById('owner-contact');
  var contact = holder ? { text: holder.getAttribute('data-owner-text') || '', link: holder.getAttribute('data-owner-link') || '' } : null;
  var page = document.getElementById('open-page');
  var pagePath = root.getAttribute('data-page') || '';
  var legacy = !pagePath;
  if (pagePath) {
    if (!/^\/(?!\/)/.test(pagePath) || /[\\?#]/.test(pagePath) || location.pathname !== pagePath + '/support.html') pagePath = '';
  } else {
    var id = location.hash.slice(1);
    var path = location.pathname;
    var marker = path.indexOf('/sub/~pwa/');
    if (marker === -1) marker = path.indexOf('/pwa/');
    if (/^[A-Za-z0-9_-]{1,64}$/.test(id) && marker !== -1) pagePath = path.slice(0, marker) + '/sub/' + id;
  }
  var hasContact = false;
  function render() {
    var text = TEXT[lang];
    root.lang = lang;
    root.dir = lang === 'fa' ? 'rtl' : 'ltr';
    document.title = text[0];
    document.getElementById('title').textContent = text[0];
    hasContact = window.MeridianContact && window.MeridianContact.mount(holder, contact, lang);
    document.getElementById('status').textContent = !pagePath ? text[2] : hasContact ? text[4] : text[1];
    if (page) {
      page.hidden = !pagePath || !hasContact;
      page.textContent = text[3];
      if (pagePath) page.setAttribute('href', pagePath);
    }
    var languages = document.getElementById('languages');
    if (languages) languages.setAttribute('aria-label', text[5]);
    document.querySelectorAll('.lang-btn').forEach(function (button) {
      var active = button.getAttribute('data-lang') === lang;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }
  document.querySelectorAll('.lang-btn').forEach(function (button) {
    button.addEventListener('click', function () {
      lang = button.getAttribute('data-lang');
      if (!Object.prototype.hasOwnProperty.call(TEXT, lang)) return;
      try { localStorage.setItem('meridian-lang', lang); } catch (e) {}
      render();
    });
  });
  render();
  if (!pagePath) return;
  /* A fragment never reaches the service: move old contact links to personal HTML. */
  if (legacy && hasContact) location.replace(pagePath + '/support.html');
  else if (!hasContact) location.replace(pagePath);
})();
