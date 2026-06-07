(function () {
  "use strict";

  const PROMPT_DELAY_MS = 60000;
  const DISMISS_COOLDOWN_MS = 24 * 60 * 60 * 1000;
  const DISMISS_KEY = "pulsePwaInstallDismissedAt";
  const INSTALLED_KEY = "pulsePwaInstalledAt";
  const EVENT_PREFIX = "pulse_pwa_install_";
  let deferredPrompt = null;
  let promptShown = false;
  let usageTimer = null;
  let usageStartedAt = 0;
  let usageMatured = false;

  function now() {
    return Date.now();
  }

  function storageGet(key) {
    try {
      return Number(localStorage.getItem(key) || 0);
    } catch (_) {
      return 0;
    }
  }

  function storageSet(key, value) {
    try {
      localStorage.setItem(key, String(value));
    } catch (_) {
      /* localStorage can be unavailable in locked-down browsers. */
    }
  }

  function log(eventName, detail) {
    const name = EVENT_PREFIX + eventName;
    try {
      if (window.coinPilotXTrack) window.coinPilotXTrack(name, detail || {});
      else if (window.gtag) window.gtag("event", name, detail || {});
    } catch (_) {
      /* Analytics must never block install UX. */
    }
    try {
      window.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
    } catch (_) {
      /* CustomEvent is only for local QA hooks. */
    }
  }

  function isStandalone() {
    return Boolean(
      window.matchMedia?.("(display-mode: standalone)")?.matches ||
      window.matchMedia?.("(display-mode: fullscreen)")?.matches ||
      window.navigator.standalone === true
    );
  }

  function isSecureEnough() {
    return window.isSecureContext || location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1";
  }

  function recentlyDismissed() {
    const dismissedAt = storageGet(DISMISS_KEY);
    return dismissedAt > 0 && now() - dismissedAt < DISMISS_COOLDOWN_MS;
  }

  function isInstalledKnown() {
    return isStandalone() || storageGet(INSTALLED_KEY) > 0;
  }

  function userAgent() {
    return navigator.userAgent || "";
  }

  function isIOS() {
    return /iphone|ipad|ipod/i.test(userAgent()) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }

  function isSafariLike() {
    const ua = userAgent();
    return /safari/i.test(ua) && !/chrome|crios|fxios|edgios|opr\//i.test(ua);
  }

  function canShowIOSInstructions() {
    return isIOS() && isSafariLike() && !isInstalledKnown() && !recentlyDismissed() && isSecureEnough();
  }

  function canShowBrowserPrompt() {
    return Boolean(deferredPrompt) && !isInstalledKnown() && !recentlyDismissed() && isSecureEnough();
  }

  function ensureStyle() {
    if (document.getElementById("pulse-pwa-install-style")) return;
    const style = document.createElement("style");
    style.id = "pulse-pwa-install-style";
    style.textContent = `
      .pulse-pwa-install{position:fixed;right:18px;bottom:calc(18px + env(safe-area-inset-bottom));z-index:2147483000;width:min(92vw,390px);border:1px solid rgba(110,223,246,.34);border-radius:16px;background:linear-gradient(180deg,rgba(8,19,35,.98),rgba(3,8,17,.98));box-shadow:0 24px 80px rgba(0,0,0,.46),0 0 36px rgba(54,229,143,.16);color:#f2fbff;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow:hidden}
      .pulse-pwa-install[hidden]{display:none!important}
      .pulse-pwa-install__top{display:flex;gap:12px;align-items:flex-start;padding:16px 16px 10px}
      .pulse-pwa-install__logo{width:44px;height:44px;border-radius:12px;object-fit:cover;box-shadow:0 0 22px rgba(54,229,143,.24);flex:0 0 auto}
      .pulse-pwa-install h2{margin:0;font-size:20px;line-height:1.12;letter-spacing:0}
      .pulse-pwa-install p{margin:6px 0 0;color:#a9bac8;line-height:1.45;font-size:14px}
      .pulse-pwa-install__close{margin-left:auto;width:36px;height:36px;border-radius:999px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);color:#f2fbff;font-size:20px;line-height:1;cursor:pointer}
      .pulse-pwa-install__actions{display:flex;gap:8px;padding:0 16px 16px}
      .pulse-pwa-install button{font:inherit;font-weight:900}
      .pulse-pwa-install__primary,.pulse-pwa-install__later{min-height:42px;border-radius:10px;padding:9px 12px;cursor:pointer}
      .pulse-pwa-install__primary{border:0;background:linear-gradient(135deg,#36e58f,#6edff6);color:#06101b;flex:1}
      .pulse-pwa-install__later{border:1px solid rgba(110,223,246,.22);background:rgba(255,255,255,.06);color:#f2fbff}
      @media(max-width:640px){.pulse-pwa-install{left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));width:auto}.pulse-pwa-install__actions{display:grid;grid-template-columns:1fr}.pulse-pwa-install__later{width:100%}}
    `;
    document.head.appendChild(style);
  }

  function createPrompt(kind) {
    ensureStyle();
    document.querySelector("[data-pulse-pwa-install]")?.remove();
    const prompt = document.createElement("section");
    prompt.className = "pulse-pwa-install";
    prompt.dataset.pulsePwaInstall = kind;
    prompt.setAttribute("role", "dialog");
    prompt.setAttribute("aria-live", "polite");
    prompt.setAttribute("aria-label", kind === "ios" ? "Add PulseSoc to your home screen" : "Install PulseSoc");
    const body = kind === "ios"
      ? "Tap Share, then Add to Home Screen. PulseSoc.com opens faster when PulseSoc is on your home screen."
      : "Add PulseSoc to your home screen for quicker access to PulseSoc.com.";
    prompt.innerHTML = `
      <div class="pulse-pwa-install__top">
        <img class="pulse-pwa-install__logo" src="/static/brand/pulsesoc-logo-20260606.png" alt="" width="44" height="44">
        <div>
          <h2>${kind === "ios" ? "Add PulseSoc to your home screen" : "Install PulseSoc"}</h2>
          <p>${body}</p>
        </div>
        <button class="pulse-pwa-install__close" type="button" data-pulse-pwa-dismiss aria-label="Close install prompt">×</button>
      </div>
      <div class="pulse-pwa-install__actions">
        ${kind === "ios" ? "" : '<button class="pulse-pwa-install__primary" type="button" data-pulse-pwa-install-button>Install PulseSoc</button>'}
        <button class="pulse-pwa-install__later" type="button" data-pulse-pwa-dismiss>Maybe later</button>
      </div>
    `;
    document.body.appendChild(prompt);
    prompt.querySelectorAll("[data-pulse-pwa-dismiss]").forEach((button) => {
      button.addEventListener("click", () => dismissPrompt(prompt, kind));
    });
    const installButton = prompt.querySelector("[data-pulse-pwa-install-button]");
    if (installButton) installButton.addEventListener("click", () => install(prompt));
    log(kind === "ios" ? "ios_instructions_shown" : "prompt_shown", { path: location.pathname });
  }

  function dismissPrompt(prompt, kind) {
    storageSet(DISMISS_KEY, now());
    prompt?.remove();
    promptShown = false;
    log(kind === "ios" ? "ios_instructions_dismissed" : "dismissed", { path: location.pathname });
  }

  async function install(prompt) {
    if (!deferredPrompt) return;
    try {
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      const outcome = choice?.outcome || "unknown";
      log(outcome === "accepted" ? "accepted" : "dismissed", { outcome, path: location.pathname });
      if (outcome === "accepted") storageSet(INSTALLED_KEY, now());
      else storageSet(DISMISS_KEY, now());
    } catch (error) {
      storageSet(DISMISS_KEY, now());
      log("error", { message: String(error?.message || error || "install prompt failed").slice(0, 180) });
    } finally {
      deferredPrompt = null;
      prompt?.remove();
      promptShown = false;
    }
  }

  function maybeShowPrompt() {
    usageMatured = true;
    if (promptShown || isInstalledKnown() || recentlyDismissed()) return;
    if (canShowBrowserPrompt()) {
      promptShown = true;
      createPrompt("browser");
      return;
    }
    if (canShowIOSInstructions()) {
      promptShown = true;
      createPrompt("ios");
    }
  }

  function startMeaningfulUsageTimer() {
    if (usageTimer || isInstalledKnown() || recentlyDismissed()) return;
    if (!usageStartedAt) usageStartedAt = now();
    usageTimer = window.setTimeout(maybeShowPrompt, PROMPT_DELAY_MS);
  }

  function promptWhenUsageIsMeaningful() {
    if (!usageStartedAt) usageStartedAt = now();
    if (usageMatured || now() - usageStartedAt >= PROMPT_DELAY_MS) {
      maybeShowPrompt();
      return;
    }
    startMeaningfulUsageTimer();
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator) || !isSecureEnough()) return;
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((registration) => {
        log("service_worker_registered", { scope: registration.scope });
      }).catch((error) => {
        log("service_worker_failed", { message: String(error?.message || error || "registration failed").slice(0, 180) });
      });
    }, { once: true });
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    log("available", { path: location.pathname });
    promptWhenUsageIsMeaningful();
  });

  window.addEventListener("appinstalled", () => {
    storageSet(INSTALLED_KEY, now());
    deferredPrompt = null;
    document.querySelector("[data-pulse-pwa-install]")?.remove();
    log("installed", { path: location.pathname });
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) startMeaningfulUsageTimer();
  });

  document.addEventListener("DOMContentLoaded", () => {
    registerServiceWorker();
    startMeaningfulUsageTimer();
  });

  window.PulsePWAInstall = {
    maybeShowPrompt,
    promptWhenUsageIsMeaningful,
    get deferredPromptAvailable() {
      return Boolean(deferredPrompt);
    },
    get usageMatured() {
      return usageMatured;
    },
    isStandalone,
  };
})();
