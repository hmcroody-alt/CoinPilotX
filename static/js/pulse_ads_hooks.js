(function () {
  "use strict";

  const TRACKED = new WeakSet();
  const MEDIA_EVENTS = new WeakMap();
  const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SAFE_SCHEMES = new Set(["http:", "https:"]);

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const input = document.querySelector('input[name="csrf_token"]');
    return input && input.value ? input.value : "";
  }

  function sanitizeClass(value, fallback) {
    return String(value || fallback || "signal-card").replace(/[^a-z0-9_-]/gi, "").slice(0, 80) || fallback || "signal-card";
  }

  function safeText(value, fallback) {
    return String(value || fallback || "").replace(/\s+/g, " ").trim();
  }

  function safeUrl(value) {
    try {
      const url = new URL(String(value || ""), window.location.origin);
      return SAFE_SCHEMES.has(url.protocol) ? url.href : "";
    } catch (_) {
      return "";
    }
  }

  function isVideo(ad) {
    const type = String(ad.creative_type || "").toLowerCase();
    const url = String(ad.media_url || "").toLowerCase();
    return type === "video" || /\.(mp4|webm|mov|m4v)(\?|#|$)/.test(url);
  }

  function isAudio(ad) {
    const type = String(ad.creative_type || "").toLowerCase();
    const url = String(ad.media_url || "").toLowerCase();
    return type === "audio" || /\.(mp3|m4a|aac|wav|ogg|webm)(\?|#|$)/.test(url);
  }

  function createText(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = safeText(text);
    return node;
  }

  function createChrome(style) {
    const chrome = document.createElement("div");
    chrome.className = "pulse-sponsored-signal__chrome";
    chrome.setAttribute("aria-hidden", "true");
    if (REDUCED_MOTION) {
      chrome.classList.add("is-static");
      return chrome;
    }
    if (style === "ufo-side") {
      ["orbit", "ship", "beam"].forEach((part) => {
        const span = document.createElement("span");
        span.className = `pulse-sponsored-signal__${part}`;
        chrome.appendChild(span);
      });
      return chrome;
    }
    if (style === "hologram") {
      ["holo", "scan", "projection"].forEach((part) => {
        const span = document.createElement("span");
        span.className = `pulse-sponsored-signal__${part}`;
        chrome.appendChild(span);
      });
      return chrome;
    }
    const pulse = document.createElement("span");
    pulse.className = "pulse-sponsored-signal__projection";
    chrome.appendChild(pulse);
    return chrome;
  }

  async function postEvent(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken()
      },
      body: JSON.stringify(payload || {})
    });
    if (!response.ok) return null;
    return response.json();
  }

  function trackingPayload(ad) {
    return {
      ad_id: ad.ad_id,
      creative_id: ad.creative_id,
      campaign_id: ad.campaign_id,
      placement_key: ad.placement_key,
      delivery_token: ad.delivery_token,
      tracking_nonce: ad.tracking_nonce,
      contextual_category: ad.contextual_category || "",
      country: ad.country || "",
      language: ad.language || ""
    };
  }

  function trackAdEvent(ad, eventType, extra) {
    return postEvent("/api/pulse/ads/event", Object.assign(trackingPayload(ad), extra || {}, {
      event_type: eventType
    })).catch(() => null);
  }

  function renderMedia(ad) {
    const mediaUrl = safeUrl(ad.media_url);
    const thumbnailUrl = safeUrl(ad.thumbnail_url);
    if (!mediaUrl && !thumbnailUrl) return null;

    const shell = document.createElement("div");
    shell.className = "pulse-sponsored-signal__media-shell";
    if (isVideo(ad) && mediaUrl) {
      const video = document.createElement("video");
      video.className = "pulse-sponsored-signal__media pulse-sponsored-signal__video";
      video.src = mediaUrl;
      if (thumbnailUrl) video.poster = thumbnailUrl;
      video.muted = true;
      video.loop = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.setAttribute("aria-label", "Sponsored video preview");
      shell.appendChild(video);
      wireMediaTracking(video, ad, "video");
      return shell;
    }
    if (isAudio(ad) && mediaUrl) {
      const control = document.createElement("button");
      control.type = "button";
      control.className = "pulse-sponsored-signal__audio-control";
      control.setAttribute("aria-label", "Play sponsored audio preview");
      control.append(createText("span", "pulse-sponsored-signal__audio-icon", "Play"));
      const waveform = document.createElement("span");
      waveform.className = "pulse-sponsored-signal__waveform";
      for (let i = 0; i < 10; i += 1) waveform.appendChild(document.createElement("i"));
      control.appendChild(waveform);
      const audio = document.createElement("audio");
      audio.preload = "metadata";
      audio.src = mediaUrl;
      control.addEventListener("click", () => {
        if (audio.paused) {
          audio.play().then(() => {
            control.classList.add("is-playing");
            control.querySelector("span").textContent = "Pause";
            trackAdEvent(ad, "audio_start");
          }).catch(() => trackAdEvent(ad, "error", { reason: "audio_play_failed" }));
        } else {
          audio.pause();
          control.classList.remove("is-playing");
          control.querySelector("span").textContent = "Play";
        }
      });
      audio.addEventListener("ended", () => trackAdEvent(ad, "audio_complete"));
      shell.append(control, audio);
      return shell;
    }
    const img = document.createElement("img");
    img.className = "pulse-sponsored-signal__media";
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.src = thumbnailUrl || mediaUrl;
    shell.appendChild(img);
    return shell;
  }

  function wireMediaTracking(media, ad, kind) {
    const fired = new Set();
    MEDIA_EVENTS.set(media, fired);
    const eventFor = (name) => {
      if (fired.has(name)) return;
      fired.add(name);
      trackAdEvent(ad, name);
    };
    media.addEventListener("play", () => eventFor(kind === "video" ? "video_start" : "audio_start"));
    media.addEventListener("ended", () => eventFor(kind === "video" ? "video_complete" : "audio_complete"));
    media.addEventListener("error", () => trackAdEvent(ad, "error", { reason: `${kind}_media_error` }));
    if (kind === "video") {
      media.addEventListener("timeupdate", () => {
        const duration = Number(media.duration || 0);
        if (!duration) return;
        const pct = media.currentTime / duration;
        if (pct >= 0.25) eventFor("video_25");
        if (pct >= 0.5) eventFor("video_50");
        if (pct >= 0.75) eventFor("video_75");
      });
    }
  }

  function controlVisibleMedia(card, visible) {
    card.querySelectorAll("video.pulse-sponsored-signal__video").forEach((video) => {
      if (REDUCED_MOTION || document.hidden || !visible) {
        video.pause();
        return;
      }
      video.play().catch(() => {});
    });
    if (!visible || document.hidden) {
      card.querySelectorAll("audio").forEach((audio) => audio.pause());
    }
  }

  function renderAd(ad, options) {
    const card = document.createElement("article");
    const cardStyle = sanitizeClass(ad.card_style, "signal-card");
    const placementType = sanitizeClass(ad.placement_type, "feed");
    const placementKey = sanitizeClass(ad.placement_key, "feed_inline");
    card.className = `pulse-sponsored-signal pulse-sponsored-signal--${cardStyle} pulse-sponsored-signal--${placementType} pulse-sponsored-signal--${placementKey}`;
    card.dataset.creativeId = String(ad.creative_id || "");
    card.dataset.campaignId = String(ad.campaign_id || "");
    card.dataset.placementKey = String(ad.placement_key || "");
    card.dataset.pulseSciFiAd = "true";
    card.setAttribute("aria-label", "Sponsored PulseSoc signal");

    const chrome = createChrome(cardStyle);
    const label = createText("span", "pulse-sponsored-signal__label", ad.label || "Sponsored");
    const title = createText("h3", "pulse-sponsored-signal__title", ad.title || "Sponsored signal");
    const body = createText("p", "pulse-sponsored-signal__body", ad.body || "");
    const media = renderMedia(ad);
    const actions = document.createElement("div");
    actions.className = "pulse-sponsored-signal__actions";
    const open = createText("button", "pulse-sponsored-signal__cta", ad.call_to_action || "Learn more");
    open.type = "button";
    const hide = createText("button", "pulse-sponsored-signal__hide", "Hide");
    hide.type = "button";
    const why = document.createElement("details");
    why.className = "pulse-sponsored-signal__why";
    why.append(createText("summary", "", "Why this signal?"));
    why.append(createText("p", "", "This is an approved sponsored placement delivered with review gates, frequency caps, clear labels, and privacy-safe context."));
    const report = createText("button", "pulse-sponsored-signal__report", "Report");
    report.type = "button";

    open.addEventListener("click", async () => {
      const result = await postEvent("/api/pulse/ads/click", trackingPayload(ad));
      const destination = safeUrl(result && result.destination_url ? result.destination_url : ad.destination_url);
      if (destination) window.open(destination, "_blank", "noopener,noreferrer");
    });
    hide.addEventListener("click", async () => {
      await postEvent("/api/pulse/ads/hide", Object.assign(trackingPayload(ad), { event_type: "hide" }));
      card.remove();
    });
    report.addEventListener("click", async () => {
      await trackAdEvent(ad, "report", { reason: "user_reported" });
      report.textContent = "Reported";
      report.disabled = true;
    });

    actions.append(open, hide, report);
    card.append(chrome, label);
    if (media) card.appendChild(media);
    card.append(title, body, actions, why);
    observeImpression(card, ad, options);
    return card;
  }

  function observeImpression(card, ad, options) {
    if (TRACKED.has(card)) return;
    TRACKED.add(card);
    const payload = Object.assign(trackingPayload(ad), {
      viewport: `${window.innerWidth}x${window.innerHeight}`
    });
    postEvent("/api/pulse/ads/impression", payload)
      .then((result) => {
        if (result && result.impression_id) card.dataset.impressionId = String(result.impression_id);
      })
      .catch(() => {});

    if (!("IntersectionObserver" in window)) return;
    let visibleTimer = 0;
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const visible = entry.isIntersecting && entry.intersectionRatio >= 0.5;
        controlVisibleMedia(card, visible);
        if (!visible) {
          if (visibleTimer) window.clearTimeout(visibleTimer);
          visibleTimer = 0;
          continue;
        }
        if (visibleTimer) continue;
        visibleTimer = window.setTimeout(() => {
          if (!card.dataset.impressionId) return;
          const visibleMs = options && options.visibleMs ? options.visibleMs : 1200;
          postEvent("/api/pulse/ads/viewability", {
            impression_id: card.dataset.impressionId,
            visible_ms: visibleMs
          }).catch(() => {});
        }, 1200);
      }
    }, { threshold: [0, 0.5, 0.75] });
    observer.observe(card);
    document.addEventListener("visibilitychange", () => controlVisibleMedia(card, !document.hidden), { passive: true });
  }

  async function loadPlacement(container, options) {
    const target = typeof container === "string" ? document.querySelector(container) : container;
    if (!target) return [];
    const config = Object.assign({
      context: target.dataset.adContext || "home",
      limit: Number(target.dataset.adLimit || 1),
      device_type: window.innerWidth < 760 ? "mobile" : "desktop",
      viewport: `${window.innerWidth}x${window.innerHeight}`
    }, options || {});
    if (target.dataset.adPlacement) config.placement_hint = target.dataset.adPlacement;
    const params = new URLSearchParams(config);
    const response = await fetch(`/api/pulse/ads/placements?${params.toString()}`, { credentials: "same-origin" });
    if (!response.ok) return [];
    const data = await response.json();
    let ads = Array.isArray(data.ads) ? data.ads : [];
    if (target.dataset.adPlacement) {
      ads = ads.filter((ad) => String(ad.placement_key || "") === target.dataset.adPlacement);
    }
    if (target.dataset.adReplace === "true" && ads.length) target.replaceChildren();
    ads.forEach((ad) => target.appendChild(renderAd(ad, config)));
    target.classList.toggle("has-live-sponsored-signal", ads.length > 0);
    return ads;
  }

  function bootAutoPlacements() {
    document.querySelectorAll("[data-pulse-ad-zone]").forEach((zone) => {
      if (zone.dataset.pulseAdBooted === "true") return;
      zone.dataset.pulseAdBooted = "true";
      loadPlacement(zone).catch(() => {
        zone.classList.add("pulse-sponsored-ad-zone--fallback");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootAutoPlacements, { once: true });
  } else {
    bootAutoPlacements();
  }

  document.documentElement.dataset.pulseAdsHook = "ready";
  window.PulseAds = {
    loadPlacement,
    renderAd,
    bootAutoPlacements
  };
})();
