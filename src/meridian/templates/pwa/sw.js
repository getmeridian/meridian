/* PWA — cache only shared, content-versioned assets. Person pages always use the network. */
var CACHE_VERSION = 'pwa-v1';
var LEGACY = self.location.pathname.indexOf('/sub/~pwa/') === -1;
var ASSET_BASE = new URL('./' + CACHE_VERSION + '/', self.location.href);
var SHELL_ASSETS = ['styles.css', 'app.js', 'account.js', 'selfcheck.js', 'icon.svg', 'apps.json']
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

self.addEventListener('fetch', function(event) {
  var request = event.request;
  var url = new URL(request.url);
  if (LEGACY || request.method !== 'GET' || request.cache === 'no-store' ||
      url.origin !== ASSET_BASE.origin || url.pathname.indexOf(ASSET_BASE.pathname) !== 0 ||
      url.pathname.endsWith('/probe.bin')) return;
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
