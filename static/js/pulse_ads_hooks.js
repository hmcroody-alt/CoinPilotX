(function () {
  "use strict";

  const TRACKED = new WeakSet();

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const input = document.querySelector('input[name="csrf_token"]');
    return input && input.value ? input.value : "";
  }

  function createText(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text || "";
    return node;
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

  function renderAd(ad, options) {
    const card = document.createElement("article");
    const cardStyle = String(ad.card_style || "signal-card").replace(/[^a-z0-9_-]/gi, "");
    const placementType = String(ad.placement_type || "feed").replace(/[^a-z0-9_-]/gi, "");
    card.className = `pulse-sponsored-signal pulse-sponsored-signal--${cardStyle} pulse-sponsored-signal--${placementType}`;
    card.dataset.creativeId = String(ad.creative_id || "");
    card.dataset.campaignId = String(ad.campaign_id || "");
    card.dataset.placementKey = ad.placement_key || "";

    const label = createText("span", "pulse-sponsored-signal__label", ad.label || "Sponsored");
    const title = createText("h3", "pulse-sponsored-signal__title", ad.title || "");
    const body = createText("p", "pulse-sponsored-signal__body", ad.body || "");
    const actions = document.createElement("div");
    actions.className = "pulse-sponsored-signal__actions";
    const open = createText("button", "pulse-sponsored-signal__cta", ad.call_to_action || "Learn more");
    open.type = "button";
    const hide = createText("button", "pulse-sponsored-signal__hide", "Hide");
    hide.type = "button";
    const report = createText("button", "pulse-sponsored-signal__report", "Report");
    report.type = "button";

    if (ad.thumbnail_url || ad.media_url) {
      const img = document.createElement("img");
      img.className = "pulse-sponsored-signal__media";
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = "";
      img.src = ad.thumbnail_url || ad.media_url;
      card.appendChild(img);
    }

    open.addEventListener("click", async () => {
      const result = await postEvent("/api/pulse/ads/click", trackingPayload(ad));
      const destination = result && result.destination_url ? result.destination_url : ad.destination_url;
      if (destination) {
        window.open(destination, "_blank", "noopener,noreferrer");
      }
    });
    hide.addEventListener("click", async () => {
      await postEvent("/api/pulse/ads/hide", Object.assign(trackingPayload(ad), { event_type: "hide" }));
      card.remove();
    });
    report.addEventListener("click", async () => {
      await postEvent("/api/pulse/ads/event", Object.assign(trackingPayload(ad), {
        event_type: "report",
        reason: "user_reported"
      }));
      report.textContent = "Reported";
      report.disabled = true;
    });

    actions.append(open, hide, report);
    card.append(label, title, body, actions);
    observeImpression(card, ad, options);
    return card;
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

  function observeImpression(card, ad, options) {
    if (TRACKED.has(card)) return;
    TRACKED.add(card);
    const payload = Object.assign(trackingPayload(ad), {
      viewport: `${window.innerWidth}x${window.innerHeight}`
    });
    postEvent("/api/pulse/ads/impression", payload)
      .then((result) => {
        if (result && result.impression_id) {
          card.dataset.impressionId = String(result.impression_id);
        }
      })
      .catch(() => {});

    if (!("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting || entry.intersectionRatio < 0.5) continue;
        window.setTimeout(() => {
          if (!card.dataset.impressionId) return;
          const visibleMs = options && options.visibleMs ? options.visibleMs : 1200;
          postEvent("/api/pulse/ads/viewability", {
            impression_id: card.dataset.impressionId,
            visible_ms: visibleMs
          }).catch(() => {});
        }, 1200);
        observer.disconnect();
        break;
      }
    }, { threshold: [0.5] });
    observer.observe(card);
  }

  async function loadPlacement(container, options) {
    const target = typeof container === "string" ? document.querySelector(container) : container;
    if (!target) return [];
    const config = Object.assign({
      context: "home",
      limit: 1,
      device_type: window.innerWidth < 760 ? "mobile" : "desktop",
      viewport: `${window.innerWidth}x${window.innerHeight}`
    }, options || {});
    const params = new URLSearchParams(config);
    const response = await fetch(`/api/pulse/ads/placements?${params.toString()}`, { credentials: "same-origin" });
    if (!response.ok) return [];
    const data = await response.json();
    const ads = Array.isArray(data.ads) ? data.ads : [];
    ads.forEach((ad) => target.appendChild(renderAd(ad, config)));
    return ads;
  }

  window.PulseAds = {
    loadPlacement,
    renderAd
  };
})();
