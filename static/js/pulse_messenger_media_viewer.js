(() => {
  "use strict";

  const TRIGGER = "[data-messenger-media-viewer-trigger]";
  const OPEN_CLASS = "pulse-messenger-media-viewer-open";
  const HIDDEN = "true";
  const VISIBLE = "false";
  const AUTO_HIDE_MS = 3200;
  const MAX_ZOOM = 4;
  const MIN_ZOOM = 1;

  const state = {
    bound: false,
    overlay: null,
    gallery: [],
    context: {},
    options: {},
    index: 0,
    open: false,
    controlsVisible: true,
    hideTimer: 0,
    statusTimer: 0,
    priorFocus: null,
    savedScrollTop: 0,
    scale: 1,
    panX: 0,
    panY: 0,
    lastTapAt: 0,
    lastPointer: null,
    touchStartY: 0,
    touchDistance: 0,
    touchScale: 1,
    pointers: new Map(),
  };

  function esc(value) {
    return String(value || "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function isReducedMotion() {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  }

  function normalizeItem(item, index = 0) {
    const raw = item || {};
    const highUrl = raw.high_url || raw.full_url || raw.playback_url || raw.cdn_url || raw.url || raw.media_url || raw.download_url || raw.thumbnail_url || "";
    const thumbUrl = raw.thumbnail_url || raw.thumb_url || raw.poster_url || highUrl;
    const messageId = Number(raw.message_id || raw.messageId || 0);
    const conversationId = Number(raw.conversation_id || raw.conversationId || state.context.conversationId || 0);
    const attachmentId = raw.attachment_id || raw.media_upload_id || raw.media_id || raw.id || "";
    const viewerId = raw.viewer_id || raw.viewerId || `${conversationId || "conversation"}:${messageId || "message"}:${attachmentId || index}`;
    return {
      ...raw,
      index,
      viewer_id: String(viewerId),
      media_id: attachmentId,
      attachment_id: raw.attachment_id || raw.id || "",
      message_id: messageId,
      conversation_id: conversationId,
      url: highUrl,
      high_url: highUrl,
      thumbnail_url: thumbUrl,
      sender_name: raw.sender_name || raw.senderName || "PulseSoc member",
      created_at: raw.created_at || raw.createdAt || "",
      filename: raw.filename || raw.original_filename || "PulseSoc media",
      media_type: raw.media_type || raw.type || "image",
      can_download: raw.can_download !== false,
      can_share: raw.can_share !== false,
      can_forward: raw.can_forward !== false,
    };
  }

  function galleryFromDom() {
    return Array.from(document.querySelectorAll(TRIGGER)).map((node, index) => normalizeItem({
      viewer_id: node.dataset.mediaViewerId || "",
      media_id: node.dataset.mediaId || "",
      attachment_id: node.dataset.attachmentId || "",
      message_id: Number(node.dataset.messageId || 0),
      conversation_id: Number(node.dataset.conversationId || 0),
      url: node.dataset.mediaUrl || "",
      high_url: node.dataset.mediaFullUrl || node.dataset.mediaUrl || "",
      thumbnail_url: node.dataset.mediaThumb || "",
      sender_name: node.dataset.senderName || "",
      created_at: node.dataset.createdAt || "",
      filename: node.dataset.filename || "",
      media_type: node.dataset.mediaType || "image",
    }, index)).filter(item => item.url);
  }

  function currentGallery() {
    try {
      if (typeof state.options.getGallery === "function") {
        const custom = state.options.getGallery();
        if (Array.isArray(custom) && custom.length) {
          state.gallery = custom.map((item, index) => normalizeItem(item, index)).filter(item => item.url);
          return state.gallery;
        }
      }
    } catch (error) {
      console.warn("PulseSoc media viewer gallery callback failed", error);
    }
    state.gallery = galleryFromDom();
    return state.gallery;
  }

  function createOverlay() {
    if (state.overlay) return state.overlay;
    const overlay = document.createElement("section");
    overlay.className = "pulse-messenger-media-viewer";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-hidden", HIDDEN);
    overlay.setAttribute("aria-label", "Messenger media viewer");
    overlay.innerHTML = `
      <div class="pmmv-ambient" aria-hidden="true"></div>
      <header class="pmmv-topbar" data-pmmv-controls>
        <button class="pmmv-icon" type="button" data-pmmv-close aria-label="Close media viewer">x</button>
        <div class="pmmv-meta">
          <strong data-pmmv-sender>PulseSoc media</strong>
          <small><span data-pmmv-time></span><span data-pmmv-counter></span></small>
        </div>
        <button class="pmmv-icon" type="button" data-pmmv-download aria-label="Save media">Save</button>
        <button class="pmmv-icon" type="button" data-pmmv-more aria-label="More media options">...</button>
      </header>
      <button class="pmmv-nav pmmv-prev" type="button" data-pmmv-prev aria-label="Previous media">&lt;</button>
      <figure class="pmmv-stage" data-pmmv-stage>
        <img data-pmmv-image alt="Messenger media">
        <figcaption data-pmmv-loader>Loading media...</figcaption>
      </figure>
      <button class="pmmv-nav pmmv-next" type="button" data-pmmv-next aria-label="Next media">&gt;</button>
      <footer class="pmmv-actionbar" data-pmmv-controls>
        <button type="button" data-pmmv-action="reply" aria-label="Reply to this media"><span aria-hidden="true">R</span>Reply</button>
        <button type="button" data-pmmv-action="react" aria-label="React to this media"><span aria-hidden="true">H</span>React</button>
        <button type="button" data-pmmv-action="share" aria-label="Share this media"><span aria-hidden="true">S</span>Share</button>
        <button type="button" data-pmmv-action="forward" aria-label="Forward this media"><span aria-hidden="true">F</span>Forward</button>
        <button type="button" data-pmmv-action="report" aria-label="Report this media"><span aria-hidden="true">!</span>Report</button>
      </footer>
      <div class="pmmv-more-panel" data-pmmv-more-panel hidden>
        <button type="button" data-pmmv-action="details">Details</button>
        <button type="button" data-pmmv-action="report">Report media</button>
      </div>
      <p class="pmmv-status" data-pmmv-status role="status" aria-live="polite"></p>
    `;
    document.body.appendChild(overlay);
    state.overlay = overlay;
    bindOverlay(overlay);
    return overlay;
  }

  function currentItem() {
    return state.gallery[state.index] || null;
  }

  function controls(show, persist = false) {
    state.controlsVisible = Boolean(show);
    state.overlay?.classList.toggle("is-controls-hidden", !state.controlsVisible);
    clearTimeout(state.hideTimer);
    if (state.open && state.controlsVisible && !persist) {
      state.hideTimer = window.setTimeout(() => controls(false), AUTO_HIDE_MS);
    }
  }

  function announce(message) {
    const node = state.overlay?.querySelector("[data-pmmv-status]");
    if (!node) return;
    node.textContent = message || "";
    clearTimeout(state.statusTimer);
    if (message) state.statusTimer = window.setTimeout(() => { node.textContent = ""; }, 2600);
  }

  function formatTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function resetTransform() {
    state.scale = 1;
    state.panX = 0;
    state.panY = 0;
    updateTransform();
  }

  function updateTransform() {
    const image = state.overlay?.querySelector("[data-pmmv-image]");
    if (!image) return;
    image.style.transform = `translate3d(${state.panX}px, ${state.panY}px, 0) scale(${state.scale})`;
    state.overlay?.classList.toggle("is-zoomed", state.scale > 1.02);
  }

  function render() {
    const overlay = createOverlay();
    const item = currentItem();
    if (!item) return;
    const image = overlay.querySelector("[data-pmmv-image]");
    const loader = overlay.querySelector("[data-pmmv-loader]");
    const sender = overlay.querySelector("[data-pmmv-sender]");
    const time = overlay.querySelector("[data-pmmv-time]");
    const counter = overlay.querySelector("[data-pmmv-counter]");
    const download = overlay.querySelector("[data-pmmv-download]");
    const prev = overlay.querySelector("[data-pmmv-prev]");
    const next = overlay.querySelector("[data-pmmv-next]");
    overlay.classList.add("is-loading");
    resetTransform();
    if (sender) sender.textContent = item.sender_name || "PulseSoc media";
    if (time) time.textContent = formatTime(item.created_at);
    if (counter) counter.textContent = `${state.index + 1} / ${state.gallery.length || 1}`;
    if (download) download.disabled = !item.can_download || !item.url;
    if (prev) prev.hidden = state.gallery.length < 2;
    if (next) next.hidden = state.gallery.length < 2;
    if (loader) loader.textContent = "Loading media...";
    if (image) {
      image.onload = () => {
        overlay.classList.remove("is-loading");
        if (loader) loader.textContent = "";
      };
      image.onerror = () => {
        overlay.classList.remove("is-loading");
        if (loader) loader.textContent = "Media could not load.";
        announce("Media could not load.");
      };
      image.alt = item.filename || `Image from ${item.sender_name || "PulseSoc member"}`;
      image.src = item.thumbnail_url || item.url;
      if (item.high_url && item.high_url !== image.src) {
        window.setTimeout(() => { image.src = item.high_url; }, 40);
      }
    }
    preloadNeighbors();
    controls(true);
  }

  function open(index = 0, trigger = null) {
    const gallery = currentGallery();
    if (!gallery.length) return;
    state.index = clamp(Number(index || 0), 0, gallery.length - 1);
    state.priorFocus = document.activeElement instanceof HTMLElement ? document.activeElement : trigger;
    const scrollContainer = state.options.getScrollContainer?.() || document.querySelector("[data-messages]");
    state.savedScrollTop = scrollContainer ? scrollContainer.scrollTop : window.scrollY;
    state.open = true;
    createOverlay().setAttribute("aria-hidden", VISIBLE);
    document.documentElement.classList.add(OPEN_CLASS);
    document.body.classList.add(OPEN_CLASS);
    render();
    window.setTimeout(() => state.overlay?.querySelector("[data-pmmv-close]")?.focus({ preventScroll: true }), 10);
  }

  function close() {
    if (!state.open) return;
    const image = state.overlay?.querySelector("[data-pmmv-image]");
    if (image) {
      image.removeAttribute("src");
      image.onload = null;
      image.onerror = null;
    }
    state.open = false;
    clearTimeout(state.hideTimer);
    state.overlay?.setAttribute("aria-hidden", HIDDEN);
    document.documentElement.classList.remove(OPEN_CLASS);
    document.body.classList.remove(OPEN_CLASS);
    const scrollContainer = state.options.getScrollContainer?.() || document.querySelector("[data-messages]");
    if (scrollContainer) scrollContainer.scrollTop = state.savedScrollTop;
    else window.scrollTo({ top: state.savedScrollTop, behavior: "auto" });
    if (state.priorFocus instanceof HTMLElement) {
      state.priorFocus.focus?.({ preventScroll: true });
    }
  }

  function move(delta) {
    if (!state.gallery.length) return;
    state.index = (state.index + delta + state.gallery.length) % state.gallery.length;
    render();
  }

  function preloadNeighbors() {
    [-1, 1].forEach(offset => {
      const item = state.gallery[state.index + offset];
      const src = item?.high_url || item?.url || "";
      if (!src) return;
      const image = new Image();
      image.decoding = "async";
      image.src = src;
    });
  }

  async function downloadItem(item) {
    if (!item?.url || item.can_download === false) return announce("This media cannot be saved.");
    const link = document.createElement("a");
    link.href = item.url;
    link.download = item.filename || "pulsesoc-media";
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
    announce("Save started.");
  }

  async function shareItem(item) {
    if (!item) return;
    const safeLink = item.message_id && item.conversation_id
      ? `${location.origin}/pulse/messages/${encodeURIComponent(item.conversation_id)}?message_id=${encodeURIComponent(item.message_id)}`
      : `${location.origin}/pulse/messages`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "PulseSoc media", text: "Shared from PulseSoc Messenger", url: safeLink });
        return announce("Share opened.");
      } catch (error) {
        if (error?.name === "AbortError") return;
      }
    }
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(safeLink);
      return announce("Conversation link copied.");
    }
    announce("Share is unavailable on this device.");
  }

  async function runAction(action) {
    const item = currentItem();
    if (!item) return;
    try {
      if (action === "reply") {
        await state.options.onReply?.(item);
        close();
        return;
      }
      if (action === "react") {
        await state.options.onReact?.(item, "heart");
        announce("Reaction sent.");
        return;
      }
      if (action === "share") return await shareItem(item);
      if (action === "forward") {
        if (item.can_forward === false || !item.message_id || item.message_id < 0) return announce("Forwarding is available after the media sends.");
        await state.options.onForward?.(item);
        announce("Forward action opened.");
        return;
      }
      if (action === "report") {
        if (!item.message_id || item.message_id < 0) return announce("Reports are available after the media sends.");
        await state.options.onReport?.(item, "media_report");
        announce("Report sent to moderation.");
        return;
      }
      if (action === "details") {
        const size = item.file_size || item.file_size_bytes ? ` - ${Math.round(Number(item.file_size || item.file_size_bytes) / 1024)} KB` : "";
        announce(`${item.media_type || "image"}${size}`);
      }
    } catch (error) {
      announce(error?.message || "Action could not be completed.");
    }
  }

  function toggleMore(force) {
    const panel = state.overlay?.querySelector("[data-pmmv-more-panel]");
    if (!panel) return;
    const show = typeof force === "boolean" ? force : panel.hidden;
    panel.hidden = !show;
    controls(true, show);
  }

  function bindOverlay(overlay) {
    overlay.addEventListener("click", event => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest("[data-pmmv-close]")) return close();
      if (target.closest("[data-pmmv-prev]")) return move(-1);
      if (target.closest("[data-pmmv-next]")) return move(1);
      if (target.closest("[data-pmmv-download]")) return downloadItem(currentItem());
      if (target.closest("[data-pmmv-more]")) return toggleMore();
      const action = target.closest("[data-pmmv-action]");
      if (action) {
        toggleMore(false);
        return runAction(action.getAttribute("data-pmmv-action") || "");
      }
    });
    const stage = overlay.querySelector("[data-pmmv-stage]");
    stage?.addEventListener("click", event => {
      if ((event.target instanceof Element) && event.target.closest("button")) return;
      const now = Date.now();
      if (now - state.lastTapAt < 320) {
        state.scale = state.scale > 1 ? 1 : 2.35;
        if (state.scale === 1) {
          state.panX = 0;
          state.panY = 0;
        }
        updateTransform();
        runAction("react");
      } else {
        controls(!state.controlsVisible);
      }
      state.lastTapAt = now;
    });
    stage?.addEventListener("wheel", event => {
      if (!state.open) return;
      event.preventDefault();
      const direction = event.deltaY > 0 ? -0.14 : 0.14;
      state.scale = clamp(state.scale + direction, MIN_ZOOM, MAX_ZOOM);
      if (state.scale <= 1.01) {
        state.scale = 1;
        state.panX = 0;
        state.panY = 0;
      }
      updateTransform();
      controls(true);
    }, { passive: false });
    stage?.addEventListener("pointerdown", event => {
      state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      state.lastPointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
      state.touchStartY = event.clientY;
      stage.setPointerCapture?.(event.pointerId);
    });
    stage?.addEventListener("pointermove", event => {
      if (!state.lastPointer || state.lastPointer.id !== event.pointerId || state.scale <= 1.01) return;
      state.panX += event.clientX - state.lastPointer.x;
      state.panY += event.clientY - state.lastPointer.y;
      state.lastPointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
      updateTransform();
    });
    stage?.addEventListener("pointerup", event => {
      state.pointers.delete(event.pointerId);
      const deltaY = event.clientY - state.touchStartY;
      if (state.scale <= 1.01 && deltaY > 120) close();
      state.lastPointer = null;
    });
    stage?.addEventListener("touchstart", event => {
      if (event.touches.length === 2) {
        state.touchDistance = touchDistance(event.touches);
        state.touchScale = state.scale;
      }
    }, { passive: true });
    stage?.addEventListener("touchmove", event => {
      if (event.touches.length !== 2 || !state.touchDistance) return;
      event.preventDefault();
      const nextDistance = touchDistance(event.touches);
      state.scale = clamp(state.touchScale * (nextDistance / state.touchDistance), MIN_ZOOM, MAX_ZOOM);
      if (state.scale <= 1.01) {
        state.scale = 1;
        state.panX = 0;
        state.panY = 0;
      }
      updateTransform();
    }, { passive: false });
  }

  function touchDistance(touches) {
    const [a, b] = touches;
    if (!a || !b) return 0;
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  }

  function onTriggerClick(event) {
    const trigger = event.target?.closest?.(TRIGGER);
    if (!trigger) return;
    event.preventDefault();
    event.stopPropagation();
    const gallery = currentGallery();
    const id = trigger.dataset.mediaViewerId || trigger.dataset.mediaId || "";
    const index = gallery.findIndex(item => item.viewer_id === id || String(item.media_id || "") === id);
    open(index >= 0 ? index : Number(trigger.dataset.mediaIndex || 0), trigger);
  }

  function focusables() {
    return Array.from(state.overlay?.querySelectorAll("button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex='-1'])") || [])
      .filter(node => node instanceof HTMLElement && !node.hidden && node.offsetParent !== null);
  }

  function onKeydown(event) {
    if (!state.open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      move(-1);
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      move(1);
      return;
    }
    if (event.key === "Tab") {
      const nodes = focusables();
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  function bind(root = document, options = {}) {
    state.options = { ...state.options, ...options };
    if (state.bound) return;
    state.bound = true;
    createOverlay();
    document.addEventListener("click", onTriggerClick, true);
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("mousemove", () => {
      if (state.open && !isReducedMotion()) controls(true);
    }, { passive: true });
  }

  function refresh(items = [], context = {}) {
    state.context = { ...state.context, ...context };
    state.gallery = (items || []).map((item, index) => normalizeItem(item, index)).filter(item => item.url);
    if (state.open) render();
  }

  function openById(viewerId) {
    const gallery = currentGallery();
    const index = gallery.findIndex(item => item.viewer_id === String(viewerId));
    if (index >= 0) open(index);
  }

  window.PulseMessengerMediaViewer = {
    bind,
    refresh,
    openById,
    close,
  };
})();
