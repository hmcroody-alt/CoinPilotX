(function () {
  "use strict";

  const MAX_RETRIES = 2;
  const LOADED = "is-ready";
  const LOADING = "is-loading";
  const BROKEN = "is-broken";
  const PORTAL_CSS_ID = "pulse-cinematic-media-css";
  const CONTROL_GUARD_CSS_ID = "pulse-global-video-control-guard";
  const SOUND_KEY = "pulseMediaSoundEnabled";
  const REELS_SOUND_KEY = "pulseReelsSoundEnabled";
  const metadataCache = new Map();
  const processingPolls = new Map();
  const predictiveMediaCache = new Map();
  const preloadControllers = new WeakMap();
  const PRELOAD_WINDOW = Object.freeze({ previous: 1, next: 2 });
  const PRELOAD_MAX_CACHE = 72;
  const PRELOAD_ROOT_SELECTOR = [
    ".reels-immersive",
    "[data-status-viewer]",
    "[data-status-story-media]",
    "[data-status-strip]",
    ".videos-grid",
    "#videosGrid",
    ".feed",
    "[data-feed]",
    ".messages-list",
    "main",
  ].join(",");
  let hlsLoaderPromise = null;
  let activeHlsVideo = null;
  let lastScrollAt = 0;
  let controlsObserver = null;
  const HYDRATE_INITIAL_LIMIT = 10;
  const HYDRATE_CHUNK_SIZE = 8;

  function ensurePortalStyles() {
    if (document.getElementById(PORTAL_CSS_ID)) return;
    const link = document.createElement("link");
    link.id = PORTAL_CSS_ID;
    link.rel = "stylesheet";
    link.href = "/static/css/pulse_cinematic_media.css?v=global-media-ui-20260613d";
    document.head.appendChild(link);
  }

  function runIdle(fn, timeout = 700) {
    if (typeof fn !== "function") return;
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(() => {
        try {
          fn();
        } catch (error) {
          console.warn("PulseSoc media idle task skipped", error);
        }
      }, { timeout });
      return;
    }
    setTimeout(() => {
      try {
        fn();
      } catch (error) {
        console.warn("PulseSoc media idle task skipped", error);
      }
    }, 0);
  }

  function processInChunks(items, worker, chunkSize = HYDRATE_CHUNK_SIZE, done) {
    const list = Array.from(items || []);
    let index = 0;
    const step = () => {
      const end = Math.min(list.length, index + chunkSize);
      for (; index < end; index += 1) {
        try {
          worker(list[index], index);
        } catch (error) {
          console.warn("PulseSoc media hydration item skipped", error);
        }
      }
      if (index < list.length) {
        runIdle(step, 900);
      } else if (typeof done === "function") {
        done();
      }
    };
    step();
  }

  function ensureControlGuardStyles() {
    if (document.getElementById(CONTROL_GUARD_CSS_ID)) return;
    const style = document.createElement("style");
    style.id = CONTROL_GUARD_CSS_ID;
    style.textContent = `
      .reel-center-play,
      .reel-card.show-controls .reel-center-play,
      .reel-center-play:focus-visible {
        display: none !important;
        opacity: 0 !important;
        pointer-events: none !important;
      }
      .reel-action .reel-action-label {
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        overflow: hidden !important;
        clip: rect(0 0 0 0) !important;
        clip-path: inset(50%) !important;
        white-space: nowrap !important;
      }
      #pulseStatusStoryViewer .pulse-status-story-close,
      .pulse-status-story-viewer .pulse-status-story-close {
        z-index: 10090 !important;
        width: 56px !important;
        height: 56px !important;
        min-height: 56px !important;
        pointer-events: auto !important;
        touch-action: manipulation !important;
      }
      .pulse-media-wrap video::-webkit-media-controls,
      .pulse-status-story-media video::-webkit-media-controls,
      .pulse-media-lightbox-stage video::-webkit-media-controls,
      .video-detail-player video::-webkit-media-controls,
      .reels-media-stage video::-webkit-media-controls,
      .profile-post video::-webkit-media-controls,
      .group-media-frame video::-webkit-media-controls,
      .message-media::-webkit-media-controls {
        display: none !important;
        opacity: 0 !important;
        pointer-events: none !important;
      }
    `;
    document.head.appendChild(style);
  }

  function mediaUrl(wrap) {
    return wrap?.dataset.mediaUrl || wrap?.dataset.mediaSrc || "";
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function safeUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    if (/^https?:\/\//i.test(raw) || raw.startsWith("/") || raw.startsWith("data:") || raw.startsWith("blob:")) return raw;
    return "/" + raw.replace(/^\/+/, "");
  }

  function nativeHlsSupported(video = document.createElement("video")) {
    try {
      return !!(
        video?.canPlayType?.("application/vnd.apple.mpegurl") ||
        video?.canPlayType?.("application/x-mpegURL")
      );
    } catch (_) {
      return false;
    }
  }

  function controlSurfaceFor(video) {
    return video?.closest?.([
      ".pulse-media-wrap",
      ".pulse-status-story-media",
      ".pulse-media-lightbox-stage",
      ".pulse-status-preview",
      ".pulse-status2-media-preview",
      ".pulse-selected-media",
      ".video-detail-player",
      ".reels-media-stage",
      ".profile-post",
      ".group-media-frame",
      ".message-media",
    ].join(","));
  }

  function stripNativeVideoControls(video) {
    if (!video || video.tagName !== "VIDEO") return;
    video.controls = false;
    video.removeAttribute("controls");
    video.setAttribute("controlsList", "nodownload noplaybackrate noremoteplayback");
    video.setAttribute("disablepictureinpicture", "");
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    if (video.dataset.pulseControlSanitized === "1") return;
    video.dataset.pulseControlSanitized = "1";
    video.addEventListener("loadedmetadata", () => {
      video.controls = false;
      video.removeAttribute("controls");
    }, { passive: true });
    video.addEventListener("click", event => {
      if (event.defaultPrevented || event.target.closest?.("button,a,input,textarea,select")) return;
      if (video.closest?.(".reel-card,[data-status-viewer-player]")) return;
      const surface = controlSurfaceFor(video);
      if (!surface) return;
      showTapIcon(surface, video.paused ? "▶" : (video.muted || Number(video.volume || 0) === 0 ? "🔇" : "🔊"));
    }, { passive: true });
  }

  function sanitizeNativeVideoControls(root = document) {
    if (!root) return;
    if (root.tagName === "VIDEO") stripNativeVideoControls(root);
    root.querySelectorAll?.("video")?.forEach(stripNativeVideoControls);
  }

  function observeNativeVideoControls() {
    if (controlsObserver || !("MutationObserver" in window)) return;
    const root = document.documentElement || document.body;
    if (!root || typeof root.nodeType !== "number") {
      document.addEventListener("DOMContentLoaded", observeNativeVideoControls, { once: true });
      return;
    }
    controlsObserver = new MutationObserver(records => {
      records.forEach(record => {
        record.addedNodes?.forEach(node => {
          if (node.nodeType === 1) sanitizeNativeVideoControls(node);
        });
      });
    });
    controlsObserver.observe(root, {
      childList: true,
      subtree: true,
    });
  }

  function isHlsUrl(url) {
    return /\.m3u8(?:[?#]|$)/i.test(String(url || ""));
  }

  function muxHlsUrl(playbackId) {
    const id = String(playbackId || "").trim();
    return id ? `https://stream.mux.com/${id}.m3u8` : "";
  }

  function sourceKind(url, item = {}) {
    const source = String(url || "").toLowerCase();
    if (source.includes("stream.mux.com/") || item.mux_playback_id || item.muxPlaybackId || item.playback_id) return "mux_hls";
    if (isPulseStreamUrl(source)) return "first_party_stream";
    if (source.includes("cdn.coinpilotx.app")) return "cdn";
    if (source.startsWith("blob:")) return "blob";
    if (source.startsWith("data:")) return "data";
    return source ? "direct" : "unknown";
  }

  function loadHlsLibrary() {
    if (window.Hls) return Promise.resolve(window.Hls);
    if (hlsLoaderPromise) return hlsLoaderPromise;
    hlsLoaderPromise = new Promise((resolve, reject) => {
      const existing = document.querySelector("script[data-pulse-hls-js]");
      if (existing) {
        existing.addEventListener("load", () => resolve(window.Hls), { once: true });
        existing.addEventListener("error", reject, { once: true });
        return;
      }
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js";
      script.async = true;
      script.defer = true;
      script.dataset.pulseHlsJs = "1";
      script.onload = () => resolve(window.Hls);
      script.onerror = () => reject(new Error("HLS playback loader failed."));
      document.head.appendChild(script);
    });
    return hlsLoaderPromise;
  }

  function mediaDebugEnabled() {
    try {
      return window.localStorage?.getItem("pulseDebugMedia") === "1" || ["localhost", "127.0.0.1"].includes(location.hostname);
    } catch (_) {
      return false;
    }
  }

  function soundEnabled() {
    try {
      const saved = window.localStorage?.getItem(SOUND_KEY);
      if (saved === "true") return true;
      if (saved === "false") return false;
      const legacyReels = window.localStorage?.getItem(REELS_SOUND_KEY);
      if (legacyReels === "false") return false;
      if (legacyReels === "true") return true;
      return true;
    } catch (_) {
      return true;
    }
  }

  function autoplayAllowed() {
    try {
      if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return false;
      const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (connection?.saveData) return false;
    } catch (_) {}
    return true;
  }

  function desktopPointer() {
    try {
      return window.matchMedia?.("(hover: hover) and (pointer: fine)")?.matches;
    } catch (_) {
      return false;
    }
  }

  function mobilePerformanceMode() {
    try {
      if (window.PulseSocNative) return true;
      if (window.matchMedia?.("(max-width: 768px)")?.matches) return true;
      const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (connection?.saveData) return true;
    } catch (_) {}
    return false;
  }

  function connectionConstrained() {
    try {
      const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (!connection) return false;
      if (connection.saveData) return true;
      return /(^|-)2g$/i.test(String(connection.effectiveType || ""));
    } catch (_) {
      return false;
    }
  }

  function setSoundEnabled(enabled) {
    try {
      window.localStorage?.setItem(SOUND_KEY, String(!!enabled));
      window.localStorage?.setItem(REELS_SOUND_KEY, String(!!enabled));
    } catch (_) {}
    document.querySelectorAll("[data-pulse-media-sound]").forEach(button => {
      button.hidden = !!enabled && button.dataset.pulseSoundBlocked !== "1";
      button.textContent = enabled ? "Sound on" : "Tap for sound";
    });
    try {
      window.dispatchEvent(new CustomEvent("pulse:media-sound-change", { detail: { enabled: !!enabled } }));
    } catch (_) {}
  }

  function setVideoMuted(video, muted, reason = "system") {
    if (!video) return;
    video.dataset.pulseSoundChangeReason = reason;
    video._pulseExpectedMuted = !!muted;
    video.muted = !!muted;
    if (muted) video.setAttribute("muted", "");
    else video.removeAttribute("muted");
    queueMicrotask(() => {
      if (video.dataset.pulseSoundChangeReason === reason && video.muted === video._pulseExpectedMuted) {
        delete video.dataset.pulseSoundChangeReason;
        video._pulseExpectedMuted = undefined;
      }
    });
  }

  function isPulseStreamUrl(url) {
    return /\/api\/pulse\/media\/\d+\/stream(?:[?#]|$)/.test(String(url || ""));
  }

  function inferMediaType(item, url) {
    const explicit = String(item?.media_type || item?.type || item?.message_type || item?.kind || "").toLowerCase();
    const mime = String(item?.mime_type || item?.mime || "").toLowerCase();
    const source = String(url || item?.media_url || item?.valid_url || "").toLowerCase();
    if (explicit === "gif") return "image";
    if (explicit === "image" || explicit === "video" || explicit === "audio" || explicit === "file") return explicit;
    if (mime.startsWith("video/") || /\.(mp4|webm|mov|m4v)(\?|#|$)/i.test(source)) return "video";
    if (mime.startsWith("audio/") || /\.(mp3|m4a|aac|wav|ogg)(\?|#|$)/i.test(source)) return "audio";
    if (mime.startsWith("image/") || /\.(jpg|jpeg|png|webp|gif|avif)(\?|#|$)/i.test(source)) return "image";
    return "image";
  }

  function normalizeMedia(input = {}) {
    const item = input || {};
    const muxPlaybackId = String(item.mux_playback_id || item.muxPlaybackId || item.playback_id || "").trim();
    const canonicalMuxHlsUrl = safeUrl(muxHlsUrl(muxPlaybackId));
    const muxHlsUrlValue = safeUrl(item.mux_hls_url || item.hls_url || canonicalMuxHlsUrl);
    const muxThumbnailUrl = safeUrl(item.mux_thumbnail_url || (muxPlaybackId ? `https://image.mux.com/${muxPlaybackId}/thumbnail.jpg` : ""));
    const directUrl = safeUrl(item.valid_url || item.cdn_url || item.media_url || item.url || item.src || "");
    const itemType = inferMediaType(item, item.playback_url || muxHlsUrlValue || directUrl);
    const playbackUrl = safeUrl(itemType === "video" ? (canonicalMuxHlsUrl || muxHlsUrlValue || item.playback_url || directUrl) : (item.playback_url || directUrl));
    const url = playbackUrl || directUrl;
    const thumb = safeUrl(item.thumbnail_url || item.thumbnail || item.thumb || muxThumbnailUrl || "");
    const poster = safeUrl(item.poster_url || item.poster || muxThumbnailUrl || thumb || "");
    const type = inferMediaType(item, url);
    const mime = String(isHlsUrl(url) ? "application/vnd.apple.mpegurl" : item.playback_mime_type || item.mime_type || item.mime || (type === "video" ? "video/mp4" : type === "image" ? "image/jpeg" : "")).toLowerCase();
    const id = item.id || item.media_id || item.message_id || item.reel_id || "";
    const hasAudio = item.has_audio === undefined || item.has_audio === null || item.has_audio === "" ? "" : String(item.has_audio === true || item.has_audio === 1 || item.has_audio === "1" || item.has_audio === "true");
    return {
      id,
      url,
      valid_url: safeUrl(item.valid_url || directUrl || url),
      playback_url: playbackUrl,
      cdn_url: safeUrl(item.cdn_url || ""),
      thumb,
      poster,
      type,
      mime,
      mux_playback_id: muxPlaybackId,
      playback_mime_type: String(item.playback_mime_type || "").toLowerCase(),
      mux_hls_url: muxHlsUrlValue,
      mux_thumbnail_url: muxThumbnailUrl,
      mux_status: String(item.mux_status || "").toLowerCase(),
      mux_processing: !!(type === "video" && !muxPlaybackId && String(item.mux_status || item.processing_status || "").toLowerCase() && !["ready", "asset_ready", "available"].includes(String(item.mux_status || item.processing_status || "").toLowerCase())),
      processing_status: String(item.processing_status || "").toLowerCase(),
      source_type: sourceKind(url, item),
      duration: Number(item.duration || item.duration_seconds || 0),
      has_audio: hasAudio,
      created_at: item.created_at || "",
      width: Number(item.width || 0),
      height: Number(item.height || 0),
      aspect_ratio: Number(item.aspect_ratio || 0),
      orientation: item.orientation || "",
      is_available: item.is_available !== false && !!url,
      embed_type: item.embed_type || "upload",
      source_platform: item.source_platform || "coinpilotx",
      preload_priority: item.preload_priority || "nearby",
      alt: item.alt || item.title || "PulseSoc media",
      diag: item.diag || item.diagnostics || "",
    };
  }

  function layersHtml() {
    if (mobilePerformanceMode()) {
      return '<div class="pulse-media-backdrop" aria-hidden="true"></div><div class="pulse-media-vignette" aria-hidden="true"></div>';
    }
    return '<div class="pulse-media-backdrop" aria-hidden="true"></div><div class="pulse-media-soft-glow" aria-hidden="true"></div><div class="pulse-media-color-orb" aria-hidden="true"></div><div class="pulse-media-depth-layer" aria-hidden="true"></div><div class="pulse-media-aura" aria-hidden="true"></div><div class="pulse-media-vignette" aria-hidden="true"></div>';
  }

  function renderMedia(input = {}, options = {}) {
    const media = normalizeMedia(input);
    const processing = (media.mux_processing || media.processing_status === "mux_processing") && !media.mux_playback_id;
    const ratio = media.width > 0 && media.height > 0 ? media.width / media.height : media.aspect_ratio;
    const orientation = media.orientation || (ratio ? Math.abs(ratio - 1) < .08 ? "square" : ratio >= 2 ? "ultrawide" : ratio > 1 ? "landscape" : "portrait" : "unknown");
    const backdrop = media.type === "video" ? (media.poster || media.thumb) : (media.thumb || media.valid_url || media.url);
    const surface = options.surface || "pulse";
    const className = options.className || "";
    const shellClass = [
      "pulse-media-wrap",
      "pulse-cinematic-media-shell",
      "pulse-media-galaxy-shell",
      "pulse-media-ambient-shell",
      media.type === "video" ? "pulse-unified-video-player" : "",
      `is-${orientation}`,
      `media-kind-${media.type}`,
      `pulse-media-surface-${surface}`,
      media.is_available && !processing ? "" : BROKEN,
      className,
    ].filter(Boolean).join(" ");
    const style = `${ratio ? `--media-ratio:${ratio};` : ""}${backdrop ? `--media-backdrop:url('${esc(backdrop)}');` : ""}`;
    const diag = esc(JSON.stringify({
      id: media.id || 0,
      type: media.type,
      mime_type: media.mime,
      src: media.playback_url || media.valid_url || media.url,
      cdn_url: media.cdn_url,
      mux_playback_id: media.mux_playback_id,
      source_type: media.source_type,
      surface,
      available: media.is_available,
    }).slice(0, 600));
    const extraAttrs = options.attrs ? String(options.attrs) : "";
    const data = [
      `data-media-id="${esc(media.id)}"`,
      `data-media-url="${esc(media.playback_url || media.valid_url || media.url)}"`,
      `data-media-src="${esc(media.playback_url || media.valid_url || media.url)}"`,
      `data-media-cdn="${esc(media.cdn_url)}"`,
      `data-media-type="${esc(media.type)}"`,
      `data-media-mime="${esc(media.mime)}"`,
      `data-media-orientation="${esc(orientation)}"`,
      `data-media-aspect-ratio="${esc(ratio || "")}"`,
      `data-media-width="${esc(media.width || "")}"`,
      `data-media-height="${esc(media.height || "")}"`,
      `data-media-thumb="${esc(media.thumb)}"`,
      `data-media-poster="${esc(media.poster)}"`,
      `data-media-mux-playback-id="${esc(media.mux_playback_id)}"`,
      `data-media-hls="${esc(media.mux_hls_url)}"`,
      `data-media-source-type="${esc(media.source_type)}"`,
      `data-media-native-hls="${esc(nativeHlsSupported() && isHlsUrl(media.playback_url || media.valid_url || media.url) ? "1" : "0")}"`,
      `data-media-duration="${esc(media.duration)}"`,
      `data-media-has-audio="${esc(media.has_audio)}"`,
      `data-media-created-at="${esc(media.created_at)}"`,
      `data-media-backdrop="${esc(backdrop)}"`,
      `data-media-embed="${esc(media.embed_type)}"`,
      `data-media-platform="${esc(media.source_platform)}"`,
      `data-media-surface="${esc(surface)}"`,
      `data-media-processing-status="${esc(media.processing_status)}"`,
      `data-media-diag="${diag}"`,
    ].join(" ") + (extraAttrs ? ` ${extraAttrs}` : "");
    const fallback = `<div class="pulse-media-fallback" data-media-fallback="${esc(media.id)}"><div><strong>${processing ? "Preparing video..." : media.is_available ? "Media could not load." : "Media is being restored."}</strong><span class="muted" data-media-processing-copy>${processing ? "Playback will appear here when it is ready." : `Tap to retry. Trace media-${esc(media.id || "unknown")}`}</span><br>${processing ? `<button type="button" data-repair-media="${esc(media.id)}">Check now</button>` : "<button type=\"button\" data-retry-media>Retry</button>"}</div></div>`;
    if (!media.is_available || processing) {
      return `<div class="${shellClass}" data-fit="smart" ${data}${style ? ` style="${style}"` : ""}>${layersHtml()}${fallback}</div>`;
    }
    if (media.type === "video") {
      const poster = media.poster ? ` poster="${esc(media.poster)}"` : "";
      const type = media.mime ? ` type="${esc(media.mime)}"` : "";
      const loop = options.loop ? " loop" : "";
      return `<div class="${shellClass}" data-fit="smart" data-open-media-lightbox ${data}${style ? ` style="${style}"` : ""}>${layersHtml()}<video data-pulse-video-player${loop} playsinline webkit-playsinline x-webkit-airplay="allow" preload="metadata" controlsList="nodownload noplaybackrate noremoteplayback" disablepictureinpicture${poster}><source src="${esc(media.playback_url || media.valid_url || media.url)}"${type}></video><button class="pulse-media-sound-unlock" type="button" data-pulse-media-sound hidden>Tap for sound</button>${fallback}</div>`;
    }
    if (media.type === "audio") {
      return `<div class="${shellClass} media-kind-audio" data-fit="smart" ${data}${style ? ` style="${style}"` : ""}>${layersHtml()}<audio controls preload="metadata" src="${esc(media.valid_url || media.url)}"></audio>${fallback}</div>`;
    }
    return `<div class="${shellClass}" data-fit="smart" data-open-media-lightbox ${data}${style ? ` style="${style}"` : ""}>${layersHtml()}<img src="${esc(media.thumb || media.valid_url || media.url)}" data-full-src="${esc(media.valid_url || media.url)}" alt="${esc(media.alt)}" loading="${media.preload_priority === "high" ? "eager" : "lazy"}" decoding="async">${fallback}</div>`;
  }

  function renderInto(root, mediaList, options = {}) {
    const node = typeof root === "string" ? document.querySelector(root) : root;
    if (!node) return;
    node.innerHTML = (Array.isArray(mediaList) ? mediaList : [mediaList]).map(item => renderMedia(item, options)).join("");
    hydrate(node);
  }

  function mark(wrap, state) {
    if (!wrap) return;
    wrap.classList.remove(LOADED, LOADING, BROKEN);
    wrap.classList.add(state);
  }

  function startProcessingPoll(wrap) {
    const mediaId = wrap?.dataset.mediaId || "";
    if (!wrap || !mediaId || wrap.dataset.mediaProcessingStatus !== "mux_processing") return;
    if (processingPolls.has(mediaId)) return;
    const started = Date.now();
    const poll = async () => {
      if (!document.body.contains(wrap)) {
        clearInterval(processingPolls.get(mediaId));
        processingPolls.delete(mediaId);
        return;
      }
      const copy = wrap.querySelector("[data-media-processing-copy]");
      if (copy && Date.now() - started > 180000) copy.textContent = "Processing is taking longer than usual. We are still checking the video.";
      try {
        const response = await fetch(`/api/pulse/media/${encodeURIComponent(mediaId)}/status`, { credentials: "same-origin", cache: "no-store" });
        const data = await response.json();
        if (!response.ok || data.ok === false) return;
        const status = String(data.processing_status || data.media?.processing_status || "").toLowerCase();
        if (status === "ready" || data.media?.mux_playback_id || data.media?.playback_url) {
          clearInterval(processingPolls.get(mediaId));
          processingPolls.delete(mediaId);
          const parent = wrap.parentElement;
          wrap.outerHTML = renderMedia(data.media || {}, { surface: wrap.dataset.mediaSurface || "pulse" });
          hydrate(parent || document);
        }
      } catch (_) {}
    };
    processingPolls.set(mediaId, setInterval(poll, 18000));
    setTimeout(poll, 800);
  }

  function setBackdrop(wrap) {
    const src = wrap?.dataset.mediaBackdrop || wrap?.dataset.mediaPoster || wrap?.dataset.mediaThumb || mediaUrl(wrap);
    if (!wrap || !src || wrap.style.getPropertyValue("--media-backdrop")) return;
    wrap.style.setProperty("--media-backdrop", `url("${src.replace(/"/g, "%22")}")`);
  }

  function applyAmbientColor(wrap, media, force = false) {
    if (!wrap || !media || (!force && wrap.dataset.mediaAmbient === "1")) return;
    if (mobilePerformanceMode()) {
      wrap.dataset.mediaAmbient = "1";
      wrap.style.setProperty("--pulse-media-rgb", "110, 223, 246");
      wrap.style.setProperty("--pulse-media-secondary-rgb", "54, 229, 143");
      wrap.style.setProperty("--pulse-media-accent-rgb", "155, 92, 255");
      return;
    }
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
      const primary = `${Math.min(255, r)}, ${Math.min(255, tealBias)}, ${Math.min(255, blueBias)}`;
      const secondary = `${Math.min(255, Math.round((r + 54) * .72))}, ${Math.min(255, Math.round((g + 110) * .88))}, ${Math.min(255, Math.round((b + 92) * .78))}`;
      const accent = `${Math.min(255, Math.round((r + 138) * .72))}, ${Math.min(255, Math.round((g + 60) * .62))}, ${Math.min(255, Math.round((b + 178) * .78))}`;
      wrap.style.setProperty("--pulse-media-rgb", primary);
      wrap.style.setProperty("--pulse-media-secondary-rgb", secondary);
      wrap.style.setProperty("--pulse-media-accent-rgb", accent);
      wrap.style.setProperty("--pulse-media-x", `${38 + (r % 26)}%`);
      wrap.style.setProperty("--pulse-media-y", `${28 + (b % 32)}%`);
    } catch (_) {
      wrap.style.setProperty("--pulse-media-rgb", "110, 223, 246");
      wrap.style.setProperty("--pulse-media-secondary-rgb", "54, 229, 143");
      wrap.style.setProperty("--pulse-media-accent-rgb", "155, 92, 255");
    }
  }

  function bindVideoAmbient(wrap, video) {
    if (!wrap || !video || wrap.dataset.videoAmbientBound === "1") return;
    wrap.dataset.videoAmbientBound = "1";
    let sampleCount = 0;
    const sample = () => {
      if (sampleCount >= 2) return;
      sampleCount += 1;
      applyAmbientColor(wrap, video, true);
    };
    video.addEventListener("loadeddata", sample, { once: true });
    video.addEventListener("playing", sample);
  }

  function ensureSoundButton(wrap) {
    if (!wrap || wrap.querySelector("[data-pulse-media-sound]")) return;
    const video = wrap.querySelector("video");
    if (!video) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pulse-media-sound-unlock";
    button.dataset.pulseMediaSound = "1";
    button.textContent = "Tap for sound";
    button.hidden = true;
    wrap.appendChild(button);
  }

  function showSoundPrompt(wrap, visible = true, force = false) {
    const button = wrap?.querySelector?.("[data-pulse-media-sound]");
    if (!button) return;
    button.dataset.pulseSoundBlocked = force ? "1" : "";
    button.hidden = !visible || (!force && soundEnabled());
    button.textContent = "Tap for sound";
    if (!button.hidden) {
      clearTimeout(button._pulseSoundTimer);
      button._pulseSoundTimer = setTimeout(() => {
        if (!force && !soundEnabled()) button.hidden = true;
      }, 3200);
    }
  }

  let activeVideo = null;
  let hoverVideo = null;
  let playbackObserver = null;
  let playbackRaf = 0;
  let pendingBestEntry = null;

  function mediaVideoWrap(video) {
    return video?.closest?.(".pulse-media-wrap") || video?.closest?.(".reel-card") || video?.parentElement || null;
  }

  function isManagedReelVideo(video) {
    if (!video) return false;
    const wrap = mediaVideoWrap(video);
    return video.dataset.reelsManaged === "1"
      || !!video.closest?.(".reel-card")
      || String(wrap?.dataset?.mediaSurface || "").toLowerCase() === "reels"
      || !!wrap?.closest?.(".reel-card");
  }

  function prepareMobileVideo(video) {
    if (!video) return;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.setAttribute("x-webkit-airplay", "allow");
    if (isManagedReelVideo(video)) {
      if (!video.preload || video.preload === "none") video.preload = "metadata";
      return;
    }
    video.defaultMuted = false;
    video.removeAttribute("muted");
    video.volume = Number(video.dataset.pulsePreferredVolume || video.volume || 1) || 1;
    if (!soundEnabled()) setVideoMuted(video, true, "prepare-user-muted");
    if (!video.preload || video.preload === "none") video.preload = "metadata";
  }

  function pauseOtherVideos(except) {
    if (activeVideo && activeVideo !== except && !activeVideo.paused) activeVideo.pause();
    if (hoverVideo && hoverVideo !== except && !hoverVideo.paused) hoverVideo.pause();
  }

  function destroyHls(video) {
    if (!video?._pulseHls) return;
    try {
      video._pulseHls.destroy();
    } catch (_) {}
    video._pulseHls = null;
    video.dataset.pulseHlsBound = "";
    if (activeHlsVideo === video) activeHlsVideo = null;
  }

  async function playVisibleVideo(video, preferSound = false) {
    if (!video || !autoplayAllowed()) return;
    if (isManagedReelVideo(video)) return;
    const wrap = mediaVideoWrap(video);
    pauseOtherVideos(video);
    activeVideo = video;
    prepareMobileVideo(video);
    video.preload = "auto";
    const shouldTrySound = preferSound !== false && soundEnabled();
    video.volume = Number(video.dataset.pulsePreferredVolume || 1);
    video.defaultMuted = false;
    video.removeAttribute("muted");
    setVideoMuted(video, !shouldTrySound, "autoplay");
    try {
      await video.play();
      wrap?.classList.add("is-playing");
      if (shouldTrySound) {
        setSoundEnabled(true);
        showSoundPrompt(wrap, false);
      }
      else showSoundPrompt(wrap, true);
    } catch (error) {
      if (shouldTrySound) {
        setVideoMuted(video, true, "autoplay-fallback");
        try {
          await video.play();
          showSoundPrompt(wrap, true, true);
          return;
        } catch (_) {}
      }
      if (mediaDebugEnabled()) console.warn("PulseSoc media autoplay blocked", {
        src: videoSource(video),
        message: error?.message || String(error),
      });
    }
  }

  function preloadNextVideo(video) {
    const wrap = mediaVideoWrap(video);
    if (wrap) schedulePredictivePreload(wrap, "playback");
  }

  function scheduleVisiblePlayback(entry) {
    if (!entry?.target) return;
    if (!pendingBestEntry || entry.intersectionRatio > pendingBestEntry.intersectionRatio) {
      pendingBestEntry = entry;
    }
    if (playbackRaf) return;
    playbackRaf = requestAnimationFrame(() => {
      const best = pendingBestEntry;
      pendingBestEntry = null;
      playbackRaf = 0;
      if (!best?.target || !best.isIntersecting || best.intersectionRatio < .58) return;
      const vid = best.target;
      if (isManagedReelVideo(vid)) return;
      const targetWrap = mediaVideoWrap(vid);
      const isReelSurface = isManagedReelVideo(vid);
      if (!isReelSurface && desktopPointer() && hoverVideo && hoverVideo !== vid) return;
      const run = () => {
        targetWrap?.classList.add("is-active-media");
        if (soundEnabled()) playVisibleVideo(vid, true);
        else playVisibleVideo(vid, false);
        preloadNextVideo(vid);
      };
      if (mobilePerformanceMode() && Date.now() - lastScrollAt < 120) {
        setTimeout(run, 120);
      } else {
        run();
      }
    });
  }

  function bindAutoplayVideo(wrap, video) {
    if (!wrap || !video || video.dataset.pulseAutoplayBound === "1") return;
    video.dataset.pulseAutoplayBound = "1";
    video.controls = false;
    video.removeAttribute("controls");
    video.setAttribute("disablepictureinpicture", "");
    video.setAttribute("controlsList", "nodownload noplaybackrate noremoteplayback");
    ensureSoundButton(wrap);
    const isReelSurface = isManagedReelVideo(video);
    if (isReelSurface || video.dataset.reelsManaged === "1") {
      video.dataset.reelsManaged = "1";
      return;
    }
    const canHoverPreview = desktopPointer() && !isReelSurface;
    if (canHoverPreview) {
      wrap.addEventListener("pointerenter", () => {
        hoverVideo = video;
        playVisibleVideo(video, soundEnabled());
      });
      wrap.addEventListener("pointerleave", () => {
        if (hoverVideo === video) hoverVideo = null;
        if (!video.paused) video.pause();
        video.preload = "metadata";
      });
    }
    video.addEventListener("play", () => {
      pauseOtherVideos(video);
      activeVideo = video;
      wrap.classList.add("is-playing");
    });
    video.addEventListener("pause", () => wrap.classList.remove("is-playing"));
    video.addEventListener("click", event => {
      if (event.defaultPrevented || event.target.closest?.("button,a")) return;
      if (String(wrap.dataset.mediaSurface || "").toLowerCase() === "reels" || wrap.closest?.(".reel-card")) return;
      const nextMuted = !(video.muted || Number(video.volume || 0) === 0);
      setVideoMuted(video, nextMuted, "user-toggle");
      setSoundEnabled(!nextMuted);
      showTapIcon(wrap, nextMuted ? "🔇" : "🔊");
      if (!nextMuted) {
        video.volume = Number(video.dataset.pulsePreferredVolume || 1) || 1;
        video.play().catch(() => showSoundPrompt(wrap, true, true));
      } else {
        showSoundPrompt(wrap, true);
      }
    });
    video.addEventListener("volumechange", () => {
      const reason = video.dataset.pulseSoundChangeReason || "";
      if (reason && video.muted === video._pulseExpectedMuted) return;
      if (reason) {
        delete video.dataset.pulseSoundChangeReason;
        video._pulseExpectedMuted = undefined;
      }
      if (video.muted || Number(video.volume || 0) === 0) {
        setSoundEnabled(false);
        showSoundPrompt(wrap, true);
      } else {
        video.dataset.pulsePreferredVolume = String(video.volume || 1);
        setSoundEnabled(true);
        showSoundPrompt(wrap, false);
      }
    });
    if (!("IntersectionObserver" in window)) return;
    if (!playbackObserver) {
      playbackObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          const vid = entry.target;
          if (isManagedReelVideo(vid)) return;
          const targetWrap = mediaVideoWrap(vid);
          if (!entry.isIntersecting || entry.intersectionRatio < .58) {
            if (!vid.paused) vid.pause();
            vid.preload = "metadata";
            targetWrap?.classList.remove("is-active-media");
            return;
          }
          scheduleVisiblePlayback(entry);
        });
      }, { threshold: [0, .25, .58, .75, 1], rootMargin: "0px" });
    }
    if (video && typeof video.nodeType === "number") playbackObserver.observe(video);
  }

  function showTapIcon(wrap, label) {
    if (!wrap) return;
    let icon = null;
    try {
      icon = wrap.querySelector(":scope > .pulse-media-tap-icon");
    } catch (_) {
      icon = Array.from(wrap.children || []).find(child => child.classList?.contains("pulse-media-tap-icon")) || null;
    }
    if (!icon) {
      icon = document.createElement("span");
      icon.className = "pulse-media-tap-icon";
      icon.setAttribute("aria-hidden", "true");
      wrap.appendChild(icon);
    }
    icon.textContent = label || "•";
    icon.classList.remove("show");
    void icon.offsetWidth;
    icon.classList.add("show");
    clearTimeout(icon._pulseTapTimer);
    icon._pulseTapTimer = setTimeout(() => icon.classList.remove("show"), 1000);
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

  function setVideoSource(video, src, mime = "") {
    if (!video || !src) return;
    const current = videoSource(video);
    const source = video.querySelector("source");
    if (source) {
      source.src = src;
      if (mime) source.type = mime;
    } else {
      video.src = src;
    }
    if (current !== src) {
      try {
        video.load();
      } catch (_) {}
    }
  }

  function preloadKey(wrap, media) {
    const source = mediaSource(media) || mediaUrl(wrap) || wrap?.dataset.mediaPoster || wrap?.dataset.mediaThumb || "";
    return `${wrap?.dataset.mediaType || media?.tagName || "media"}:${source}`;
  }

  function rememberPreloaded(key, value = {}) {
    if (!key) return;
    predictiveMediaCache.set(key, { ...value, at: Date.now() });
    if (predictiveMediaCache.size <= PRELOAD_MAX_CACHE) return;
    const oldest = [...predictiveMediaCache.entries()].sort((a, b) => (a[1].at || 0) - (b[1].at || 0)).slice(0, predictiveMediaCache.size - PRELOAD_MAX_CACHE);
    oldest.forEach(([cacheKey]) => predictiveMediaCache.delete(cacheKey));
  }

  function warmImage(url, key, signal) {
    if (!url || predictiveMediaCache.has(key) || signal?.aborted) return;
    const img = new Image();
    img.decoding = "async";
    img.loading = "eager";
    img.onload = () => rememberPreloaded(key, { type: "image", url });
    img.onerror = () => rememberPreloaded(key, { type: "image-error", url });
    if (signal) {
      signal.addEventListener("abort", () => {
        img.onload = null;
        img.onerror = null;
        img.src = "";
      }, { once: true });
    }
    img.src = url;
  }

  function warmVideo(wrap, video, priority = "nearby") {
    if (!video || !wrap) return;
    if (isManagedReelVideo(video)) return;
    const key = preloadKey(wrap, video);
    if (predictiveMediaCache.has(key) && priority !== "current") return;
    video.preload = connectionConstrained() && priority !== "current" ? "metadata" : "auto";
    if (priority === "current") video.dataset.pulsePreloadPriority = "current";
    else video.dataset.pulsePreloadPriority = "nearby";
    try {
      if (video.readyState === 0 || priority === "current") video.load();
    } catch (_) {}
    rememberPreloaded(key, {
      type: "video",
      url: videoSource(video) || mediaUrl(wrap),
      priority,
      readyState: video.readyState || 0,
    });
  }

  function warmMediaWrap(wrap, priority = "nearby", signal) {
    if (!wrap || signal?.aborted) return;
    const isWrapper = wrap.classList?.contains("pulse-media-wrap");
    if (isWrapper) hydrateWrap(wrap);
    const media = isWrapper ? wrap.querySelector("img,video,audio") : wrap;
    const poster = wrap.dataset?.mediaPoster || wrap.dataset?.mediaThumb || wrap.dataset?.mediaBackdrop || wrap.getAttribute?.("poster") || "";
    const source = mediaSource(media) || (isWrapper ? mediaUrl(wrap) : media?.src || "");
    if (poster) warmImage(poster, `poster:${poster}`, signal);
    if (!media) return;
    if (media.tagName === "VIDEO") {
      if (isManagedReelVideo(media)) return;
      warmVideo(wrap, media, priority);
      return;
    }
    if (media.tagName === "IMG") {
      const full = media.dataset.fullSrc || source || media.currentSrc || media.src || poster;
      warmImage(full, preloadKey(wrap, media), signal);
      return;
    }
    if (media.tagName === "AUDIO") {
      media.preload = connectionConstrained() ? "metadata" : "auto";
      try {
        if (media.readyState === 0) media.load();
      } catch (_) {}
      rememberPreloaded(preloadKey(wrap, media), { type: "audio", url: source, priority });
    }
  }

  function mediaPreloadScope(wrap) {
    return wrap?.closest?.(PRELOAD_ROOT_SELECTOR) || document;
  }

  function mediaPreloadItems(scope) {
    return Array.from((scope || document).querySelectorAll(".pulse-media-wrap, [data-status-home-video]"))
      .map(node => node.closest?.(".pulse-media-wrap") || node)
      .filter(Boolean)
      .filter(node => {
        const video = node.tagName === "VIDEO" ? node : node.querySelector?.("video");
        return !isManagedReelVideo(video);
      })
      .filter((node, index, list) => list.indexOf(node) === index);
  }

  function cancelStalePreloads(scope, keep) {
    if (!scope) return;
    const active = preloadControllers.get(scope);
    if (!active) return;
    active.forEach((controller, node) => {
      if (keep.has(node)) return;
      try {
        controller.abort();
      } catch (_) {}
      active.delete(node);
      if (node.tagName === "VIDEO" && node !== activeVideo && !node.matches(".is-active-media video")) {
        node.preload = "metadata";
      } else {
        const video = node.querySelector?.("video");
        if (video && video !== activeVideo && !video.closest?.(".is-active-media")) video.preload = "metadata";
      }
    });
  }

  function schedulePredictivePreload(activeNode, reason = "visible") {
    const activeWrap = activeNode?.closest?.(".pulse-media-wrap") || activeNode;
    if (!activeWrap) return;
    const scope = mediaPreloadScope(activeWrap);
    const items = mediaPreloadItems(scope);
    const index = items.indexOf(activeWrap);
    if (index < 0) return;
    const start = Math.max(0, index - PRELOAD_WINDOW.previous);
    const end = Math.min(items.length - 1, index + PRELOAD_WINDOW.next);
    const keep = new Set(items.slice(start, end + 1));
    cancelStalePreloads(scope, keep);
    let activeControllers = preloadControllers.get(scope);
    if (!activeControllers) {
      activeControllers = new Map();
      preloadControllers.set(scope, activeControllers);
    }
    const run = () => {
      items.slice(start, end + 1).forEach((item, itemIndex) => {
        if (!document.documentElement.contains(item)) return;
        const absoluteIndex = start + itemIndex;
        const priority = absoluteIndex === index ? "current" : "nearby";
        let controller = activeControllers.get(item);
        if (!controller || controller.signal.aborted) {
          controller = new AbortController();
          activeControllers.set(item, controller);
        }
        item.dataset.pulsePredictivePreload = priority;
        item.dataset.pulsePreloadReason = reason;
        warmMediaWrap(item, priority, controller.signal);
      });
    };
    if ("requestIdleCallback" in window && reason !== "playback") {
      window.requestIdleCallback(run, { timeout: 550 });
    } else {
      setTimeout(run, 0);
    }
  }

  let predictiveObserver = null;
  function observePredictivePreload(wraps) {
    if (!("IntersectionObserver" in window)) {
      wraps.forEach(wrap => schedulePredictivePreload(wrap, "fallback"));
      return;
    }
    if (!predictiveObserver) {
      predictiveObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          if (entry.intersectionRatio < .2) return;
          schedulePredictivePreload(entry.target, "intersection");
        });
      }, {
        threshold: [0, .2, .55, 1],
        rootMargin: mobilePerformanceMode() ? "260px 0px" : "520px 0px",
      });
    }
    wraps.forEach(wrap => {
      if (wrap && typeof wrap.nodeType === "number") predictiveObserver.observe(wrap);
    });
  }

  async function attachHlsPlayback(wrap, video) {
    const src = wrap?.dataset.mediaHls || mediaUrl(wrap) || videoSource(video);
    if (!video || !isHlsUrl(src) || video.dataset.pulseHlsBound === "1") return;
    video.dataset.pulseHlsBound = "1";
    prepareMobileVideo(video);
    if (nativeHlsSupported(video)) {
      destroyHls(video);
      video.dataset.pulseNativeHls = "1";
      setVideoSource(video, src, "application/vnd.apple.mpegurl");
      return;
    }
    try {
      const Hls = await loadHlsLibrary();
      if (!Hls?.isSupported?.()) {
        throw new Error("HLS is not supported in this browser.");
      }
      if (activeHlsVideo && activeHlsVideo !== video) destroyHls(activeHlsVideo);
      const source = video.querySelector("source");
      if (source) source.remove();
      const hls = new Hls({
        capLevelToPlayerSize: true,
        maxBufferLength: 18,
        backBufferLength: 12,
      });
      hls.loadSource(src);
      hls.attachMedia(video);
      video._pulseHls = hls;
      activeHlsVideo = video;
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (!data?.fatal) return;
        reportVideoDiagnostics(wrap, video, "hls_error");
        destroyHls(video);
        failMedia(wrap, video);
      });
    } catch (error) {
      if (mediaDebugEnabled()) console.warn("PulseSoc HLS attach failed", {
        media_id: wrap?.dataset.mediaId || "",
        src,
        message: error?.message || String(error),
      });
    }
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
      source_type: video?.querySelector?.("source")?.type || "",
    };
  }

  function probeVideoRequest(wrap, src, eventName) {
    if (!mediaDebugEnabled() || !src || src.startsWith("blob:") || src.startsWith("data:")) return;
    fetch(src, { method: "HEAD", mode: "cors", cache: "no-store" })
      .then(response => console.info("Pulse video request HEAD", {
        event: eventName,
        media_id: wrap.dataset.mediaId || "",
        src,
        status: response.status,
        content_type: response.headers.get("content-type") || "",
        accept_ranges: response.headers.get("accept-ranges") || "",
        content_length: response.headers.get("content-length") || "",
      }))
      .catch(error => console.warn("Pulse video request HEAD failed", {
        event: eventName,
        media_id: wrap.dataset.mediaId || "",
        src,
        message: error?.message || String(error),
      }));
  }

  function reportVideoDiagnostics(wrap, video, eventName) {
    if (!wrap || !video) return;
    if (!mediaDebugEnabled() && !["error", "load_error", "retry_error"].includes(eventName)) return;
    const src = videoSource(video) || mediaUrl(wrap);
    const log = eventName === "error" || eventName.includes("error") ? console.warn : console.info;
    log("Pulse video diagnostic", {
      event: eventName,
      media_id: wrap.dataset.mediaId || "",
      src,
      current_src: video.currentSrc || "",
      source_src: video.querySelector?.("source")?.src || "",
      source_type: wrap.dataset.mediaSourceType || "",
      source_mime: video.querySelector?.("source")?.type || wrap.dataset.mediaMime || "",
      native_hls: nativeHlsSupported(video),
      pulse_native_hls: video.dataset.pulseNativeHls || "0",
      mime_type: wrap.dataset.mediaMime || "",
      media_type: wrap.dataset.mediaType || "",
      poster: wrap.dataset.mediaPoster || "",
      cdn: src.startsWith("https://cdn.coinpilotx.app/"),
      private_r2: src.includes("r2.cloudflarestorage.com"),
      error: videoErrorDetails(video),
      diag: wrap.dataset.mediaDiag || "",
    });
    probeVideoRequest(wrap, src, eventName);
  }

  function revealImage(wrap, img) {
    setBackdrop(wrap);
    applyAmbientColor(wrap, img);
    const full = img.dataset.fullSrc || mediaUrl(wrap) || img.currentSrc || img.src;
    if (mobilePerformanceMode()) {
      img.style.visibility = "";
      mark(wrap, LOADED);
      return;
    }
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
        if (media?.tagName === "VIDEO" && isPulseStreamUrl(src)) {
          media.style.visibility = "hidden";
          mark(wrap, BROKEN);
          return;
        }
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
    console.warn("PulseSoc media hydration failed", {
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
      console.warn("PulseSoc media received private R2 URL; renderer requires CDN URL", {
        media_id: wrap.dataset.mediaId || "",
        src: canonicalSrc,
      });
    }
    if (mediaDebugEnabled()) {
      console.debug("PulseSoc media render state", {
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
      if (wrap.dataset.mediaProcessingStatus === "mux_processing") {
        startProcessingPoll(wrap);
        return;
      }
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
    prepareMobileVideo(media);
    attachHlsPlayback(wrap, media);
    bindAutoplayVideo(wrap, media);
    const cached = metadataCache.get(videoSource(media) || canonicalSrc);
    if (cached) {
      wrap.dataset.mediaDurationCached = String(cached.duration || 0);
      wrap.dataset.mediaDimensionsCached = `${cached.width || 0}x${cached.height || 0}`;
    }
    media.addEventListener("loadedmetadata", () => {
      metadataCache.set(videoSource(media) || canonicalSrc, {
        duration: media.duration || 0,
        width: media.videoWidth || 0,
        height: media.videoHeight || 0,
      });
      if (mediaDebugEnabled()) console.info("PulseSoc video metadata loaded", {
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
      if (mediaDebugEnabled()) console.info("PulseSoc video canplay", {
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
    try {
      ensurePortalStyles();
      ensureControlGuardStyles();
      observeNativeVideoControls();
      runIdle(() => sanitizeNativeVideoControls(scope), 500);
    } catch (error) {
      console.warn("PulseSoc media control guard skipped", error);
    }
    const wraps = Array.from(scope.querySelectorAll(".pulse-media-wrap"));
    if (!wraps.length) {
      runIdle(() => enhanceMobileReels(scope), 900);
      return;
    }
    if ("IntersectionObserver" in window) {
      if (!observer) {
        observer = new IntersectionObserver(entries => {
          entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            observer.unobserve(entry.target);
            hydrateWrap(entry.target);
          });
        }, { rootMargin: mobilePerformanceMode() ? "220px 0px" : "420px 0px" });
      }
      wraps.forEach(wrap => {
        if (wrap && typeof wrap.nodeType === "number") observer.observe(wrap);
      });
    } else {
      processInChunks(wraps, hydrateWrap);
    }
    const visibleWraps = wraps.slice(0, HYDRATE_INITIAL_LIMIT);
    processInChunks(visibleWraps, wrap => {
      const video = wrap.querySelector("video");
      if (video) bindAutoplayVideo(wrap, video);
    });
    if (wraps.length > HYDRATE_INITIAL_LIMIT) {
      runIdle(() => processInChunks(wraps.slice(HYDRATE_INITIAL_LIMIT), wrap => {
        const video = wrap.querySelector("video");
        if (video) bindAutoplayVideo(wrap, video);
      }), 1200);
    }
    runIdle(() => observePredictivePreload(wraps), 1000);
    runIdle(() => enhanceMobileReels(scope), 1100);
  }

  const mobileReelsQuery = window.matchMedia?.("(max-width: 900px)");

  function mobileReelsActive() {
    return !!(mobileReelsQuery?.matches && document.querySelector(".reels-shell[data-reels-mobile-shell]"));
  }

  function compactCount(value) {
    const count = Number(String(value || "0").replace(/[^\d.-]/g, "")) || 0;
    if (count >= 1000000) return `${(count / 1000000).toFixed(count >= 10000000 ? 0 : 1)}M`;
    if (count >= 1000) return `${(count / 1000).toFixed(count >= 10000 ? 0 : 1)}K`;
    return String(count);
  }

  function formatReelTime(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const mins = Math.floor(value / 60);
    const secs = Math.floor(value % 60);
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }

  function reelPrimaryVideo(card) {
    return card?.querySelector?.(".reels-media-stage video:not(.reel-blur-bg), video.reel-media");
  }

  function ensureReelProgress(card) {
    const progress = card?.querySelector?.(".reel-progress");
    if (!progress || progress.dataset.mobileProgressReady === "1") return;
    progress.dataset.mobileProgressReady = "1";
    progress.insertAdjacentHTML("afterbegin", '<span class="reel-time-current">0:00</span><span class="reel-time-duration">0:00</span>');
    const video = reelPrimaryVideo(card);
    const update = () => {
      const current = progress.querySelector(".reel-time-current");
      const duration = progress.querySelector(".reel-time-duration");
      if (current) current.textContent = formatReelTime(video?.currentTime || 0);
      if (duration) duration.textContent = video?.duration && Number.isFinite(video.duration) ? formatReelTime(video.duration) : "0:00";
    };
    if (video) {
      ["loadedmetadata", "durationchange", "timeupdate"].forEach(eventName => video.addEventListener(eventName, update, { passive: true }));
      update();
    }
  }

  function seekReelProgress(event) {
    const progress = event.target.closest?.(".reel-progress");
    if (!progress || !mobileReelsActive()) return;
    const card = progress.closest(".reel-card");
    const video = reelPrimaryVideo(card);
    if (!video || !video.duration || !Number.isFinite(video.duration)) return;
    const rect = progress.getBoundingClientRect();
    const start = rect.left + 42;
    const width = Math.max(1, rect.width - 90);
    const x = Math.min(width, Math.max(0, event.clientX - start));
    video.currentTime = (x / width) * video.duration;
  }

  function actionLabel(button, fallback) {
    const small = button?.querySelector?.("small");
    if (!small) return;
    const current = small.textContent?.trim() || "";
    if (!current || /^(share|more|save|remix)$/i.test(current)) small.textContent = fallback;
  }

  function cloneCreatorAvatar(card) {
    const rail = card.querySelector(".reel-actions");
    if (!rail || rail.querySelector(".reel-action-avatar")) return;
    const avatar = card.querySelector(".reel-caption-creator .reel-avatar")?.cloneNode(true);
    if (!avatar) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "reel-action reel-action-avatar";
    button.dataset.followCreator = card.dataset.authorId || "";
    button.setAttribute("aria-label", "Follow creator");
    button.appendChild(avatar);
    const plus = document.createElement("small");
    plus.textContent = "+";
    button.appendChild(plus);
    rail.insertBefore(button, rail.firstChild);
  }

  function ensureRemixAction(card) {
    const rail = card.querySelector(".reel-actions");
    if (!rail || rail.querySelector("[data-reel-remix]")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "reel-action reel-remix-action";
    button.dataset.reelRemix = card.dataset.reelId || "";
    button.setAttribute("aria-label", "Remix");
    button.innerHTML = '⟳<small>Remix</small>';
    rail.appendChild(button);
  }

  function enhanceMobileReelCard(card) {
    if (!card || card.dataset.mobileReelsEnhanced === "1") return;
    card.dataset.mobileReelsEnhanced = "1";
    cloneCreatorAvatar(card);
    ensureRemixAction(card);
    actionLabel(card.querySelector("[data-share-reel]"), compactCount(card.querySelector(".reel-details-stats span:nth-child(3) strong")?.textContent || "Share"));
    actionLabel(card.querySelector("[data-reel-save]"), "Save");
    actionLabel(card.querySelector("[data-open-comments]"), compactCount(card.querySelector("[data-comment-count]")?.textContent || "0"));
    actionLabel(card.querySelector("[data-reel-react]"), compactCount(card.querySelector("[data-fire-count]")?.textContent || "0"));
    ensureReelProgress(card);
    card.querySelectorAll("video").forEach(video => {
      video.setAttribute("playsinline", "");
      video.setAttribute("webkit-playsinline", "");
      if (!card.classList.contains("is-active")) video.preload = "metadata";
    });
  }

  function enhanceMobileReels(root = document) {
    if (!mobileReelsActive()) return;
    root.querySelectorAll?.(".reel-card")?.forEach(enhanceMobileReelCard);
  }

  function burstReelEmoji(target, emoji = "❤️") {
    if (!mobileReelsActive()) return;
    const rect = target.getBoundingClientRect();
    const floater = document.createElement("span");
    floater.className = "reel-floating-emoji";
    floater.textContent = emoji;
    floater.style.left = `${rect.left + rect.width / 2}px`;
    floater.style.top = `${rect.top + rect.height / 2}px`;
    document.body.appendChild(floater);
    setTimeout(() => floater.remove(), 760);
  }

  function optimisticReelAction(event) {
    const button = event.target.closest?.("[data-reel-react], [data-reel-save], [data-share-reel], [data-reel-remix], [data-follow-creator]");
    if (!button || !mobileReelsActive()) return;
    button.classList.remove("is-popping");
    void button.offsetWidth;
    button.classList.add("is-popping");
    setTimeout(() => button.classList.remove("is-popping"), 320);
    if (button.matches("[data-reel-react]")) {
      button.classList.add("active");
      button.setAttribute("aria-pressed", "true");
      const count = button.querySelector("small,[data-fire-count]");
      if (count && button.dataset.optimisticReact !== "1") {
        count.textContent = compactCount((Number(String(count.textContent || "0").replace(/[^\d.-]/g, "")) || 0) + 1);
        button.dataset.optimisticReact = "1";
      }
      burstReelEmoji(button, "❤️");
    } else if (button.matches("[data-reel-save]")) {
      button.classList.add("active");
      button.setAttribute("aria-pressed", "true");
      burstReelEmoji(button, "✓");
    } else if (button.matches("[data-share-reel]")) {
      burstReelEmoji(button, "↗");
    } else if (button.matches("[data-follow-creator]")) {
      const small = button.querySelector("small");
      if (small) small.textContent = "✓";
      const text = button.childNodes.length === 1 ? button : null;
      if (text) text.textContent = "Following";
    } else if (button.matches("[data-reel-remix]")) {
      event.preventDefault();
      event.stopPropagation();
      const id = button.dataset.reelRemix || button.closest(".reel-card")?.dataset.reelId || "";
      window.location.href = `/pulse/camera/reel${id ? `?remix=${encodeURIComponent(id)}` : ""}`;
    }
  }

  document.addEventListener("click", event => {
    optimisticReelAction(event);
    const soundButton = event.target.closest("[data-pulse-media-sound]");
    if (soundButton) {
      event.preventDefault();
      event.stopPropagation();
      setSoundEnabled(true);
      const video = soundButton.closest(".pulse-media-wrap")?.querySelector("video") || activeVideo;
      if (video) window.PulseMediaRenderer?.playVisibleVideo?.(video, true);
      return;
    }
    const button = event.target.closest("[data-retry-media]");
    if (button) {
      retry(button.closest(".pulse-media-wrap"));
      return;
    }
    const repair = event.target.closest("[data-repair-media]");
    if (!repair) return;
    const wrap = repair.closest(".pulse-media-wrap");
    const mediaId = repair.dataset.repairMedia || wrap?.dataset.mediaId || "";
    if (!mediaId) return;
    repair.disabled = true;
    repair.textContent = "Checking...";
    fetch(`/api/pulse/media/${encodeURIComponent(mediaId)}/repair`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(response => response.json().then(data => ({ response, data })))
      .then(({ response, data }) => {
        if (!response.ok || data.ok === false) throw new Error(data.message || "Video is still preparing.");
        const parent = wrap?.parentElement;
        if (wrap && data.media) {
          wrap.outerHTML = renderMedia(data.media, { surface: wrap.dataset.mediaSurface || "pulse" });
          hydrate(parent || document);
        }
      })
      .catch(error => {
        repair.textContent = error?.message || "Still preparing";
        setTimeout(() => {
          repair.disabled = false;
          repair.textContent = "Check now";
        }, 2200);
      });
  });

  document.addEventListener("pointerdown", seekReelProgress, { passive: true });
  document.addEventListener("pointermove", event => {
    if (event.buttons === 1) seekReelProgress(event);
  }, { passive: true });

  window.addEventListener("scroll", () => {
    lastScrollAt = Date.now();
  }, { passive: true });

  window.addEventListener("pagehide", () => {
    document.querySelectorAll("video").forEach(video => destroyHls(video));
    predictiveMediaCache.clear();
  });

  if ("MutationObserver" in window) {
    const cleanupRoot = document.documentElement || document.body;
    if (cleanupRoot && typeof cleanupRoot.nodeType === "number") {
      const cleanupObserver = new MutationObserver(() => {
        if (activeHlsVideo && !cleanupRoot.contains(activeHlsVideo)) {
          destroyHls(activeHlsVideo);
        }
      });
      cleanupObserver.observe(cleanupRoot, { childList: true, subtree: true });
    }
  }

  document.addEventListener("DOMContentLoaded", () => runIdle(() => hydrate(document), 900));
  const PulseVideo = { hydrate, retry, normalizeMedia, renderMedia, renderInto, playVisibleVideo, setSoundEnabled, setVideoMuted, soundEnabled, nativeHlsSupported, schedulePredictivePreload };
  window.PulseVideo = PulseVideo;
  window.PulseMediaRenderer = PulseVideo;
})();
