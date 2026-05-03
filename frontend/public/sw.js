/**
 * BauLV service worker.
 *
 * The previous revision (v1) cached index.html under a fixed
 * CACHE_NAME that was never bumped. That meant any deploy was
 * invisible to existing users — their browser kept serving the
 * old index.html which pointed at the old hashed JS bundle, even
 * after a successful backend+frontend release. Bug reports of
 * "your fix didn't work in production" traced directly to this.
 *
 * New strategy:
 *
 * 1. CACHE_NAME carries a version suffix. Bump it on every change
 *    to the cache policy so the ``activate`` handler (which deletes
 *    every cache whose name doesn't match the current one) wipes
 *    the stale cache. Yes, the filename hashes in /assets/ would
 *    eventually roll over as the bundle changes, but we don't rely
 *    on that — we rely on HTML always being fresh.
 *
 * 2. HTML navigation requests are **network-first**. The browser
 *    asks the network for ``/`` on every navigation and only falls
 *    back to cache when offline. This guarantees the user picks up
 *    new index.html → new hashed JS bundle on the very next reload
 *    after a deploy.
 *
 * 3. Hashed assets (/assets/..., /icons/..., /manifest.json) are
 *    cache-first. Vite embeds a content hash in asset filenames, so
 *    a changed asset has a new filename — cached entries can't go
 *    stale by definition.
 *
 * 4. API calls bypass the service worker entirely.
 */

// Keep this string in lock-step with APP_BUILD_TAG in src/main.tsx.
// The activate handler below deletes any cache whose name doesn't
// match, so bumping this is how we evict stale assets on every deploy.
// See ``docs/DEPLOY.md`` for the bump checklist.
const CACHE_NAME = "baulv-v23.2-2026-05-01-consent-snapshots";
const STATIC_ASSETS = [
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .catch(() => {
        // Individual asset failures shouldn't prevent install.
      })
  );
  // Take over immediately so the new SW replaces the old one without
  // waiting for every tab to close.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never cache non-GET.
  if (request.method !== "GET") return;

  // API: go straight to network. Auth is stateful, responses are
  // per-user, caching is a footgun.
  if (url.pathname.startsWith("/api")) return;

  // Navigation / HTML: network-first with cache fallback for offline.
  // This is what actually makes deploys visible to users.
  const isHtml =
    request.mode === "navigate" ||
    request.destination === "document" ||
    (request.headers.get("accept") || "").includes("text/html");
  if (isHtml) {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          // Cache a copy of the root so an offline reload still
          // shows something usable.
          if (response.ok && url.pathname === "/") {
            const cache = await caches.open(CACHE_NAME);
            cache.put("/", response.clone());
          }
          return response;
        } catch {
          const cache = await caches.open(CACHE_NAME);
          const fallback = await cache.match("/");
          if (fallback) return fallback;
          throw new Error("Offline and no cached root available");
        }
      })()
    );
    return;
  }

  // Hashed assets / icons / manifest: cache-first.
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(request);
      if (cached) return cached;
      const response = await fetch(request);
      const isCacheable =
        url.pathname.startsWith("/assets/") ||
        url.pathname.startsWith("/icons/") ||
        url.pathname === "/manifest.json";
      if (response.ok && isCacheable) {
        cache.put(request, response.clone());
      }
      return response;
    })()
  );
});
