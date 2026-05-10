const CACHE_NAME = "coinpilotxai-static-v3";
const STATIC_ASSETS = [
  "/offline",
  "/manifest.json",
  "/static/analytics.js",
  "/static/Coinpilot%20Logo/NewLogo.png",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png"
];

function isNeverCachePath(pathname) {
  return (
    pathname === "/health" ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/admin/") ||
    pathname.startsWith("/debug/") ||
    pathname.startsWith("/login") ||
    pathname.startsWith("/logout") ||
    pathname.startsWith("/signup") ||
    pathname.startsWith("/account") ||
    pathname.startsWith("/forgot-password") ||
    pathname.startsWith("/reset-password") ||
    pathname.startsWith("/verify-email") ||
    pathname === "/stripe-webhook"
  );
}

function isStaticAsset(request, pathname) {
  return (
    request.destination === "style" ||
    request.destination === "script" ||
    request.destination === "image" ||
    request.destination === "font" ||
    /\.(?:css|js|png|jpg|jpeg|webp|gif|svg|ico|woff2?|ttf)$/i.test(pathname)
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET") {
    return;
  }

  if (isNeverCachePath(url.pathname)) {
    event.respondWith(fetch(request, { cache: "no-store" }).catch((error) => {
      console.log("[CoinPilotXAI SW] fetch failure", url.pathname, error && error.message ? error.message : error);
      throw error;
    }));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .catch((error) => {
          console.log("[CoinPilotXAI SW] navigation fetch failure", url.pathname, error && error.message ? error.message : error);
          return caches.match("/offline");
        })
    );
    return;
  }

  if (isStaticAsset(request, url.pathname)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) {
          return cached;
        }
        return fetch(request).then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        }).catch((error) => {
          console.log("[CoinPilotXAI SW] static fetch failure", url.pathname, error && error.message ? error.message : error);
          throw error;
        });
      })
    );
    return;
  }

  event.respondWith(fetch(request));
});
