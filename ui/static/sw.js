/* Jarvis Service Worker — minimal install/activate.
 * Purpose: enable PWA install prompt. Does NOT cache anything by default —
 * the browser's own HTTP cache + our cache-busted query strings handle freshness.
 *
 * Earlier versions cached index.html / app.js / style.css which led to stale
 * versions surviving across redeploys. Now we just register the SW for PWA
 * install eligibility and pass every fetch through to the network. */

const SHELL_CACHE = 'jarvis-shell-v6';

self.addEventListener('install', (event) => {
  // Take over immediately — don't wait for old tabs to close
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Wipe ALL caches from any previous SW version
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Pass-through for all fetches — the SW's only job is to be registered (PWA
// install prompt requires a SW). We do NOT cache anything.
self.addEventListener('fetch', (event) => {
  // Just let the browser handle it normally. No respondWith() = default behaviour.
});
