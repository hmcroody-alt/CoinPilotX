(function () {
  const STATE = {
    prefs: null,
    lastUnread: null,
    lastAlertAt: 0,
    interacted: false,
    pollTimer: null
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

  function alertUser() {
    if (!canAlert(STATE.prefs || {})) return;
    playSound();
    vibrate();
    STATE.lastAlertAt = Date.now();
  }

  async function unreadCount() {
    const response = await fetch("/api/notifications?limit=20", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return null;
    const payload = await response.json();
    const list = payload.notifications || [];
    return list.filter(item => item.status !== "read").length;
  }

  async function pollNotifications() {
    try {
      if (!STATE.prefs) await loadPreferences();
      const count = await unreadCount();
      if (count === null) return;
      if (STATE.lastUnread !== null && count > STATE.lastUnread) alertUser();
      STATE.lastUnread = count;
      document.querySelectorAll("[data-notification-unread]").forEach(node => { node.textContent = count; node.hidden = count <= 0; });
    } catch (_) {}
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
  }

  async function testNotification() {
    const response = await fetch("/api/notifications/test", { method: "POST", credentials: "same-origin", cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || "Test notification failed.");
    alertUser();
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
      note.textContent = "Push permission: " + pushState + ". Some phones only allow vibration or sound after app permission is granted.";
    }
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
        if (action.dataset.notificationAction === "test-sound") { STATE.interacted = true; playSound(); }
        if (action.dataset.notificationAction === "test-vibration") vibrate();
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

  window.CoinPilotNotifications = { loadPreferences, subscribePush, unsubscribePush, testNotification, playSound, vibrate };

  document.addEventListener("DOMContentLoaded", async () => {
    bindSettings();
    await loadPreferences().then(renderSettings).catch(() => {});
    pollNotifications();
    STATE.pollTimer = window.setInterval(pollNotifications, 45000);
  });
})();
