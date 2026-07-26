const CACHE_NAME = "pulsesoc-cache-v26-launch-readiness";
const DEBUG_SW = false;
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
  return fetch("/offline?from=service-worker&ts=" + Date.now(), { cache: "no-store" }).catch(() => new Response(
    "<!doctype html><title>Offline</title><main style='font-family:system-ui;padding:24px'><h1>You are offline.</h1><p>PulseSoc needs an internet connection for live intelligence.</p><p><a href='/pulse?offline_recovered=1'>Open PulseSoc Home</a> <a href='/reset-pwa'>Reset app cache</a></p></main>",
    { headers: { "Content-Type": "text/html; charset=utf-8" } }
  ));
}

function onlineNavigationError(pathname) {
  const videoRoute = pathname.startsWith("/pulse/videos/");
  const title = videoRoute ? "Video temporarily unavailable" : "PulseSoc could not open this page";
  const body = videoRoute
    ? "The video page could not be loaded. Your connection is online; retry or return to Videos."
    : "This page could not be loaded. Retry without leaving PulseSoc.";
  const fallbackUrl = videoRoute ? "/pulse/videos" : "/pulse";
  const fallbackLabel = videoRoute ? "Open Videos" : "Open PulseSoc Home";
  return new Response(
    `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><main style="min-height:100vh;display:grid;place-items:center;padding:24px;background:#020812;color:#f2fbff;font-family:system-ui"><section style="max-width:520px;border:1px solid rgba(110,223,246,.28);border-radius:20px;padding:24px;background:#071321"><h1>${title}</h1><p style="color:#a8bbc9;line-height:1.5">${body}</p><p><button onclick="location.reload()" style="min-height:44px;border:0;border-radius:12px;padding:10px 16px;background:#36e58f;color:#041019;font-weight:900">Retry</button> <a href="${fallbackUrl}" style="margin-left:10px;color:#6edff6">${fallbackLabel}</a></p></section></main>`,
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } }
  );
}

self.addEventListener("install", (event) => {
  if (DEBUG_SW) console.log("[CoinPlotXAI SW] service worker installed", CACHE_NAME);
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  if (DEBUG_SW) console.log("[CoinPlotXAI SW] service worker activated", CACHE_NAME);
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => {
        if (key !== CACHE_NAME) {
          if (DEBUG_SW) console.log("[CoinPlotXAI SW] old cache deleted", key);
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
    if (DEBUG_SW) console.log("[CoinPlotXAI SW] navigation fetch attempted", url.pathname);
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .then((response) => {
          if (DEBUG_SW) console.log("[CoinPlotXAI SW] navigation fetch succeeded", url.pathname, response.status);
          return response;
        })
        .catch((error) => {
          const browserOffline = self.navigator && self.navigator.onLine === false;
          if (DEBUG_SW) console.log("[CoinPlotXAI SW] navigation fetch failed", url.pathname, browserOffline ? "offline" : "online", error && error.message ? error.message : error);
          if (browserOffline) return offlineResponse();
          return fetch("/health?sw_recovery=" + Date.now(), { cache: "no-store" })
            .then((health) => health && health.ok ? onlineNavigationError(url.pathname) : offlineResponse())
            .catch(() => onlineNavigationError(url.pathname));
        })
    );
    return;
  }

  if (isNeverCachePath(url.pathname)) {
    event.respondWith(fetch(request, { cache: "no-store" }).catch((error) => {
      if (DEBUG_SW) console.log("[CoinPlotXAI SW] fetch failure", url.pathname, error && error.message ? error.message : error);
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
        if (DEBUG_SW) console.log("[CoinPlotXAI SW] runtime fetch fallback", url.pathname, error && error.message ? error.message : error);
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
          if (DEBUG_SW) console.log("[CoinPlotXAI SW] static fetch failure", url.pathname, error && error.message ? error.message : error);
          throw error;
        });
      })
    );
    return;
  }

  event.respondWith(fetch(request));
});

function normalizePushPayload(event) {
  try {
    return event.data ? event.data.json() : {};
  } catch (error) {
    return { title: "PulseSoc Alert", body: event.data ? event.data.text() : "New intelligence alert." };
  }
}

function buildPushNotification(payload) {
  payload = payload || {};
  const data = payload.data || {};
  const conversationId = data.conversationId || data.conversation_id || payload.conversationId || payload.conversation_id;
  const notificationType = data.type || data.notification_type || payload.type || payload.notification_type || "";
  const notificationCategory = data.category || payload.category || "";
  const isIntelligence = /^intelligence_/.test(String(notificationType)) || notificationCategory === "intelligence";
  const priority = String(payload.priority || data.priority || data.priority_badge || "").toLowerCase();
  const soundKey = String(payload.sound_key || data.sound_key || payload.sound || data.sound || "").toLowerCase();
  const defaultUrl = conversationId ? `/pulse/messages/${conversationId}` : (isIntelligence ? "/pulse/alerts" : "/pulse/notifications");
  const targetUrl = safeNotificationUrl(data.web_url || data.url || data.target_url || data.deep_link || payload.web_url || payload.url || payload.target_url || payload.deep_link || defaultUrl);
  const title = isIntelligence ? "PULSESOC ALERT" : (payload.title || "PulseSoc Alert");
  const intelligenceHeadline = isIntelligence ? String(payload.headline || data.headline || "").trim().toUpperCase().slice(0, 64) : "";
  const displayBody = isIntelligence && intelligenceHeadline
    ? `${intelligenceHeadline}\n${payload.body || payload.message || "Open PulseSoc to review this signal."}`
    : (payload.body || payload.message || "New PulseSoc update.");
  const defaultBadge = "/static/brand/pulsesoc-icon-192-20260606.png";
  const badgeAsset = typeof payload.badge === "string" && payload.badge.trim().startsWith("/") ? payload.badge : defaultBadge;
  const notificationTag = payload.tag || (
    conversationId
      ? `pulsesoc-message-${conversationId}`
      : isIntelligence
        ? `pulsesoc-intelligence-${payload.notification_id || data.notification_id || data.signal_id || data.event_id || "pulse"}`
        : "coinplotxai-alert"
  );
  const options = {
    body: displayBody,
    icon: payload.icon || "/static/brand/pulsesoc-icon-192-20260606.png",
    badge: badgeAsset,
    vibrate: payload.vibrate || payload.vibration || data.vibrate || data.vibration || [200, 100, 200],
    data: {
      ...data,
      url: targetUrl,
      web_url: targetUrl,
      deepLink: targetUrl,
      deep_link: targetUrl,
      type: notificationType,
      category: notificationCategory,
      headline: intelligenceHeadline || data.headline || payload.headline || "",
      priority,
      sound_key: soundKey || data.sound_key || "",
      vibration: payload.vibration || data.vibration || "",
      notification_id: payload.notification_id || data.notification_id || "",
      signal_id: payload.signal_id || data.signal_id || data.event_id || ""
    },
    tag: notificationTag,
    renotify: payload.renotify !== false,
    silent: payload.silent === true || payload.sound === "silent" || data.sound_key === "silent",
    timestamp: payload.timestamp || Date.now(),
    actions: payload.actions || [
      { action: "open", title: conversationId ? "Open Chat" : (isIntelligence ? "Open Alerts" : "Open Alerts") },
      { action: "dismiss", title: "Dismiss" }
    ]
  };
  if (priority === "urgent" || priority === "critical" || data.priority_badge === "CRITICAL") options.requireInteraction = true;
  return {
    payload,
    data,
    options,
    title,
    body: displayBody,
    targetUrl,
    isIntelligence,
    priority,
    notificationType,
    notificationCategory,
    headline: intelligenceHeadline,
  };
}

function isForegroundClient(client) {
  if (!client || !client.url || !client.url.startsWith(self.location.origin)) return false;
  return client.focused || client.visibilityState === "visible";
}

async function postForegroundNotification(notification) {
  if (!notification.isIntelligence) return false;
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const visibleClients = clients.filter(isForegroundClient);
  if (!visibleClients.length) return false;
  const message = {
    type: "PULSESOC_FOREGROUND_NOTIFICATION",
    title: notification.title,
    body: notification.body,
    headline: notification.headline || notification.options.data.headline || "",
    priority: notification.priority,
    category: notification.notificationCategory,
    notification_type: notification.notificationType,
    url: notification.targetUrl,
    data: notification.options.data,
    timestamp: notification.options.timestamp || Date.now(),
  };
  visibleClients.forEach((client) => {
    try { client.postMessage(message); } catch (_) {}
  });
  return true;
}

self.addEventListener("push", (event) => {
  const notification = buildPushNotification(normalizePushPayload(event));
  event.waitUntil((async () => {
    const foregroundHandled = await postForegroundNotification(notification);
    if (foregroundHandled) return;
    await self.registration.showNotification(notification.title, notification.options);
  })());
});

function safeNotificationUrl(rawUrl) {
  try {
    const value = String(rawUrl || "/pulse/notifications").trim();
    if (!value || /[\r\n\t]/.test(value) || /^(javascript|data|blob|file):/i.test(value) || value.startsWith("//")) {
      return "/pulse/notifications";
    }
    const url = value.startsWith("/") ? new URL(value, self.location.origin) : new URL(value);
    if (url.origin !== self.location.origin && !/(^|\.)pulsesoc\.com$/i.test(url.hostname)) {
      return "/pulse/notifications";
    }
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/static/") || url.pathname.startsWith("/admin/")) {
      return "/pulse/notifications";
    }
    return `${url.pathname}${url.search}${url.hash}` || "/pulse/notifications";
  } catch (error) {
    return "/pulse/notifications";
  }
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  if (event.action === "dismiss") return;
  const data = event.notification.data || {};
  const conversationId = data.conversationId || data.conversation_id;
  const url = safeNotificationUrl(data.web_url || data.url || data.target_url || data.deep_link || (conversationId ? `/pulse/messages/${conversationId}` : "/pulse/notifications"));
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
