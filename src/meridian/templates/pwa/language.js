/* Run synchronously in the head, before paint and before page/config scripts. */
(function () {
  'use strict';
  var root = document.documentElement;
  function supported(value) {
    return value === 'en' || value === 'ru' || value === 'fa' || value === 'zh';
  }
  var language = '';
  try { language = localStorage.getItem('meridian-lang'); } catch (e) {}
  if (!supported(language)) language = root.getAttribute('data-language');
  if (!supported(language)) language = (navigator.language || '').slice(0, 2);
  if (!supported(language)) language = 'en';
  root.lang = language;
  root.dir = language === 'fa' ? 'rtl' : 'ltr';
  window.MeridianLanguage = { current: language };
})();
