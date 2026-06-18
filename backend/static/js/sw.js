/* Lensfy service worker — installable PWA + offline app shell.
 *
 * The app is served from localhost, so caching same-origin assets buys no speed
 * (the "network" is the local server) and only risks serving stale JS/CSS after
 * an edit. Strategy is therefore:
 *  - /api/* and /ws/* are LIVE cluster data: never cached (network only).
 *  - all other same-origin (shell + /static + sw + manifest): NETWORK-FIRST,
 *    falling back to cache only when offline. Always fresh while the server runs.
 *  - cross-origin CDN libs (xterm/monaco/fontawesome): cache-first (big, stable).
 */
const VERSION = 'v4';
const SAME_CACHE = `lensfy-same-${VERSION}`;
const CDN_CACHE = `lensfy-cdn-${VERSION}`;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SAME_CACHE).then((c) => c.add('/')).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  const keep = new Set([SAME_CACHE, CDN_CACHE]);
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Network-first: serve fresh from the server, cache the response for offline,
// and fall back to the cache (or cached "/" for navigations) when offline.
function networkFirst(event) {
  const req = event.request;
  return fetch(req)
    .then((resp) => {
      if (resp && resp.ok) {
        const copy = resp.clone();
        caches.open(SAME_CACHE).then((c) => c.put(req, copy));
      }
      return resp;
    })
    .catch(() =>
      caches.open(SAME_CACHE).then((c) =>
        c.match(req).then((hit) => hit || (req.mode === 'navigate' ? c.match('/') : undefined))
      )
    );
}

function cacheFirst(event, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).then((resp) => {
          if (resp && (resp.ok || resp.type === 'opaque')) cache.put(event.request, resp.clone());
          return resp;
        })
    )
  );
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // mutations always hit the network

  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  // Live data — never intercept.
  if (sameOrigin && (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws'))) {
    return;
  }

  if (sameOrigin) {
    event.respondWith(networkFirst(event)); // shell + static + sw + manifest
    return;
  }

  // CDN libraries (xterm, monaco, fontawesome).
  event.respondWith(cacheFirst(event, CDN_CACHE));
});
