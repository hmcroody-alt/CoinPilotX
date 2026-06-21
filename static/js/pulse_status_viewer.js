(function () {
  "use strict";

  const defaults = {
    background_color: "#111827",
    gradient: "aurora",
    text_color: "#f8fafc",
    font_family: "Inter",
    font_size: 34,
    text_align: "center",
    card_style: "soft",
    bold: true,
    italic: false,
  };
  const gradients = {
    aurora: "radial-gradient(circle at 20% 18%,rgba(54,229,143,.34),transparent 30%),linear-gradient(145deg,#111827,#020617)",
    midnight: "radial-gradient(circle at 80% 10%,rgba(110,223,246,.22),transparent 28%),linear-gradient(145deg,#020617,#111827)",
    sunrise: "radial-gradient(circle at 18% 18%,rgba(255,209,102,.34),transparent 32%),linear-gradient(145deg,#3b1024,#101827)",
    emerald: "radial-gradient(circle at 28% 20%,rgba(54,229,143,.38),transparent 32%),linear-gradient(145deg,#052e24,#07111d)",
    none: "",
  };
  const fonts = {
    Inter: "Inter,system-ui,sans-serif",
    Serif: "Georgia,Times New Roman,serif",
    Mono: "ui-monospace,SFMono-Regular,Menlo,monospace",
    Display: "Trebuchet MS,Inter,system-ui,sans-serif",
  };
  const esc = value => String(value || "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const mediaUrl = media => media.mux_playback_id
    ? `https://stream.mux.com/${media.mux_playback_id}.m3u8`
    : (media.mux_hls_url || media.playback_url || media.valid_url || media.cdn_url || media.media_url || media.url || media.src || "");

  function styleFor(item) {
    const style = { ...defaults, ...(item.status_style || item.status_tools?.status_style || {}) };
    const background = gradients[style.gradient] || style.background_color || defaults.background_color;
    const align = ["left", "center", "right"].includes(style.text_align) ? style.text_align : "center";
    const size = Math.max(24, Math.min(64, Number(style.font_size || defaults.font_size)));
    return [
      `background:${background}`,
      `color:${style.text_color || defaults.text_color}`,
      `font-family:${fonts[style.font_family] || fonts.Inter}`,
      `--status-story-font-size:${size}px`,
      `text-align:${align}`,
      `font-weight:${style.bold ? 900 : 650}`,
      `font-style:${style.italic ? "italic" : "normal"}`,
    ].join(";");
  }

  function kindFor(item, media, src) {
    const explicit = String(media.media_type || media.type || item.status_type || "").toLowerCase();
    const mime = String(media.mime_type || media.mime || "").toLowerCase();
    if (explicit === "video" || mime.startsWith("video/") || /\.(mp4|mov|webm|m4v)(\?|#|$)/i.test(src)) return "video";
    if (["photo", "image"].includes(explicit) || mime.startsWith("image/") || /\.(jpg|jpeg|png|webp|gif|avif)(\?|#|$)/i.test(src)) return "image";
    return "text";
  }

  function render(item = {}) {
    const media = (item.media || [])[0] || {};
    const src = mediaUrl(media);
    const kind = kindFor(item, media, src);
    const poster = media.poster_url || media.poster || media.thumbnail_url || media.mux_thumbnail_url || media.thumb || "";
    const text = esc(item.body || item.music?.title || "PulseSoc Status");
    if (src && window.PulseMediaRenderer) {
      const rendered = window.PulseMediaRenderer.renderMedia({
        ...media,
        media_url: media.media_url || src,
        valid_url: media.valid_url || src,
        playback_url: src,
        poster_url: poster,
        thumbnail_url: media.thumbnail_url || media.mux_thumbnail_url || media.thumb || poster,
        media_type: kind === "image" ? "image" : "video",
        mime_type: src.includes(".m3u8") ? "application/vnd.apple.mpegurl" : (media.playback_mime_type || media.mime_type || media.mime || ""),
      }, {
        surface: "status",
        className: "pulse-status-viewer-player",
        loop: false,
        attrs: 'data-status-viewer-player="1"',
      });
      return `${rendered}${item.body ? `<p>${text}</p>` : ""}`;
    }
    if (src && kind === "video") {
      if (window.PulseMediaRenderer?.renderMedia) {
        return `${window.PulseMediaRenderer.renderMedia({
          id: item.id || item.status_id || "",
          media_type: "video",
          media_url: src,
          playback_url: src,
          thumbnail_url: poster,
          poster_url: poster,
          mux_playback_id: item.mux_playback_id || "",
          duration_seconds: item.duration_seconds || item.duration || 0,
        }, { surface: "status", loop: true })}${item.body ? `<p>${text}</p>` : ""}`;
      }
      return `<video src="${esc(src)}" ${poster ? `poster="${esc(poster)}"` : ""} autoplay muted playsinline webkit-playsinline preload="metadata" controlsList="nodownload noplaybackrate noremoteplayback" disablepictureinpicture></video>${item.body ? `<p>${text}</p>` : ""}`;
    }
    if (src && kind === "image") return `<img src="${esc(src)}" alt="${text}" loading="eager" decoding="async">${item.body ? `<p>${text}</p>` : ""}`;
    return `<div class="pulse-status-story-text style-${esc(item.status_style?.card_style || item.status_tools?.status_style?.card_style || "soft")}" style="${esc(styleFor(item))}"><strong>${text}</strong></div>`;
  }

  const statusActionMeta = {
    love: ["❤️", "0"],
    comment: ["💬", "Comment"],
    share: ["↗️", "Share"],
    save: ["🔖", "Save"],
    more: ["•••", "More"],
    mute: ["🔇", "Sound"],
  };

  function decorateActionButton(button, key) {
    if (!button || button.dataset.statusActionDecorated === "1") return;
    let [icon, label] = statusActionMeta[key] || ["•", button.textContent.trim() || "Action"];
    if (key === "mute" && /tap/i.test(button.textContent || "")) {
      icon = "🔇";
      label = "Tap for sound";
    } else if (key === "mute" && /sound/i.test(button.textContent || "")) {
      icon = "🔊";
      label = "Sound";
    }
    const count = button.matches("[data-status-story-react]")
      ? (button.querySelector("[data-status-story-reaction-count]")?.textContent || "0")
      : label;
    button.classList.add("pulse-status-action");
    if (key === "love") button.classList.add("pulse-status-react");
    button.innerHTML = `<span class="pulse-status-action-icon" aria-hidden="true">${icon}</span><small ${key === "love" ? 'data-status-story-reaction-count' : ""}>${count}</small>`;
    button.dataset.statusActionDecorated = "1";
    if (key === "love" && !button.hasAttribute("aria-pressed")) button.setAttribute("aria-pressed", "false");
  }

  const storyRuntime = {
    viewer: null,
    timer: 0,
    progressTimer: 0,
    startedAt: 0,
    durationMs: 5000,
    paused: false,
    pauseStartedAt: 0,
    elapsedBeforePause: 0,
    pointer: null,
    closePointer: null,
    closeAt: 0,
    controlHideTimer: 0,
    pressTimer: 0,
    longPress: false,
    signature: "",
    currentStatusId: "",
    reportedCompletion: "",
  };

  function revealStatusChrome(viewer = activeViewer(), options = {}) {
    if (!viewer) return;
    window.clearTimeout(storyRuntime.controlHideTimer);
    viewer.classList.add("is-ui-visible");
    const keepOpen =
      options.persist ||
      viewer.matches(":focus-within") ||
      viewer.querySelector?.(".pulse-status-story-actions:hover");
    if (keepOpen) return;
    storyRuntime.controlHideTimer = window.setTimeout(() => {
      const active = activeViewer();
      if (!active || active !== viewer) return;
      if (active.matches(":focus-within") || active.querySelector?.(".pulse-status-story-actions:hover")) return;
      active.classList.remove("is-ui-visible");
    }, Number(options.timeout || 2600));
  }

  function hardenStatusCloseButton(button) {
    if (!button) return;
    if (button.dataset.statusCloseHardened === "1") return;
    button.dataset.statusCloseHardened = "1";
    button.style.zIndex = "10090";
    button.style.width = "52px";
    button.style.height = "52px";
    button.style.minHeight = "52px";
    button.style.pointerEvents = "auto";
    button.style.touchAction = "manipulation";
  }

  function decorateStatusActions(root = document) {
    const scope = root?.querySelectorAll ? root : document;
    scope.querySelectorAll?.("[data-status-story-react]").forEach(button => decorateActionButton(button, "love"));
    scope.querySelectorAll?.("[data-status-story-comment]").forEach(button => decorateActionButton(button, "comment"));
    scope.querySelectorAll?.("[data-status-story-share]").forEach(button => decorateActionButton(button, "share"));
    scope.querySelectorAll?.("[data-status-story-save]").forEach(button => decorateActionButton(button, "save"));
    scope.querySelectorAll?.("[data-status-story-more]").forEach(button => decorateActionButton(button, "more"));
    scope.querySelectorAll?.("[data-status-story-mute]").forEach(button => decorateActionButton(button, "mute"));
    scope.querySelectorAll?.("[data-status-story-close]").forEach(hardenStatusCloseButton);
  }

  function viewerRootFrom(node) {
    return node?.closest?.("#pulseStatusStoryViewer,.pulse-status-story-viewer,[data-status-viewer]");
  }

  function closeStatusViewerNow(viewer) {
    const root = viewer || storyRuntime.viewer || document.querySelector("#pulseStatusStoryViewer,.pulse-status-story-viewer.open,[data-status-viewer].open");
    if (!root) return;
    reportStoryCompletion(root, false);
    clearStoryTimers();
    root.querySelectorAll("video,audio").forEach(media => {
      try { media.pause(); } catch (_) {}
    });
    root.classList.remove("open");
    root.setAttribute("aria-hidden", "true");
    document.body.classList.remove("status-viewer-open");
    document.body.classList.remove("pulse-status-viewer-open");
    storyRuntime.viewer = null;
  }

  function closeViewer(viewer) {
    closeStatusViewerNow(viewer);
  }

  function closeStatusViewerIntentional(event) {
    const closeButton = event.target?.closest?.("[data-status-story-close],[data-status-viewer-close]");
    if (!closeButton) return;
    hardenStatusCloseButton(closeButton);
    event.preventDefault();
    event.stopImmediatePropagation();
    closeStatusViewerNow(viewerRootFrom(closeButton));
  }

  function clearStoryTimers() {
    window.clearTimeout(storyRuntime.timer);
    window.clearTimeout(storyRuntime.pressTimer);
    window.clearTimeout(storyRuntime.controlHideTimer);
    window.clearInterval(storyRuntime.progressTimer);
    storyRuntime.timer = 0;
    storyRuntime.pressTimer = 0;
    storyRuntime.controlHideTimer = 0;
    storyRuntime.progressTimer = 0;
  }

  function activeViewer() {
    return document.querySelector("#pulseStatusStoryViewer.open,.pulse-status-story-viewer.open,[data-status-viewer].open");
  }

  function isInteractiveTarget(target) {
    return !!target?.closest?.("button,a,input,textarea,select,label,[role='button'],[data-status-story-actions],.pulse-status-story-actions,[data-status-viewer-mute],[data-status-story-mute]");
  }

  function actionSelector(kind) {
    if (kind === "next") return "[data-status-story-next],[data-status-viewer-next]";
    if (kind === "prev") return "[data-status-story-prev],[data-status-viewer-prev]";
    return "";
  }

  function navigateStory(direction) {
    const viewer = activeViewer();
    if (!viewer) return;
    if (direction > 0) reportStoryCompletion(viewer, true);
    const button = viewer.querySelector(actionSelector(direction > 0 ? "next" : "prev"));
    if (button && !button.disabled) {
      button.click();
      window.setTimeout(() => scheduleStoryProgress(viewer), 80);
      return;
    }
    if (direction > 0) {
      closeViewer(viewer);
    }
  }

  function updateViewerSoundButton(viewer, video) {
    const button = viewer?.querySelector?.("[data-status-story-mute],[data-status-viewer-mute]");
    if (!button || !video) return;
    button.hidden = false;
    if (button.matches("[data-status-story-mute]")) {
      delete button.dataset.statusActionDecorated;
      button.textContent = video.muted || Number(video.volume || 0) === 0 ? "Tap for sound" : "Sound";
      decorateActionButton(button, "mute");
      return;
    }
    button.innerHTML = `<span class="pulse-status-action-icon" aria-hidden="true">${video.muted || Number(video.volume || 0) === 0 ? "🔇" : "🔊"}</span><small>${video.muted || Number(video.volume || 0) === 0 ? "Tap sound" : "Sound"}</small>`;
  }

  function unmuteViewerVideo(viewer = activeViewer()) {
    const video = viewer?.querySelector?.("video");
    if (!video) return false;
    video.defaultMuted = false;
    video.removeAttribute("muted");
    video.volume = Number(video.dataset.pulsePreferredVolume || video.volume || 1) || 1;
    if (window.PulseMediaRenderer?.setVideoMuted) window.PulseMediaRenderer.setVideoMuted(video, false, "status-user-unmute");
    else video.muted = false;
    window.PulseMediaRenderer?.setSoundEnabled?.(true);
    video.play?.().catch(() => {});
    updateViewerSoundButton(viewer, video);
    return true;
  }

  function toggleViewerSound(viewer = activeViewer()) {
    const video = viewer?.querySelector?.("video");
    if (!video) return false;
    const shouldMute = !(video.muted || Number(video.volume || 0) === 0);
    viewer.dataset.statusSoundToggledAt = String(Date.now());
    video.defaultMuted = false;
    if (!shouldMute) {
      video.removeAttribute("muted");
      video.volume = Number(video.dataset.pulsePreferredVolume || video.volume || 1) || 1;
    }
    if (window.PulseMediaRenderer?.setVideoMuted) window.PulseMediaRenderer.setVideoMuted(video, shouldMute, "status-tap-toggle-sound");
    else video.muted = shouldMute;
    window.PulseMediaRenderer?.setSoundEnabled?.(!shouldMute);
    video.play?.().catch(() => {});
    updateViewerSoundButton(viewer, video);
    revealStatusChrome(viewer, { timeout: 900 });
    return true;
  }

  function ensureImmersiveStatusHud(viewer = activeViewer()) {
    if (!viewer) return;
    const shell = viewer.querySelector?.(".pulse-status-story-shell");
    if (!shell) return;
    let sound = shell.querySelector("[data-status-now-playing]");
    if (!sound) {
      sound = document.createElement("div");
      sound.className = "pulse-status-now-playing";
      sound.dataset.statusNowPlaying = "1";
      sound.innerHTML = `
        <span class="pulse-status-now-playing-dot" aria-hidden="true">♪</span>
        <strong data-status-now-title>PulseSoc Status</strong>
        <span class="pulse-status-now-wave" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span>
      `;
      shell.insertBefore(sound, shell.querySelector(".pulse-status-story-footer") || null);
    }
    const body =
      viewer.dataset.statusMusicTitle ||
      viewer.querySelector("[data-status-viewer-body],[data-status-story-body]")?.textContent?.trim() ||
      "PulseSoc Status";
    const artist =
      viewer.dataset.statusMusicArtist ||
      viewer.querySelector("[data-status-viewer-author],[data-status-story-author]")?.textContent?.trim() ||
      "PulseSoc Music";
    const title = sound.querySelector("[data-status-now-title]");
    if (title) title.textContent = `${body} · ${artist}`;
  }

  function storyDuration(viewer) {
    const video = viewer?.querySelector?.("video");
    if (video) {
      const seconds = Number(video.duration || video.dataset.durationSeconds || video.getAttribute("duration") || 0);
      if (Number.isFinite(seconds) && seconds > 1) return Math.max(2500, Math.min(seconds * 1000, 30000));
      return 30000;
    }
    const media = viewer?.querySelector?.("[data-status-story-media],[data-status-viewer-media]");
    const custom = Number(media?.dataset.durationMs || media?.dataset.duration || viewer?.dataset.statusDurationMs || 0);
    if (Number.isFinite(custom) && custom > 0) return custom > 100 ? custom : custom * 1000;
    return 5000;
  }

  function currentStoryId(viewer) {
    return String(viewer?.dataset.statusCurrentId || viewer?.dataset.statusId || viewer?.querySelector?.("[data-status-current-id]")?.dataset.statusCurrentId || "").trim();
  }

  function storyElapsedRatio(viewer) {
    const elapsed = storyRuntime.paused
      ? storyRuntime.elapsedBeforePause
      : storyRuntime.elapsedBeforePause + Math.max(0, Date.now() - (storyRuntime.startedAt || Date.now()));
    const ratio = Math.max(0, Math.min(1, elapsed / Math.max(1, storyRuntime.durationMs || storyDuration(viewer))));
    return { elapsed, ratio };
  }

  function reportStoryCompletion(viewer = activeViewer(), completed = false) {
    const statusId = currentStoryId(viewer);
    if (!statusId || !navigator.onLine) return;
    const { elapsed, ratio } = storyElapsedRatio(viewer);
    const finalRatio = completed ? Math.max(ratio, 0.95) : ratio;
    const key = `${statusId}:${completed ? "complete" : Math.floor(finalRatio * 10)}`;
    if (!completed && finalRatio < 0.25) return;
    if (storyRuntime.reportedCompletion === key) return;
    storyRuntime.reportedCompletion = key;
    const payload = JSON.stringify({
      source: "status_viewer",
      completed: !!completed || finalRatio >= 0.95,
      completion_ratio: finalRatio,
      watch_ms: Math.round(elapsed),
    });
    try {
      if (navigator.sendBeacon) {
        const blob = new Blob([payload], { type: "application/json" });
        navigator.sendBeacon(`/api/pulse/status/${encodeURIComponent(statusId)}/view`, blob);
        return;
      }
    } catch (_) {}
    fetch(`/api/pulse/status/${encodeURIComponent(statusId)}/view`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
    }).catch(() => {});
  }

  function storySignature(viewer) {
    if (!viewer) return "";
    const media = viewer.querySelector("[data-status-story-media],[data-status-viewer-media]");
    const count = viewer.querySelector("[data-status-viewer-count],[data-status-story-count]")?.textContent || "";
    return [
      viewer.dataset.statusStoryIndex || "0",
      viewer.dataset.statusStoryCount || "1",
      count,
      media?.innerHTML?.length || 0,
    ].join(":");
  }

  function ensureProgressSegments(viewer) {
    const holder = viewer?.querySelector?.("[data-story-progress],.pulse-status-story-progress");
    if (!holder) return [];
    const countText = viewer.querySelector("[data-status-viewer-count],[data-status-story-count]")?.textContent || "";
    const match = countText.match(/(\d+)\s*\/\s*(\d+)/);
    const total = match ? Math.max(1, Math.min(Number(match[2]) || 1, 30)) : Math.max(1, Number(viewer.dataset.statusStoryCount || 1));
    const current = match ? Math.max(0, (Number(match[1]) || 1) - 1) : Number(viewer.dataset.statusStoryIndex || 0);
    if (holder.dataset.segmentCount !== String(total)) {
      holder.innerHTML = Array.from({ length: total }, (_, index) => `<span class="pulse-status-story-segment" data-story-segment="${index}"><i></i></span>`).join("");
      holder.dataset.segmentCount = String(total);
    }
    const segments = Array.from(holder.querySelectorAll("[data-story-segment]"));
    segments.forEach((segment, index) => {
      segment.classList.toggle("is-complete", index < current);
      segment.classList.toggle("is-current", index === current);
      const fill = segment.querySelector("i");
      if (fill && index < current) fill.style.transform = "scaleX(1)";
      if (fill && index > current) fill.style.transform = "scaleX(0)";
    });
    return segments;
  }

  function setProgress(viewer, ratio) {
    const segments = ensureProgressSegments(viewer);
    const current = segments.find(segment => segment.classList.contains("is-current")) || segments[0];
    const fill = current?.querySelector("i");
    if (fill) fill.style.transform = `scaleX(${Math.max(0, Math.min(1, ratio))})`;
    viewer?.classList.toggle("is-paused", !!storyRuntime.paused);
  }

  function scheduleStoryProgress(viewer = activeViewer()) {
    if (!viewer) return;
    clearStoryTimers();
    storyRuntime.viewer = viewer;
    storyRuntime.signature = storySignature(viewer);
    storyRuntime.currentStatusId = currentStoryId(viewer);
    storyRuntime.reportedCompletion = "";
    storyRuntime.paused = false;
    storyRuntime.elapsedBeforePause = 0;
    storyRuntime.startedAt = Date.now();
    storyRuntime.durationMs = storyDuration(viewer);
    ensureProgressSegments(viewer);
    ensureImmersiveStatusHud(viewer);
    setProgress(viewer, 0);
    revealStatusChrome(viewer);
    const video = viewer.querySelector("video");
    if (video) {
      video.loop = false;
      video.addEventListener("ended", () => {
        reportStoryCompletion(viewer, true);
        navigateStory(1);
      }, { once: true });
      video.play?.().catch(() => {});
    } else {
      storyRuntime.timer = window.setTimeout(() => navigateStory(1), storyRuntime.durationMs);
    }
    storyRuntime.progressTimer = window.setInterval(() => {
      if (!activeViewer() || storyRuntime.paused) return;
      const elapsed = storyRuntime.elapsedBeforePause + Date.now() - storyRuntime.startedAt;
      setProgress(viewer, elapsed / Math.max(1, storyRuntime.durationMs));
      if (!video && elapsed >= storyRuntime.durationMs) navigateStory(1);
    }, 100);
  }

  function pauseStory() {
    const viewer = activeViewer();
    if (!viewer || storyRuntime.paused) return;
    storyRuntime.paused = true;
    storyRuntime.pauseStartedAt = Date.now();
    storyRuntime.elapsedBeforePause += Date.now() - storyRuntime.startedAt;
    window.clearTimeout(storyRuntime.timer);
    viewer.querySelectorAll("video").forEach(video => {
      try { video.pause(); } catch (_) {}
    });
    viewer.classList.add("is-paused");
  }

  function resumeStory() {
    const viewer = activeViewer();
    if (!viewer || !storyRuntime.paused) return;
    storyRuntime.paused = false;
    storyRuntime.startedAt = Date.now();
    viewer.querySelectorAll("video").forEach(video => video.play?.().catch(() => {}));
    viewer.classList.remove("is-paused");
    if (!viewer.querySelector("video")) {
      const remaining = Math.max(250, storyRuntime.durationMs - storyRuntime.elapsedBeforePause);
      storyRuntime.timer = window.setTimeout(() => navigateStory(1), remaining);
    }
  }

  function bindStoryTouchControls() {
    document.addEventListener("pointerdown", event => {
      const viewer = activeViewer();
      if (!viewer) return;
      revealStatusChrome(viewer, { timeout: 2200 });
      const close = event.target?.closest?.("[data-status-story-close],[data-status-viewer-close]");
      if (close) {
        hardenStatusCloseButton(close);
        storyRuntime.closePointer = { x: event.clientX, y: event.clientY, at: Date.now(), id: event.pointerId };
        return;
      }
      if (!viewer.contains(event.target) || isInteractiveTarget(event.target)) return;
      storyRuntime.pointer = { x: event.clientX, y: event.clientY, at: Date.now(), id: event.pointerId };
      storyRuntime.longPress = false;
      storyRuntime.pressTimer = window.setTimeout(() => {
        storyRuntime.longPress = true;
        pauseStory();
      }, 420);
    }, true);
    document.addEventListener("pointerup", event => {
      const viewer = activeViewer();
      const close = event.target?.closest?.("[data-status-story-close],[data-status-viewer-close]");
      if (close && storyRuntime.closePointer) {
        const dx = Math.abs(event.clientX - storyRuntime.closePointer.x);
        const dy = Math.abs(event.clientY - storyRuntime.closePointer.y);
        const now = Date.now();
        if (dx <= 10 && dy <= 10 && now - storyRuntime.closeAt > 250) {
          storyRuntime.closeAt = now;
          closeStatusViewerIntentional(event);
        }
        storyRuntime.closePointer = null;
        return;
      }
      if (!viewer || !storyRuntime.pointer) return;
      window.clearTimeout(storyRuntime.pressTimer);
      const dx = event.clientX - storyRuntime.pointer.x;
      const dy = event.clientY - storyRuntime.pointer.y;
      const absX = Math.abs(dx);
      const absY = Math.abs(dy);
      if (storyRuntime.longPress) {
        resumeStory();
      } else if (dy >= 76 && absY > absX * 1.08) {
        closeViewer(viewer);
      } else if (absX >= 52 && absX > absY * 1.18) {
        navigateStory(dx < 0 ? 1 : -1);
      } else if (absX < 10 && absY < 10 && !isInteractiveTarget(event.target)) {
        toggleViewerSound(viewer);
      }
      storyRuntime.pointer = null;
      storyRuntime.longPress = false;
    }, true);
    document.addEventListener("pointercancel", () => {
      window.clearTimeout(storyRuntime.pressTimer);
      if (storyRuntime.longPress) resumeStory();
      storyRuntime.pointer = null;
      storyRuntime.closePointer = null;
      storyRuntime.longPress = false;
    }, true);
  }

  function parseCount(value) {
    const match = String(value || "").replace(/,/g, "").match(/\d+(?:\.\d+)?/);
    return match ? Number(match[0]) || 0 : 0;
  }

  function syncStatusReactionButton(viewer) {
    const root = viewer || document.querySelector("#pulseStatusStoryViewer,.pulse-status-story-viewer");
    const button = root?.querySelector?.("[data-status-story-react]");
    const count = button?.querySelector?.("[data-status-story-reaction-count]");
    if (!button || !count) return;
    const footerText = root.querySelector?.("[data-status-story-count],[data-status-viewer-count]")?.textContent || "";
    const reactionMatch = footerText.replace(/,/g, "").match(/(\d+)\s+reactions?/i);
    if (reactionMatch) count.textContent = reactionMatch[1];
    button.classList.toggle("active", button.getAttribute("aria-pressed") === "true" || button.classList.contains("active"));
  }

  function optimisticStatusReaction(event) {
    const button = event.target?.closest?.("[data-status-story-react]");
    if (!button || button.dataset.statusUiPending === "1") return;
    decorateActionButton(button, "love");
    const count = button.querySelector("[data-status-story-reaction-count]");
    const previous = count?.textContent || "0";
    button.dataset.statusUiPending = "1";
    button.dataset.statusPreviousCount = previous;
    button.classList.remove("is-popping");
    void button.offsetWidth;
    button.classList.add("active", "is-popping");
    button.setAttribute("aria-pressed", "true");
    if (count) count.textContent = String(parseCount(previous) + 1);
    setTimeout(() => button.classList.remove("is-popping"), 360);
    setTimeout(() => {
      delete button.dataset.statusUiPending;
      syncStatusReactionButton(button.closest("#pulseStatusStoryViewer,.pulse-status-story-viewer"));
    }, 1400);
  }

  bindStoryTouchControls();
  document.addEventListener("click", event => {
    const closeButton = event.target?.closest?.("[data-status-story-close],[data-status-viewer-close]");
    if (closeButton) {
      hardenStatusCloseButton(closeButton);
      closeStatusViewerIntentional(event);
    }
  }, true);
  document.addEventListener("click", optimisticStatusReaction, true);
  document.addEventListener("focusin", event => {
    const viewer = viewerRootFrom(event.target);
    if (viewer) revealStatusChrome(viewer, { persist: true });
  }, true);
  document.addEventListener("focusout", event => {
    const viewer = viewerRootFrom(event.target);
    if (viewer) window.setTimeout(() => revealStatusChrome(viewer, { timeout: 1400 }), 60);
  }, true);
  document.addEventListener("DOMContentLoaded", () => {
    decorateStatusActions(document);
    requestAnimationFrame(() => decorateStatusActions(document));
    setTimeout(() => decorateStatusActions(document), 500);
  });
  if ("MutationObserver" in window) {
    const observerRoot = document.documentElement || document.body;
    if (!observerRoot || typeof observerRoot.nodeType !== "number") {
      document.addEventListener("DOMContentLoaded", () => window.PulseStatusViewer?.decorateStatusActions?.(document), { once: true });
    } else {
      let statusProgressFrame = 0;
      const scheduleActiveViewerOnce = () => {
        if (statusProgressFrame) return;
        statusProgressFrame = requestAnimationFrame(() => {
          statusProgressFrame = 0;
          const viewer = activeViewer();
          if (!viewer) return;
          const signature = storySignature(viewer);
          if (viewer !== storyRuntime.viewer || signature !== storyRuntime.signature) scheduleStoryProgress(viewer);
        });
      };
      const statusObserver = new MutationObserver(records => {
        let shouldCheckViewer = false;
        records.forEach(record => {
          record.addedNodes?.forEach(node => {
            if (node.nodeType !== 1) return;
            decorateStatusActions(node);
            if (
              node.matches?.("#pulseStatusStoryViewer,.pulse-status-story-viewer,[data-status-viewer]") ||
              node.querySelector?.("#pulseStatusStoryViewer,.pulse-status-story-viewer,[data-status-viewer]")
            ) {
              shouldCheckViewer = true;
            }
          });
        });
        if (shouldCheckViewer) scheduleActiveViewerOnce();
      });
      statusObserver.observe(observerRoot, {
        childList: true,
        subtree: true,
      });
    }
  }

  window.PulseStatusViewer = { render, styleFor, kindFor, decorateStatusActions, scheduleStoryProgress, pauseStory, resumeStory, navigateStory, unmuteViewerVideo, toggleViewerSound, updateViewerSoundButton, closeStatusViewerNow };
})();
