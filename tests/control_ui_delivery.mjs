/* Delivery scenarios compose with the shared console DOM boundary. */
import fs from 'node:fs';
import path from 'node:path';

export function deliveryReply(url, options, data, writes) {
  if (url === 'api/subscription/preview') {
    writes.push(JSON.parse(options.body));
    return data.response;
  }
  if (url === 'api/language') {
    if (options.method === 'GET') return { language: data.language || 'en', changed: false };
    const body = JSON.parse(options.body);
    writes.push(body);
    return { ...body, changed: true };
  }
  if (url !== 'api/notice') throw new Error('Unexpected delivery request: ' + url);
  const body = options.method === 'GET' ? { notice: data.notice || null, for_user: null, confirm: false }
    : JSON.parse(options.body);
  if (options.method !== 'GET') writes.push(body);
  return { ...body, active: !!body.notice, changed: body.confirm,
    preview: body.notice ? body.notice.text.replace(/\{name\}/g, body.for_user || 'person') : '' };
}

export async function runDeliveryScenario({ w, M, ui, mode, locale, data, view, report, events, writes, settle }) {
  if (mode === 'delivery-support-paint') {
    const pwa = path.join(ui, '../../templates/pwa');
    const html = fs.readFileSync(path.join(pwa, 'support.html'), 'utf8');
    view.innerHTML = html.split('<body>')[1].split('</body>')[0];
    const style = w.document.createElement('style');
    style.textContent = fs.readFileSync(path.join(pwa, 'styles.css'), 'utf8');
    w.document.head.appendChild(style);
    w.document.documentElement.setAttribute('data-language', data.language || 'ru');
    w.localStorage.setItem('meridian-lang', locale);
    w.eval(fs.readFileSync(path.join(pwa, 'language.js'), 'utf8'));
    function visibleText(element) {
      if (!element.children.length) return element.textContent.trim();
      return [...element.children].filter(child => w.getComputedStyle(child).display !== 'none')
        .map(child => child.textContent).join('').trim();
    }
    report.title = visibleText(view.querySelector('#title'));
    report.status = visibleText(view.querySelector('#status'));
    report.lang = w.document.documentElement.lang;
    report.dir = w.document.documentElement.dir;
    report.requests = events.length;
    return;
  }
  if (mode === 'delivery-format-preview') {
    w.eval(fs.readFileSync(path.join(ui, 'format-preview.js'), 'utf8'));
    M.mount(view, M.formatPreviewSection());
    view.querySelector('[data-preview="person"]').value = data.person || 'person';
    view.querySelector('[data-preview="user_agent"]').value = data.user_agent || 'Hiddify/2';
    view.querySelector('[data-preview="accept"]').value = data.accept || '';
    view.querySelector('[data-preview="suffix"]').value = data.suffix || '';
    view.querySelector('form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    for (let i = 0; i < 6; i++) await settle();
    report.text = view.querySelector('[data-preview="result"]').textContent;
    report.error = view.querySelector('.form-error').textContent;
    report.writes = writes;
    report.stored = w.localStorage.length;
    report.injected = !!view.querySelector('img') || !!w.pwned;
    return;
  }
  if (mode === 'delivery-page-notice') {
    const panel = w.document.createElement('section');
    view.appendChild(panel);
    w.eval(fs.readFileSync(path.join(ui, '../../templates/pwa/notice.js'), 'utf8'));
    w.MeridianNotice.mount(panel, data.notice, locale);
    report.text = panel.textContent;
    report.hidden = panel.hidden;
    report.injected = !!panel.querySelector('img') || !!w.pwned;
    report.requests = events.length;
    report.stored = w.localStorage.length;
    return;
  }
  w.eval(fs.readFileSync(path.join(ui, 'delivery-settings.js'), 'utf8'));
  if (mode === 'delivery-language') {
    M.mount(view, M.languageSection());
    await settle();
    report.initial = view.querySelector('select').value;
    report.options = [...view.querySelectorAll('option')].map(option => ({ value: option.value, text: option.textContent }));
    view.querySelector('select').value = data.next || 'ru';
    view.querySelector('form').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    for (let i = 0; i < 6; i++) await settle();
    report.text = view.textContent;
    report.writes = writes;
    return;
  }
  const confirmations = [];
  const confirm = M.confirm;
  M.confirm = async (title, text, label, danger) => {
    const pending = confirm(title, text, label, danger);
    await settle();
    const dialog = w.document.querySelector('dialog');
    confirmations.push({ title, text: dialog.querySelector('p').textContent });
    dialog.querySelectorAll('.actions button')[data.confirm === false ? 0 : 1].click();
    return pending;
  };
  M.mount(view, M.noticeSection());
  await settle();
  report.initialText = view.querySelector('textarea').value;
  if (data.text !== undefined) view.querySelector('textarea').value = data.text;
  if (data.person) view.querySelector('[data-notice="person"]').value = data.person;
  if (data.until) view.querySelector('[data-notice="until"]').value = data.until;
  const form = view.querySelector('form');
  if (data.action === 'clear') form.querySelectorAll('.row-actions button')[1].click();
  else if (data.action === 'preview') form.querySelectorAll('.row-actions button')[0].click();
  else form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  for (let i = 0; i < 6; i++) await settle();
  report.text = view.textContent;
  report.preview = view.querySelector('[data-notice="preview"]').textContent;
  report.error = view.querySelector('.form-error').textContent;
  report.confirmations = confirmations;
  report.writes = writes;
  report.injected = !!view.querySelector('img[src="x"]') || !!w.pwned;
  report.stored = w.localStorage.length;
}
