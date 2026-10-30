/* Literal owner notices; memory only, with no requests or link parsing. */
(function () {
'use strict';
var TITLES = {
  en: 'Notice from the owner', ru: 'Сообщение от владельца',
  fa: 'پیام مدیر اتصال', zh: '管理者通知'
};
var timer = null;
function mount(panel, notice, lang) {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  if (!panel) return;
  panel.textContent = '';
  panel.hidden = true;
  if (!notice || typeof notice.text !== 'string' || !notice.text.trim() ||
      notice.text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, 'x').length > 1024 ||
      /[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]/.test(notice.text)) return;
  var until = notice.until === null || notice.until === undefined ? null : Date.parse(notice.until);
  if (until !== null && (!isFinite(until) || until <= Date.now())) return;
  var title = document.createElement('strong');
  title.textContent = TITLES[lang] || TITLES.en;
  var text = document.createElement('p');
  text.dir = 'auto';
  text.textContent = notice.text;
  panel.appendChild(title);
  panel.appendChild(text);
  panel.hidden = false;
  if (until !== null) timer = setTimeout(function () {
    mount(panel, notice, document.documentElement.lang);
  }, Math.max(1, Math.min(60000, until - Date.now())));
}
window.MeridianNotice = { mount: mount };
})();
