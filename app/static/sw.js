// Outfit Planner service worker.
// - App shell (CSS, JS, icons) is precached.
// - Photos are cache-first: filenames are immutable, so once seen they stay.
// - Pages are network-first with the last good copy as the offline fallback,
//   so the library and outfit pages she has opened keep working offline.
// - Anything that isn't a GET goes straight to the network.
const VERSION = '__VERSION__';
const SHELL = 'shell-' + VERSION;
const PAGES = 'pages-' + VERSION;
const PHOTOS = 'photos-v1';
const SHELL_URLS = [
  '/static/app.css',
  '/static/app.js',
  '/static/vendor/htmx.min.js',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/offline',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== SHELL && k !== PAGES && k !== PHOTOS).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

function isPage(req, url) {
  return req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/mcp') || url.pathname === '/healthz' || url.pathname.startsWith('/backup')) return;
  // HTMX partial requests are never cached as pages.
  if (req.headers.get('HX-Request') === 'true') return;

  if (url.pathname.startsWith('/photos/')) {
    event.respondWith(
      caches.open(PHOTOS).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      })
    );
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(req, copy)); }
        return res;
      }))
    );
    return;
  }

  if (isPage(req, url)) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok && res.type === 'basic' && !url.pathname.startsWith('/login')) {
          // Clone before the body streams to the page, or clone() throws.
          const copy = res.clone();
          caches.open(PAGES).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }).catch(async () => {
        const cached = await caches.match(req);
        if (cached) return cached;
        if (url.pathname === '/') {
          const lib = await caches.match('/outfits');
          if (lib) return lib;
        }
        return caches.match('/offline');
      })
    );
  }
});
