/* Fresh format limits only; no private policy assignment data or persistent cache. */
(function () {
  'use strict';
  var TEXT = {
    en: { title: 'Client routing', rules_not_carried: 'Routing rules and DNS must be configured in this app.',
      dns_not_carried: 'This format carries no DNS settings.', geosite_not_supported: 'Geosite lists are omitted; inline domains still apply.',
      geoip_not_supported: 'GeoIP lists are omitted; private IP handling still applies.', bittorrent_not_supported: 'BitTorrent detection is available only in Xray JSON.',
      dns_transport_not_supported: 'An unsupported DNS transport is omitted.', fake_ip_not_supported: 'Fake-IP is omitted.',
      exit_unavailable: 'A rule targeting an unavailable exit blocks that destination.' },
    ru: { title: 'Маршрутизация в приложении', rules_not_carried: 'Правила маршрутизации и DNS нужно настроить в самом приложении.',
      dns_not_carried: 'Этот формат не передаёт настройки DNS.', geosite_not_supported: 'Списки geosite не передаются; правила для доменов действуют.',
      geoip_not_supported: 'Списки geoip не передаются; обход частных адресов сохраняется.', bittorrent_not_supported: 'Распознавание BitTorrent доступно только в Xray JSON.',
      dns_transport_not_supported: 'Неподдерживаемый транспорт DNS не передаётся.', fake_ip_not_supported: 'Режим fake-IP не передаётся.',
      exit_unavailable: 'Если нужный выходной сервер недоступен в профиле, правило блокирует этот адрес назначения.' },
    fa: { title: 'مسیریابی برنامه', rules_not_carried: 'مسیریابی و DNS باید در خود این برنامه تنظیم شوند.',
      dns_not_carried: 'این قالب تنظیم DNS ندارد.', geosite_not_supported: 'فهرست‌های geosite حذف می‌شوند؛ قانون‌های دامنه برقرارند.',
      geoip_not_supported: 'فهرست‌های geoip حذف می‌شوند؛ رسیدگی به IP خصوصی برقرار است.', bittorrent_not_supported: 'تشخیص BitTorrent فقط در Xray JSON موجود است.',
      dns_transport_not_supported: 'انتقال DNS پشتیبانی‌نشده حذف می‌شود.', fake_ip_not_supported: 'fake-IP حذف می‌شود.',
      exit_unavailable: 'قانونِ خروجی ناموجود، آن مقصد را مسدود می‌کند.' },
    zh: { title: '应用路由', rules_not_carried: '请在此应用中自行配置路由规则与 DNS。',
      dns_not_carried: '此格式不携带 DNS 设置。', geosite_not_supported: '省略 geosite 列表，内联域名规则仍然生效。',
      geoip_not_supported: '省略 geoip 列表，私有 IP 处理仍然生效。', bittorrent_not_supported: 'BitTorrent 检测仅在 Xray JSON 中可用。',
      dns_transport_not_supported: '省略不支持的 DNS 传输。', fake_ip_not_supported: '省略 fake-IP。',
      exit_unavailable: '规则指定的出口不在当前配置中时，会阻止该目的地。' },
  };
  var FORMATS = ['xray-json', 'mihomo', 'stash', 'singbox', 'base64'];
  function read(value) {
    if (!value || !Array.isArray(value.formats) || value.formats.length > 5) return null;
    var rows = [], seen = {};
    for (var i = 0; i < value.formats.length; i++) {
      var row = value.formats[i];
      if (!row || FORMATS.indexOf(row.format) < 0 || seen[row.format] ||
          typeof row.routing_supported !== 'boolean' || typeof row.dns_supported !== 'boolean' ||
          !Array.isArray(row.omitted) || row.omitted.length > 8) return null;
      for (var j = 0; j < row.omitted.length; j++) if (!Object.prototype.hasOwnProperty.call(TEXT.en, row.omitted[j])) return null;
      seen[row.format] = true;
      rows.push({ format: row.format, routing_supported: row.routing_supported, dns_supported: row.dns_supported, omitted: row.omitted.slice() });
    }
    return rows;
  }
  function mount(parent, value, language) {
    var rows = read(value);
    if (!rows) return;
    var text = TEXT[language] || TEXT.en, details = document.createElement('details');
    var summary = document.createElement('summary'); summary.textContent = text.title; details.appendChild(summary);
    rows.forEach(function (row) {
      if (!row.omitted.length) return;
      var paragraph = document.createElement('p');
      paragraph.textContent = row.format + ': ' + row.omitted.map(function (reason) { return text[reason]; }).join(' ');
      details.appendChild(paragraph);
    });
    parent.appendChild(details);
  }
  window.MeridianDeliveryCapabilities = { read: read, mount: mount };
})();
