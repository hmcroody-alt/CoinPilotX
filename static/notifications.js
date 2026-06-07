(function () {
  const STATE = {
    prefs: null,
    lastUnread: null,
    lastAlertAt: 0,
    interacted: false,
    pollTimer: null,
    listLoaded: false,
    realtimeBound: false,
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
    const response = await fetch("/api/pulse/notifications/unread-count", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return null;
    const payload = await response.json();
    return Number(payload.unread_count || payload.count || 0);
  }

  function setBadges(count) {
    document.querySelectorAll("[data-notification-unread]").forEach(node => {
      node.textContent = count;
      node.hidden = count <= 0;
    });
    document.title = count > 0 && !/^\(\d+\)/.test(document.title) ? `(${count}) ${document.title}` : document.title.replace(/^\(\d+\)\s+/, count > 0 ? `(${count}) ` : "");
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
    const count = Number(payload?.unread_count || 0);
    if (count || count === 0) {
      if (STATE.lastUnread !== null && count > STATE.lastUnread) alertUser();
      STATE.lastUnread = count;
      setBadges(count);
    }
    await refreshNotificationList().catch(() => {});
    broadcastRefresh("live-event");
  }

  async function pollNotifications(options = {}) {
    try {
      if (!STATE.prefs) await loadPreferences();
      const count = await unreadCount();
      if (count === null) return;
      if (STATE.lastUnread !== null && count > STATE.lastUnread) alertUser();
      STATE.lastUnread = count;
      setBadges(count);
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
    window.PulseRealtime.on("notification_created", (event) => handleLiveNotification(event.payload || event));
    window.PulseRealtime.on("message_notification", (event) => handleLiveNotification(event.payload || event));
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

  STATE.channel?.addEventListener("message", event => {
    if (event.data?.type === "pulse-notification-refresh") pollNotifications({ refreshList: true });
  });
  window.addEventListener("storage", event => {
    if (event.key === "pulseNotificationRefresh") pollNotifications({ refreshList: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollNotifications({ refreshList: true });
    schedulePolling(document.hidden ? 45000 : 12000);
  });

  window.CoinPilotNotifications = { loadPreferences, loadPulsePreferences, subscribePush, unsubscribePush, testNotification, playSound, vibrate, pollNotifications, refreshNotificationList, handleLiveNotification };

  document.addEventListener("DOMContentLoaded", async () => {
    bindSettings();
    await loadPreferences().then(renderSettings).catch(() => {});
    bindRealtime();
    window.setTimeout(bindRealtime, 500);
    pollNotifications({ refreshList: true });
    schedulePolling(12000);
  });
})();
