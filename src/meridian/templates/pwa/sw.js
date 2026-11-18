/* PWA — cache only shared, content-versioned assets. Person pages always use the network. */
var CACHE_VERSION = 'pwa-v1';
var LEGACY = self.location.pathname.indexOf('/sub/~pwa/') === -1;
var ASSET_BASE = new URL('./' + CACHE_VERSION + '/', self.location.href);
var SHELL_ASSETS = ['styles.css', 'app.js', 'account.js', 'selfcheck.js', 'contact.js', 'notice.js', 'icon.svg', 'apps.json']
  .map(function(name) { return ASSET_BASE.href + name; });

self.addEventListener('install', function(event) {
  event.waitUntil(LEGACY ? self.skipWaiting() :
    caches.open(CACHE_VERSION)
      .then(function(cache) { return cache.addAll(SHELL_ASSETS); })
      .then(function() { return self.skipWaiting(); }));
});

self.addEventListener('activate', function(event) {
  event.waitUntil(caches.keys().then(function(keys) {
    return Promise.all(keys.filter(function(key) {
      return /^pwa-(?:v1|[0-9a-f]{8}|[0-9a-f]{16})$/.test(key) && (LEGACY || key !== CACHE_VERSION);
    }).map(function(key) { return caches.delete(key); }));
  }).then(function() {
    if (!LEGACY) return self.clients.claim();
    /* This same file answers the old /pwa/sw.js update URL after SSH files retire.
       Unregister and reload controlled pages so their UUID requests reach the 302. */
    return self.registration.unregister().then(function() {
      return self.clients.matchAll({ type: 'window' });
    }).then(function(clients) {
      return Promise.all(clients.map(function(client) {
        if (client.navigate) return client.navigate(client.url);
      }));
    });
  }));
});

/* Only a failed document navigation gets a reconnect message. An HTTP refusal/revocation
   still reaches the browser unchanged. Neither the request nor its response is cached. */
function reconnectPage() {
  var messages = {
    en: ['Connection page unavailable', 'Reconnect to the internet, then reload this page to see your connection details.'],
    ru: ['Страница подключения недоступна', 'Подключитесь к интернету и обновите страницу, чтобы увидеть данные для подключения.'],
    fa: ['صفحهٔ اتصال در دسترس نیست', 'به اینترنت وصل شوید و این صفحه را دوباره بارگذاری کنید تا اطلاعات اتصال را ببینید.'],
    zh: ['无法打开连接页面', '请连接互联网，然后刷新此页面查看连接信息。']
  };
  var lang = (self.navigator.language || 'en').toLowerCase().split('-')[0];
  if (!Object.prototype.hasOwnProperty.call(messages, lang)) lang = 'en';
  var copy = messages[lang];
  var html = '<!doctype html><html lang="' + lang + '" dir="' + (lang === 'fa' ? 'rtl' : 'ltr') +
    '"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' +
    copy[0] + '</title><main><h1>' + copy[0] + '</h1><p>' + copy[1] + '</p></main></html>';
  return new Response(html, { status: 503, headers: {
    'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    'X-Robots-Tag': 'noindex, nofollow, noarchive'
  } });
}

self.addEventListener('fetch', function(event) {
  var request = event.request;
  var url = new URL(request.url);
  if (LEGACY || request.method !== 'GET' || url.origin !== ASSET_BASE.origin) return;
  if (request.mode === 'navigate' && request.destination === 'document') {
    var scopePath = new URL(self.registration.scope).pathname;
    if (url.pathname.indexOf(scopePath) === 0 && /^[A-Za-z0-9_-]{16,64}(?:\/index\.html|\/)?$/.test(url.pathname.slice(scopePath.length))) {
      event.respondWith(fetch(request).catch(reconnectPage));
    }
    return;
  }
  if (request.cache === 'no-store' || url.search || url.pathname.indexOf(ASSET_BASE.pathname) !== 0 ||
      url.pathname.endsWith('/probe.bin') || url.pathname.endsWith('/support.html')) return;
  event.respondWith(caches.match(request).then(function(cached) {
    if (cached) return cached;
    return fetch(request).then(function(response) {
      if (response.ok && (response.headers.get('Cache-Control') || '').indexOf('no-store') === -1) {
        var copy = response.clone();
        event.waitUntil(caches.open(CACHE_VERSION).then(function(cache) { return cache.put(request, copy); }));
      }
      return response;
    });
  }));
});
