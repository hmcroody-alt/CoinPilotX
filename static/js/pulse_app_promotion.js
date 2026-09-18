/**
 * PulseSoc app promotion — the frequency policy, in one place.
 *
 * The server renders every promotion surface; this script decides which of
 * them is allowed to be visible, remembers dismissals, and reports what
 * happened. It deliberately renders nothing: copy, links and the QR all come
 * from services/app_promotion.py, so a surface cannot end up saying something
 * the server never approved.
 *
 * THE ONE RULE
 * ------------
 * At most one uninvited promotional surface is visible at a time, including
 * the PWA install prompt, which is owned by static/js/pulse_pwa_install.js.
 * That prompt keeps its own 24h dismissal key and its own copy; the two
 * scripts only agree to stand down for each other. Without that agreement a
 * visitor on an iPhone can be asked to add a home-screen bookmark and install
 * a native app in the same breath, which reads as spam and converts neither.
 *
 * WHY DISMISSALS ARE WRITTEN TWICE
 * --------------------------------
 * localStorage for the seven-day memory, sessionStorage for the tab. In
 * private browsing and in locked-down configurations localStorage writes throw
 * or are silently discarded, and the failure mode there is the worst one: the
 * surface reappears on every single navigation for a member who has already
 * said no. sessionStorage usually survives where localStorage does not, and an
 * in-memory set backs up both, so a dismissal degrades from "seven days" to
 * "this tab" rather than to "nothing".
 */
(function () {
  "use strict";

  var config = window.PULSE_APP_PROMOTION || {};
  var SURFACES = config.surfaces || {};
  var EVENTS = config.events || {};
  var PREFIX = config.storagePrefix || "pulseAppPromo:";
  var SESSION_PREFIX = config.sessionStoragePrefix || "pulseAppPromoSession:";
  var MEMORY_MS = Number(config.dismissMemoryMs) || 0;
  var TRACK_ENDPOINT = config.trackEndpoint || "/api/track";

  // Last-resort backing store. Survives neither navigation nor a new tab, but
  // it is the difference between one wasted impression and a surface that
  // cannot be dismissed at all when both Storage APIs are unavailable.
  var memoryDismissed = Object.create(null);
  var shownThisPage = Object.create(null);

  function readStore(store, key) {
    try {
      return store ? store.getItem(key) : null;
    } catch (_) {
      return null;
    }
  }

  function writeStore(store, key, value) {
    try {
      if (store) store.setItem(key, value);
    } catch (_) {
      /* Storage is optional. The policy degrades, the page does not break. */
    }
  }

  function dismissedAt(surface) {
    var raw = readStore(window.localStorage, PREFIX + surface);
    var value = Number(raw || 0);
    return isFinite(value) && value > 0 ? value : 0;
  }

  function isDismissed(surface) {
    if (memoryDismissed[surface]) return true;
    if (readStore(window.sessionStorage, SESSION_PREFIX + surface)) return true;
    var at = dismissedAt(surface);
    // A timestamp from the future means the clock moved backwards or the value
    // was tampered with. Treat it as not dismissed rather than as a dismissal
    // that never expires.
    if (at > Date.now()) return false;
    return at > 0 && Date.now() - at < MEMORY_MS;
  }

  function seenThisSession(surface) {
    return Boolean(readStore(window.sessionStorage, SESSION_PREFIX + "seen:" + surface));
  }

  function markSeenThisSession(surface) {
    writeStore(window.sessionStorage, SESSION_PREFIX + "seen:" + surface, "1");
  }

  function track(eventName, surface) {
    if (!eventName) return;
    // Surface key only. No path, no query string, no resource id: which
    // listing someone was looking at is none of a promotion event's business,
    // and /api/track already records the session and user agent it needs.
    var metadata = { surface: surface };
    try {
      if (window.coinPilotXTrack) {
        window.coinPilotXTrack(eventName, metadata);
        return;
      }
    } catch (_) {
      /* Fall through to the beacon. */
    }
    try {
      if (!navigator.sendBeacon) return;
      navigator.sendBeacon(
        TRACK_ENDPOINT,
        new Blob([JSON.stringify({ event_name: eventName, metadata: metadata })], {
          type: "application/json"
        })
      );
    } catch (_) {
      /* Analytics must never block the page. */
    }
  }

  function elementFor(surface) {
    return document.querySelector('[data-app-promo="' + surface + '"]');
  }

  /**
   * Really painting, not merely un-hidden.
   *
   * The `hidden` attribute alone is not enough. A host page can hide a surface
   * with CSS -- the feed's right rail blanket-hides every child it does not
   * name -- and a surface that never paints must not count against the
   * one-surface budget, or it suppresses every other surface for the whole
   * page view and the member is shown nothing at all.
   */
  function isVisible(element) {
    return Boolean(element) && !element.hidden && element.getClientRects().length > 0;
  }

  function pwaPromptVisible() {
    return Boolean(document.querySelector("[data-pulse-pwa-install]"));
  }

  /** Any uninvited surface currently on screen, ours or the PWA prompt's. */
  function hasVisibleSurface() {
    if (pwaPromptVisible()) return true;
    for (var surface in SURFACES) {
      if (isVisible(elementFor(surface))) return true;
    }
    return false;
  }

  function canShow(surface) {
    var policy = SURFACES[surface];
    if (!policy) return false;
    if (isDismissed(surface)) return false;
    if (policy.oncePerSession && (shownThisPage[surface] || seenThisSession(surface))) return false;
    return !hasVisibleSurface();
  }

  function show(surface) {
    var element = elementFor(surface);
    if (!element || isVisible(element)) return false;
    if (!canShow(surface)) {
      track(EVENTS.suppressed, surface);
      return false;
    }
    element.hidden = false;
    shownThisPage[surface] = true;
    if (SURFACES[surface] && SURFACES[surface].oncePerSession) markSeenThisSession(surface);
    track(EVENTS.shown, surface);
    return true;
  }

  function dismiss(surface) {
    memoryDismissed[surface] = true;
    writeStore(window.localStorage, PREFIX + surface, String(Date.now()));
    writeStore(window.sessionStorage, SESSION_PREFIX + surface, "1");
    var element = elementFor(surface);
    if (element) element.hidden = true;
    track(EVENTS.dismissed, surface);
  }

  /**
   * Surfaces the server rendered visible but the member has already dismissed.
   *
   * The sidebar card ships un-hidden so it is painted with the page and costs
   * no layout shift; the server cannot know about a dismissal, because the
   * memory is in the browser. Hiding it here is a shift, which is why the card
   * is the last element in its column -- removing it moves nothing above it.
   */
  function hideDismissed() {
    for (var surface in SURFACES) {
      var element = elementFor(surface);
      if (!element || element.hidden) continue;
      if (isDismissed(surface) || pwaPromptVisible()) {
        element.hidden = true;
        continue;
      }
      shownThisPage[surface] = true;
      if (SURFACES[surface].oncePerSession) markSeenThisSession(surface);
      track(EVENTS.shown, surface);
    }
  }

  function onDismissClick(event) {
    var trigger = event.target.closest("[data-app-promo-dismiss]");
    if (!trigger) return;
    dismiss(trigger.getAttribute("data-app-promo-dismiss"));
  }

  function onActionClick(event) {
    var trigger = event.target.closest('[data-app-promo-action="app-store"]');
    if (!trigger) return;
    track(EVENTS.appStore, trigger.getAttribute("data-app-promo-surface") || "unknown");
  }

  function onHeaderToggle(event) {
    var details = event.target.closest("[data-app-promo-header]");
    if (!details || !details.open) return;
    track(EVENTS.headerOpened, "header_control");
  }

  /**
   * The Marketplace explanation, on the first reach for a Marketplace link.
   *
   * `pointerenter` and `focusin` rather than `click`: clicking a Marketplace
   * link navigates to the `/open/` interstitial, so an explanation shown on
   * click would be destroyed by the navigation it was explaining. Hovering or
   * tabbing onto the link is the last moment it can still be useful.
   *
   * Listeners are removed after the first firing that actually *showed* the
   * note, so the "once" in `oncePerSession` is enforced by the DOM as well as
   * by storage. Retiring them on a suppressed attempt instead would make the
   * note unreachable on every desktop shell page: the sidebar card is visible
   * at first paint, so the first hover is always suppressed, and the member
   * would get nothing on any later hover -- including after dismissing the
   * card, the one moment the note has room to appear.
   */
  function watchMarketplaceIntent() {
    var surface = "marketplace_note";
    if (!SURFACES[surface] || !elementFor(surface)) return;
    var links = document.querySelectorAll("[data-app-promo-marketplace]");
    if (!links.length) return;

    function trigger() {
      if (show(surface)) stop();
    }

    function stop() {
      links.forEach(function (link) {
        link.removeEventListener("pointerenter", trigger);
        link.removeEventListener("focus", trigger);
      });
    }

    links.forEach(function (link) {
      link.addEventListener("pointerenter", trigger);
      link.addEventListener("focus", trigger);
    });
  }

  function start() {
    if (!Object.keys(SURFACES).length) return;
    document.addEventListener("click", onDismissClick);
    document.addEventListener("click", onActionClick);
    document.addEventListener("toggle", onHeaderToggle, true);
    hideDismissed();
    watchMarketplaceIntent();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }

  window.PulseAppPromotion = {
    hasVisibleSurface: hasVisibleSurface,
    isDismissed: isDismissed,
    dismiss: dismiss,
    show: show,
    canShow: canShow
  };
})();
