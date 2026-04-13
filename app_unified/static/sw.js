/**
 * Service Worker — PWA offline support
 * Strategy:
 *   - Static assets (/static/*): cache-first
 *   - API calls (/notes/api/*, /weather/api/*): network-first
 *   - Navigation: network-first, fallback to cache
 */
const CACHE_NAME = 'note-weather-v19';
const STATIC_CACHE_URLS = [
  '/static/js/camera.js',
  '/static/js/voice_input.js',
  '/static/js/face_capture.js',
  '/static/manifest.json',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_CACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API requests: network-first
  if (url.pathname.startsWith('/notes/api') ||
      url.pathname.startsWith('/weather/api') ||
      url.pathname.startsWith('/auth/')) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
    return;
  }

  // Static assets: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        });
      })
    );
    return;
  }

  // Navigation: network-first
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
