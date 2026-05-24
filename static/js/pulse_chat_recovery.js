(function () {
  "use strict";

  const PREFIX = "pulse-chat-recovery:v1:";
  const MAX_AGE = 1000 * 60 * 30;
  const MAX_ITEMS = 80;

  function read(key, fallback) {
    try {
      const raw = localStorage.getItem(PREFIX + key);
      if (!raw) return fallback;
      const value = JSON.parse(raw);
      if (value.at && Date.now() - value.at > MAX_AGE) return fallback;
      return value.data ?? fallback;
    } catch (_) {
      return fallback;
    }
  }

  function write(key, data) {
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify({ at: Date.now(), data }));
    } catch (_) {}
  }

  function esc(value) {
    return String(value || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function skeleton(copy) {
    return `<div class="unified-empty pulse-chat-skeleton" aria-live="polite">
      <strong>${esc(copy || "Restoring conversation...")}</strong>
      <span>Messages syncing securely. Recent activity appears as soon as the connection stabilizes.</span>
      <i></i><i></i><i></i>
    </div>`;
  }

  function renderThreadCache(thread, conversationId) {
    const cached = read("thread:" + conversationId, null);
    if (!thread || !cached || !Array.isArray(cached.messages) || !cached.messages.length) return false;
    const rows = cached.messages.slice(-40).map(message => {
      const mine = message.is_mine ? " me" : " them";
      return `<div class="unified-bubble${mine}" data-message-id="${esc(message.message_id || message.id || "")}">
        ${message.body || message.content ? `<div>${esc(message.body || message.content)}</div>` : ""}
        <small>${esc(message.delivery_status || message.status || "cached")}</small>
      </div>`;
    }).join("");
    thread.innerHTML = rows || skeleton("Messages restored locally...");
    thread.dataset.restoredFromCache = "1";
    thread.scrollTop = thread.scrollHeight;
    return true;
  }

  function queuePending(item) {
    const pending = read("pending", []);
    pending.push({ ...item, queued_at: Date.now() });
    write("pending", pending.slice(-MAX_ITEMS));
  }

  function pending() {
    return read("pending", []);
  }

  function clearPending(clientId) {
    if (!clientId) return;
    write("pending", pending().filter(item => item.client_id !== clientId));
  }

  function setStatus(mode, copy) {
    const target = document.querySelector("[data-presence]");
    if (!target) return;
    const text = copy || {
      restoring: "Restoring conversation...",
      syncing: "Messages syncing...",
      reconnecting: "Reconnecting securely...",
      offline: "Offline temporarily. Messages will send when you are back online.",
    }[mode] || "Messages syncing...";
    target.innerHTML = `<span class="presence-dot"></span><span>${esc(text)}</span>`;
  }

  function install() {
    const root = document.querySelector("[data-unified-messenger]");
    if (!root) return;
    const thread = document.querySelector("[data-unified-thread]");
    if (thread && !thread.children.length) thread.innerHTML = skeleton("Loading conversation...");
    window.addEventListener("offline", () => setStatus("offline"));
    window.addEventListener("online", () => setStatus("syncing"));
  }

  document.addEventListener("DOMContentLoaded", install);
  window.PulseChatRecovery = {
    saveList: (tab, items) => write("list:" + tab, items || []),
    loadList: tab => read("list:" + tab, null),
    saveThread: (conversationId, messages) => write("thread:" + conversationId, { messages: (messages || []).slice(-MAX_ITEMS) }),
    loadThread: conversationId => read("thread:" + conversationId, null),
    renderThreadCache,
    skeleton,
    queuePending,
    pending,
    clearPending,
    setStatus,
  };
})();
