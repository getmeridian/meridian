/* Public owner contact. Text stays text; links open only after the person clicks. */
(function () {
  'use strict';
  var TEXT = {
    en: ["Owner's help", 'Contact owner'],
    ru: ['Помощь владельца', 'Связаться с владельцем'],
    fa: ['راهنمای مالک', 'تماس با مالک'],
    zh: ['所有者提供的帮助', '联系所有者']
  };
  var CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

  function plainText(value) {
    if (typeof value !== 'string' || CONTROL.test(value)) return false;
    var points = value.replace(/[\ud800-\udbff][\udc00-\udfff]/g, 'x');
    return points.length <= 120 && !/[\ud800-\udfff]/.test(points);
  }

  function safeLink(value) {
    if (typeof value !== 'string' || value.length > 2048) return false;
    if (!value) return true;
    if (!/^[\x21-\x7e]+$/.test(value) || /[\\"<>]/.test(value)) return false;
    try {
      if (CONTROL.test(decodeURIComponent(value))) return false;
      var url = new URL(value);
      if (url.protocol === 'https:') return !!url.hostname && !url.username && !url.password;
      if (url.protocol === 'tg:') return /^[a-z][a-z0-9_-]{0,63}$/i.test(url.host) && (!url.pathname || url.pathname === '/');
      return url.protocol === 'mailto:' && !url.host && !url.hash && /^[^\s@]+@[^\s@]+$/.test(url.pathname);
    } catch (e) { return false; }
  }

  function mount(container, contact, lang) {
    if (!container) return false;
    container.textContent = '';
    container.hidden = true;
    if (!contact || typeof contact !== 'object') return false;
    var text = contact.text === undefined ? '' : contact.text;
    var link = contact.link === undefined ? '' : contact.link;
    if (!plainText(text) || !safeLink(link) || (!text && !link)) return false;
    var labels = TEXT[lang] || TEXT.en;
    container.className = 'owner-contact';
    var heading = document.createElement('h3');
    heading.textContent = labels[0];
    container.appendChild(heading);
    if (text) {
      var instructions = document.createElement('p');
      instructions.setAttribute('dir', 'auto');
      instructions.textContent = text;
      container.appendChild(instructions);
    }
    if (link) {
      var anchor = document.createElement('a');
      anchor.className = 'btn btn-ghost';
      anchor.setAttribute('href', link);
      anchor.setAttribute('target', '_blank');
      anchor.setAttribute('rel', 'noopener noreferrer');
      anchor.textContent = labels[1];
      container.appendChild(anchor);
    }
    container.hidden = false;
    return true;
  }

  window.MeridianContact = { mount: mount };
})();
