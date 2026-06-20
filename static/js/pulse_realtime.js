(function () {
  "use strict";

  if (window.PulseRealtime) return;

  const state = {
    source: null,
    listeners: new Map(),
    connected: false,
    attempts: 0,
    url: "/api/pulse/live/stream",
    timer: 0,
    seen: new Set(),
    queue: [],
    flushTimer: 0,
    maxSeen: 800,
    lastEventId: 0
  };

  function legacySseEnabled() {
    return document.documentElement.dataset.pulseLegacySse === "enabled";
  }

  function emit(type, payload) {
    const list = state.listeners.get(type) || [];
    list.forEach((handler) => {
      try { handler(payload); } catch (error) { console.error("PulseSoc realtime listener failed", error); }
    });
    const all = state.listeners.get("*") || [];
    all.forEach((handler) => {
      try { handler(type, payload); } catch (error) { console.error("PulseSoc realtime wildcard failed", error); }
    });
  }

  function enqueue(type, payload) {
    const eventId = payload && (payload.id || payload.event_id || payload.message_id && `${type}:${payload.message_id}`);
    if (eventId) {
      const key = String(eventId);
      if (state.seen.has(key)) return;
      state.seen.add(key);
      if (state.seen.size > state.maxSeen) {
        state.seen = new Set(Array.from(state.seen).slice(-Math.floor(state.maxSeen / 2)));
      }
    }
    state.queue.push([type, payload]);
    if (state.flushTimer) return;
    state.flushTimer = window.requestAnimationFrame ? window.requestAnimationFrame(flush) : window.setTimeout(flush, 16);
  }

  function flush() {
    state.flushTimer = 0;
    const batch = state.queue.splice(0, 80);
    batch.forEach(function (item) { emit(item[0], item[1]); });
    if (state.queue.length) {
      state.flushTimer = window.requestAnimationFrame ? window.requestAnimationFrame(flush) : window.setTimeout(flush, 16);
    }
  }

  function connect(url) {
    state.url = url || state.url;
    if (!legacySseEnabled()) {
      state.connected = false;
      emit("fallback", { transport: "polling" });
      return;
    }
    if (state.source || !("EventSource" in window)) return;
    try {
      state.source = new EventSource(state.url, { withCredentials: true });
      state.source.onopen = function () {
        state.connected = true;
        state.attempts = 0;
        emit("connected", {});
      };
      state.source.onmessage = function (event) {
        try {
          const data = JSON.parse(event.data || "{}");
          if (data.id) state.lastEventId = Math.max(state.lastEventId, Number(data.id) || 0);
          enqueue(data.event_type || data.type || "message", data);
        } catch (error) {
          enqueue("message", { raw: event.data });
        }
      };
      state.source.addEventListener("pulse", function (event) {
        try {
          const data = JSON.parse(event.data || "{}");
          if (data.latest_event_id) state.lastEventId = Math.max(state.lastEventId, Number(data.latest_event_id) || 0);
          (data.events || []).forEach(function (item) {
            enqueue(item.event_type || item.type || "pulse", item);
          });
          enqueue("pulse", data);
        } catch (error) {
          enqueue("pulse", { raw: event.data });
        }
      });
      state.source.onerror = function () {
        disconnect();
        const jitter = Math.floor(Math.random() * 700);
        const delay = Math.min(30000, 1000 * Math.pow(1.6, state.attempts++)) + jitter;
        state.timer = window.setTimeout(function () { connect(state.url); }, delay);
        emit("reconnecting", { delay });
      };
    } catch (error) {
      console.error("PulseSoc realtime connect failed", error);
    }
  }

  function disconnect() {
    if (state.timer) window.clearTimeout(state.timer);
    if (state.flushTimer) {
      if (window.cancelAnimationFrame) window.cancelAnimationFrame(state.flushTimer);
      else window.clearTimeout(state.flushTimer);
    }
    state.timer = 0;
    state.flushTimer = 0;
    if (state.source) {
      try { state.source.close(); } catch (error) {}
    }
    state.source = null;
    state.connected = false;
  }

  function on(type, handler) {
    if (!state.listeners.has(type)) state.listeners.set(type, []);
    const list = state.listeners.get(type);
    if (!list.includes(handler)) list.push(handler);
    return function off() {
      const current = state.listeners.get(type) || [];
      state.listeners.set(type, current.filter((item) => item !== handler));
    };
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      disconnect();
      return;
    }
    if (!state.source) connect(state.url);
  });

  window.addEventListener("online", function () { connect(state.url); emit("online", {}); });
  window.addEventListener("offline", function () { emit("offline", {}); });
  window.addEventListener("pagehide", disconnect);

  window.PulseRealtime = { connect, disconnect, on, emit, state };
})();
