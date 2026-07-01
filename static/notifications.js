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
    notesById: new Map(),
    channel: "BroadcastChannel" in window ? new BroadcastChannel("pulse-notifications") : null
  };
  const NOTIFICATION_ROUTE_PREFIXES = [
    "/pulse/notifications",
    "/pulse/messages",
    "/pulse/live",
    "/pulse/reels",
    "/pulse/videos",
    "/pulse/post",
    "/pulse/status",
    "/pulse/profile",
    "/pulse/alerts",
    "/pulse/purchases",
    "/pulse/premium",
    "/pulse/marketplace",
    "/dashboard/crypto",
    "/dashboard/account/security",
    "/dashboard/economy",
    "/account/security",
    "/billing/portal"
  ];

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
        ...(payload || {}),
        title: payload?.title || "PulseSoc",
        body: payload?.body || payload?.message || "New PulseSoc activity.",
        sound: payload?.sound || "default",
        priority: payload?.priority || "high",
        category: payload?.category || payload?.type || payload?.notification_type || "notification",
        type: payload?.type || payload?.notification_type || "notification",
        badge: Number(payload?.badge || payload?.alert_unread_count || payload?.chat_unread_count || 0),
        url: payload?.native_url || payload?.app_url || payload?.mobile_deep_link || payload?.deepLink || payload?.url || payload?.deep_link || payload?.target_url || "/pulse/notifications"
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

  function setTextNodes(selector, count) {
    const value = Number(count || 0);
    document.querySelectorAll(selector).forEach(node => {
      node.textContent = displayCount(value);
    });
  }

  function setBadges(counts) {
    const alertCount = Number(typeof counts === "number" ? counts : counts?.alert || 0);
    const chatCount = Number(typeof counts === "number" ? 0 : counts?.chat || 0);
    setBadgeNodes("[data-alert-unread], [data-notification-unread]", alertCount);
    setBadgeNodes("[data-chat-unread]", chatCount);
    setTextNodes("[data-notification-unread-total]", alertCount);
    setTextNodes("[data-message-unread-total]", chatCount);
    document.title = alertCount > 0 && !/^\(\d+\)/.test(document.title) ? `(${alertCount}) ${document.title}` : document.title.replace(/^\(\d+\)\s+/, alertCount > 0 ? `(${alertCount}) ` : "");
  }

  function applyBadgeCounts(payload) {
    const source = payload?.badge_counts || payload;
    if (!source || typeof source !== "object") return;
    if (!("alert_unread_count" in source) && !("chat_unread_count" in source)) return;
    const counts = {
      alert: Number(source.alert_unread_count || 0),
      chat: Number(source.chat_unread_count || 0),
      total: Number(source.total_unread_count || 0)
    };
    STATE.lastAlertUnread = counts.alert;
    STATE.lastChatUnread = counts.chat;
    setBadges(counts);
  }

  function noteMeta(note) {
    return note?.metadata && typeof note.metadata === "object" ? note.metadata : {};
  }

  function noteValue(note, ...keys) {
    const meta = noteMeta(note);
    for (const key of keys) {
      const value = note?.[key];
      if (value !== undefined && value !== null && String(value) !== "") return value;
      const metaValue = meta?.[key];
      if (metaValue !== undefined && metaValue !== null && String(metaValue) !== "") return metaValue;
    }
    return "";
  }

  function safeInternalUrl(value) {
    const raw = String(value || "").trim();
    if (!raw || /[\r\n\t]/.test(raw) || /^(javascript|data|blob|file):/i.test(raw)) return "";
    try {
      const url = raw.startsWith("/") ? new URL(raw, window.location.origin) : new URL(raw);
      if (url.origin !== window.location.origin && !/(^|\.)pulsesoc\.com$/i.test(url.hostname)) return "";
      if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/static/") || url.pathname.startsWith("/admin/")) return "";
      if (!NOTIFICATION_ROUTE_PREFIXES.some(prefix => url.pathname === prefix || url.pathname.startsWith(`${prefix}/`))) return "";
      return `${url.pathname}${url.search}${url.hash}` || "/pulse/notifications";
    } catch (_) {
      return "";
    }
  }

  function localNotificationTarget(note) {
    const type = String(noteValue(note, "type", "notification_type", "event_type") || "").toLowerCase();
    const entityType = String(noteValue(note, "entity_type", "content_type", "source_type") || "").toLowerCase();
    const entityId = noteValue(note, "entity_id", "content_id");
    let conversationId = noteValue(note, "conversation_id", "conversationId", "thread_id", "threadId");
    let postId = noteValue(note, "post_id", "postId");
    let reelId = noteValue(note, "reel_id", "reelId");
    let videoId = noteValue(note, "video_id", "videoId");
    let statusId = noteValue(note, "status_id", "statusId");
    const commentId = noteValue(note, "comment_id", "commentId", "reply_id", "replyId");
    let profileId = noteValue(note, "profile_user_id", "profile_id", "followed_user_id", "sender_user_id", "actor_user_id");
    let liveId = noteValue(note, "live_id", "liveId", "stream_id", "streamId");
    let alertId = noteValue(note, "alert_id", "alert_rule_id", "rule_id", "alertId");
    let purchaseId = noteValue(note, "order_id", "purchase_id", "payment_id", "receipt_id", "orderId", "purchaseId");
    const symbol = String(noteValue(note, "symbol", "coin", "asset", "ticker") || "").trim().toUpperCase();

    if (entityId) {
      if (!conversationId && /(message|chat|conversation)/.test(entityType)) conversationId = entityId;
      if (!postId && entityType.includes("post")) postId = entityId;
      if (!reelId && entityType.includes("reel")) reelId = entityId;
      if (!videoId && entityType.includes("video")) videoId = entityId;
      if (!statusId && entityType.includes("status")) statusId = entityId;
      if (!profileId && /(profile|user|follow)/.test(entityType)) profileId = entityId;
      if (!liveId && entityType.includes("live")) liveId = entityId;
      if (!alertId && /(alert|crypto)/.test(entityType)) alertId = entityId;
      if (!purchaseId && /(order|purchase|payment|receipt|subscription)/.test(entityType)) purchaseId = entityId;
    }

    if (conversationId || type.includes("message") || type.includes("chat")) return conversationId ? `/pulse/messages/${encodeURIComponent(conversationId)}` : "/pulse/messages";
    if (type.includes("cohost_request")) return liveId ? `/pulse/live/studio/${encodeURIComponent(liveId)}` : "/pulse/live/studio";
    if (liveId || type.includes("live") || type.includes("cohost")) return liveId ? `/pulse/live/${encodeURIComponent(liveId)}` : "/pulse/live";
    if (reelId) return `/pulse/reels/${encodeURIComponent(reelId)}`;
    if (videoId) return `/pulse/videos/${encodeURIComponent(videoId)}`;
    if (postId) return `/pulse/post/${encodeURIComponent(postId)}${commentId ? `#comment-${encodeURIComponent(commentId)}` : ""}`;
    if (statusId) return `/pulse/status/${encodeURIComponent(statusId)}`;
    if (profileId && (type.includes("follow") || entityType.includes("profile") || entityType.includes("user"))) return `/pulse/profile/${encodeURIComponent(profileId)}`;
    if (alertId || type.includes("crypto") || type.includes("scam")) return alertId ? `/pulse/alerts/${encodeURIComponent(alertId)}` : (symbol ? `/dashboard/crypto/alerts?symbol=${encodeURIComponent(symbol)}` : "/dashboard/crypto/alerts");
    if (type.includes("security") || ["account_login", "new_device", "suspicious_login", "password_changed", "password_reset", "password_reset_requested", "email_changed", "phone_changed", "account_locked"].includes(type)) return "/account/security";
    if (purchaseId || /(purchase|payment|order|subscription)/.test(type)) return purchaseId ? `/pulse/purchases/${encodeURIComponent(purchaseId)}` : "/dashboard/economy/subscriptions";
    if (type.includes("premium")) return "/pulse/premium";
    return safeInternalUrl(note?.deep_link || note?.target_url || noteMeta(note).deep_link || noteMeta(note).target_url) || "/pulse/notifications";
  }

  function noteUrl(note) {
    const semantic = localNotificationTarget(note);
    const explicit = safeInternalUrl(note?.deep_link || note?.target_url);
    if (semantic && semantic !== "/pulse/notifications") return semantic;
    if (explicit && !["/", "/pulse"].includes(explicit)) return explicit;
    return semantic || explicit || "/pulse/notifications";
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function notificationCard(note, compact) {
    const unread = !note.read && note.status !== "read";
    const title = note.title || "PulseSoc update";
    const body = note.body || "";
    const noteId = Number(note.id || 0);
    return `
      <article class="pulse-notification-card ${unread ? "unread" : ""}" data-note-id="${noteId}" data-note-type="${escapeHtml(note.type || note.notification_type || "")}">
        <div class="pulse-note-head"><span class="pill">${escapeHtml(note.category || note.type || "PulseSoc")}</span><small>${escapeHtml(note.created_at || "")}</small></div>
        <h${compact ? "3" : "2"}>${escapeHtml(title)}</h${compact ? "3" : "2"}>
        <p>${escapeHtml(body)}</p>
        <p class="pulse-note-state" data-note-state hidden></p>
        <div class="actions">
          <a class="button primary" data-open-note="${noteId}" href="${escapeHtml(noteUrl(note))}">Open</a>
          ${compact ? "" : `<button class="button" data-read-note="${noteId}">Mark read</button><button class="button" data-delete-note="${noteId}">Delete</button>`}
        </div>
      </article>
    `;
  }

  function notificationSection(note) {
    const type = String(note?.type || note?.notification_type || "").toLowerCase();
    const category = String(note?.category || "").toLowerCase();
    const priority = String(note?.priority || note?.metadata?.priority || "").toLowerCase();
    const isPriority = ["high", "critical", "urgent"].includes(priority) || ["security_alert", "account_login", "new_device", "password_changed", "email_changed", "phone_changed", "suspicious_login", "account_locked", "crypto_price_alert", "crypto_alert_triggered", "cohost_request", "cohost_request_received", "payment_failed", "admin_security_event"].includes(type) || ["security", "system_security", "admin_security"].includes(category);
    if (isPriority) return "Priority";
    const created = new Date(note?.created_at || Date.now());
    const now = new Date();
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startCreated = new Date(created.getFullYear(), created.getMonth(), created.getDate());
    const days = Math.floor((startToday - startCreated) / 86400000);
    if (days <= 0) return "Today";
    if (days === 1) return "Yesterday";
    if (days <= 7) return "Earlier This Week";
    return "Earlier";
  }

  function groupedTitle(items) {
    const first = items[0] || {};
    if (items.length <= 1) return first.title || "PulseSoc update";
    const actor = first.actor_name || "Someone";
    const type = String(first.type || "").toLowerCase();
    let verb = "reacted to your post";
    if (type.includes("comment") || type.includes("reply")) verb = "commented on your content";
    else if (type.includes("save")) verb = "saved your post";
    else if (type.includes("share") || type.includes("repost")) verb = "shared your post";
    else if (type.includes("follow")) verb = "followed you";
    else if (type.includes("mention") || type.includes("tag")) verb = "mentioned you";
    else if (type.includes("like") || type.includes("reaction")) verb = "liked your post";
    return `${actor} and ${items.length - 1} others ${verb}.`;
  }

  function groupNotifications(notes) {
    const socialTypes = new Set(["like", "reaction", "comment", "reply", "save", "share", "repost", "mention", "tag", "follow", "follow_request", "status_reaction", "reel_like", "reel_comment", "video_like", "video_comment", "video_save"]);
    const groups = new Map();
    notes.forEach(note => {
      const type = String(note?.type || "").toLowerCase();
      const category = String(note?.category || "").toLowerCase();
      const section = notificationSection(note);
      const groupable = socialTypes.has(type) || ["social", "likes", "comments", "mentions", "follows", "status"].includes(category);
      const key = groupable
        ? ["group", section, type || category, note?.entity_type || note?.content_type || "", note?.entity_id || note?.postId || note?.statusId || "", noteUrl(note)].join("|")
        : `single|${Number(note?.id || 0)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(note);
    });
    return [...groups.values()].map(items => items.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))));
  }

  function notificationSectionsHtml(notes, compact) {
    const sectionOrder = ["Priority", "Today", "Yesterday", "Earlier This Week", "Earlier"];
    const buckets = Object.fromEntries(sectionOrder.map(label => [label, []]));
    groupNotifications(notes).forEach(items => {
      const first = items[0] || {};
      buckets[notificationSection(first)].push(items);
    });
    return sectionOrder.map(section => {
      const groups = buckets[section] || [];
      if (!groups.length) return "";
      groups.sort((a, b) => String((b[0] || {}).created_at || "").localeCompare(String((a[0] || {}).created_at || "")));
      const cards = groups.map(items => {
        if (items.length <= 1) return notificationCard(items[0], compact);
        const first = { ...items[0], title: groupedTitle(items), body: `Grouped repeated activity. ${items[0]?.original_preview || items[0]?.preview_text || ""}`.trim() };
        return notificationCard(first, compact);
      }).join("");
      return `<section class="pulse-notification-section" data-notification-section="${escapeHtml(section)}"><h2>${escapeHtml(section)}</h2><div class="pulse-notification-list">${cards}</div></section>`;
    }).join("");
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
    STATE.notesById = new Map();
    notes.forEach(note => STATE.notesById.set(Number(note.id || 0), note));
    targets.forEach(target => {
      const compact = target.dataset.notificationList === "dropdown";
      target.innerHTML = notes.length
        ? notificationSectionsHtml(notes, compact)
        : `<div class="empty-state">No notifications yet.</div>`;
    });
    STATE.listLoaded = true;
  }

  function broadcastRefresh(reason) {
    const message = { type: "pulse-notification-refresh", reason: reason || "update", at: Date.now() };
    try { STATE.channel?.postMessage(message); } catch (_) {}
    try { localStorage.setItem("pulseNotificationRefresh", JSON.stringify(message)); } catch (_) {}
  }

  async function notificationApi(url, options = {}) {
    const isForm = options.body instanceof FormData;
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: isForm ? (options.headers || {}) : { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options
    });
    const payload = await response.json().catch(() => ({ ok: false, message: "Notification action returned an unreadable response." }));
    if (!response.ok || payload.ok === false) {
      const error = new Error(payload.message || "Notification action failed.");
      Object.assign(error, payload, { status: response.status });
      throw error;
    }
    return payload;
  }

  function cardForAction(action) {
    return action?.closest?.(".pulse-notification-card");
  }

  function noteIdForAction(action) {
    const card = cardForAction(action);
    return Number(action?.dataset.openNote || action?.dataset.readNote || action?.dataset.deleteNote || card?.dataset.noteId || 0);
  }

  function setCardState(card, message, mode = "") {
    if (!card) return;
    const state = card.querySelector("[data-note-state]");
    if (!state) return;
    state.textContent = message || "";
    state.hidden = !message;
    state.dataset.state = mode || "";
  }

  function setActionBusy(action, busy, label) {
    if (!action) return () => {};
    const previous = action.textContent;
    action.setAttribute("aria-busy", busy ? "true" : "false");
    action.classList.toggle("is-loading", Boolean(busy));
    if ("disabled" in action) action.disabled = Boolean(busy);
    if (busy && label) action.textContent = label;
    return () => {
      action.setAttribute("aria-busy", "false");
      action.classList.remove("is-loading");
      if ("disabled" in action) action.disabled = false;
      if (action.dataset.readComplete === "true") action.disabled = true;
      action.textContent = previous;
    };
  }

  function markCardRead(card) {
    if (!card) return;
    card.classList.remove("unread");
    const readButton = card.querySelector("[data-read-note]");
    if (readButton) {
      readButton.textContent = "Read";
      readButton.disabled = true;
      readButton.dataset.readComplete = "true";
    }
  }

  function adjustVisibleNotificationCount(delta) {
    document.querySelectorAll("[data-notification-visible-count]").forEach(node => {
      node.textContent = displayCount(Math.max(0, Number(node.textContent || 0) + Number(delta || 0)));
    });
  }

  function pruneEmptyNotificationSections() {
    document.querySelectorAll(".pulse-notification-section").forEach(section => {
      if (!section.querySelector(".pulse-notification-card")) section.remove();
    });
    document.querySelectorAll("[data-pulse-notification-list], [data-notification-list]").forEach(list => {
      if (!list.querySelector(".pulse-notification-card") && !list.querySelector(".empty-state")) {
        list.innerHTML = `<div class="empty-state">No notifications yet.</div>`;
      }
    });
  }

  async function openNoteAction(action) {
    const card = cardForAction(action);
    const noteId = noteIdForAction(action);
    const note = STATE.notesById.get(noteId) || {};
    const restore = setActionBusy(action, true, "Opening...");
    setCardState(card, "Opening notification...", "loading");
    let target = safeInternalUrl(action?.getAttribute("href")) || localNotificationTarget(note) || "/pulse/notifications";
    try {
      if (noteId > 0) {
        const traceId = (window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).slice(0, 80);
        const payload = await notificationApi(`/api/pulse/notifications/${noteId}/resolve`, {
          method: "POST",
          headers: { "X-Trace-Id": traceId },
          body: JSON.stringify({ mark_read: true, client_target: action?.getAttribute("href") || "" })
        });
        applyBadgeCounts(payload);
        markCardRead(card);
        target = safeInternalUrl(payload.target_url) || target || "/pulse/notifications";
        if (payload.fallback_used) setCardState(card, "That destination was unavailable. Opening Notifications instead.", "warning");
      }
    } catch (error) {
      console.warn("[PulseSoc notification open fallback]", { notification_id: noteId, type: note?.type || card?.dataset.noteType || "", target_url: target, trace_id: error.trace_id || "", error });
      setCardState(card, "That destination was unavailable. Opening Notifications instead.", "error");
      target = "/pulse/notifications";
    } finally {
      restore();
    }
    window.setTimeout(() => {
      window.location.assign(safeInternalUrl(target) || "/pulse/notifications");
    }, 40);
  }

  async function markReadAction(action) {
    const card = cardForAction(action);
    const noteId = noteIdForAction(action);
    if (!noteId) return;
    const wasUnread = card?.classList.contains("unread");
    const restore = setActionBusy(action, true, "Saving...");
    markCardRead(card);
    setCardState(card, "Marked read.", "success");
    try {
      const payload = await notificationApi(`/api/pulse/notifications/${noteId}/read`, { method: "POST", body: JSON.stringify({ notification_id: noteId }) });
      applyBadgeCounts(payload);
      broadcastRefresh("mark-read");
    } catch (error) {
      if (wasUnread) card?.classList.add("unread");
      setCardState(card, error.message || "Could not mark this notification read.", "error");
    } finally {
      restore();
    }
  }

  async function deleteNoteAction(action) {
    const card = cardForAction(action);
    const parent = card?.parentNode;
    const next = card?.nextSibling;
    const noteId = noteIdForAction(action);
    if (!noteId || !card || !parent) return;
    const restore = setActionBusy(action, true, "Deleting...");
    card.style.opacity = "0.52";
    window.setTimeout(() => {
      if (card.isConnected) card.remove();
      pruneEmptyNotificationSections();
    }, 30);
    try {
      const payload = await notificationApi(`/api/pulse/notifications/${noteId}`, { method: "DELETE", body: JSON.stringify({ notification_id: noteId }) });
      applyBadgeCounts(payload);
      STATE.notesById.delete(noteId);
      adjustVisibleNotificationCount(-1);
      broadcastRefresh("delete");
    } catch (error) {
      if (!card.isConnected) parent.insertBefore(card, next || null);
      card.style.opacity = "";
      setCardState(card, error.message || "Could not delete this notification.", "error");
    } finally {
      restore();
    }
  }

  async function markAllReadAction(action) {
    const restore = setActionBusy(action, true, "Saving...");
    document.querySelectorAll(".pulse-notification-card.unread").forEach(markCardRead);
    try {
      const payload = await notificationApi("/api/pulse/notifications/read-all", { method: "POST", body: JSON.stringify({}) });
      applyBadgeCounts(payload);
      broadcastRefresh("mark-all-read");
    } catch (error) {
      document.querySelectorAll("[data-note-state]").forEach(node => {
        node.textContent = error.message || "Could not mark all notifications read.";
        node.hidden = false;
        node.dataset.state = "error";
      });
    } finally {
      restore();
    }
  }

  function bindNotificationActions() {
    if (window.__pulseNotificationActionsBound) return;
    window.__pulseNotificationActionsBound = true;
    document.addEventListener("click", event => {
      const open = event.target.closest("[data-open-note]");
      const read = event.target.closest("[data-read-note]");
      const del = event.target.closest("[data-delete-note]");
      const all = event.target.closest("[data-read-all-notes]");
      const action = open || read || del || all;
      if (!action) return;
      event.preventDefault();
      event.stopPropagation();
      if (open) openNoteAction(open);
      else if (read) markReadAction(read);
      else if (del) deleteNoteAction(del);
      else if (all) markAllReadAction(all);
    }, true);
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
      schedulePolling(document.hidden ? 45000 : 30000);
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
    schedulePolling(document.hidden ? 45000 : 30000);
  });
  window.addEventListener("scroll", () => {
    STATE.lastScrollAt = Date.now();
  }, { passive: true });

  window.CoinPilotNotifications = { loadPreferences, loadPulsePreferences, loadPushStatus, subscribePush, unsubscribePush, testNotification, playSound, vibrate, pollNotifications, refreshNotificationList, handleLiveNotification };

  document.addEventListener("DOMContentLoaded", async () => {
    bindNotificationActions();
    bindSettings();
    await loadPreferences().then(renderSettings).catch(() => {});
    await loadPushStatus().catch(() => {});
    bindRealtime();
    window.setTimeout(bindRealtime, 500);
    pollNotifications({ refreshList: true, force: true });
    schedulePolling(30000);
  });
})();
