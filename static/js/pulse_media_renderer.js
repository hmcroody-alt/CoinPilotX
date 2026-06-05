(function () {
  "use strict";

  const MAX_RETRIES = 2;
  const LOADED = "is-ready";
  const LOADING = "is-loading";
  const BROKEN = "is-broken";
  const PORTAL_CSS_ID = "pulse-cinematic-media-css";
  const SOUND_KEY = "pulseMediaSoundEnabled";
  const REELS_SOUND_KEY = "pulseReelsSoundEnabled";
  const metadataCache = new Map();
  const processingPolls = new Map();
  let hlsLoaderPromise = null;

  function ensurePortalStyles() {
    if (document.getElementById(PORTAL_CSS_ID)) return;
    const link = document.createElement("link");
    link.id = PORTAL_CSS_ID;
    link.rel = "stylesheet";
    link.href = "/static/css/pulse_cinematic_media.css?v=video-stage-size-20260604b";
    document.head.appendChild(link);
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
      return window.localStorage?.getItem(SOUND_KEY) === "true" || window.localStorage?.getItem(REELS_SOUND_KEY) === "true";
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
      button.hidden = !!enabled;
      button.textContent = enabled ? "Sound on" : "Tap for sound";
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
      alt: item.alt || item.title || "Pulse media",
      diag: item.diag || item.diagnostics || "",
    };
  }

  function layersHtml() {
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
      const controls = options.controls === false ? "" : " controls";
      const loop = options.loop ? " loop" : "";
      return `<div class="${shellClass}" data-fit="smart" data-open-media-lightbox ${data}${style ? ` style="${style}"` : ""}>${layersHtml()}<video data-pulse-video-player muted${controls}${loop} playsinline webkit-playsinline x-webkit-airplay="allow" preload="metadata"${poster}><source src="${esc(media.playback_url || media.valid_url || media.url)}"${type}></video><button class="pulse-media-sound-unlock" type="button" data-pulse-media-sound hidden>Tap for sound</button>${fallback}</div>`;
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
    processingPolls.set(mediaId, setInterval(poll, 12000));
    setTimeout(poll, 800);
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

  function showSoundPrompt(wrap, visible = true) {
    const button = wrap?.querySelector?.("[data-pulse-media-sound]");
    if (!button) return;
    button.hidden = !visible || soundEnabled();
    if (!button.hidden) {
      clearTimeout(button._pulseSoundTimer);
      button._pulseSoundTimer = setTimeout(() => {
        if (!soundEnabled()) button.hidden = true;
      }, 3200);
    }
  }

  let activeVideo = null;
  let playbackObserver = null;

  function mediaVideoWrap(video) {
    return video?.closest?.(".pulse-media-wrap") || video?.closest?.(".reel-card") || video?.parentElement || null;
  }

  function prepareMobileVideo(video) {
    if (!video) return;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.setAttribute("x-webkit-airplay", "allow");
    if (!video.preload || video.preload === "none") video.preload = "metadata";
  }

  function pauseOtherVideos(except) {
    document.querySelectorAll("video").forEach(video => {
      if (video !== except && !video.paused) video.pause();
    });
  }

  async function playVisibleVideo(video, preferSound = soundEnabled()) {
    if (!video) return;
    const wrap = mediaVideoWrap(video);
    pauseOtherVideos(video);
    activeVideo = video;
    prepareMobileVideo(video);
    video.preload = "auto";
    const shouldTrySound = !!preferSound && soundEnabled();
    video.muted = !shouldTrySound;
    try {
      await video.play();
      wrap?.classList.add("is-playing");
      if (shouldTrySound) setSoundEnabled(true);
      else showSoundPrompt(wrap, true);
    } catch (error) {
      if (shouldTrySound) {
        video.muted = true;
        setSoundEnabled(false);
        try {
          await video.play();
          showSoundPrompt(wrap, true);
          return;
        } catch (_) {}
      }
      if (mediaDebugEnabled()) console.warn("Pulse media autoplay blocked", {
        src: videoSource(video),
        message: error?.message || String(error),
      });
    }
  }

  function preloadNextVideo(video) {
    const wrap = mediaVideoWrap(video);
    const scope = wrap?.closest?.(".feed,.reels-immersive,[data-status-viewer],main,body") || document;
    const videos = Array.from(scope.querySelectorAll(".pulse-media-wrap video, [data-status-viewer] video, .reel-card video"));
    const index = videos.indexOf(video);
    const next = index >= 0 ? videos[index + 1] : null;
    if (!next || next.dataset.pulseLightPreloaded === "1") return;
    next.dataset.pulseLightPreloaded = "1";
    next.preload = "metadata";
    try {
      if (next.readyState === 0) next.load();
    } catch (_) {}
  }

  function bindAutoplayVideo(wrap, video) {
    if (!wrap || !video || video.dataset.pulseAutoplayBound === "1") return;
    video.dataset.pulseAutoplayBound = "1";
    ensureSoundButton(wrap);
    video.addEventListener("play", () => {
      pauseOtherVideos(video);
      activeVideo = video;
      wrap.classList.add("is-playing");
    });
    video.addEventListener("pause", () => wrap.classList.remove("is-playing"));
    if (!("IntersectionObserver" in window)) return;
    if (!playbackObserver) {
      playbackObserver = new IntersectionObserver(entries => {
        let best = null;
        entries.forEach(entry => {
          const vid = entry.target;
          const targetWrap = mediaVideoWrap(vid);
          if (!entry.isIntersecting || entry.intersectionRatio < .58) {
            if (!vid.paused) vid.pause();
            vid.preload = "metadata";
            targetWrap?.classList.remove("is-active-media");
            return;
          }
          if (!best || entry.intersectionRatio > best.intersectionRatio) best = entry;
        });
        if (!best) return;
        const vid = best.target;
        const targetWrap = mediaVideoWrap(vid);
        targetWrap?.classList.add("is-active-media");
        playVisibleVideo(vid, soundEnabled());
        preloadNextVideo(vid);
      }, { threshold: [0, .25, .58, .75, 1], rootMargin: "0px" });
    }
    playbackObserver.observe(video);
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

  async function attachHlsPlayback(wrap, video) {
    const src = wrap?.dataset.mediaHls || mediaUrl(wrap) || videoSource(video);
    if (!video || !isHlsUrl(src) || video.dataset.pulseHlsBound === "1") return;
    video.dataset.pulseHlsBound = "1";
    prepareMobileVideo(video);
    if (nativeHlsSupported(video)) {
      if (video._pulseHls) {
        try {
          video._pulseHls.destroy();
        } catch (_) {}
        video._pulseHls = null;
      }
      video.dataset.pulseNativeHls = "1";
      setVideoSource(video, src, "application/vnd.apple.mpegurl");
      return;
    }
    try {
      const Hls = await loadHlsLibrary();
      if (!Hls?.isSupported?.()) {
        throw new Error("HLS is not supported in this browser.");
      }
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
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (!data?.fatal) return;
        reportVideoDiagnostics(wrap, video, "hls_error");
        hls.destroy();
        video.dataset.pulseHlsBound = "";
        failMedia(wrap, video);
      });
    } catch (error) {
      if (mediaDebugEnabled()) console.warn("Pulse HLS attach failed", {
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
    if (mediaDebugEnabled()) {
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
      if (mediaDebugEnabled()) console.info("Pulse video metadata loaded", {
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
      if (mediaDebugEnabled()) console.info("Pulse video canplay", {
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
    ensurePortalStyles();
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
    wraps.forEach(wrap => {
      const video = wrap.querySelector("video");
      if (video) bindAutoplayVideo(wrap, video);
    });
  }

  document.addEventListener("click", event => {
    const soundButton = event.target.closest("[data-pulse-media-sound]");
    if (soundButton) {
      event.preventDefault();
      event.stopPropagation();
      setSoundEnabled(true);
      const video = soundButton.closest(".pulse-media-wrap")?.querySelector("video") || activeVideo;
      if (video) playVisibleVideo(video, true);
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

  document.addEventListener("DOMContentLoaded", () => hydrate(document));
  const PulseVideo = { hydrate, retry, normalizeMedia, renderMedia, renderInto, playVisibleVideo, setSoundEnabled, soundEnabled, nativeHlsSupported };
  window.PulseVideo = PulseVideo;
  window.PulseMediaRenderer = PulseVideo;
})();
