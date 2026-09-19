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

  /**
   * The canonical App Store URL, read from the page rather than written here.
   *
   * `services/app_links.py` is the only thing allowed to decide this string --
   * it validates `PULSESOC_APP_STORE_URL` against the apps.apple.com prefix and
   * falls back to the released listing, so a mistyped environment variable
   * cannot point the website at someone else's app. Hard-coding the id here
   * would create a second source of truth that drifts silently: the server
   * would be corrected and this banner would keep sending people to the old
   * listing, with nothing failing.
   *
   * Three server-emitted sources, most authoritative first:
   *   - `window.PULSE_APP_PROMOTION.appStoreUrl` -- `app_links.app_store_url()`
   *     verbatim, published by `app_promotion.runtime_config()`
   *   - `a[data-app-link="app-store"]` -- the `app_store_badge()` macro
   *   - `<meta name="apple-itunes-app" content="app-id=...">` -- the Smart App
   *     Banner tag, on the paths `wants_smart_app_banner()` scopes it to
   *
   * The two DOM readings are not redundancy for its own sake: this script is
   * injected into every non-gateway page, and the promotion assets are not.
   * Each of the three covers a different slice of the site, and a page carrying
   * none of them gets no banner, which is the correct answer rather than a
   * guess at the id.
   */
  function appStoreUrl() {
    const configured = window.PULSE_APP_PROMOTION?.appStoreUrl;
    if (typeof configured === "string" && configured.indexOf("https://apps.apple.com/") === 0) return configured;
    const badge = document.querySelector('a[data-app-link="app-store"]');
    const href = badge && badge.href ? String(badge.href) : "";
    if (href.indexOf("https://apps.apple.com/") === 0) return href;
    const meta = document.querySelector('meta[name="apple-itunes-app"]');
    const appId = /app-id\s*=\s*(\d+)/i.exec(meta?.content || "")?.[1];
    return appId ? `https://apps.apple.com/app/id${appId}` : "";
  }

  /**
   * On iOS the native app is the product, so this banner promotes the App Store
   * listing instead of teaching the Add to Home Screen gesture.
   *
   * A home-screen bookmark on iOS is a Safari shortcut wearing an app icon: no
   * push notifications, no share-sheet target, no background audio, no CallKit
   * -- none of the things PulseSoc is actually built around. Sending an iPhone
   * visitor there costs the install and hands them the weaker product, and
   * `docs/` makes promoting the native app a standing requirement of the site.
   *
   * Deliberately not gated on Safari any more. The gesture required Safari
   * because only Safari can add to the home screen; a link to the App Store
   * works in Chrome, Firefox and every in-app browser on iOS, and those are
   * exactly the visitors who previously saw nothing at all.
   *
   * Fails closed: with no server-supplied URL this shows nothing rather than
   * falling back to the bookmark instructions.
   */
  function canShowIOSAppPromo() {
    return isIOS() && !isInstalledKnown() && !recentlyDismissed() && isSecureEnough() && Boolean(appStoreUrl());
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
      .pulse-pwa-install button,.pulse-pwa-install a{font:inherit;font-weight:900}
      .pulse-pwa-install__primary,.pulse-pwa-install__later{min-height:42px;border-radius:10px;padding:9px 12px;cursor:pointer}
      .pulse-pwa-install__primary{border:0;background:linear-gradient(135deg,#36e58f,#6edff6);color:#06101b;flex:1}
      /* The iOS primary is an anchor, so it needs the box behaviour a button
         gets for free: centred label, no underline, and the same box-sizing. */
      a.pulse-pwa-install__primary{display:flex;align-items:center;justify-content:center;text-align:center;text-decoration:none;box-sizing:border-box}
      .pulse-pwa-install__later{border:1px solid rgba(110,223,246,.22);background:rgba(255,255,255,.06);color:#f2fbff}
      @media(max-width:640px){.pulse-pwa-install{left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));width:auto}.pulse-pwa-install__actions{display:grid;grid-template-columns:1fr}.pulse-pwa-install__later{width:100%}}
    `;
    document.head.appendChild(style);
  }

  function escapeAttribute(value) {
    return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /**
   * The primary control, which is a different *kind* of control per platform.
   *
   * On Android and desktop the browser owns the install: the only thing that can
   * start it is a click handler calling `deferredPrompt.prompt()`, so it has to
   * be a `<button>`.
   *
   * On iOS the destination is the App Store listing, and that has to be a real
   * `<a href>` rather than a button that navigates. An anchor is what lets iOS
   * hand the tap to the App Store app instead of loading apps.apple.com in a web
   * view, and it is what makes the control long-pressable, shareable, and
   * reachable by VoiceOver as a link. A scripted `location.href` in a tap
   * handler loses all of that and is the kind of thing content blockers eat.
   */
  function primaryControl(kind) {
    if (kind !== "ios") {
      return '<button class="pulse-pwa-install__primary" type="button" data-pulse-pwa-install-button>Install PulseSoc</button>';
    }
    const href = escapeAttribute(appStoreUrl());
    return `<a class="pulse-pwa-install__primary" href="${href}" data-app-link="app-store" data-pulse-pwa-app-store>Get the PulseSoc app</a>`;
  }

  function createPrompt(kind) {
    ensureStyle();
    document.querySelector("[data-pulse-pwa-install]")?.remove();
    const prompt = document.createElement("section");
    prompt.className = "pulse-pwa-install";
    prompt.dataset.pulsePwaInstall = kind;
    prompt.setAttribute("role", "dialog");
    prompt.setAttribute("aria-live", "polite");
    prompt.setAttribute("aria-label", kind === "ios" ? "Get the PulseSoc app" : "Install PulseSoc");
    // The iOS copy sells the native app, not a bookmark. Everything the old
    // wording promised -- "opens faster" -- is the *weakest* thing the real app
    // does, and the three named here are things a home-screen shortcut on iOS
    // simply cannot do at all.
    const body = kind === "ios"
      ? "Get the free PulseSoc app on the App Store for notifications, calls and live video."
      : "Add PulseSoc to your home screen for quicker access to PulseSoc.com.";
    prompt.innerHTML = `
      <div class="pulse-pwa-install__top">
        <img class="pulse-pwa-install__logo" src="/static/brand/pulsesoc-mark-20260913.png" alt="" width="44" height="44">
        <div>
          <h2>${kind === "ios" ? "PulseSoc on iPhone" : "Install PulseSoc"}</h2>
          <p>${body}</p>
        </div>
        <button class="pulse-pwa-install__close" type="button" data-pulse-pwa-dismiss aria-label="Close install prompt">×</button>
      </div>
      <div class="pulse-pwa-install__actions">
        ${primaryControl(kind)}
        <button class="pulse-pwa-install__later" type="button" data-pulse-pwa-dismiss>Maybe later</button>
      </div>
    `;
    document.body.appendChild(prompt);
    prompt.querySelectorAll("[data-pulse-pwa-dismiss]").forEach((button) => {
      button.addEventListener("click", () => dismissPrompt(prompt, kind));
    });
    const installButton = prompt.querySelector("[data-pulse-pwa-install-button]");
    if (installButton) installButton.addEventListener("click", () => install(prompt));
    const appStoreLink = prompt.querySelector("[data-pulse-pwa-app-store]");
    if (appStoreLink) {
      appStoreLink.addEventListener("click", () => {
        // Sets the cooldown rather than the installed flag: leaving for the App
        // Store is not evidence of an install, and iOS gives the website no way
        // to learn the outcome. A day of quiet is the honest reading of it, and
        // `apple-itunes-app` still covers them at the top of the page.
        storageSet(DISMISS_KEY, now());
        log("app_store_opened", { path: location.pathname });
      });
    }
    log(kind === "ios" ? "app_promo_shown" : "prompt_shown", { path: location.pathname });
  }

  function dismissPrompt(prompt, kind) {
    storageSet(DISMISS_KEY, now());
    prompt?.remove();
    promptShown = false;
    log(kind === "ios" ? "app_promo_dismissed" : "dismissed", { path: location.pathname });
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
    // The other half of the one-promotion-at-a-time agreement with
    // static/js/pulse_app_promotion.js. Asking someone to bookmark the website
    // while a card beside it asks them to install the native app reads as spam
    // and converts neither. This prompt yields because it is the one on a
    // timer: it can simply come back on the next navigation, whereas the app
    // surfaces are tied to what the member just reached for.
    if (window.PulseAppPromotion && window.PulseAppPromotion.hasVisibleSurface()) return;
    if (canShowBrowserPrompt()) {
      promptShown = true;
      createPrompt("browser");
      return;
    }
    if (canShowIOSAppPromo()) {
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
