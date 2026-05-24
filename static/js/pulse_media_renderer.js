(function () {
  "use strict";

  const MAX_RETRIES = 2;
  const LOADED = "is-ready";
  const LOADING = "is-loading";
  const BROKEN = "is-broken";

  function mediaUrl(wrap) {
    return wrap?.dataset.mediaUrl || wrap?.dataset.mediaSrc || "";
  }

  function mark(wrap, state) {
    if (!wrap) return;
    wrap.classList.remove(LOADED, LOADING, BROKEN);
    wrap.classList.add(state);
  }

  function retryUrl(url, count) {
    if (!url || url.startsWith("data:") || url.startsWith("blob:")) return url;
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}pulse_retry=${Date.now()}_${count}`;
  }

  function revealImage(wrap, img) {
    const full = img.dataset.fullSrc || mediaUrl(wrap) || img.currentSrc || img.src;
    if (full && img.src !== new URL(full, location.origin).href) {
      const upgraded = new Image();
      upgraded.decoding = "async";
      upgraded.onload = () => {
        img.src = full;
        img.style.visibility = "";
        mark(wrap, LOADED);
      };
      upgraded.onerror = () => mark(wrap, LOADED);
      upgraded.src = full;
      return;
    }
    img.style.visibility = "";
    mark(wrap, LOADED);
  }

  function failMedia(wrap, media) {
    const retries = Number(wrap.dataset.mediaRetries || 0);
    if (retries < MAX_RETRIES) {
      wrap.dataset.mediaRetries = String(retries + 1);
      const src = media.dataset.fullSrc || mediaUrl(wrap) || media.currentSrc || media.src;
      if (src) {
        media.style.visibility = "hidden";
        media.src = retryUrl(src.split("#")[0], retries + 1);
        if (media.tagName === "VIDEO") media.load();
        return;
      }
    }
    media.style.visibility = "hidden";
    mark(wrap, BROKEN);
    console.warn("Pulse media hydration failed", {
      media_id: wrap.dataset.mediaId || "",
      type: wrap.dataset.mediaType || "",
      src: mediaUrl(wrap),
      diag: wrap.dataset.mediaDiag || "",
    });
  }

  function hydrateWrap(wrap) {
    if (!wrap || wrap.dataset.mediaHydrated === "1") return;
    wrap.dataset.mediaHydrated = "1";
    const media = wrap.querySelector("img,video");
    if (!media) {
      if (wrap.classList.contains(BROKEN)) return;
      mark(wrap, BROKEN);
      return;
    }
    mark(wrap, LOADING);
    media.addEventListener("error", () => failMedia(wrap, media));
    if (media.tagName === "IMG") {
      if (media.complete && media.naturalWidth > 0) {
        revealImage(wrap, media);
      } else {
        media.addEventListener("load", () => revealImage(wrap, media), { once: true });
      }
      return;
    }
    media.addEventListener("loadedmetadata", () => mark(wrap, LOADED), { once: true });
    media.addEventListener("canplay", () => mark(wrap, LOADED), { once: true });
    if (media.readyState >= 1) mark(wrap, LOADED);
  }

  function retry(wrap) {
    if (!wrap) return;
    const media = wrap.querySelector("img,video");
    if (!media) return;
    wrap.dataset.mediaHydrated = "";
    wrap.dataset.mediaRetries = "0";
    wrap.classList.remove(BROKEN);
    const src = media.dataset.fullSrc || mediaUrl(wrap) || media.currentSrc || media.src;
    if (src) media.src = retryUrl(src.split("#")[0], 0);
    if (media.tagName === "VIDEO") media.load();
    hydrateWrap(wrap);
  }

  let observer = null;
  function hydrate(root) {
    const scope = root || document;
    const wraps = Array.from(scope.querySelectorAll(".pulse-media-wrap"));
    if ("IntersectionObserver" in window) {
      if (!observer) {
        observer = new IntersectionObserver(entries => {
          entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            observer.unobserve(entry.target);
            hydrateWrap(entry.target);
          });
        }, { rootMargin: "420px 0px" });
      }
      wraps.forEach(wrap => observer.observe(wrap));
    } else {
      wraps.forEach(hydrateWrap);
    }
  }

  document.addEventListener("click", event => {
    const button = event.target.closest("[data-retry-media]");
    if (!button) return;
    retry(button.closest(".pulse-media-wrap"));
  });

  document.addEventListener("DOMContentLoaded", () => hydrate(document));
  window.PulseMediaRenderer = { hydrate, retry };
})();
