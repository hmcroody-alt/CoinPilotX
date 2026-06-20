const CACHE_NAME = "coinpilotx-cache-v18-pulse-home-bandwidth";
const STATIC_ASSETS = [
  "/manifest.json",
  "/static/analytics.js",
  "/static/notifications.js",
  "/static/sounds/notification-soft.wav",
  "/static/brand/pulsesoc-logo-20260606.png",
  "/static/brand/pulsesoc-icon-192-20260606.png",
  "/static/brand/pulsesoc-icon-512-20260606.png",
  "/static/brand/pulsesoc-apple-touch-icon-20260606.png"
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
    pathname.startsWith("/command-center") ||
    pathname.startsWith("/intelligence") ||
    pathname.startsWith("/chat") ||
    pathname.startsWith("/messages") ||
    pathname === "/pulse" ||
    pathname.startsWith("/pulse/") ||
    pathname.startsWith("/api/pulse/") ||
    pathname.startsWith("/pulse/notifications") ||
    pathname.startsWith("/static/js/pulse_live_studio") ||
    pathname.startsWith("/static/vendor/livekit-client") ||
    pathname.startsWith("/alerts") ||
    pathname.startsWith("/upgrade") ||
    pathname.startsWith("/forgot-password") ||
    pathname.startsWith("/forgot-username") ||
    pathname.startsWith("/reset-password") ||
    pathname.startsWith("/verify-email") ||
    pathname === "/stripe-webhook" ||
    pathname.startsWith("/stripe/")
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

function isRuntimeAsset(request, pathname) {
  return (
    request.destination === "style" ||
    request.destination === "script" ||
    /\.(?:css|js)$/i.test(pathname)
  );
}

function offlineResponse() {
  return fetch("/offline?ts=" + Date.now(), { cache: "no-store" }).catch(() => new Response(
    "<!doctype html><title>Offline</title><main style='font-family:system-ui;padding:24px'><h1>You are offline.</h1><p>PulseSoc needs an internet connection for live intelligence.</p><p><a href='/reset-pwa'>Reset app cache</a></p></main>",
    { headers: { "Content-Type": "text/html; charset=utf-8" } }
  ));
}

function onlineNavigationError(pathname) {
  const videoRoute = pathname.startsWith("/pulse/videos/");
  const title = videoRoute ? "Video temporarily unavailable" : "PulseSoc could not open this page";
  const body = videoRoute
    ? "The video page could not be loaded. Your connection is online; retry or return to Videos."
    : "This page could not be loaded. Retry without leaving PulseSoc.";
  return new Response(
    `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><main style="min-height:100vh;display:grid;place-items:center;padding:24px;background:#020812;color:#f2fbff;font-family:system-ui"><section style="max-width:520px;border:1px solid rgba(110,223,246,.28);border-radius:20px;padding:24px;background:#071321"><h1>${title}</h1><p style="color:#a8bbc9;line-height:1.5">${body}</p><p><button onclick="location.reload()" style="min-height:44px;border:0;border-radius:12px;padding:10px 16px;background:#36e58f;color:#041019;font-weight:900">Retry</button> <a href="/pulse/videos" style="margin-left:10px;color:#6edff6">Open Videos</a></p></section></main>`,
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } }
  );
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
          const offline = self.navigator && self.navigator.onLine === false;
          console.log("[CoinPilotXAI SW] navigation fetch failed", url.pathname, offline ? "offline" : "online", error && error.message ? error.message : error);
          return offline ? offlineResponse() : onlineNavigationError(url.pathname);
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

  if (isRuntimeAsset(request, url.pathname)) {
    event.respondWith(
      fetch(request, { cache: "no-store" }).then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch((error) => {
        console.log("[CoinPilotXAI SW] runtime fetch fallback", url.pathname, error && error.message ? error.message : error);
        return caches.match(request).then((cached) => cached || Promise.reject(error));
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

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = { title: "PulseSoc Alert", body: event.data ? event.data.text() : "New intelligence alert." };
  }
  const data = payload.data || {};
  const conversationId = data.conversationId || data.conversation_id || payload.conversationId || payload.conversation_id;
  const targetUrl = data.deepLink || data.deep_link || data.url || payload.deepLink || payload.deep_link || payload.url || (conversationId ? `/pulse/messages/${conversationId}` : "/pulse/notifications");
  const title = payload.title || "PulseSoc Alert";
  const options = {
    body: payload.body || payload.message || "New CoinPilotXAI intelligence update.",
    icon: payload.icon || "/static/brand/pulsesoc-icon-192-20260606.png",
    badge: payload.badge || "/static/brand/pulsesoc-icon-192-20260606.png",
    vibrate: payload.vibrate || [200, 100, 200],
    data: { ...data, url: targetUrl, deepLink: targetUrl },
    tag: payload.tag || (conversationId ? `pulsesoc-message-${conversationId}` : "coinpilotxai-alert"),
    renotify: payload.renotify !== false,
    silent: payload.silent === true ? true : false,
    timestamp: payload.timestamp || Date.now(),
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
  const data = event.notification.data || {};
  const conversationId = data.conversationId || data.conversation_id;
  const url = data.deepLink || data.deep_link || data.url || (conversationId ? `/pulse/messages/${conversationId}` : "/pulse/notifications");
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
