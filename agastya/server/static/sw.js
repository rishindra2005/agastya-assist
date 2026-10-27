/**
 * sw.js — Service Worker for Agastya Assist (अगस्त्य सहायक)
 * Caches only the static application shell using a network-first strategy.
 * Explicitly NEVER caches /api, /ws, or /mock endpoints.
 */

const CACHE_NAME = 'agastya-shell-v1';

const STATIC_SHELL = [
  '/',
  '/static/index.html',
  '/static/style.css',
  '/static/app.js',
  '/static/live.js',
  '/static/recorder.js',
  '/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async (cache) => {
      // Add items resiliently so a failure on one item does not break the entire service worker
      for (const url of STATIC_SHELL) {
        try {
          await cache.add(url);
        } catch (err) {
          console.warn('[SW] Failed to precache asset:', url, err);
        }
      }
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;

  // Only handle GET requests
  if (request.method !== 'GET') {
    return;
  }

  const url = new URL(request.url);

  // NEVER cache /api, /ws, /mock, or WebSocket/live streams
  if (
    url.pathname.startsWith('/api') ||
    url.pathname.startsWith('/ws') ||
    url.pathname.startsWith('/mock') ||
    url.protocol === 'ws:' ||
    url.protocol === 'wss:'
  ) {
    return; // Pass through directly to network
  }

  // Network-first strategy for the static app shell
  event.respondWith(
    fetch(request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseToCache);
          });
        }
        return networkResponse;
      })
      .catch(async () => {
        // Fallback to cache when offline
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
          return cachedResponse;
        }
        // Fallback navigation requests to index shell
        if (request.mode === 'navigate') {
          const rootCached = await caches.match('/');
          if (rootCached) {
            return rootCached;
          }
        }
        return new Response('Network error (offline)', {
          status: 503,
          statusText: 'Service Unavailable',
          headers: new Headers({ 'Content-Type': 'text/plain; charset=utf-8' }),
        });
      })
  );
});
