const CACHE_NAME = "coinpilotx-cache-v9";
const STATIC_ASSETS = [
  "/manifest.json",
  "/static/analytics.js",
  "/static/Coinpilot%20Logo/NewLogo.png",
  "/static/assets/coinpilotxai-share-card.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png"
];

function isNeverCachePath(pathname) {
  return (
    pathname === "/" ||
    pathname === "/offline" ||
    pathname === "/health" ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/admin/") ||
    pathname.startsWith("/debug/") ||
    pathname.startsWith("/login") ||
    pathname.startsWith("/logout") ||
    pathname.startsWith("/signup") ||
    pathname.startsWith("/account") ||
    pathname.startsWith("/dashboard") ||
    pathname.startsWith("/upgrade") ||
    pathname.startsWith("/forgot-password") ||
    pathname.startsWith("/forgot-username") ||
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

function offlineResponse() {
  return fetch("/offline?ts=" + Date.now(), { cache: "no-store" }).catch(() => new Response(
    "<!doctype html><title>Offline</title><main style='font-family:system-ui;padding:24px'><h1>You are offline.</h1><p>CoinPilotXAI Inc. needs an internet connection for live intelligence.</p><p><a href='/reset-pwa'>Reset app cache</a></p></main>",
    { headers: { "Content-Type": "text/html; charset=utf-8" } }
  ));
}

self.addEventListener("install", (event) => {
  console.log("[CoinPilotXAI SW] service worker installed", CACHE_NAME);
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  console.log("[CoinPilotXAI SW] service worker activated", CACHE_NAME);
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => {
        if (key !== CACHE_NAME) {
          console.log("[CoinPilotXAI SW] old cache deleted", key);
        }
        return key === CACHE_NAME ? Promise.resolve() : caches.delete(key);
      }))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET") {
    return;
  }

  if (request.mode === "navigate") {
    console.log("[CoinPilotXAI SW] navigation fetch attempted", url.pathname);
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .then((response) => {
          console.log("[CoinPilotXAI SW] navigation fetch succeeded", url.pathname, response.status);
          return response;
        })
        .catch((error) => {
          console.log("[CoinPilotXAI SW] navigation fallback to offline used", url.pathname, error && error.message ? error.message : error);
          return offlineResponse();
        })
    );
    return;
  }

  if (isNeverCachePath(url.pathname)) {
    event.respondWith(fetch(request, { cache: "no-store" }).catch((error) => {
      console.log("[CoinPilotXAI SW] fetch failure", url.pathname, error && error.message ? error.message : error);
      throw error;
    }));
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
