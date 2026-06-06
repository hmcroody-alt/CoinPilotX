const CACHE_NAME = "coinpilotx-cache-v13";
const STATIC_ASSETS = [
  "/manifest.json",
  "/static/analytics.js",
  "/static/notifications.js",
  "/static/sounds/notification-soft.wav",
  "/static/brand/pulse-logo-20260606.png",
  "/static/brand/pulse-icon-192-20260606.png",
  "/static/brand/pulse-icon-512-20260606.png",
  "/static/brand/pulse-apple-touch-icon-20260606.png"
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
    pathname.startsWith("/app") ||
    pathname.startsWith("/intelligence") ||
    pathname.startsWith("/pulse/notifications") ||
    pathname.startsWith("/alerts") ||
    pathname.startsWith("/upgrade") ||
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

function offlineResponse() {
  return fetch("/offline?ts=" + Date.now(), { cache: "no-store" }).catch(() => new Response(
    "<!doctype html><title>Offline</title><main style='font-family:system-ui;padding:24px'><h1>You are offline.</h1><p>Pulse needs an internet connection for live intelligence.</p><p><a href='/reset-pwa'>Reset app cache</a></p></main>",
    { headers: { "Content-Type": "text/html; charset=utf-8" } }
  ));
}

self.addEventListener("install", (event) => {
  console.log("[Pulse SW] service worker installed", CACHE_NAME);
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  console.log("[Pulse SW] service worker activated", CACHE_NAME);
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => {
        if (key !== CACHE_NAME) {
          console.log("[Pulse SW] old cache deleted", key);
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
    console.log("[Pulse SW] navigation fetch attempted", url.pathname);
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .then((response) => {
          console.log("[Pulse SW] navigation fetch succeeded", url.pathname, response.status);
          return response;
        })
        .catch((error) => {
          console.log("[Pulse SW] navigation fallback to offline used", url.pathname, error && error.message ? error.message : error);
          return offlineResponse();
        })
    );
    return;
  }

  if (isNeverCachePath(url.pathname)) {
    event.respondWith(fetch(request, { cache: "no-store" }).catch((error) => {
      console.log("[Pulse SW] fetch failure", url.pathname, error && error.message ? error.message : error);
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
          console.log("[Pulse SW] static fetch failure", url.pathname, error && error.message ? error.message : error);
          throw error;
        });
      })
    );
    return;
  }

  event.respondWith(fetch(request));
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = { title: "Pulse Alert", body: event.data ? event.data.text() : "New intelligence alert." };
  }
  const title = payload.title || "Pulse Alert";
  const options = {
    body: payload.body || payload.message || "New Pulse intelligence update.",
    icon: payload.icon || "/static/brand/pulse-icon-192-20260606.png",
    badge: payload.badge || "/static/brand/pulse-icon-192-20260606.png",
    vibrate: payload.vibrate || [200, 100, 200],
    data: payload.data || { url: payload.url || "/pulse/notifications" },
    tag: payload.tag || "coinpilotxai-alert",
    renotify: payload.renotify !== false,
    actions: payload.actions || [
      { action: "open", title: "Open Alerts" },
      { action: "dismiss", title: "Dismiss" }
    ]
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  if (event.action === "dismiss") return;
  const url = (event.notification.data && event.notification.data.url) || "/pulse/notifications";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client && client.url.includes(self.location.origin)) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
