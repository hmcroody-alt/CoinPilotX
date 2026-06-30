(function () {
  "use strict";

  if (window.PulseShell && window.PulseShell.isNative) {
    window.dispatchEvent(new CustomEvent("PulseShellReady", {
      detail: { native: true, fallback: false, version: window.PulseShell.version || "" }
    }));
    return;
  }

  var VERSION = "2026.06.30";
  var MODE_KEY = "pulseshell.performance.mode";
  var TIERS = ["ultra", "balanced", "battery-saver", "reduced-motion", "low-end"];

  function unavailable(moduleName) {
    return Promise.resolve({
      ok: false,
      available: false,
      module: moduleName,
      message: "PulseShell native " + moduleName + " is unavailable in this browser session."
    });
  }

  function ok(data) {
    return Promise.resolve({ ok: true, available: true, data: data || {} });
  }

  function normalizeMode(value) {
    var mode = String(value || "").toLowerCase().replace(/_/g, "-");
    return TIERS.indexOf(mode) >= 0 ? mode : "";
  }

  function defaultPerformanceMode() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return "reduced-motion";
    var nav = window.navigator || {};
    if (Number(nav.deviceMemory || 0) > 0 && Number(nav.deviceMemory || 0) <= 2) return "low-end";
    var connection = nav.connection || nav.mozConnection || nav.webkitConnection;
    if (connection && (connection.saveData || /(^|-)2g$/.test(String(connection.effectiveType || "")))) return "battery-saver";
    return "balanced";
  }

  function getMode() {
    return normalizeMode(window.localStorage && window.localStorage.getItem(MODE_KEY)) || defaultPerformanceMode();
  }

  function setMode(value) {
    var mode = normalizeMode(value);
    if (!mode) return Promise.resolve({ ok: false, available: true, message: "Unsupported PulseShell performance mode." });
    try {
      window.localStorage && window.localStorage.setItem(MODE_KEY, mode);
    } catch (error) {
      // localStorage can be unavailable in strict private sessions. The mode still applies for this page.
    }
    document.documentElement.dataset.pulseshellPerformance = mode;
    window.dispatchEvent(new CustomEvent("PulseShellPerformanceModeChanged", { detail: { mode: mode, tiers: TIERS } }));
    return ok({ mode: mode, tiers: TIERS });
  }

  function share(payload) {
    var data = payload || {};
    var url = String(data.url || window.location.href);
    var title = String(data.title || document.title || "PulseSoc");
    var text = String(data.text || title);
    if (navigator.share) {
      return navigator.share({ title: title, text: text, url: url })
        .then(function () { return { ok: true, available: true, data: { shared: true, native: false } }; })
        .catch(function (error) { return { ok: false, available: true, message: error && error.message ? error.message : "Share cancelled." }; });
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(url)
        .then(function () { return { ok: true, available: true, data: { copied: true, native: false } }; })
        .catch(function () { return unavailable("ShareBridge"); });
    }
    return unavailable("ShareBridge");
  }

  function safeAreaInsets() {
    var style = getComputedStyle(document.documentElement);
    return ok({
      insets: {
        top: style.getPropertyValue("--safe-area-inset-top") || "env(safe-area-inset-top)",
        right: style.getPropertyValue("--safe-area-inset-right") || "env(safe-area-inset-right)",
        bottom: style.getPropertyValue("--safe-area-inset-bottom") || "env(safe-area-inset-bottom)",
        left: style.getPropertyValue("--safe-area-inset-left") || "env(safe-area-inset-left)"
      }
    });
  }

  window.PulseShell = {
    version: VERSION,
    platform: "web",
    isNative: false,
    isAvailable: false,
    camera: { requestPermission: function () { return unavailable("CameraBridge"); } },
    microphone: { requestPermission: function () { return unavailable("MicrophoneBridge"); } },
    live: { startHostSession: function () { return unavailable("LiveStreamingBridge"); } },
    push: { registerDevice: function () { return unavailable("PushNotificationBridge"); } },
    share: { openNativeShareSheet: share },
    filePicker: { open: function () { return unavailable("FilePickerBridge"); } },
    haptics: { impact: function () { return unavailable("HapticsBridge"); } },
    deepLinks: {
      open: function (target) {
        var url = typeof target === "string" ? target : target && (target.url || target.path);
        if (!url) return Promise.resolve({ ok: false, available: true, message: "No route was provided." });
        window.location.href = String(url);
        return ok({ url: String(url) });
      }
    },
    device: {
      getInfo: function () {
        return ok({
          shell: "PulseShell",
          shellVersion: VERSION,
          native: false,
          platform: "web",
          userAgent: navigator.userAgent,
          performanceMode: getMode()
        });
      }
    },
    permissions: {
      check: function (permission) {
        return ok({ permission: permission || "", status: "browser-managed", granted: false });
      },
      request: function (permission) {
        return unavailable("PermissionBridge:" + (permission || "unknown"));
      }
    },
    performance: {
      getMode: function () { return ok({ mode: getMode(), tiers: TIERS }); },
      setMode: setMode
    },
    safeArea: { getInsets: safeAreaInsets },
    keyboard: { status: function () { return unavailable("KeyboardBridge"); } },
    backgroundAudio: { status: function () { return unavailable("BackgroundAudioBridge"); } },
    payment: { status: function () { return unavailable("PaymentBridge"); } },
    offlineCache: { status: function () { return unavailable("OfflineCacheBridge"); } },
    crashRecovery: { status: function () { return unavailable("CrashRecoveryBridge"); } }
  };

  document.documentElement.dataset.pulseshellPerformance = getMode();
  window.dispatchEvent(new CustomEvent("PulseShellReady", {
    detail: { native: false, fallback: true, version: VERSION, performanceMode: getMode() }
  }));
})();
