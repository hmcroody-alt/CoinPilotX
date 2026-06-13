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
        loop: kind === "video",
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
    const [icon, label] = statusActionMeta[key] || ["•", button.textContent.trim() || "Action"];
    const count = button.matches("[data-status-story-react]")
      ? (button.querySelector("[data-status-story-reaction-count]")?.textContent || "0")
      : label;
    button.classList.add("pulse-status-action");
    if (key === "love") button.classList.add("pulse-status-react");
    button.innerHTML = `<span class="pulse-status-action-icon" aria-hidden="true">${icon}</span><small ${key === "love" ? 'data-status-story-reaction-count' : ""}>${count}</small>`;
    button.dataset.statusActionDecorated = "1";
    if (key === "love" && !button.hasAttribute("aria-pressed")) button.setAttribute("aria-pressed", "false");
  }

  function hardenStatusCloseButton(button) {
    if (!button) return;
    if (button.dataset.statusCloseHardened === "1") return;
    button.dataset.statusCloseHardened = "1";
    button.style.zIndex = "10090";
    button.style.width = "56px";
    button.style.height = "56px";
    button.style.minHeight = "56px";
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

  function closeStatusViewerNow(event) {
    const closeButton = event.target?.closest?.("[data-status-story-close]");
    if (!closeButton) return;
    hardenStatusCloseButton(closeButton);
    event.preventDefault();
    event.stopImmediatePropagation();
    const viewer = closeButton.closest("#pulseStatusStoryViewer,.pulse-status-story-viewer");
    viewer?.querySelectorAll("video").forEach(video => {
      try { video.pause(); } catch (_) {}
    });
    viewer?.classList.remove("open");
    viewer?.setAttribute("aria-hidden", "true");
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

  document.addEventListener("pointerdown", closeStatusViewerNow, true);
  document.addEventListener("touchstart", closeStatusViewerNow, { capture: true, passive: false });
  document.addEventListener("click", event => {
    const closeButton = event.target?.closest?.("[data-status-story-close]");
    if (closeButton) hardenStatusCloseButton(closeButton);
  }, true);
  document.addEventListener("click", optimisticStatusReaction, true);
  document.addEventListener("DOMContentLoaded", () => {
    decorateStatusActions(document);
    requestAnimationFrame(() => decorateStatusActions(document));
    setTimeout(() => decorateStatusActions(document), 500);
  });
  if ("MutationObserver" in window) {
    const statusObserver = new MutationObserver(records => {
      records.forEach(record => {
        if (record.target?.matches?.("[data-status-story-mute]")) {
          delete record.target.dataset.statusActionDecorated;
          decorateActionButton(record.target, "mute");
        }
        if (record.target?.matches?.("[data-status-story-close]")) hardenStatusCloseButton(record.target);
        record.addedNodes?.forEach(node => {
          if (node.nodeType === 1) decorateStatusActions(node);
        });
      });
    });
    statusObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  window.PulseStatusViewer = { render, styleFor, kindFor, decorateStatusActions };
})();
