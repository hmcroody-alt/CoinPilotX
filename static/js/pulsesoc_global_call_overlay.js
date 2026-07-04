(() => {
  "use strict";

  const VERSION = "fullscreen-incoming-20260704";
  const COMM_REALTIME_URL = "/api/pulse/communications/v2/realtime/stream?limit=80";
  const CALL_CSS = `/static/css/pulsesoc_global_call_overlay.css?v=${VERSION}`;
  const REALTIME_JS = `/static/js/pulse_realtime.js?v=${VERSION}`;
  const LIVEKIT_JS = `/static/vendor/livekit-client.umd.js?v=${VERSION}`;
  const CALLS_JS = `/static/pulsesoc_calls.js?v=${VERSION}`;

  if (window.PulseSocGlobalCallOverlay?.version) return;

  const state = {
    booted: false,
    booting: null,
    accepted: false,
    pausedMedia: [],
    preservedDrafts: [],
    restoreTimer: 0,
  };

  function sameScript(srcPart) {
    return Array.from(document.scripts || []).some((script) => String(script.src || "").includes(srcPart));
  }

  function loadCss(href) {
    if (document.querySelector(`link[href^="${href.split("?")[0]}"]`)) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  function loadScript(src, test) {
    if (test?.()) return Promise.resolve();
    const clean = src.split("?")[0];
    const existing = Array.from(document.scripts || []).find((script) => String(script.src || "").includes(clean));
    if (existing) {
      return new Promise((resolve) => {
        if (test?.()) return resolve();
        existing.addEventListener("load", () => resolve(), { once: true });
        existing.addEventListener("error", () => resolve(), { once: true });
        window.setTimeout(resolve, 1200);
      });
    }
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = src;
      script.defer = true;
      script.addEventListener("load", () => resolve(), { once: true });
      script.addEventListener("error", () => resolve(), { once: true });
      document.head.appendChild(script);
    });
  }

  function isCallMedia(el) {
    return Boolean(el.closest?.("[data-pulsesoc-call-shell]"));
  }

  function pauseActiveMedia() {
    state.pausedMedia = [];
    document.querySelectorAll("video, audio").forEach((el) => {
      if (isCallMedia(el)) return;
      state.pausedMedia.push({
        el,
        paused: el.paused,
        muted: el.muted,
        volume: el.volume,
      });
      if (!el.paused) {
        try { el.pause(); } catch (_) {}
      }
    });
  }

  function restorePausedMedia() {
    window.clearTimeout(state.restoreTimer);
    const items = state.pausedMedia.splice(0);
    items.forEach((item) => {
      const el = item.el;
      if (!el || !document.contains(el)) return;
      try {
        el.muted = item.muted;
        el.volume = item.volume;
        if (!item.paused) el.play?.().catch(() => {});
      } catch (_) {}
    });
  }

  function preserveDrafts() {
    state.preservedDrafts = [];
    document.querySelectorAll("textarea, input[type='text'], input[type='search'], [contenteditable='true']").forEach((el) => {
      if (isCallMedia(el) || el.closest?.("[data-pulsesoc-call-shell]")) return;
      state.preservedDrafts.push({
        el,
        value: el.isContentEditable ? el.innerHTML : el.value,
      });
    });
  }

  function restoreDrafts() {
    state.preservedDrafts.forEach((item) => {
      const el = item.el;
      if (!el || !document.contains(el)) return;
      try {
        if (el.isContentEditable && el.innerHTML !== item.value) el.innerHTML = item.value;
        else if (!el.isContentEditable && el.value !== item.value) el.value = item.value;
      } catch (_) {}
    });
  }

  function focusIncomingShell() {
    const shell = document.querySelector("[data-pulsesoc-call-shell]");
    if (!shell || shell.hidden) return;
    try { shell.focus({ preventScroll: true }); } catch (_) {}
  }

  function vibrateIncoming() {
    try {
      if (navigator.vibrate) navigator.vibrate([90, 45, 90]);
    } catch (_) {}
  }

  function markInterrupted(active) {
    document.documentElement.classList.toggle("pulsesoc-global-call-interrupted", Boolean(active));
    document.body?.classList.toggle("pulsesoc-global-call-interrupted", Boolean(active));
  }

  function handleIncomingCall() {
    state.accepted = false;
    window.clearTimeout(state.restoreTimer);
    preserveDrafts();
    pauseActiveMedia();
    markInterrupted(true);
    vibrateIncoming();
    window.setTimeout(focusIncomingShell, 50);
  }

  function handleAcceptedCall() {
    state.accepted = true;
    markInterrupted(false);
  }

  function finishInterruption() {
    markInterrupted(false);
    restoreDrafts();
    state.restoreTimer = window.setTimeout(() => {
      restorePausedMedia();
      state.accepted = false;
    }, state.accepted ? 350 : 80);
  }

  function bindLifecycle() {
    window.addEventListener("pulsesoc:incoming-call", handleIncomingCall);
    window.addEventListener("pulsesoc:call-accepted", handleAcceptedCall);
    window.addEventListener("pulsesoc:call-declined", finishInterruption);
    window.addEventListener("pulsesoc:call-terminal", finishInterruption);
    window.addEventListener("pulsesoc:call-interruption-ended", finishInterruption);
  }

  async function boot() {
    if (state.booted) return;
    if (state.booting) return state.booting;
    state.booting = (async () => {
      loadCss(CALL_CSS);
      await loadScript(REALTIME_JS, () => Boolean(window.PulseRealtime));
      if (window.PulseRealtime?.connect) {
        try { window.PulseRealtime.connect(COMM_REALTIME_URL); } catch (_) {}
      }
      await loadScript(LIVEKIT_JS, () => Boolean(window.LivekitClient || window.LiveKitClient || window.livekitClient));
      if (!window.PulseSocCalls && !sameScript("/static/pulsesoc_calls.js")) {
        await loadScript(CALLS_JS, () => Boolean(window.PulseSocCalls));
      } else if (!window.PulseSocCalls) {
        await loadScript(CALLS_JS, () => Boolean(window.PulseSocCalls));
      }
      state.booted = true;
      document.documentElement.dataset.pulsesocGlobalCallOverlay = "ready";
    })();
    return state.booting;
  }

  bindLifecycle();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  window.PulseSocGlobalCallOverlay = {
    version: VERSION,
    boot,
    state,
    restore: finishInterruption,
  };
})();
