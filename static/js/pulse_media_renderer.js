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

  function setBackdrop(wrap) {
    const src = wrap?.dataset.mediaBackdrop || wrap?.dataset.mediaPoster || wrap?.dataset.mediaThumb || mediaUrl(wrap);
    if (!wrap || !src || wrap.style.getPropertyValue("--media-backdrop")) return;
    wrap.style.setProperty("--media-backdrop", `url("${src.replace(/"/g, "%22")}")`);
  }

  function applyAmbientColor(wrap, media, force = false) {
    if (!wrap || !media || (!force && wrap.dataset.mediaAmbient === "1")) return;
    wrap.dataset.mediaAmbient = "1";
    try {
      const canvas = document.createElement("canvas");
      const size = 24;
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return;
      ctx.drawImage(media, 0, 0, size, size);
      const data = ctx.getImageData(0, 0, size, size).data;
      let r = 0, g = 0, b = 0, n = 0;
      for (let i = 0; i < data.length; i += 16) {
        const alpha = data[i + 3];
        if (alpha < 24) continue;
        r += data[i];
        g += data[i + 1];
        b += data[i + 2];
        n += 1;
      }
      if (!n) return;
      r = Math.round(r / n);
      g = Math.round(g / n);
      b = Math.round(b / n);
      const tealBias = Math.max(g, Math.round((g + 54) * .86));
      const blueBias = Math.max(b, Math.round((b + 70) * .82));
      wrap.style.setProperty("--pulse-media-rgb", `${Math.min(255, r)}, ${Math.min(255, tealBias)}, ${Math.min(255, blueBias)}`);
      wrap.style.setProperty("--pulse-media-x", `${38 + (r % 26)}%`);
      wrap.style.setProperty("--pulse-media-y", `${28 + (b % 32)}%`);
    } catch (_) {
      wrap.style.setProperty("--pulse-media-rgb", "110, 223, 246");
    }
  }

  function bindVideoAmbient(wrap, video) {
    if (!wrap || !video || wrap.dataset.videoAmbientBound === "1") return;
    wrap.dataset.videoAmbientBound = "1";
    let lastSample = 0;
    const sample = () => {
      const now = Date.now();
      if (now - lastSample < 1800) return;
      lastSample = now;
      applyAmbientColor(wrap, video, true);
    };
    video.addEventListener("playing", sample);
    video.addEventListener("timeupdate", sample);
  }

  function retryUrl(url, count) {
    if (!url || url.startsWith("data:") || url.startsWith("blob:")) return url;
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}pulse_retry=${Date.now()}_${count}`;
  }

  function videoSource(media) {
    return media?.currentSrc || media?.src || media?.querySelector?.("source")?.src || "";
  }

  function mediaSource(media) {
    if (!media) return "";
    return media.tagName === "VIDEO" ? videoSource(media) : (media.dataset.fullSrc || media.currentSrc || media.src || "");
  }

  function setMediaSource(media, url) {
    if (!media || !url) return;
    if (media.tagName === "VIDEO") {
      const source = media.querySelector("source");
      if (source) {
        source.src = url;
      } else {
        media.src = url;
      }
      media.load();
      return;
    }
    media.src = url;
  }

  function videoErrorDetails(video) {
    const error = video?.error;
    const codes = {
      1: "aborted",
      2: "network",
      3: "decode",
      4: "source_not_supported",
    };
    return {
      code: error?.code || 0,
      reason: codes[error?.code] || "unknown",
      message: error?.message || "",
      network_state: video?.networkState,
      ready_state: video?.readyState,
      current_src: videoSource(video),
    };
  }

  function reportVideoDiagnostics(wrap, video, eventName) {
    if (!wrap || !video) return;
    const src = videoSource(video) || mediaUrl(wrap);
    console.warn("Pulse video diagnostic", {
      event: eventName,
      media_id: wrap.dataset.mediaId || "",
      src,
      mime_type: wrap.dataset.mediaMime || "",
      media_type: wrap.dataset.mediaType || "",
      poster: wrap.dataset.mediaPoster || "",
      cdn: src.startsWith("https://cdn.coinpilotx.app/"),
      private_r2: src.includes("r2.cloudflarestorage.com"),
      error: videoErrorDetails(video),
      diag: wrap.dataset.mediaDiag || "",
    });
    if (src && src.startsWith("https://cdn.coinpilotx.app/")) {
      fetch(src, { method: "HEAD", mode: "cors", cache: "no-store" })
        .then(response => console.info("Pulse video CDN HEAD", {
          media_id: wrap.dataset.mediaId || "",
          status: response.status,
          content_type: response.headers.get("content-type") || "",
          accept_ranges: response.headers.get("accept-ranges") || "",
          content_length: response.headers.get("content-length") || "",
        }))
        .catch(error => console.warn("Pulse video CDN HEAD failed", {
          media_id: wrap.dataset.mediaId || "",
          src,
          message: error?.message || String(error),
        }));
    }
  }

  function revealImage(wrap, img) {
    setBackdrop(wrap);
    applyAmbientColor(wrap, img);
    const full = img.dataset.fullSrc || mediaUrl(wrap) || img.currentSrc || img.src;
    if (full && img.src !== new URL(full, location.origin).href) {
      const upgraded = new Image();
      upgraded.decoding = "async";
      upgraded.onload = () => {
        img.src = full;
        img.style.visibility = "";
        setBackdrop(wrap);
        applyAmbientColor(wrap, img);
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
    if (media?.tagName === "VIDEO") reportVideoDiagnostics(wrap, media, retries ? "retry_error" : "load_error");
    if (retries < MAX_RETRIES) {
      wrap.dataset.mediaRetries = String(retries + 1);
      const src = mediaSource(media) || mediaUrl(wrap);
      if (src) {
        media.style.visibility = "hidden";
        if (media.tagName === "VIDEO") {
          media.addEventListener("loadedmetadata", () => {
            media.style.visibility = "";
            mark(wrap, LOADED);
          }, { once: true });
          setMediaSource(media, retryUrl(src.split("#")[0], retries + 1));
        } else {
          media.addEventListener("load", () => revealImage(wrap, media), { once: true });
          setMediaSource(media, retryUrl(src.split("#")[0], retries + 1));
        }
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
    setBackdrop(wrap);
    const media = wrap.querySelector("img,video");
    const canonicalSrc = mediaUrl(wrap);
    if (canonicalSrc.includes("r2.cloudflarestorage.com")) {
      console.warn("Pulse media received private R2 URL; renderer requires CDN URL", {
        media_id: wrap.dataset.mediaId || "",
        src: canonicalSrc,
      });
    }
    if (window.localStorage?.getItem("pulseDebugMedia") === "1") {
      console.debug("Pulse media render state", {
        media_id: wrap.dataset.mediaId || "",
        type: wrap.dataset.mediaType || "",
        src: mediaUrl(wrap),
        thumb: wrap.dataset.mediaThumb || "",
        poster: wrap.dataset.mediaPoster || "",
        has_element: !!media,
        tag: media?.tagName || "",
        current_src: media?.currentSrc || media?.src || "",
        complete: media?.complete,
        natural_width: media?.naturalWidth,
        ready_state: media?.readyState,
        diag: wrap.dataset.mediaDiag || "",
      });
    }
    if (!media) {
      if (wrap.classList.contains(BROKEN)) return;
      mark(wrap, BROKEN);
      return;
    }
    mark(wrap, LOADING);
    media.addEventListener("error", () => {
      if (media.tagName === "VIDEO") reportVideoDiagnostics(wrap, media, "error");
      failMedia(wrap, media);
    });
    if (media.tagName === "IMG") {
      if (media.complete && media.naturalWidth > 0) {
        revealImage(wrap, media);
      } else if (media.complete && media.naturalWidth === 0) {
        failMedia(wrap, media);
      } else {
        media.addEventListener("load", () => revealImage(wrap, media), { once: true });
      }
      return;
    }
    bindVideoAmbient(wrap, media);
    media.addEventListener("loadedmetadata", () => {
      console.info("Pulse video metadata loaded", {
        media_id: wrap.dataset.mediaId || "",
        src: videoSource(media) || mediaUrl(wrap),
        duration: media.duration,
        width: media.videoWidth,
        height: media.videoHeight,
        ready_state: media.readyState,
      });
      mark(wrap, LOADED);
    }, { once: true });
    media.addEventListener("canplay", () => {
      console.info("Pulse video canplay", {
        media_id: wrap.dataset.mediaId || "",
        src: videoSource(media) || mediaUrl(wrap),
      });
      applyAmbientColor(wrap, media);
      mark(wrap, LOADED);
    }, { once: true });
    if (media.readyState >= 1) {
      applyAmbientColor(wrap, media);
      mark(wrap, LOADED);
    }
  }

  function retry(wrap) {
    if (!wrap) return;
    const media = wrap.querySelector("img,video");
    if (!media) return;
    wrap.dataset.mediaHydrated = "";
    wrap.dataset.mediaRetries = "0";
    wrap.classList.remove(BROKEN);
    const src = mediaSource(media) || mediaUrl(wrap);
    if (src) setMediaSource(media, retryUrl(src.split("#")[0], 0));
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
