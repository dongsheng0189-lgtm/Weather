/**
 * Service Worker — PWA offline support
 * Strategy:
 *   - Static assets (/static/*): cache-first
 *   - API calls (/notes/api/*, /weather/api/*): network-first
 *   - Navigation: network-first, fallback to cache
 */
const CACHE_NAME = 'note-weather-v46';
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

// caches.match() 沒命中時回 undefined，respondWith(undefined) 會丟
// "Failed to convert value to 'Response'" — 所有 fallback 都要保證回到一個 Response。
async function networkFirst(request) {
  try {
    return await fetch(request);
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    return Response.error();
  }
}

self.addEventListener('fetch', event => {
  // 只接管 GET。Cache API 本來就只存得了 GET，攔 POST/PUT/DELETE 一點好處都沒有，
  // 卻讓每個帶 body 的請求都得經過 SW 再 fetch() 一次 —— request body 是 one-shot
  // stream，SW 冷啟動 / 網路抖動時可能整個掉光，server 就收到一個 headers 和 cookie
  // 都在、body 卻是空的請求。附件上傳撞到這個就變成「缺少 note_id」400。
  // （2026-07-13 prod log 實錄：[note_id-missing] note_id/item_id/filename 全部 None，
  //   user=250 還在 —— 只有「body 掉了、header 沒掉」能同時解釋這三件事。）
  // 不呼叫 respondWith 直接 return = 完全交回瀏覽器原生處理，SW 不碰。
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // API requests: network-first
  // 路徑已被 server-side alias 縮短：/n/api = /notes/api, /m/api = /mynotes/api
  if (url.pathname.startsWith('/n/api') ||
      url.pathname.startsWith('/m/api') ||
      url.pathname.startsWith('/weather/api') ||
      url.pathname.startsWith('/auth/')) {
    event.respondWith(networkFirst(event.request));
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
        }).catch(() => Response.error());
      })
    );
    return;
  }

  // Navigation: network-first
  event.respondWith(networkFirst(event.request));
});
