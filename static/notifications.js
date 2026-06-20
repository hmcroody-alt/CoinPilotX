(function () {
  const STATE = {
    prefs: null,
    lastAlertUnread: null,
    lastChatUnread: null,
    lastAlertAt: 0,
    interacted: false,
    pollTimer: null,
    listLoaded: false,
    realtimeBound: false,
    lastScrollAt: 0,
    pushStatus: null,
    channel: "BroadcastChannel" in window ? new BroadcastChannel("pulse-notifications") : null
  };

  function bool(value, fallback) {
    return typeof value === "boolean" ? value : fallback;
  }

  function inQuietHours(prefs) {
    if (!prefs || !prefs.quiet_hours_enabled) return false;
    const start = prefs.quiet_hours_start || "22:00";
    const end = prefs.quiet_hours_end || "07:00";
    const now = new Date();
    const current = now.getHours() * 60 + now.getMinutes();
    const parse = value => {
      const parts = String(value || "00:00").split(":").map(Number);
      return (parts[0] || 0) * 60 + (parts[1] || 0);
    };
    const s = parse(start);
    const e = parse(end);
    return s <= e ? current >= s && current <= e : current >= s || current <= e;
  }

  function canAlert(prefs) {
    return prefs && !inQuietHours(prefs) && Date.now() - STATE.lastAlertAt > 15000;
  }

  async function loadPreferences() {
    const response = await fetch("/api/notification-preferences", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error("Notification preferences unavailable.");
    const payload = await response.json();
    STATE.prefs = payload.experience || {};
    return payload;
  }

  async function saveExperience(experience) {
    const response = await fetch("/api/notification-preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify({ experience })
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || "Could not save notification settings.");
    STATE.prefs = payload.experience || experience;
    renderSettings(payload);
    return payload;
  }

  async function loadPushStatus() {
    const response = await fetch("/api/push/status", { cache: "no-store", credentials: "same-origin" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.message || "Push status unavailable.");
    STATE.pushStatus = payload;
    renderSettings({ experience: STATE.prefs || {}, push_status: payload });
    return payload;
  }

  function waitForNativePushResult(timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        window.removeEventListener("PulseSocNativeMessage", onMessage);
        reject(new Error("PulseSoc did not receive device push confirmation. Reopen the app and try Enable Push again."));
      }, timeoutMs);
      function onMessage(event) {
        const detail = event.detail || {};
        if (detail.type !== "PULSESOC_PUSH_RESULT") return;
        window.clearTimeout(timer);
        window.removeEventListener("PulseSocNativeMessage", onMessage);
        resolve(detail);
      }
      window.addEventListener("PulseSocNativeMessage", onMessage);
    });
  }

  function playSound() {
    const prefs = STATE.prefs || {};
    if (!bool(prefs.enable_notification_sound, true) || !canAlert(prefs)) return;
    const audio = new Audio("/static/sounds/notification-soft.wav");
    audio.volume = 0.34;
    audio.play().catch(() => {
      if (!STATE.interacted || !window.AudioContext) return;
      try {
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.0001, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.045, ctx.currentTime + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.28);
        osc.connect(gain).connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.3);
      } catch (_) {}
    });
  }

  function vibrate() {
    const prefs = STATE.prefs || {};
    if (!bool(prefs.enable_notification_vibration, true) || !canAlert(prefs)) return;
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  }

  function nativeDeviceAlert(payload) {
    const bridge = window.PulseSocNative;
    if (!bridge || typeof bridge.notify !== "function") return;
    try {
      bridge.notify({
        title: payload?.title || "PulseSoc",
        body: payload?.body || payload?.message || "New PulseSoc activity.",
        url: payload?.url || payload?.deep_link || payload?.target_url || "/pulse/notifications"
      });
    } catch (_) {}
  }

  function alertUser(payload) {
    if (!canAlert(STATE.prefs || {})) return;
    nativeDeviceAlert(payload || {});
    playSound();
    vibrate();
    STATE.lastAlertAt = Date.now();
  }

  async function badgeCounts() {
    const response = await fetch("/api/pulse/badge-counts", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return null;
    const payload = await response.json();
    return {
      alert: Number(payload.alert_unread_count || 0),
      chat: Number(payload.chat_unread_count || 0),
      total: Number(payload.total_unread_count || 0)
    };
  }

  function displayCount(count) {
    const value = Number(count || 0);
    return value > 99 ? "99+" : String(value);
  }

  function setBadgeNodes(selector, count) {
    const value = Number(count || 0);
    document.querySelectorAll(selector).forEach(node => {
      node.textContent = displayCount(value);
      node.hidden = value <= 0;
    });
  }

  function setBadges(counts) {
    const alertCount = Number(typeof counts === "number" ? counts : counts?.alert || 0);
    const chatCount = Number(typeof counts === "number" ? 0 : counts?.chat || 0);
    setBadgeNodes("[data-alert-unread], [data-notification-unread]", alertCount);
    setBadgeNodes("[data-chat-unread]", chatCount);
    document.title = alertCount > 0 && !/^\(\d+\)/.test(document.title) ? `(${alertCount}) ${document.title}` : document.title.replace(/^\(\d+\)\s+/, alertCount > 0 ? `(${alertCount}) ` : "");
  }

  function noteUrl(note) {
    return note.deep_link || note.target_url || "/pulse/notifications";
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function notificationCard(note, compact) {
    const unread = !note.read && note.status !== "read";
    const title = note.title || "PulseSoc update";
    const body = note.body || "";
    return `
      <article class="pulse-notification-card ${unread ? "unread" : ""}" data-note-id="${Number(note.id || 0)}">
        <div class="pulse-note-head"><span class="pill">${escapeHtml(note.category || note.type || "PulseSoc")}</span><small>${escapeHtml(note.created_at || "")}</small></div>
        <h${compact ? "3" : "2"}>${escapeHtml(title)}</h${compact ? "3" : "2"}>
        <p>${escapeHtml(body)}</p>
        <div class="actions">
          <a class="button primary" data-open-note href="${escapeHtml(noteUrl(note))}">Open</a>
          ${compact ? "" : `<button class="button" data-read-note="${Number(note.id || 0)}">Mark read</button><button class="button" data-delete-note="${Number(note.id || 0)}">Delete</button>`}
        </div>
      </article>
    `;
  }

  function ensureDropdownList() {
    let target = document.querySelector("[data-notification-list]");
    if (target) return target;
    const dropdownCard = document.querySelector(".pulse-notification-dropdown .card");
    if (!dropdownCard) return null;
    target = document.createElement("div");
    target.dataset.notificationList = "dropdown";
    target.className = "pulse-notification-live-list";
    const firstAction = dropdownCard.querySelector("a,button");
    dropdownCard.insertBefore(target, firstAction || null);
    return target;
  }

  async function refreshNotificationList() {
    const targets = [ensureDropdownList(), ...document.querySelectorAll("[data-pulse-notification-list]")].filter(Boolean);
    if (!targets.length) return;
    const response = await fetch("/api/pulse/notifications?limit=12", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return;
    const payload = await response.json();
    const notes = payload.notifications || [];
    targets.forEach(target => {
      const compact = target.dataset.notificationList === "dropdown";
      target.innerHTML = notes.length
        ? notes.map(note => notificationCard(note, compact)).join("")
        : `<div class="empty-state">No notifications yet.</div>`;
    });
    STATE.listLoaded = true;
  }

  function broadcastRefresh(reason) {
    const message = { type: "pulse-notification-refresh", reason: reason || "update", at: Date.now() };
    try { STATE.channel?.postMessage(message); } catch (_) {}
    try { localStorage.setItem("pulseNotificationRefresh", JSON.stringify(message)); } catch (_) {}
  }

  async function handleLiveNotification(payload) {
    const isChat = ["message_notification", "message_created", "chat_message", "group_message", "room_message"].includes(String(payload?._event_type || payload?.event_type || payload?.type || payload?.notification_type || ""));
    if (isChat) {
      const count = Number(payload?.chat_unread_count || 0);
      const note = payload?.notification || payload || {};
      const conversationId = payload?.conversation_id || payload?.conversationId || note?.conversation_id || note?.conversationId || "";
      const onSameConversation = conversationId && new RegExp(`/pulse/messages(?:-v2)?(?:/|\\\\?conversation=)${conversationId}(?:\\\\b|$)`).test(window.location.pathname + window.location.search);
      if (count || count === 0) {
        if (STATE.lastChatUnread !== null && count > STATE.lastChatUnread && !onSameConversation) {
          alertUser({
            title: note.title || payload?.title || "New PulseSoc message",
            body: note.body || payload?.body || "Open PulseSoc to view.",
            url: note.deep_link || note.target_url || payload?.deep_link || payload?.target_url || (conversationId ? `/pulse/messages/${conversationId}` : "/pulse/messages")
          });
        }
        STATE.lastChatUnread = count;
        setBadges({ alert: STATE.lastAlertUnread || 0, chat: count });
      }
      broadcastRefresh("chat-event");
      return;
    }
    const count = Number(payload?.alert_unread_count || 0);
    if (count || count === 0) {
      if (STATE.lastAlertUnread !== null && count > STATE.lastAlertUnread) alertUser(payload);
      STATE.lastAlertUnread = count;
      setBadges({ alert: count, chat: STATE.lastChatUnread || 0 });
    }
    await refreshNotificationList().catch(() => {});
    broadcastRefresh("live-event");
  }

  async function pollNotifications(options = {}) {
    try {
      if (!options.force && Date.now() - STATE.lastScrollAt < 900) {
        schedulePolling(1800);
        return;
      }
      if (!STATE.prefs) await loadPreferences();
      const counts = await badgeCounts();
      if (counts === null) return;
      if (STATE.lastAlertUnread !== null && counts.alert > STATE.lastAlertUnread) alertUser({ title: "PulseSoc", body: "New PulseSoc activity.", url: "/pulse/notifications" });
      STATE.lastAlertUnread = counts.alert;
      STATE.lastChatUnread = counts.chat;
      setBadges(counts);
      if (options.refreshList || !STATE.listLoaded) await refreshNotificationList().catch(() => {});
    } catch (_) {}
  }

  function schedulePolling(delay) {
    window.clearTimeout(STATE.pollTimer);
    STATE.pollTimer = window.setTimeout(async () => {
      if (!document.hidden) await pollNotifications({ refreshList: true });
      schedulePolling(document.hidden ? 45000 : 12000);
    }, delay);
  }

  function bindRealtime() {
    if (STATE.realtimeBound || !window.PulseRealtime) return;
    STATE.realtimeBound = true;
    window.PulseRealtime.on("notification_created", (event) => handleLiveNotification({ ...(event.payload || event), _event_type: event.event_type || event.type || "notification_created" }));
    window.PulseRealtime.on("message_notification", (event) => handleLiveNotification({ ...(event.payload || event), _event_type: event.event_type || event.type || "message_notification" }));
    window.PulseRealtime.connect();
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
    return outputArray;
  }

  async function subscribePush() {
    if (window.PulseSocNative && typeof window.PulseSocNative.registerPush === "function") {
      const pendingNativeResult = waitForNativePushResult();
      window.PulseSocNative.registerPush();
      const nativeResult = await pendingNativeResult;
      if (!nativeResult.ok) {
        throw new Error(nativeResult.message || "PulseSoc could not register this device for push notifications.");
      }
      const status = await loadPushStatus();
      if (Number(status.active_subscriptions || 0) < 1 || Number(status.active_devices || 0) < 1) {
        throw new Error("PulseSoc did not save an active device token. Stay logged in, reopen the app, and tap Enable Push again.");
      }
      await saveExperience({ ...(STATE.prefs || {}), enable_push_notifications: true });
      await loadPushStatus().catch(() => {});
      return { ...status, ok: true, provider: "native", message: "PulseSoc mobile push is connected to this device." };
    }
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      throw new Error("This browser does not support web push here.");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Push permission was not granted.");
    const keyPayload = await fetch("/api/push/public-key", { cache: "no-store", credentials: "same-origin" }).then(r => r.json());
    if (!keyPayload.public_key) throw new Error("Push keys are not configured yet.");
    const registration = await navigator.serviceWorker.register("/static/service-worker.js");
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(keyPayload.public_key)
    });
    const response = await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(subscription)
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.message || "Push subscription failed.");
    await saveExperience({ ...(STATE.prefs || {}), enable_push_notifications: true });
    await loadPushStatus().catch(() => {});
    return payload;
  }

  async function unsubscribePush() {
    let endpoint = "";
    if ("serviceWorker" in navigator) {
      const registration = await navigator.serviceWorker.getRegistration("/static/service-worker.js");
      const subscription = registration ? await registration.pushManager.getSubscription() : null;
      if (subscription) {
        endpoint = subscription.endpoint;
        await subscription.unsubscribe().catch(() => {});
      }
    }
    await fetch("/api/push/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ endpoint })
    }).catch(() => {});
    await saveExperience({ ...(STATE.prefs || {}), enable_push_notifications: false });
    STATE.pushStatus = null;
  }

  async function testNotification() {
    const response = await fetch("/api/notifications/test", { method: "POST", credentials: "same-origin", cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || "Test notification failed.");
    alertUser({ title: "PulseSoc test notification", body: "Your notification sound and vibration are ready to test.", url: "/pulse/notifications" });
    return payload;
  }

  async function testChannel(channel) {
    const permission = "Notification" in window ? Notification.permission : "unsupported";
    const response = await fetch(`/api/${channel}/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify({ permission })
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || payload.status || `${channel} test failed.`);
    return payload;
  }

  function renderSettings(payload) {
    const root = document.querySelector("[data-notification-settings]");
    if (!root) return;
    const prefs = (payload && payload.experience) || STATE.prefs || {};
    root.querySelectorAll("[data-notification-pref]").forEach(input => {
      const key = input.dataset.notificationPref;
      if (input.type === "checkbox") input.checked = bool(prefs[key], input.defaultChecked);
      else input.value = prefs[key] || input.value || "";
    });
    const note = root.querySelector("[data-notification-status]");
    if (note) {
      const pushState = "Notification" in window ? Notification.permission : "unsupported";
      const status = (payload && payload.push_status) || STATE.pushStatus || {};
      const activeDevices = Number(status.active_devices || status.active_subscriptions || 0);
      const deviceText = activeDevices > 0 ? ` Active push devices: ${activeDevices}.` : " No active push device is saved yet.";
      note.textContent = "Push permission: " + pushState + "." + deviceText + " Some phones only allow vibration or sound after app permission is granted.";
    }
  }

  async function loadPulsePreferences() {
    const response = await fetch("/api/pulse/notifications/preferences", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error("PulseSoc notification preferences unavailable.");
    return response.json();
  }

  function bindSettings() {
    const root = document.querySelector("[data-notification-settings]");
    if (!root) return;
    root.addEventListener("change", async event => {
      const input = event.target.closest("[data-notification-pref]");
      if (!input) return;
      const prefs = { ...(STATE.prefs || {}) };
      prefs[input.dataset.notificationPref] = input.type === "checkbox" ? input.checked : input.value;
      try { await saveExperience(prefs); } catch (error) { renderMessage(error.message); }
    });
    root.addEventListener("click", async event => {
      const action = event.target.closest("[data-notification-action]");
      if (!action) return;
      try {
        if (action.dataset.notificationAction === "enable-push") await subscribePush();
        if (action.dataset.notificationAction === "disable-push") await unsubscribePush();
        if (action.dataset.notificationAction === "test-push") await testChannel("push");
        if (action.dataset.notificationAction === "test-sms") await testChannel("sms");
        if (action.dataset.notificationAction === "test-telegram") await testChannel("telegram");
        if (action.dataset.notificationAction === "test-sound") { STATE.interacted = true; nativeDeviceAlert({ title: "PulseSoc sound test", body: "Sound test is running.", url: "/pulse/notifications" }); playSound(); }
        if (action.dataset.notificationAction === "test-vibration") { nativeDeviceAlert({ title: "PulseSoc vibration test", body: "Vibration test is running.", url: "/pulse/notifications" }); vibrate(); }
        if (action.dataset.notificationAction === "test-notification") await testNotification();
        renderMessage("Notification test completed.");
      } catch (error) {
        renderMessage(error.message || "Notification action failed.");
      }
    });
  }

  function renderMessage(message) {
    document.querySelectorAll("[data-notification-message]").forEach(node => { node.textContent = message || ""; });
  }

  ["pointerdown", "keydown", "touchstart"].forEach(type => {
    window.addEventListener(type, () => { STATE.interacted = true; }, { once: true, passive: true });
  });

  STATE.channel?.addEventListener("message", event => {
    if (event.data?.type === "pulse-notification-refresh") pollNotifications({ refreshList: true });
  });
  window.addEventListener("storage", event => {
    if (event.key === "pulseNotificationRefresh") pollNotifications({ refreshList: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollNotifications({ refreshList: true, force: true });
    schedulePolling(document.hidden ? 45000 : 12000);
  });
  window.addEventListener("scroll", () => {
    STATE.lastScrollAt = Date.now();
  }, { passive: true });

  window.CoinPilotNotifications = { loadPreferences, loadPulsePreferences, loadPushStatus, subscribePush, unsubscribePush, testNotification, playSound, vibrate, pollNotifications, refreshNotificationList, handleLiveNotification };

  document.addEventListener("DOMContentLoaded", async () => {
    bindSettings();
    await loadPreferences().then(renderSettings).catch(() => {});
    await loadPushStatus().catch(() => {});
    bindRealtime();
    window.setTimeout(bindRealtime, 500);
    pollNotifications({ refreshList: true, force: true });
    schedulePolling(12000);
  });
})();
