/* Offline support: the app shell is cached on install and served cache-first.
 * Fonts are cached as they load, so they also work offline after first use.
 * Bump VERSION whenever a shell file changes. */
var VERSION = 'xo-v1';
var SHELL = [
  './',
  'index.html',
  'styles.css',
  'xo-core.js',
  'app.js',
  'manifest.webmanifest',
  'icons/icon.svg',
  'icons/icon-192.png',
  'icons/icon-512.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(VERSION).then(function (c) { return c.addAll(SHELL); }));
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== VERSION; })
      .map(function (k) { return caches.delete(k); }));
  }));
  self.clients.claim();
});

self.addEventListener('fetch', function (e) {
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then(function (hit) {
    if (hit) return hit;
    return fetch(e.request).then(function (res) {
      var url = new URL(e.request.url);
      var cacheable = res.ok && (url.origin === location.origin ||
        url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com');
      if (cacheable) {
        var copy = res.clone();
        caches.open(VERSION).then(function (c) { c.put(e.request, copy); });
      }
      return res;
    });
  }));
});
