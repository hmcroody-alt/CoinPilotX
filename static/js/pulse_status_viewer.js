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

  window.PulseStatusViewer = { render, styleFor, kindFor };
})();
