(function () {
  "use strict";

  if (window.PulseRealtime) return;

  const state = {
    source: null,
    listeners: new Map(),
    connected: false,
    attempts: 0,
    url: "/api/pulse/live/stream",
    timer: 0
  };

  function emit(type, payload) {
    const list = state.listeners.get(type) || [];
    list.forEach((handler) => {
      try { handler(payload); } catch (error) { console.error("Pulse realtime listener failed", error); }
    });
    const all = state.listeners.get("*") || [];
    all.forEach((handler) => {
      try { handler(type, payload); } catch (error) { console.error("Pulse realtime wildcard failed", error); }
    });
  }

  function connect(url) {
    state.url = url || state.url;
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
          emit(data.event_type || data.type || "message", data);
        } catch (error) {
          emit("message", { raw: event.data });
        }
      };
      state.source.addEventListener("pulse", function (event) {
        try {
          const data = JSON.parse(event.data || "{}");
          (data.events || []).forEach(function (item) {
            emit(item.event_type || item.type || "pulse", item);
          });
          emit("pulse", data);
        } catch (error) {
          emit("pulse", { raw: event.data });
        }
      });
      state.source.onerror = function () {
        disconnect();
        const delay = Math.min(30000, 1000 * Math.pow(1.6, state.attempts++));
        state.timer = window.setTimeout(function () { connect(state.url); }, delay);
        emit("reconnecting", { delay });
      };
    } catch (error) {
      console.error("Pulse realtime connect failed", error);
    }
  }

  function disconnect() {
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = 0;
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
    if (document.hidden) return;
    if (!state.source) connect(state.url);
  });

  window.addEventListener("pagehide", disconnect);

  window.PulseRealtime = { connect, disconnect, on, emit, state };
})();
