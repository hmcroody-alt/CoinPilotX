(function () {
  "use strict";

  var VERSION = "2026.06.30";
  var MODE_KEY = "pulseshell.performance.mode";
  var TIERS = ["ultra", "balanced", "battery-saver", "reduced-motion", "low-end"];
  var performanceListenersInstalled = false;

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
    var stored = "";
    try {
      stored = window.localStorage && window.localStorage.getItem(MODE_KEY);
    } catch (error) {
      stored = "";
    }
    return normalizeMode(document.documentElement.dataset.pulseshellPerformance)
      || normalizeMode(stored)
      || defaultPerformanceMode();
  }

  function performanceProfile(mode) {
    switch (normalizeMode(mode) || "balanced") {
      case "ultra":
        return { motion: "1", blur: "1", preload: "1", effects: "full" };
      case "battery-saver":
        return { motion: ".35", blur: ".45", preload: ".38", effects: "throttled" };
      case "reduced-motion":
        return { motion: "0", blur: ".22", preload: ".25", effects: "static" };
      case "low-end":
        return { motion: ".2", blur: ".32", preload: ".25", effects: "minimal" };
      default:
        return { motion: ".72", blur: ".7", preload: ".62", effects: "balanced" };
    }
  }

  function applyPulseShellPerformanceMode(value, source) {
    var mode = normalizeMode(value) || defaultPerformanceMode();
    var profile = performanceProfile(mode);
    var root = document.documentElement;
    root.dataset.pulseshellPerformance = mode;
    root.dataset.pulseshellEffects = profile.effects;
    root.style.setProperty("--pulseshell-motion-scale", profile.motion);
    root.style.setProperty("--pulseshell-blur-scale", profile.blur);
    root.style.setProperty("--pulseshell-preload-scale", profile.preload);
    root.classList.toggle("pulseshell-performance-constrained", mode === "battery-saver" || mode === "reduced-motion" || mode === "low-end");
    root.classList.toggle("pulseshell-performance-static", mode === "reduced-motion" || mode === "low-end");
    if (document.body) {
      document.body.classList.toggle("pulseshell-performance-constrained", root.classList.contains("pulseshell-performance-constrained"));
      document.body.classList.toggle("pulseshell-performance-static", root.classList.contains("pulseshell-performance-static"));
    }
    window.dispatchEvent(new CustomEvent("PulseShellPerformanceChanged", {
      detail: { mode: mode, profile: profile, tiers: TIERS, source: source || "web" }
    }));
    return mode;
  }

  function installPulseShellPerformanceGovernance() {
    if (performanceListenersInstalled) return;
    performanceListenersInstalled = true;
    window.PulseShellPerformance = window.PulseShellPerformance || {
      mode: getMode,
      isConstrained: function () {
        var mode = getMode();
        return mode === "battery-saver" || mode === "reduced-motion" || mode === "low-end";
      },
      shouldThrottleEffects: function () {
        var mode = getMode();
        return mode === "battery-saver" || mode === "reduced-motion" || mode === "low-end";
      },
      mediaRootMargin: function (desktop, mobile, constrained) {
        return this.isConstrained() ? (constrained || "180px 0px") : (window.matchMedia && window.matchMedia("(max-width: 768px)").matches ? (mobile || "260px 0px") : (desktop || "520px 0px"));
      }
    };
    window.addEventListener("PulseShellPerformanceModeChanged", function (event) {
      applyPulseShellPerformanceMode(event && event.detail && event.detail.mode, "event");
    });
    window.addEventListener("PulseSocNativeMessage", function (event) {
      var detail = event && event.detail ? event.detail : {};
      if (detail.type === "PULSESHELL_PERFORMANCE_MODE") {
        applyPulseShellPerformanceMode(detail.mode, "native-message");
      }
    });
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        applyPulseShellPerformanceMode(getMode(), "dom-ready");
      }, { once: true });
    } else {
      applyPulseShellPerformanceMode(getMode(), "init");
    }
  }

  function setMode(value) {
    var mode = normalizeMode(value);
    if (!mode) return Promise.resolve({ ok: false, available: true, message: "Unsupported PulseShell performance mode." });
    try {
      window.localStorage && window.localStorage.setItem(MODE_KEY, mode);
    } catch (error) {
      // localStorage can be unavailable in strict private sessions. The mode still applies for this page.
    }
    applyPulseShellPerformanceMode(mode, "setMode");
    window.dispatchEvent(new CustomEvent("PulseShellPerformanceModeChanged", { detail: { mode: mode, tiers: TIERS } }));
    return ok({ mode: mode, tiers: TIERS });
  }

  installPulseShellPerformanceGovernance();

  if (window.PulseShell && window.PulseShell.isNative) {
    window.dispatchEvent(new CustomEvent("PulseShellReady", {
      detail: { native: true, fallback: false, version: window.PulseShell.version || "", performanceMode: getMode() }
    }));
    return;
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

  applyPulseShellPerformanceMode(getMode(), "fallback-ready");
  window.dispatchEvent(new CustomEvent("PulseShellReady", {
    detail: { native: false, fallback: true, version: VERSION, performanceMode: getMode() }
  }));
})();
