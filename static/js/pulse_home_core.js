(function () {
  "use strict";

  const main = document.querySelector("main");
  const feed = document.getElementById("feed");
  const loadMore = document.getElementById("loadMore");
  const tabs = document.getElementById("tabs");
  const toastNode = document.getElementById("toast");
  if (!main || !feed || !tabs) return;

  const state = {
    feed: main.dataset.feed || "for_you",
    topic: main.dataset.topic || "",
    profile: main.dataset.profile || "",
    offset: 0,
    loading: false,
  };

  const viewedPosts = new Set();
  let viewObserver = null;
  let feedVideoObserver = null;
  let longPressTimer = 0;
  let longPressStart = null;

  function toast(message) {
    if (!toastNode) return;
    toastNode.textContent = String(message || "PulseSoc updated.");
    toastNode.classList.add("show");
    clearTimeout(toastNode._pulseTimer);
    toastNode._pulseTimer = setTimeout(() => toastNode.classList.remove("show"), 3200);
  }

  async function api(url, options = {}) {
    const controller = typeof AbortController === "undefined" ? null : new AbortController();
    const timer = controller ? setTimeout(() => controller.abort(), Number(options.timeoutMs || 10000)) : null;
    const isForm = options.body instanceof FormData;
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        headers: isForm ? {} : { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
        signal: options.signal || controller?.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.message || data.error || "Request failed. Please retry.");
      return data;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("PulseSoc is taking too long to respond. Please retry.");
      throw error;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  const statusUi = {
    modal: document.getElementById("pulseStatusViewer"),
    form: document.getElementById("pulseStatusForm"),
    mediaInput: document.getElementById("pulseStatusMedia"),
    soundInput: document.getElementById("pulseStatusSound"),
    preview: document.getElementById("pulseStatusPreview"),
    body: document.getElementById("pulseStatusBody"),
    mode: document.getElementById("pulseStatusMode"),
    privacy: document.getElementById("pulseStatusPrivacy"),
    duration: document.getElementById("pulseStatusDuration"),
    progress: document.querySelector("#pulseStatusForm [data-upload-progress]"),
    modePicker: document.querySelector("[data-status-mode-picker]"),
    rail: document.querySelector("[data-status-strip]"),
    empty: document.querySelector("[data-status-empty]"),
    file: null,
    objectUrl: "",
    publishing: false,
  };

  function statusRenderProgress(stage, percent, message) {
    if (!statusUi.progress) return;
    if (window.PulseUploadManager?.render) {
      window.PulseUploadManager.render(statusUi.progress, { stage, percent, message });
      return;
    }
    statusUi.progress.setAttribute("aria-valuenow", String(Math.max(0, Math.min(100, Number(percent || 0)))));
    const bar = statusUi.progress.querySelector("[data-upload-progress-bar]");
    const text = statusUi.progress.querySelector("[data-upload-progress-text]");
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(percent || 0)))}%`;
    if (text) text.textContent = message || "";
  }

  function statusSetState(message = "", tone = "") {
    const node = document.querySelector("[data-status-inline-state]");
    if (!node) return;
    node.textContent = message;
    node.dataset.tone = tone;
  }

  function statusResetObjectUrl() {
    if (statusUi.objectUrl) URL.revokeObjectURL(statusUi.objectUrl);
    statusUi.objectUrl = "";
  }

  function statusClearDraft() {
    statusResetObjectUrl();
    statusUi.file = null;
    if (statusUi.mediaInput) {
      statusUi.mediaInput.value = "";
      statusUi.mediaInput.removeAttribute("capture");
    }
    if (statusUi.soundInput) statusUi.soundInput.value = "";
    if (statusUi.mode) statusUi.mode.value = "image";
    if (statusUi.body) statusUi.body.value = "";
    if (statusUi.preview) {
      statusUi.preview.textContent = "";
      const placeholder = element("span", "", "Choose a status type to preview it here.");
      const overlays = element("div", "", "");
      overlays.dataset.statusOverlays = "";
      statusUi.preview.append(placeholder, overlays);
    }
    statusSetState("");
    statusRenderProgress("idle", 0, "Choose a status type to begin.");
  }

  function statusOpenCreator(showPicker = true) {
    if (!statusUi.modal || !statusUi.form) return;
    statusUi.modal.classList.add("open");
    statusUi.modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("status-editor-open");
    statusUi.modePicker?.classList.toggle("is-hidden", !showPicker);
    statusUi.form.classList.toggle("is-choosing", !!showPicker);
    statusRenderProgress("idle", 0, showPicker ? "Choose photo, video, camera, or text." : "Status draft ready.");
  }

  function statusCloseCreator() {
    statusUi.modal?.classList.remove("open");
    statusUi.modal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("status-editor-open");
    statusResetObjectUrl();
  }

  function statusTextMode() {
    statusOpenCreator(false);
    if (statusUi.mode) statusUi.mode.value = "text";
    if (statusUi.preview) {
      statusUi.preview.textContent = "";
      const wrap = element("div", "pulse-ai-story-preview text-story", "");
      wrap.append(element("strong", "", "Text Status"), element("small", "", "Write your update, choose privacy, then post."));
      statusUi.preview.appendChild(wrap);
    }
    statusUi.body?.focus();
    statusSetState("Text status selected.", "info");
    statusRenderProgress("starting", 1, "Text status ready.");
  }

  function statusPickMedia(capture = false) {
    statusOpenCreator(false);
    if (statusUi.mode) statusUi.mode.value = "image";
    if (capture) statusUi.mediaInput?.setAttribute("capture", "environment");
    else statusUi.mediaInput?.removeAttribute("capture");
    statusRenderProgress("idle", 0, capture ? "Open your camera or choose a recent capture." : "Choose an image or video from your gallery.");
    statusUi.mediaInput?.click();
  }

  function statusRenderPreview() {
    statusResetObjectUrl();
    const file = statusUi.file;
    if (!statusUi.preview || !file) return;
    const isVideo = String(file.type || "").startsWith("video/") || /\.(mp4|mov|webm|m4v)$/i.test(file.name || "");
    statusUi.objectUrl = URL.createObjectURL(file);
    statusUi.preview.textContent = "";
    const media = document.createElement(isVideo ? "video" : "img");
    media.src = statusUi.objectUrl;
    if (isVideo) {
      media.controls = true;
      media.loop = true;
      media.playsInline = true;
      media.setAttribute("webkit-playsinline", "");
      if (statusUi.mode) statusUi.mode.value = "video";
    } else {
      media.alt = file.name || "PulseSoc Status media";
      media.decoding = "async";
      if (statusUi.mode) statusUi.mode.value = "image";
    }
    statusUi.preview.appendChild(media);
    statusRenderProgress("starting", 1, `Ready to publish ${file.name || "media"}.`);
  }

  function statusMediaUrl(media = {}) {
    return media.mux_playback_id
      ? `https://stream.mux.com/${media.mux_playback_id}.m3u8`
      : (media.mux_hls_url || media.playback_url || media.valid_url || media.cdn_url || media.media_url || media.url || "");
  }

  function statusAvatarNode(item = {}) {
    const name = item.author_name || "PulseSoc";
    const ring = element("span", "pulse-status-avatar-ring", "");
    const avatar = item.author_avatar_url || "";
    if (avatar) {
      const img = document.createElement("img");
      img.src = avatar;
      img.alt = name;
      img.loading = "lazy";
      img.decoding = "async";
      ring.appendChild(img);
    } else {
      ring.textContent = name.slice(0, 1) || "P";
    }
    return ring;
  }

  function statusCardNode(item = {}) {
    const button = element("button", `pulse-status-card ${item.viewed ? "is-viewed" : ""}`, "");
    button.type = "button";
    button.dataset.statusDynamic = "";
    button.dataset.openStatusId = item.id || "";
    button.dataset.statusId = item.id || "";
    button.dataset.statusOpenUrl = `/pulse/status?status=${item.id || ""}`;
    button.setAttribute("aria-label", `Open ${item.author_name || "PulseSoc"} Status`);
    button.appendChild(statusAvatarNode(item));
    const preview = element("span", "pulse-status-home-preview", "");
    const media = (item.media || [])[0] || {};
    const src = statusMediaUrl(media);
    const poster = media.poster_url || media.thumbnail_url || media.thumb || src;
    const kind = String(media.media_type || item.status_type || "text").toLowerCase();
    if (src && kind === "video") {
      const video = document.createElement("video");
      video.className = "pulse-status-card-media";
      video.src = src;
      if (poster) video.poster = poster;
      video.autoplay = true;
      video.muted = true;
      video.loop = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.dataset.statusHomeVideo = "";
      preview.appendChild(video);
    } else if (poster || src) {
      const img = document.createElement("img");
      img.className = "pulse-status-card-media";
      img.src = poster || src;
      img.alt = item.body || "PulseSoc Status";
      img.loading = "lazy";
      img.decoding = "async";
      preview.appendChild(img);
    }
    preview.append(element("strong", "", item.body || item.music?.title || item.status_type || "PulseSoc Status"), element("small", "", `${item.author_name || "PulseSoc member"} · ${item.viewed ? "seen" : "new"}`));
    button.appendChild(preview);
    return button;
  }

  async function statusHydrateRail() {
    if (!statusUi.rail) return;
    try {
      const data = await api("/api/pulse/status/rail?lane=for_you");
      const items = (data.items || []).slice(0, 12);
      statusUi.rail.querySelectorAll("[data-status-dynamic]").forEach(node => node.remove());
      if (statusUi.empty) statusUi.empty.hidden = !!items.length;
      const fragment = document.createDocumentFragment();
      items.forEach(item => fragment.appendChild(statusCardNode(item)));
      statusUi.rail.appendChild(fragment);
      statusUi.rail.querySelectorAll("[data-status-home-video]").forEach(video => video.play?.().catch(() => {}));
    } catch (error) {
      if (statusUi.empty && !statusUi.rail.querySelector("[data-status-dynamic]")) {
        statusUi.empty.hidden = false;
      }
    }
  }

  async function statusPublish(event) {
    event.preventDefault();
    if (statusUi.publishing) return;
    const text = (statusUi.body?.value || "").trim();
    const mode = statusUi.mode?.value || "image";
    if (!text && !statusUi.file) {
      statusSetState("Add text, a photo, or a video before posting.", "error");
      statusUi.body?.focus();
      return;
    }
    statusUi.publishing = true;
    const submitter = event.submitter || statusUi.form?.querySelector('[type="submit"]');
    if (submitter) submitter.disabled = true;
    try {
      statusSetState("Posting Status...", "info");
      const mediaIds = [];
      if (statusUi.file) {
        const formData = new FormData();
        formData.append("file", statusUi.file);
        formData.append("context_type", "pulse_status");
        formData.append("context_id", "home-draft");
        const upload = window.PulseUploadManager
          ? await window.PulseUploadManager.upload({ url: "/api/pulse/media/upload", formData, file: statusUi.file, mediaType: statusUi.file.type || "", button: submitter, progressTarget: statusUi.progress, lockKey: `pulse-home-status-${statusUi.file.name}` })
          : await api("/api/pulse/media/upload", { method: "POST", body: formData });
        if (upload.media?.id) mediaIds.push(upload.media.id);
        else throw new Error("Media uploaded, but PulseSoc did not return a media ID.");
      }
      statusRenderProgress("publishing", 96, "Publishing...");
      const created = await api("/api/pulse/status", {
        method: "POST",
        body: JSON.stringify({
          status_type: mediaIds.length ? mode : "text",
          body: text,
          media_ids: mediaIds,
          visibility: statusUi.privacy?.value || "public",
          duration_hours: Number(statusUi.duration?.value || 24),
          ai_context: { source: "pulse_home_status_creator" },
        }),
      });
      statusRenderProgress("success", 100, "Posted successfully.");
      statusSetState("Status posted.", "success");
      statusCloseCreator();
      statusClearDraft();
      await statusHydrateRail();
      const id = created.status?.id || created.status_id;
      const newCard = id ? document.querySelector(`[data-open-status-id="${CSS.escape(String(id))}"]`) : null;
      newCard?.classList.add("just-created");
      newCard?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    } catch (error) {
      statusSetState(error.message || "Status publish failed.", "error");
      statusRenderProgress("failed", 0, `${error.message || "Status publish failed."} Tap Post to retry.`);
      toast(error.message || "Status publish failed.");
    } finally {
      statusUi.publishing = false;
      if (submitter) submitter.disabled = false;
    }
  }

  function bindStatusHome() {
    if (!statusUi.modal || !statusUi.form) return;
    document.body.dataset.statusHomeWired = "1";
    window.PulseHomeStatus = {
      open: statusOpenCreator,
      close: statusCloseCreator,
      text: statusTextMode,
      pickMedia: statusPickMedia,
      hydrate: statusHydrateRail,
    };
    document.addEventListener("click", event => {
      const create = event.target.closest("[data-status-home-create]");
      if (create) {
        event.preventDefault();
        event.stopImmediatePropagation();
        statusClearDraft();
        statusOpenCreator(true);
        return;
      }
      const starter = event.target.closest("[data-status-start]");
      if (starter) {
        const mode = starter.dataset.statusStart || "";
        if (mode === "text") statusTextMode();
        else if (mode === "upload") statusPickMedia(false);
        else if (mode === "camera") statusPickMedia(true);
        else if (mode === "music" || mode === "ai" || mode === "live") {
          statusOpenCreator(false);
          if (statusUi.mode) statusUi.mode.value = mode;
          statusUi.body?.focus();
          statusSetState(`${mode === "ai" ? "AI Story" : mode.charAt(0).toUpperCase() + mode.slice(1)} Status tools are being prepared. Add text, photo, or video to post today.`, "info");
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (event.target.closest("[data-status-cancel]")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        statusCloseCreator();
        statusClearDraft();
      }
    }, true);
    statusUi.mediaInput?.addEventListener("change", event => {
      statusUi.mediaInput?.removeAttribute("capture");
      statusUi.file = event.target.files?.[0] || null;
      if (statusUi.file) statusRenderPreview();
    });
    statusUi.form.addEventListener("submit", statusPublish);
    const params = new URLSearchParams(location.search);
    if (params.has("create_status")) {
      statusClearDraft();
      statusOpenCreator(true);
    }
    statusHydrateRail().then(() => {
      const linkedStatusId = params.get("status") || params.get("status_id");
      if (linkedStatusId) {
        const card = document.querySelector(`[data-open-status-id="${CSS.escape(String(linkedStatusId))}"]`);
        if (card) card.click();
      }
    });
  }

  bindStatusHome();

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
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

  function count(value) {
    return Math.max(0, Number(value || 0));
  }

  function compactNumber(value) {
    const n = count(value);
    if (n >= 1000000) return `${(n / 1000000).toFixed(n >= 10000000 ? 0 : 1).replace(/\.0$/, "")}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "")}K`;
    return String(n);
  }

  function postUrl(post) {
    return post?.permalink || `/pulse/post/${post?.id || ""}`;
  }

  function videoDetailUrl(post, item) {
    const candidates = [item?.video_permalink, post?.video_permalink];
    const directId = Number(item?.video_id || post?.video_id || 0);
    if (directId > 0) candidates.push(`/pulse/videos/${directId}`);
    return candidates.find(value => /^\/pulse\/videos\/\d+$/.test(String(value || ""))) || "";
  }

  function mediaUrl(item) {
    return item?.playback_url || item?.media_url || item?.url || item?.source_url || item?.cdn_url || item?.valid_url || "";
  }

  function isVideoMedia(item, url) {
    const type = String(item?.media_type || item?.type || item?.mime_type || "").toLowerCase();
    return type.includes("video") || /\.(mp4|mov|webm|m4v|m3u8)(\?|$)/i.test(url || "");
  }

  function avatarNode(author, fallbackName) {
    const name = author?.display_name || fallbackName || "PulseSoc";
    const avatar = element("span", "post-card-avatar avatar", name.slice(0, 1).toUpperCase());
    const avatarUrl = author?.avatar_url || author?.avatar_thumbnail_url || author?.photo_url || "";
    if (avatarUrl) {
      avatar.textContent = "";
      const image = document.createElement("img");
      image.src = avatarUrl;
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      avatar.appendChild(image);
    }
    return avatar;
  }

  function currentViewerAvatar(post) {
    return avatarNode(post.viewer || post.current_user || {}, "You");
  }

  function mediaTypeLabel(post, mediaItems) {
    if ((mediaItems || []).some(item => isVideoMedia(item, mediaUrl(item)))) return "video";
    if ((mediaItems || []).length) return "image";
    if (post.link_url || post.url) return "link";
    return "text";
  }

  function visibilityIcon(post) {
    const value = String(post.visibility || "public").toLowerCase();
    if (value.includes("private")) return "🔒";
    if (value.includes("followers")) return "👥";
    return "🌐";
  }

  function formatTime(value) {
    if (!value) return "Just now";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Recently";
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  function reactionTotal(post) {
    const explicit = count(post.reactions_count || post.reaction_count);
    if (explicit) return explicit;
    return Object.values(post.reaction_counts || {}).reduce((sum, value) => sum + count(value), 0);
  }

  function reactionEmojis(post) {
    const map = { like: "👍", love: "❤️", fire: "🔥", laugh: "😂", wow: "😮", sad: "😢" };
    const counts = post.reaction_counts || {};
    const active = Object.keys(counts).filter(type => count(counts[type]) > 0).sort((a, b) => count(counts[b]) - count(counts[a]));
    const emojis = (active.length ? active : ["like", "love", "fire"]).slice(0, 3).map(type => map[type] || "👍");
    return emojis.join(" ");
  }

  function updateSummary(postId, key, value) {
    document.querySelectorAll(`[data-summary-${key}="${postId}"]`).forEach(node => {
      node.textContent = compactNumber(value);
    });
  }

  function actionButton(icon, label, attrs = {}) {
    const button = element("button", "post-action-button", "");
    button.type = "button";
    Object.entries(attrs).forEach(([key, value]) => {
      button.dataset[key] = value;
    });
    const iconNode = element("span", "post-action-icon", icon);
    iconNode.setAttribute("aria-hidden", "true");
    button.append(iconNode, document.createTextNode(" "), element("span", "", label));
    return button;
  }

  function renderCreatorHeader(card, post, author, authorName, label) {
    const header = element("header", "post-card-header");
    const identity = element("div", "post-card-identity");
    const nameRow = element("div", "post-card-name-row");
    const profile = author?.public_player_id ? `/pulse/profile/${encodeURIComponent(author.public_player_id)}` : "/pulse/profile";
    const nameLink = element("button", "post-card-name post-card-creator-trigger", authorName);
    nameLink.type = "button";
    nameLink.dataset.creatorDrawerOpen = post.id;
    nameRow.appendChild(nameLink);
    if (author?.premium_mark || author?.verified || author?.premium_verified) {
      const badge = element("span", "post-verified", "✓");
      badge.title = "Verified creator";
      nameRow.appendChild(badge);
    }
    identity.appendChild(nameRow);
    identity.appendChild(element("div", "post-card-creator-line", label || "PulseSoc member"));
    const meta = element("div", "post-card-meta");
    const link = element("a", "", formatTime(post.created_at));
    link.href = postUrl(post);
    const visibility = String(post.visibility || "public").replace(/_/g, " ");
    meta.append(link, element("span", "", "•"), element("span", "post-visibility-icon", `${visibilityIcon(post)} ${visibility}`));
    identity.appendChild(meta);

    const controls = element("div", "post-card-controls");
    if (!post.can_delete && author?.public_player_id && !post.viewer_follows_author) {
      const follow = element("button", "post-card-follow", "Follow");
      follow.type = "button";
      follow.dataset.followPublic = author.public_player_id;
      controls.appendChild(follow);
    }
    const menuButton = element("button", "post-card-menu-button post-menu-btn", "•••");
    menuButton.type = "button";
    menuButton.dataset.postMenu = post.id;
    menuButton.setAttribute("aria-label", "Post actions");
    controls.appendChild(menuButton);

    const main = element("div", "post-card-creator");
    const avatarButton = element("button", "post-card-avatar-trigger", "");
    avatarButton.type = "button";
    avatarButton.dataset.creatorDrawerOpen = post.id;
    avatarButton.appendChild(avatarNode(author, authorName));
    main.append(avatarButton, identity);
    header.append(main, controls);
    card.appendChild(header);
  }

  function renderCreatorDrawer(card, post, author, authorName, label) {
    const drawer = element("section", "post-creator-drawer");
    drawer.dataset.creatorDrawer = post.id;
    drawer.setAttribute("aria-hidden", "true");
    const publicId = author?.public_player_id ? `@${author.public_player_id}` : "";
    const bio = author?.bio || author?.about || "No creator bio yet.";
    drawer.append(
      avatarNode(author, authorName),
      element("div", "post-creator-drawer-copy", "")
    );
    const copy = drawer.querySelector(".post-creator-drawer-copy");
    copy.append(
      element("strong", "", authorName),
      element("small", "", [label || "PulseSoc member", publicId].filter(Boolean).join(" · ")),
      element("p", "", bio)
    );
    const close = element("button", "post-creator-drawer-close", "Close");
    close.type = "button";
    close.dataset.creatorDrawerClose = post.id;
    drawer.appendChild(close);
    card.appendChild(drawer);
  }

  function renderMenu(card, post, author) {
    const sheet = element("div", "post-sheet post-card-menu");
    sheet.dataset.postSheet = post.id;
    const items = [
      ["Save post", { savePost: post.id }],
      ["Copy link", { copyPost: postUrl(post) }],
      ["Share", { postShare: postUrl(post) }],
      ["Not interested", { notInterested: post.id }],
      ["Report", { reportPost: post.id }],
    ];
    if (author?.public_player_id) {
      items.push(["Mute user", { muteUser: author.public_player_id }], ["Block user", { blockUser: author.public_player_id }]);
    }
    if (post.can_delete) {
      items.push(["Edit", { editPost: post.id }], ["Delete", { deletePost: post.id }]);
    }
    items.forEach(([label, attrs]) => {
      const button = element("button", "", label);
      button.type = "button";
      Object.entries(attrs).forEach(([key, value]) => {
        button.dataset[key] = value;
      });
      sheet.appendChild(button);
    });
    card.appendChild(sheet);
  }

  function renderCaption(card, post) {
    const caption = element("section", "post-caption");
    if (post.title) caption.appendChild(element("h2", "", post.title));
    if (post.body) caption.appendChild(element("p", "", post.body));
    if (post.link_url || post.url) {
      const link = element("a", "post-link-preview", post.link_title || post.link_url || post.url);
      link.href = post.link_url || post.url;
      link.rel = "noopener noreferrer";
      link.target = "_blank";
      caption.appendChild(link);
    }
    if ((post.tags || []).length) {
      const tags = element("div", "tags post-card-tags");
      post.tags.slice(0, 8).forEach(tag => {
        const tagLink = element("a", "tag", `#${tag}`);
        tagLink.href = `/pulse?topic=${encodeURIComponent(tag)}`;
        tags.appendChild(tagLink);
      });
      caption.appendChild(tags);
    }
    if (caption.childElementCount) card.appendChild(caption);
  }

  function renderMedia(post, items) {
    const wrap = element("div", "post-card-media");
    (items || []).slice(0, 4).forEach((item, index) => {
      const url = mediaUrl(item);
      if (!url) return;
      const video = isVideoMedia(item, url);
      const frame = element(video ? "a" : "button", `post-card-media-frame ${video ? "is-video" : "is-image"}`);
      if (video) {
        const detailUrl = videoDetailUrl(post, item);
        frame.href = detailUrl || "/pulse/videos";
        frame.dataset.openVideoDetail = detailUrl;
      } else {
        frame.type = "button";
      }
      frame.dataset.openMediaLightbox = "1";
      frame.dataset.mediaSrc = url;
      frame.dataset.mediaType = video ? "video" : "image";
      frame.dataset.mediaPoster = item.thumbnail_url || item.poster_url || "";
      frame.dataset.doubleTapLike = post.id;
      frame.setAttribute("aria-label", video ? "Open video" : "Open image");
      if (video) {
        const poster = item.thumbnail_url || item.poster_url || item.mux_thumbnail_url || item.preview_url || "";
        const media = document.createElement("video");
        media.src = url;
        media.preload = index === 0 ? "metadata" : "none";
        media.playsInline = true;
        media.muted = true;
        media.controls = false;
        media.loop = true;
        media.tabIndex = -1;
        media.dataset.feedVideoPreview = "1";
        if (poster) media.poster = poster;
        media.addEventListener("play", () => frame.classList.add("is-playing"));
        media.addEventListener("pause", () => frame.classList.remove("is-playing"));
        frame.appendChild(media);
        const play = element("span", "post-video-play", "▶");
        frame.appendChild(play);
      } else {
        const image = document.createElement("img");
        image.src = url;
        image.alt = item.alt_text || "PulseSoc post media";
        image.loading = "lazy";
        image.decoding = "async";
        frame.appendChild(image);
      }
      wrap.appendChild(frame);
    });
    return wrap.childElementCount ? wrap : null;
  }

  function renderEngagement(card, post) {
    const row = element("div", "post-engagement-summary");
    row.setAttribute(
      "aria-label",
      `${reactionEmojis(post)} ${compactNumber(reactionTotal(post))}, ${compactNumber(post.comments_count || post.comment_count)} comments, ${compactNumber(post.repost_count || post.reposts_count)} reposts, ${compactNumber(post.share_count || post.shares_count)} shares, ${compactNumber(post.view_count)} views`
    );
    const reactions = element("span", "post-reaction-emojis", "");
    reactions.append(
      element("span", "post-reaction-icons", reactionEmojis(post)),
      document.createTextNode(" "),
      element("span", "post-reaction-total", compactNumber(reactionTotal(post)))
    );
    row.appendChild(reactions);
    const metrics = [
      ["comments", post.comments_count || post.comment_count, "Comment", "Comments"],
      ["reposts", post.repost_count || post.reposts_count, "Repost", "Reposts"],
      ["shares", post.share_count || post.shares_count, "Share", "Shares"],
      ["views", post.view_count, "View", "Views"],
    ];
    metrics.forEach(([key, value, singular, plural]) => {
      const item = element("span", "post-summary-metric", "");
      const number = element("span", "post-summary-number", compactNumber(value));
      number.dataset[`summary${key[0].toUpperCase()}${key.slice(1)}`] = post.id;
      if (key === "views") number.dataset.postViewCount = post.id;
      item.append(number, document.createTextNode(` ${count(value) === 1 ? singular : plural}`));
      row.appendChild(document.createTextNode("    "));
      row.appendChild(item);
    });
    card.appendChild(row);
  }

  function renderActions(card, post) {
    const row = element("div", "post-action-row");
    const like = actionButton("👍", "Like", { postLike: post.id });
    like.setAttribute("aria-pressed", post.viewer_reaction ? "true" : "false");
    if (post.viewer_reaction) like.classList.add("active");
    row.append(
      like,
      actionButton("💬", "Comment", { postComment: post.id }),
      actionButton("🔁", "Repost", { postRepost: post.id }),
      actionButton("↗", "Share", { postShare: postUrl(post) }),
      actionButton("🔖", "Save", { savePost: post.id })
    );
    card.appendChild(row);
  }

  function renderComposer(card, post) {
    const composer = element("section", "post-comment-composer");
    composer.appendChild(currentViewerAvatar(post));
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Write a comment...";
    input.dataset.commentInput = post.id;
    composer.appendChild(input);
    composer.appendChild(actionButton("📷", "", { commentMedia: post.id }));
    composer.appendChild(actionButton("☺", "", { commentEmoji: post.id }));
    composer.appendChild(actionButton("➤", "", { commentSend: post.id }));
    card.appendChild(composer);
  }

  function renderPost(post) {
    const author = post.author || {};
    const authorName = author.display_name || post.author_public_name || "PulseSoc creator";
    const label = author.primary_label || author.rank || (author.badges || ["Member"])[0] || "PulseSoc member";
    const mediaItems = post.media || [];
    const kind = mediaTypeLabel(post, mediaItems);
    const card = element("article", `card post post-card-modern post-card-${kind}`);
    card.dataset.postId = post.id;
    card.dataset.postType = post.post_type || kind;
    card.dataset.mediaKind = kind;

    const media = renderMedia(post, mediaItems);
    if (kind === "video" && media) {
      card.appendChild(media);
      renderEngagement(card, post);
      renderActions(card, post);
      renderCreatorHeader(card, post, author, authorName, label);
      renderCreatorDrawer(card, post, author, authorName, label);
      renderMenu(card, post, author);
      renderCaption(card, post);
    } else {
      renderCreatorHeader(card, post, author, authorName, label);
      renderCreatorDrawer(card, post, author, authorName, label);
      renderMenu(card, post, author);
      renderCaption(card, post);
      if (post.live?.live_url) {
        const live = element("a", "button primary", "Join live");
        live.href = post.live.live_url;
        card.appendChild(live);
      }
      if (media) card.appendChild(media);
      renderEngagement(card, post);
      renderActions(card, post);
    }
    renderComposer(card, post);
    return card;
  }

  function observePost(card) {
    const postId = card?.dataset?.postId;
    observeFeedVideos(card);
    if (!postId || viewedPosts.has(postId)) return;
    if (typeof IntersectionObserver === "undefined") {
      recordView(card);
      return;
    }
    if (!viewObserver) {
      viewObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (entry.isIntersecting && entry.intersectionRatio >= 0.55) {
            viewObserver.unobserve(entry.target);
            recordView(entry.target);
          }
        });
      }, { threshold: [0.55], rootMargin: "160px 0px" });
    }
    viewObserver.observe(card);
  }

  function observeFeedVideos(scope) {
    const videos = [...(scope?.querySelectorAll?.("[data-feed-video-preview]") || [])];
    if (!videos.length) return;
    if (typeof IntersectionObserver === "undefined") {
      videos.slice(0, 1).forEach(video => video.play().catch(() => {}));
      return;
    }
    if (!feedVideoObserver) {
      feedVideoObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          const video = entry.target;
          const frame = video.closest(".post-card-media-frame");
          if (entry.isIntersecting && entry.intersectionRatio >= 0.62) {
            video.preload = "metadata";
            video.muted = true;
            video.play().then(() => frame?.classList.add("is-playing")).catch(() => frame?.classList.remove("is-playing"));
          } else {
            video.pause();
            frame?.classList.remove("is-playing");
            if (!entry.isIntersecting) video.preload = "none";
          }
        });
      }, { threshold: [0, 0.62], rootMargin: "320px 0px" });
    }
    videos.forEach(video => feedVideoObserver.observe(video));
  }

  async function recordView(card) {
    const postId = card?.dataset?.postId;
    if (!postId || viewedPosts.has(postId)) return;
    viewedPosts.add(postId);
    try {
      const data = await api(`/api/pulse/posts/${postId}/view`, {
        method: "POST",
        body: JSON.stringify({ dwell_ms: 1200, source: "feed_card" }),
        timeoutMs: 5000,
      });
      const views = data.view_count ?? data.views ?? data.count;
      if (views !== undefined) {
        updateSummary(postId, "views", views);
      } else {
        const current = Number(document.querySelector(`[data-summary-views="${postId}"]`)?.textContent.replace(/[^\d.]/g, "") || 0);
        updateSummary(postId, "views", current + 1);
      }
    } catch (_) {
      viewedPosts.delete(postId);
    }
  }

  function renderFallback(message) {
    feed.textContent = "";
    const card = element("section", "card");
    card.dataset.pulseFeedFallback = "1";
    card.appendChild(element("p", "muted", message));
    const retry = element("button", "button primary", "Retry feed");
    retry.type = "button";
    retry.dataset.retryPulseFeed = "1";
    card.appendChild(retry);
    feed.appendChild(card);
  }

  async function load(reset) {
    if (state.loading) return;
    state.loading = true;
    if (reset) {
      state.offset = 0;
      feed.textContent = "";
      viewedPosts.clear();
    }
    tabs.querySelectorAll("[data-feed]").forEach(button => button.classList.toggle("active", button.dataset.feed === state.feed));
    try {
      const data = await api(`/api/pulse/feed?tab=${encodeURIComponent(state.feed)}&topic=${encodeURIComponent(state.topic)}&profile=${encodeURIComponent(state.profile)}&offset=${state.offset}&limit=12`);
      const posts = data.posts || [];
      if (!posts.length && state.offset === 0) {
        const empty = element("section", "card");
        empty.appendChild(element("p", "muted", "No posts yet. Create the first PulseSoc."));
        const create = element("a", "button primary", "Create PulseSoc");
        create.href = "/pulse/create";
        empty.appendChild(create);
        feed.appendChild(empty);
      } else {
        posts.forEach(post => {
          const card = renderPost(post);
          feed.appendChild(card);
          observePost(card);
        });
      }
      state.offset = data.next_offset ?? (state.offset + posts.length);
      if (loadMore) loadMore.style.display = data.has_more ? "inline-flex" : "none";
      const intel = document.getElementById("intel");
      if (intel) intel.textContent = data.intelligence?.suggested_action || "PulseSoc feed connected.";
    } catch (error) {
      renderFallback(error.message || "PulseSoc could not load the feed.");
      toast(error.message);
    } finally {
      state.loading = false;
    }
  }

  async function reactToPost(postId, button) {
    if (!postId || button?.disabled) return;
    const wasActive = button?.classList.contains("active");
    if (button) {
      button.disabled = true;
      button.classList.toggle("active", !wasActive);
      button.setAttribute("aria-pressed", wasActive ? "false" : "true");
    }
    try {
      const data = await api(`/api/pulse/posts/${postId}/react`, {
        method: "POST",
        body: JSON.stringify({ reaction_type: "like" }),
      });
      const total = Object.values(data.reaction_counts || {}).reduce((sum, value) => sum + count(value), 0);
      document.querySelectorAll(`[data-post-id="${postId}"] .post-reaction-emojis`).forEach(node => {
        node.textContent = `${reactionEmojis({ reaction_counts: data.reaction_counts })} ${compactNumber(total)}`;
      });
      document.querySelectorAll(`[data-post-like="${postId}"]`).forEach(node => {
        node.classList.toggle("active", !data.removed);
        node.setAttribute("aria-pressed", data.removed ? "false" : "true");
      });
    } catch (error) {
      if (button) {
        button.classList.toggle("active", wasActive);
        button.setAttribute("aria-pressed", wasActive ? "true" : "false");
      }
      toast(error.message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function sendComment(postId) {
    const input = document.querySelector(`[data-comment-input="${postId}"]`);
    const body = input?.value || "";
    if (!body.trim()) {
      input?.focus();
      return;
    }
    try {
      await api(`/api/pulse/posts/${postId}/comments`, {
        method: "POST",
        body: JSON.stringify({ body }),
      });
      input.value = "";
      const current = Number(document.querySelector(`[data-summary-comments="${postId}"]`)?.textContent.replace(/[^\d.]/g, "") || 0);
      updateSummary(postId, "comments", current + 1);
      toast("Comment posted.");
    } catch (error) {
      toast(error.message);
    }
  }

  function openLightbox(trigger) {
    const src = trigger?.dataset?.mediaSrc || "";
    if (!src) return;
    const lightbox = document.getElementById("pulseMediaLightbox");
    const stage = lightbox?.querySelector("[data-lightbox-stage]");
    if (!lightbox || !stage) {
      window.open(src, "_blank", "noopener");
      return;
    }
    const type = trigger.dataset.mediaType;
    stage.innerHTML = "";
    if (type === "video") {
      const video = document.createElement("video");
      video.src = src;
      video.controls = true;
      video.autoplay = true;
      video.playsInline = true;
      if (trigger.dataset.mediaPoster) video.poster = trigger.dataset.mediaPoster;
      stage.appendChild(video);
    } else {
      const image = document.createElement("img");
      image.src = src;
      image.alt = "PulseSoc media preview";
      stage.appendChild(image);
    }
    lightbox.classList.add("open");
    lightbox.setAttribute("aria-hidden", "false");
  }

  function closeLightbox() {
    const lightbox = document.getElementById("pulseMediaLightbox");
    const stage = lightbox?.querySelector("[data-lightbox-stage]");
    lightbox?.classList.remove("open");
    lightbox?.setAttribute("aria-hidden", "true");
    if (stage) stage.innerHTML = "";
  }

  async function shareUrl(url) {
    const absolute = new URL(url || "/pulse", location.origin).href;
    if (navigator.share) {
      await navigator.share({ title: "PulseSoc", url: absolute }).catch(() => {});
      return;
    }
    await navigator.clipboard.writeText(absolute);
    toast("Post link copied.");
  }

  tabs.addEventListener("click", event => {
    const button = event.target.closest("[data-feed]");
    if (!button) return;
    state.feed = button.dataset.feed || "for_you";
    load(true);
  });
  loadMore?.addEventListener("click", () => load(false));

  const composer = document.getElementById("pulseComposer");
  const postType = document.getElementById("postType");
  const postMedia = document.getElementById("postMedia");
  const postMediaPreview = document.getElementById("postMediaPreview");
  const composeMsg = document.getElementById("composeMsg");
  const composerAudience = document.getElementById("postAudience");
  const composerProgress = document.querySelector("#pulseComposer [data-upload-progress]");
  const composerSuggestions = document.querySelector("#pulseComposer [data-composer-ai-suggestions]");
  const publish = document.getElementById("publishBtn");
  const composerCharCounter = document.querySelector("#pulseComposer [data-composer-char-counter]");
  let composerFiles = [];
  let composerUploadItems = [];
  let composerUploadBatch = 0;
  let composerUploadSerial = 0;
  let composerPublishing = false;
  let composerMusicTrackId = "";
  let composerMusicLabel = "";
  const composerPrompts = {
    text: "What’s happening in your world?",
    poll: "Ask the PulseSoc community…",
    scam_report: "Warn the community about suspicious activity…",
    video: "Add a caption for your Reel...",
    live: "Describe your live session...",
  };
  try {
    const storedMusicTrack = sessionStorage.getItem("pulseComposerMusicTrackId");
    if (storedMusicTrack) {
      composerMusicTrackId = storedMusicTrack;
      composerMusicLabel = "Selected PulseSoc music";
      sessionStorage.removeItem("pulseComposerMusicTrackId");
    }
  } catch (_) {}

  function composerFileIsVideo(file) {
    return /^video\//i.test(file?.type || "") || /\.(mp4|mov|webm|m4v)$/i.test(file?.name || "");
  }

  function composerHasVideo() {
    return (composerUploadItems.length ? composerUploadItems.map(item => item.file) : (composerFiles.length ? composerFiles : Array.from(postMedia?.files || []))).some(composerFileIsVideo);
  }

  function formatComposerSize(file) {
    const size = Number(file?.size || 0);
    if (!size) return "Size pending";
    if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(size > 20 * 1024 * 1024 ? 0 : 1)} MB`;
    return `${Math.max(1, Math.round(size / 1024))} KB`;
  }

  function formatComposerDuration(seconds) {
    const value = Math.round(Number(seconds || 0));
    if (!Number.isFinite(value) || value <= 0) return "--:--";
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
  }

  function formatComposerSpeed(bytesPerSecond) {
    const speed = Number(bytesPerSecond || 0);
    if (!Number.isFinite(speed) || speed <= 0) return "";
    if (speed >= 1024 * 1024) return `${(speed / 1024 / 1024).toFixed(1)} MB/s`;
    return `${Math.max(1, Math.round(speed / 1024))} KB/s`;
  }

  function formatComposerEta(seconds) {
    const value = Math.ceil(Number(seconds || 0));
    if (!Number.isFinite(value) || value <= 0) return "";
    if (value >= 60) return `${Math.floor(value / 60)}m ${String(value % 60).padStart(2, "0")}s left`;
    return `${value}s left`;
  }

  function composerStageLabel(stage) {
    return {
      preparing: "Preparing",
      uploading: "Uploading",
      processing: "Processing",
      complete: "Ready to publish",
      success: "Ready to publish",
      failed: "Failed",
      idle: "Preparing",
    }[stage] || "Preparing";
  }

  function composerUploadBusy() {
    return composerUploadItems.some(item => ["preparing", "uploading", "processing", "idle"].includes(item.stage));
  }

  function composerUploadFailed() {
    return composerUploadItems.some(item => item.stage === "failed");
  }

  function composerReadyVideoSelected() {
    return composerUploadItems.some(item => item.isVideo && item.mediaId && item.stage !== "failed");
  }

  function syncComposerFiles() {
    composerFiles = composerUploadItems.map(item => item.file).filter(Boolean);
  }

  function updateComposerMusicVisibility() {
    const selectedType = postType?.value || "text";
    const show = selectedType === "video" || composerHasVideo();
    document.querySelectorAll("#pulseComposer [data-composer-music]").forEach(button => {
      button.hidden = !show;
      button.setAttribute("aria-hidden", show ? "false" : "true");
    });
  }

  function setComposerType(type) {
    const next = type || postType?.value || "text";
    if (postType) postType.value = next;
    document.querySelectorAll("#pulseComposer [data-type]").forEach(button => {
      const active = button.dataset.type === next;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    document.querySelectorAll("#pulseComposer [data-composer-row-reel]").forEach(button => {
      const active = next === "video";
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    composer?.classList.add("is-expanded");
    const bodyInput = document.getElementById("postBody");
    if (bodyInput) bodyInput.placeholder = composerPrompts[next] || composerPrompts.text;
    updateComposerMusicVisibility();
    if (composeMsg) {
      composeMsg.textContent = composerMusicLabel
        ? `Music attached: ${composerMusicLabel}`
        : next === "video"
          ? "Choose or record a video to create your Reel."
          : next === "poll"
            ? "Write the question you want the community to answer."
            : next === "scam_report"
              ? "Add the who, what, where, and why so the warning is useful."
              : "Ready to publish.";
    }
    syncComposerIntelligence();
    updateComposerPublishState();
  }

  function updateComposerCounter() {
    if (!composerCharCounter) return;
    const value = document.getElementById("postBody")?.value || "";
    composerCharCounter.textContent = `${value.length}/3000`;
  }

  function insertComposerText(text, { prefix = "", suffix = "" } = {}) {
    const bodyInput = document.getElementById("postBody");
    if (!bodyInput) return;
    const current = bodyInput.value || "";
    const start = Number(bodyInput.selectionStart ?? current.length);
    const end = Number(bodyInput.selectionEnd ?? current.length);
    const spacer = current && !/\s$/.test(current.slice(0, start)) ? " " : "";
    const next = `${current.slice(0, start)}${prefix}${spacer}${text}${suffix}${current.slice(end)}`;
    bodyInput.value = next.replace(/[ \t]+\n/g, "\n");
    const caret = Math.min(next.length, start + prefix.length + spacer.length + text.length + suffix.length);
    bodyInput.focus();
    bodyInput.setSelectionRange?.(caret, caret);
    bodyInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function syncComposerIntelligence() {
    if (!composerSuggestions || !composer) return;
    const body = (document.getElementById("postBody")?.value || "").trim();
    const type = postType?.value || "text";
    const awake = body.length > 0 || composerUploadItems.length > 0;
    composer.classList.toggle("is-ai-awake", awake);
    composerSuggestions.querySelectorAll("[data-ai-suggestion]").forEach(button => {
      const action = button.dataset.aiSuggestion || "";
      const visible = awake || action === "caption" || action === "question";
      button.hidden = !visible;
      if (action === "scam") button.hidden = !(type === "scam_report" || /scam|fraud|wallet|suspicious|warning/i.test(body));
      if (action === "question") button.hidden = !(type === "poll" || /\?$|ask|question|why|how|what/i.test(body));
      if (action === "caption") button.hidden = !(type === "video" || composerHasVideo());
    });
  }

  function clearComposerUploads() {
    composerUploadBatch += 1;
    composerUploadItems.forEach(item => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    composerUploadItems = [];
    composerFiles = [];
  }

  function composerOverallProgress() {
    if (!composerUploadItems.length) return { stage: "idle", percent: 0, message: "Ready to publish." };
    const failed = composerUploadItems.find(item => item.stage === "failed");
    if (failed) return { stage: "failed", percent: failed.percent || 0, message: failed.error || "Upload failed. Retry or remove the media." };
    const busy = composerUploadItems.find(item => ["preparing", "uploading", "processing", "idle"].includes(item.stage));
    const percent = Math.round(composerUploadItems.reduce((sum, item) => sum + Number(item.percent || 0), 0) / composerUploadItems.length);
    if (busy) return { stage: busy.stage || "uploading", percent, message: busy.message || `${composerStageLabel(busy.stage)}...` };
    return { stage: "complete", percent: 100, message: `${composerUploadItems.length} media item${composerUploadItems.length === 1 ? "" : "s"} ready to publish.` };
  }

  function renderComposerOverallProgress() {
    const progress = composerOverallProgress();
    window.PulseUploadManager?.render(composerProgress, progress);
    if (composerProgress) {
      composerProgress.setAttribute("aria-valuemin", "0");
      composerProgress.setAttribute("aria-valuemax", "100");
      composerProgress.setAttribute("aria-valuenow", String(Math.max(0, Math.min(100, Number(progress.percent || 0)))));
      const meta = composerProgress.querySelector("[data-upload-progress-meta]");
      if (meta) {
        const busy = composerUploadItems.find(item => item.speedLabel || item.etaLabel);
        meta.textContent = [busy?.speedLabel, busy?.etaLabel].filter(Boolean).join(" · ");
      }
    }
  }

  function updateComposerUploadItemDom(item) {
    const card = postMediaPreview?.querySelector(`[data-selected-media="${CSS.escape(String(item.id))}"]`);
    if (!card) return;
    card.dataset.uploadStage = item.stage || "idle";
    card.classList.toggle("is-uploading", ["preparing", "uploading", "processing", "idle"].includes(item.stage));
    card.classList.toggle("is-ready", !!item.mediaId && item.stage !== "failed");
    card.classList.toggle("is-failed", item.stage === "failed");
    const bar = card.querySelector("[data-composer-item-bar]");
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(item.percent || 0)))}%`;
    const ring = card.querySelector("[data-composer-upload-ring]");
    if (ring) ring.style.setProperty("--upload-progress", String(Math.max(0, Math.min(100, Number(item.percent || 0)))));
    const percent = card.querySelector("[data-composer-percent]");
    if (percent) percent.textContent = `${Math.max(0, Math.min(100, Math.round(Number(item.percent || 0))))}%`;
    const state = card.querySelector("[data-composer-media-state]");
    if (state) state.textContent = item.error || item.message || composerStageLabel(item.stage);
    const meta = card.querySelector("[data-composer-upload-meta]");
    if (meta) meta.textContent = [item.speedLabel, item.etaLabel].filter(Boolean).join(" · ");
    const stage = card.querySelector("[data-composer-stage]");
    if (stage) stage.textContent = composerStageLabel(item.stage);
    const retry = card.querySelector("[data-retry-composer-upload]");
    if (retry) retry.hidden = item.stage !== "failed";
    renderComposerOverallProgress();
    updateComposerPublishState();
  }

  function renderComposerPreview() {
    if (!postMediaPreview) return;
    if (!composerUploadItems.length) {
      postMediaPreview.innerHTML = "";
      updateComposerMusicVisibility();
      renderComposerOverallProgress();
      updateComposerPublishState();
      return;
    }
    postMediaPreview.innerHTML = composerUploadItems.map(item => {
      const file = item.file;
      const isVideo = item.isVideo;
      const name = file.name || "PulseSoc media";
      const media = isVideo
        ? `<div class="composer-video-frame"><video src="${esc(item.previewUrl)}" controls muted playsinline webkit-playsinline preload="metadata" aria-label="${esc(name)} preview" data-composer-preview-video="${esc(item.id)}"></video><div class="composer-video-fallback" data-composer-video-fallback hidden><strong>Preview could not load.</strong><span>Reselect or remove this video.</span></div><div class="composer-video-controls"><button type="button" data-composer-video-toggle="${esc(item.id)}">Play</button><button type="button" data-composer-video-mute="${esc(item.id)}">Muted</button><span data-composer-video-duration="${esc(item.id)}">--:--</span></div></div>`
        : `<img src="${esc(item.previewUrl)}" alt="${esc(name)}" loading="eager" decoding="async">`;
      return `<article class="pulse-selected-media ${isVideo ? "is-video" : "is-image"} ${item.stage === "failed" ? "is-failed" : item.mediaId ? "is-ready" : "is-uploading"}" data-selected-media="${esc(item.id)}" data-upload-stage="${esc(item.stage)}">${media}<footer><div class="composer-media-copy"><strong>${esc(name)}</strong><small data-composer-media-state>${esc(item.message || composerStageLabel(item.stage))}</small><span>${esc(isVideo ? "Video" : "Image")} · ${esc(file.type || "media")} · ${esc(formatComposerSize(file))}</span><div class="composer-media-specs"><span data-composer-resolution="${esc(item.id)}">${isVideo ? "Resolution pending" : "Image"}</span><span data-composer-duration-chip="${esc(item.id)}">${isVideo ? "--:--" : esc(formatComposerSize(file))}</span><span>${esc(formatComposerSize(file))}</span></div></div><div class="composer-upload-panel"><span class="composer-upload-ring" data-composer-upload-ring style="--upload-progress:${Math.max(0, Math.min(100, Number(item.percent || 0)))}"><b data-composer-percent>${Math.max(0, Math.min(100, Math.round(Number(item.percent || 0))))}%</b></span><div><span class="composer-stage-chip" data-composer-stage>${esc(composerStageLabel(item.stage))}</span><div class="composer-item-progress"><span data-composer-item-bar style="width:${Math.max(0, Math.min(100, Number(item.percent || 0)))}%"></span></div><small data-composer-upload-meta>${esc([item.speedLabel, item.etaLabel].filter(Boolean).join(" · "))}</small></div></div><div class="composer-preview-actions"><button type="button" data-retry-composer-upload="${esc(item.id)}" ${item.stage === "failed" ? "" : "hidden"}>Retry</button><button type="button" data-open-composer-picker="${esc(item.isVideo ? "video" : "")}">Replace</button><button type="button" data-remove-composer-media="${esc(item.id)}">Remove</button></div></footer></article>`;
    }).join("");
    updateComposerMusicVisibility();
    syncComposerIntelligence();
    hydrateComposerPreview();
    renderComposerOverallProgress();
    updateComposerPublishState();
  }

  function hydrateComposerPreview() {
    postMediaPreview?.querySelectorAll("[data-composer-preview-video]").forEach(video => {
      if (video.dataset.composerPreviewBound === "1") return;
      video.dataset.composerPreviewBound = "1";
      video.addEventListener("loadedmetadata", () => {
        const duration = postMediaPreview.querySelector(`[data-composer-video-duration="${CSS.escape(video.dataset.composerPreviewVideo || "")}"]`);
        const label = formatComposerDuration(video.duration);
        if (duration) duration.textContent = label;
        const chip = postMediaPreview.querySelector(`[data-composer-duration-chip="${CSS.escape(video.dataset.composerPreviewVideo || "")}"]`);
        if (chip) chip.textContent = label;
        const resolution = postMediaPreview.querySelector(`[data-composer-resolution="${CSS.escape(video.dataset.composerPreviewVideo || "")}"]`);
        if (resolution && video.videoWidth && video.videoHeight) resolution.textContent = `${video.videoHeight}p`;
      }, { once: true });
      video.addEventListener("play", () => {
        const button = postMediaPreview.querySelector(`[data-composer-video-toggle="${CSS.escape(video.dataset.composerPreviewVideo || "")}"]`);
        if (button) button.textContent = "Pause";
      });
      video.addEventListener("pause", () => {
        const button = postMediaPreview.querySelector(`[data-composer-video-toggle="${CSS.escape(video.dataset.composerPreviewVideo || "")}"]`);
        if (button) button.textContent = "Play";
      });
      video.addEventListener("volumechange", () => {
        const button = postMediaPreview.querySelector(`[data-composer-video-mute="${CSS.escape(video.dataset.composerPreviewVideo || "")}"]`);
        if (button) button.textContent = video.muted ? "Muted" : "Sound";
      });
      video.addEventListener("error", () => {
        const card = video.closest("[data-selected-media]");
        card?.classList.add("is-preview-failed");
        const fallback = card?.querySelector("[data-composer-video-fallback]");
        if (fallback) fallback.hidden = false;
      });
    });
  }

  function updateComposerPublishState() {
    if (!publish) return;
    if (composerPublishing) {
      publish.disabled = true;
      publish.textContent = "Publishing...";
      return;
    }
    const selectedType = postType?.value || "text";
    const blocked = composerUploadBusy() || composerUploadFailed() || (selectedType === "video" && !composerReadyVideoSelected());
    const bodyReady = !!(document.getElementById("postBody")?.value || "").trim();
    const mediaReady = composerUploadItems.some(item => item.mediaId && item.stage !== "failed");
    publish.disabled = blocked;
    publish.textContent = composerUploadBusy()
      ? "Uploading..."
      : composerUploadFailed()
        ? "Fix Upload"
        : selectedType === "video" && !composerReadyVideoSelected()
          ? "Add Video"
          : bodyReady || mediaReady
            ? "Ready to Publish"
            : "Publish Signal";
  }

  async function startComposerUpload(item, batch) {
    if (!item?.file || batch !== composerUploadBatch) return;
    item.stage = "preparing";
    item.percent = Math.max(1, item.percent || 1);
    item.message = item.isVideo ? "Preparing video upload..." : "Preparing media upload...";
    item.startedAt = Date.now();
    item.speedLabel = "";
    item.etaLabel = "";
    updateComposerUploadItemDom(item);
    const formData = new FormData();
    formData.append("file", item.file);
    formData.append("context_type", "pulse");
    formData.append("context_id", "draft");
    try {
      const uploaded = window.PulseUploadManager
        ? await window.PulseUploadManager.upload({
          url: "/api/pulse/media/upload",
          formData,
          file: item.file,
          progressTarget: composerProgress,
          lockKey: `pulse-composer-upload-${item.id}-${item.file.name || "media"}`,
          onProgress: step => {
            if (batch !== composerUploadBatch) return;
            item.stage = step.stage || item.stage;
            item.percent = Number(step.percent ?? item.percent ?? 0);
            item.message = step.message || item.message;
            if (step.loaded && step.total && item.startedAt) {
              const elapsed = Math.max(.1, (Date.now() - item.startedAt) / 1000);
              const speed = Number(step.loaded || 0) / elapsed;
              item.speedLabel = formatComposerSpeed(speed);
              item.etaLabel = speed > 0 ? formatComposerEta((Number(step.total || 0) - Number(step.loaded || 0)) / speed) : "";
            }
            updateComposerUploadItemDom(item);
          },
        })
        : await api("/api/pulse/media/upload", { method: "POST", body: formData, timeoutMs: item.isVideo ? 120000 : 60000 });
      if (batch !== composerUploadBatch) return;
      const mediaId = uploaded?.media?.id || uploaded?.media_id || uploaded?.id;
      if (!mediaId) throw new Error("Upload completed but media did not attach. Please retry.");
      item.mediaId = mediaId;
      item.stage = "complete";
      item.percent = 100;
      item.error = "";
      item.message = item.isVideo ? "Upload complete. Video is ready to publish." : "Upload complete. Ready to publish.";
      item.speedLabel = "";
      item.etaLabel = "";
      updateComposerUploadItemDom(item);
    } catch (error) {
      if (batch !== composerUploadBatch) return;
      item.stage = "failed";
      item.percent = 0;
      item.error = error?.message || "Upload failed. Retry or remove the media.";
      item.message = item.error;
      updateComposerUploadItemDom(item);
      toast(item.error);
    }
  }

  async function startComposerUploadQueue(batch) {
    for (const item of composerUploadItems) {
      if (batch !== composerUploadBatch) return;
      await startComposerUpload(item, batch);
    }
  }

  function prepareComposerUploads(files) {
    clearComposerUploads();
    const selected = files.slice(0, 4);
    const fileError = validateComposerFiles(selected);
    if (fileError) {
      if (postMedia) postMedia.value = "";
      toast(fileError);
      window.PulseUploadManager?.render(composerProgress, { stage: "failed", percent: 0, message: fileError });
      updateComposerPublishState();
      return;
    }
    composerUploadItems = selected.map(file => ({
      id: `media-${Date.now()}-${++composerUploadSerial}`,
      file,
      previewUrl: URL.createObjectURL(file),
      isVideo: composerFileIsVideo(file),
      stage: "preparing",
      percent: 1,
      mediaId: null,
      message: composerFileIsVideo(file) ? "Preparing video upload..." : "Preparing media upload...",
      error: "",
      speedLabel: "",
      etaLabel: "",
      startedAt: 0,
    }));
    syncComposerFiles();
    const batch = composerUploadBatch;
    renderComposerPreview();
    if (composerUploadItems.length) startComposerUploadQueue(batch);
  }

  function openComposerPicker(type = "") {
    if (!postMedia) {
      toast("Media picker is not available. Refresh and try again.");
      return;
    }
    const pickerMode = type || (postType?.value === "video" ? "video" : "");
    if (pickerMode === "video") setComposerType("video");
    const accept = pickerMode === "video"
      ? "video/mp4,video/webm,video/quicktime,.mp4,.mov,.webm"
      : pickerMode === "image"
        ? "image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp,.gif"
      : "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.webm";
    postMedia.setAttribute("accept", accept);
    postMedia.click();
  }

  postMedia?.addEventListener("change", event => {
    const selected = Array.from(event.target.files || []).slice(0, 4);
    const hasVideo = selected.some(composerFileIsVideo);
    if (selected.length) {
      setComposerType(hasVideo ? "video" : (postType?.value === "video" ? "video" : "text"));
      prepareComposerUploads(selected);
      toast(`${selected.length} media item${selected.length === 1 ? "" : "s"} selected. Upload started.`);
    } else {
      clearComposerUploads();
      renderComposerPreview();
    }
  });

  function validateComposerFiles(files) {
    const allowed = /^(image\/(jpeg|png|webp|gif)|video\/(mp4|webm|quicktime))$/i;
    for (const file of files) {
      const name = file.name || "media";
      const isImage = /^image\//i.test(file.type || "") || /\.(jpg|jpeg|png|webp|gif)$/i.test(name);
      const isVideo = composerFileIsVideo(file);
      if (!isImage && !isVideo) return `Choose a supported image or video file: ${name}`;
      if (file.type && !allowed.test(file.type) && !/\.(jpg|jpeg|png|webp|gif|mp4|mov|webm|m4v)$/i.test(name)) return `Unsupported media type for ${name}.`;
      const limit = isVideo ? 512 * 1024 * 1024 : 25 * 1024 * 1024;
      if (Number(file.size || 0) > limit) return `${name} is too large. Use ${isVideo ? "a video under 512 MB" : "an image under 25 MB"}.`;
    }
    return "";
  }

  function enhanceComposer(action = "improve") {
    const titleInput = document.getElementById("postTitle");
    const bodyInput = document.getElementById("postBody");
    if (!bodyInput || !titleInput) return;
    const type = postType?.value || "text";
    const body = (bodyInput.value || "").trim();
    if (action === "title") {
      titleInput.value = titleInput.value.trim() || (type === "scam_report" ? "Scam Alert: " : type === "poll" ? "Question for PulseSoc" : type === "video" ? "PulseSoc Reel" : "PulseSoc Update");
    } else if (action === "rewrite") {
      bodyInput.value = body ? body.charAt(0).toUpperCase() + body.slice(1) : (composerPrompts[type] || composerPrompts.text);
    } else if (action === "clarity") {
      bodyInput.value = body ? body.replace(/\s+/g, " ").trim() : (type === "poll" ? "What specific answer would help you most?" : "Add the key point, context, and what you want people to do next.");
    } else if (action === "hashtags") {
      const tags = type === "scam_report" ? "#ScamAlert #CryptoSafety #PulseSoc" : type === "poll" ? "#Question #PulseSoc" : type === "video" ? "#Reel #PulseSoc #Creator" : "#PulseSoc";
      bodyInput.value = body ? `${body}\n\n${tags}` : tags;
    } else if (action === "engagement") {
      bodyInput.value = body ? `${body}\n\nWhat do you think?` : "What should the PulseSoc community know next?";
    } else if (action === "scam") {
      titleInput.value = titleInput.value.trim() || "Scam Alert: ";
      bodyInput.value = body || "Warning:\nWhere it happened:\nWhat they asked for:\nWhy it looks suspicious:\nHow others can stay safe:";
    } else if (action === "caption") {
      titleInput.value = titleInput.value.trim() || (type === "video" ? "PulseSoc Reel" : "PulseSoc Update");
      bodyInput.value = body || (type === "video" ? "New reel on PulseSoc.\n\n#Reel #PulseSoc" : "New PulseSoc update.\n\n#PulseSoc");
    } else if (action === "question") {
      bodyInput.value = body ? (body.endsWith("?") ? body : `${body}?`) : "What should the PulseSoc community help answer?";
    } else if (type === "scam_report") {
      titleInput.value = titleInput.value.trim() || "Scam Alert: ";
      bodyInput.value = body || "Warning:\nWhere it happened:\nWhat they asked for:\nWhy it looks suspicious:\nHow others can stay safe:";
    } else if (type === "poll") {
      bodyInput.value = body ? (body.endsWith("?") ? body : `${body}?`) : "What do you think about this?";
    } else if (type === "video") {
      titleInput.value = titleInput.value.trim() || "PulseSoc Reel";
      bodyInput.value = body || "New reel on PulseSoc.\n\n#Reel #PulseSoc";
    } else {
      bodyInput.value = body ? body.charAt(0).toUpperCase() + body.slice(1) : "What is on your mind?";
    }
    bodyInput.focus();
    syncComposerIntelligence();
    updateComposerPublishState();
  }

  function ensureComposerMusicPicker() {
    let modal = document.getElementById("pulseMusicPicker");
    if (modal) return modal;
    modal = document.createElement("section");
    modal.id = "pulseMusicPicker";
    modal.className = "reels-modal";
    modal.innerHTML = `<div class="reels-sheet"><h2>Find approved music</h2><p class="muted">Only admin-approved tracks with verified commercial and edit rights appear here.</p><form data-composer-music-search><input name="topic" placeholder="Video topic"><div class="grid two"><input name="mood" placeholder="Mood"><input name="genre" placeholder="Genre"></div><input name="length" type="number" min="5" max="600" placeholder="Length in seconds"><div class="actions"><button class="primary" type="submit">Suggest tracks</button><button type="button" data-close-composer-music>Close</button></div></form><div class="sound-list" data-composer-music-results><p class="muted">Describe the mood, genre, or topic to get safe suggestions.</p></div></div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", event => {
      if (event.target === modal || event.target.closest("[data-close-composer-music]")) modal.classList.remove("open");
      const selected = event.target.closest("[data-select-composer-track]");
      if (selected) {
        composerMusicTrackId = selected.dataset.selectComposerTrack || "";
        composerMusicLabel = selected.dataset.trackLabel || "Approved PulseSoc music";
        modal.classList.remove("open");
        setComposerType(postType?.value || "text");
        toast("Approved music attached.");
      }
      const preview = event.target.closest("[data-preview-composer-track]");
      if (preview) {
        const url = preview.dataset.previewComposerTrack || "";
        if (!url) return toast("Preview is not available for this licensed track.");
        new Audio(url).play().catch(() => toast("Preview could not play."));
      }
    });
    modal.querySelector("[data-composer-music-search]")?.addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.target;
      const box = modal.querySelector("[data-composer-music-results]");
      box.innerHTML = '<p class="muted">Checking approved catalog...</p>';
      try {
        const data = await api("/api/pulse/music/ai-suggest", { method: "POST", body: JSON.stringify({ topic: form.topic.value, mood: form.mood.value, genre: form.genre.value, length: form.length.value }) });
        box.innerHTML = (data.items || []).map(track => `<article class="sound-row"><button class="sound-preview" type="button" data-preview-composer-track="${esc(track.preview_url || "")}">▶</button><div><strong>${esc(track.title || "Approved track")}</strong><p class="muted">${esc(track.artist || "PulseSoc")} · ${esc(track.mood || "approved")} · ${Math.round(track.duration_seconds || 0)}s</p><small>${esc(track.license_type || track.license || "approved")} · proof verified</small></div><div class="actions"><button class="primary" type="button" data-select-composer-track="${esc(track.id)}" data-track-label="${esc(`${track.title || "Approved track"} · ${track.artist || "PulseSoc"}`)}">Select</button></div></article>`).join("") || '<p class="muted">No approved tracks matched. Try another mood or topic.</p>';
      } catch (error) {
        box.innerHTML = `<p class="muted">${esc(error.message || "Music search failed.")}</p>`;
      }
    });
    return modal;
  }

  document.addEventListener("click", async event => {
    const typeTrigger = event.target.closest("#pulseComposer [data-type]");
    if (typeTrigger) {
      event.preventDefault();
      setComposerType(typeTrigger.dataset.type || "text");
      return;
    }
    const mediaTrigger = event.target.closest("[data-pulse-media-trigger]");
    if (mediaTrigger) {
      event.preventDefault();
      openComposerPicker("");
      return;
    }
    const reelTrigger = event.target.closest("[data-composer-reel]");
    if (reelTrigger) {
      event.preventDefault();
      setComposerType("video");
      toast("Reel mode selected. Choose Media will open video files.");
      return;
    }
    const liveTrigger = event.target.closest("#pulseComposer [data-composer-live]");
    if (liveTrigger) {
      event.preventDefault();
      window.location.assign(liveTrigger.getAttribute("href") || "/pulse/live");
      return;
    }
    const audienceTrigger = event.target.closest("[data-composer-audience]");
    if (audienceTrigger) {
      event.preventDefault();
      const panel = document.querySelector("#pulseComposer [data-composer-audience-panel]");
      if (panel) panel.hidden = !panel.hidden;
      composerAudience?.focus();
      toast(`Audience: ${composerAudience?.selectedOptions?.[0]?.textContent || "Public"}`);
      return;
    }
    const aiSuggestion = event.target.closest("[data-ai-suggestion]");
    if (aiSuggestion) {
      event.preventDefault();
      enhanceComposer(aiSuggestion.dataset.aiSuggestion || "clarity");
      return;
    }
    const aiRail = event.target.closest("[data-composer-ai]");
    if (aiRail) {
      event.preventDefault();
      const bodyInput = document.getElementById("postBody");
      if ((bodyInput?.value || "").trim() || composerUploadItems.length) {
        composer?.classList.add("is-ai-awake");
        composerSuggestions?.querySelector("[data-ai-suggestion]:not([hidden])")?.focus();
      } else {
        bodyInput?.focus();
        toast("Start typing or select media to unlock contextual AI.");
      }
      return;
    }
    const musicTrigger = event.target.closest("[data-composer-music]");
    if (musicTrigger) {
      event.preventDefault();
      if (musicTrigger.hidden || !(postType?.value === "video" || composerHasVideo())) return toast("Add Music is available for Reel or video posts.");
      ensureComposerMusicPicker().classList.add("open");
      toast("Choose an approved track for this video.");
      return;
    }
    const replaceComposerMedia = event.target.closest("[data-open-composer-picker]");
    if (replaceComposerMedia) {
      event.preventDefault();
      openComposerPicker(replaceComposerMedia.dataset.openComposerPicker || "");
      return;
    }
    const chip = event.target.closest("[data-composer-chip],[data-composer-rail]");
    if (chip) {
      event.preventDefault();
      const chipType = chip.dataset.composerChip || chip.dataset.composerRail || "";
      const snippets = {
        topic: "#Topic",
        mention: "@",
        location: "Location: ",
        feeling: "Feeling: ",
      };
      insertComposerText(snippets[chipType] || "");
      return;
    }
    const removeComposerMedia = event.target.closest("[data-remove-composer-media]");
    if (removeComposerMedia) {
      const id = removeComposerMedia.dataset.removeComposerMedia || "";
      const item = composerUploadItems.find(candidate => candidate.id === id);
      if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
      composerUploadItems = composerUploadItems.filter(candidate => candidate.id !== id);
      syncComposerFiles();
      if (postMedia && !composerUploadItems.length) postMedia.value = "";
      renderComposerPreview();
      toast(composerUploadItems.length ? "Media removed." : "Media cleared.");
      return;
    }
    const retryComposerMedia = event.target.closest("[data-retry-composer-upload]");
    if (retryComposerMedia) {
      const item = composerUploadItems.find(candidate => candidate.id === (retryComposerMedia.dataset.retryComposerUpload || ""));
      if (item) {
        item.error = "";
        item.mediaId = null;
        startComposerUpload(item, composerUploadBatch);
      }
      return;
    }
    const videoToggle = event.target.closest("[data-composer-video-toggle]");
    if (videoToggle) {
      const video = postMediaPreview?.querySelector(`[data-composer-preview-video="${CSS.escape(videoToggle.dataset.composerVideoToggle || "")}"]`);
      if (video) {
        if (video.paused) video.play().catch(() => toast("Preview could not play."));
        else video.pause();
      }
      return;
    }
    const videoMute = event.target.closest("[data-composer-video-mute]");
    if (videoMute) {
      const video = postMediaPreview?.querySelector(`[data-composer-preview-video="${CSS.escape(videoMute.dataset.composerVideoMute || "")}"]`);
      if (video) video.muted = !video.muted;
      return;
    }
    const retry = event.target.closest("[data-retry-pulse-feed]");
    if (retry) return load(true);
    if (event.target.closest("[data-close-media-lightbox]") || event.target === document.getElementById("pulseMediaLightbox")) return closeLightbox();

    const menu = event.target.closest("[data-post-menu]");
    if (menu) {
      const id = menu.dataset.postMenu;
      document.querySelectorAll(".post-sheet.open").forEach(sheet => {
        if (sheet.dataset.postSheet !== id) sheet.classList.remove("open");
      });
      document.querySelector(`[data-post-sheet="${id}"]`)?.classList.toggle("open");
      return;
    }
    if (!event.target.closest(".post-sheet,.post-menu-btn")) {
      document.querySelectorAll(".post-sheet.open").forEach(sheet => sheet.classList.remove("open"));
    }

    const creatorOpen = event.target.closest("[data-creator-drawer-open]");
    if (creatorOpen) {
      event.preventDefault();
      const id = creatorOpen.dataset.creatorDrawerOpen || "";
      document.querySelectorAll("[data-creator-drawer]").forEach(drawer => {
        const open = drawer.dataset.creatorDrawer === id && !drawer.classList.contains("open");
        drawer.classList.toggle("open", open);
        drawer.setAttribute("aria-hidden", open ? "false" : "true");
      });
      return;
    }
    const creatorClose = event.target.closest("[data-creator-drawer-close]");
    if (creatorClose) {
      const drawer = creatorClose.closest("[data-creator-drawer]");
      drawer?.classList.remove("open");
      drawer?.setAttribute("aria-hidden", "true");
      return;
    }

    const mediaOpen = event.target.closest("[data-open-media-lightbox]");
    if (mediaOpen) {
      event.preventDefault();
      if (mediaOpen.dataset.mediaType === "video") {
        const route = mediaOpen.dataset.openVideoDetail || mediaOpen.getAttribute("href") || "";
        if (/^\/pulse\/videos\/\d+$/.test(route)) {
          window.location.assign(route);
        } else {
          toast("This video is not available yet. Open Videos to continue.");
        }
        return;
      }
      const postId = mediaOpen.dataset.doubleTapLike;
      const now = Date.now();
      if (postId && now - Number(mediaOpen.dataset.lastTap || 0) < 320) {
        mediaOpen.dataset.lastTap = "0";
        await reactToPost(postId, document.querySelector(`[data-post-like="${postId}"]`));
        return;
      }
      mediaOpen.dataset.lastTap = String(now);
      setTimeout(() => {
        if (Number(mediaOpen.dataset.lastTap || 0) !== now) return;
        if (mediaOpen.dataset.mediaType === "video") {
          const video = mediaOpen.querySelector("video");
          if (!video) return;
          if (video.paused) video.play().catch(() => {});
          else video.pause();
          mediaOpen.classList.toggle("is-playing", !video.paused);
          return;
        }
        openLightbox(mediaOpen);
      }, 340);
      return;
    }

    const like = event.target.closest("[data-post-like]");
    if (like) return reactToPost(like.dataset.postLike, like);
    const comment = event.target.closest("[data-post-comment]");
    if (comment) {
      document.querySelector(`[data-comment-input="${comment.dataset.postComment}"]`)?.focus();
      return;
    }
    const send = event.target.closest("[data-comment-send]");
    if (send) return sendComment(send.dataset.commentSend);
    const emoji = event.target.closest("[data-comment-emoji]");
    if (emoji) {
      const input = document.querySelector(`[data-comment-input="${emoji.dataset.commentEmoji}"]`);
      if (input) {
        input.value = `${input.value || ""}🔥`;
        input.focus();
      }
      return;
    }
    const commentMedia = event.target.closest("[data-comment-media]");
    if (commentMedia) return toast("Comment media upload is not enabled on feed cards yet.");

    const save = event.target.closest("[data-save-post]");
    if (save) {
      try {
        const data = await api(`/api/pulse/posts/${save.dataset.savePost}/save`, { method: "POST", body: JSON.stringify({}) });
        toast(data.message || "Saved.");
      } catch (error) {
        toast(error.message);
      }
      return;
    }
    const repost = event.target.closest("[data-post-repost]");
    if (repost) {
      try {
        const data = await api(`/api/pulse/posts/${repost.dataset.postRepost}/repost`, { method: "POST", body: JSON.stringify({}) });
        toast(data.message || "Reposted.");
      } catch (error) {
        toast(error.message);
      }
      return;
    }
    const share = event.target.closest("[data-post-share],[data-copy-post]");
    if (share) {
      try {
        await shareUrl(share.dataset.postShare || share.dataset.copyPost);
      } catch (error) {
        toast(error.message || "Share link is ready.");
      }
      return;
    }
    const follow = event.target.closest("[data-follow-public]");
    if (follow && follow.dataset.followPublic) {
      try {
        await api("/api/pulse/follow", { method: "POST", body: JSON.stringify({ public_player_id: follow.dataset.followPublic }) });
        follow.textContent = "Following";
        follow.disabled = true;
        toast("Followed.");
      } catch (error) {
        toast(error.message);
      }
      return;
    }
    const report = event.target.closest("[data-report-post]");
    if (report) {
      const reason = prompt("Why are you reporting this?") || "reported";
      try {
        await api("/api/pulse/report", { method: "POST", body: JSON.stringify({ target_type: "post", target_id: report.dataset.reportPost, reason }) });
        toast("Report sent.");
      } catch (error) {
        toast(error.message);
      }
      return;
    }
    const softAction = event.target.closest("[data-not-interested],[data-mute-user],[data-block-user],[data-edit-post]");
    if (softAction) return toast("This menu action is queued for moderation tools.");
    const del = event.target.closest("[data-delete-post]");
    if (del) {
      if (!confirm("Delete this PulseSoc post?")) return;
      try {
        await api(`/api/pulse/posts/${del.dataset.deletePost}`, { method: "DELETE" });
        document.querySelector(`[data-post-id="${del.dataset.deletePost}"]`)?.remove();
        toast("Post deleted.");
      } catch (error) {
        toast(error.message);
      }
    }
  });

  document.addEventListener("keydown", event => {
    const input = event.target.closest("[data-comment-input]");
    if (input && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendComment(input.dataset.commentInput);
    }
    if (event.key === "Escape") closeLightbox();
  });

  document.addEventListener("pointerdown", event => {
    const media = event.target.closest("[data-open-media-lightbox]");
    if (!media) return;
    longPressStart = { x: event.clientX, y: event.clientY, postId: media.dataset.doubleTapLike };
    clearTimeout(longPressTimer);
    longPressTimer = setTimeout(() => {
      if (!longPressStart?.postId) return;
      document.querySelector(`[data-post-sheet="${longPressStart.postId}"]`)?.classList.add("open");
      longPressStart = null;
    }, 620);
  }, { passive: true });

  document.addEventListener("pointermove", event => {
    if (!longPressStart) return;
    if (Math.abs(event.clientX - longPressStart.x) > 12 || Math.abs(event.clientY - longPressStart.y) > 12) {
      clearTimeout(longPressTimer);
      longPressStart = null;
    }
  }, { passive: true });

  document.addEventListener("pointerup", () => {
    clearTimeout(longPressTimer);
    longPressStart = null;
  }, { passive: true });

  publish?.addEventListener("click", async () => {
    const titleInput = document.getElementById("postTitle");
    const bodyInput = document.getElementById("postBody");
    const title = titleInput?.value || "";
    let body = bodyInput?.value || "";
    const files = composerFiles.length ? composerFiles : Array.from(document.getElementById("postMedia")?.files || []);
    const mediaIds = composerUploadItems.map(item => item.mediaId).filter(Boolean);
    if (!title.trim() && !body.trim() && !files.length) return toast("Write something or attach media before publishing.");
    const selectedType = postType?.value || "text";
    if (selectedType === "video" && !composerReadyVideoSelected()) return toast("Choose or record a video to create your Reel.");
    if (composerUploadBusy()) return toast("Wait for the upload to finish before publishing.");
    if (composerUploadFailed()) return toast("Retry or remove failed media before publishing.");
    if (selectedType === "poll" && body.trim() && !body.trim().endsWith("?")) {
      body = `${body.trim()}?`;
      if (bodyInput) bodyInput.value = body;
    }
    if (selectedType === "scam_report" && body.trim().length < 24) {
      bodyInput?.focus();
      return toast("Add useful scam warning details before publishing.");
    }
    const fileError = validateComposerFiles(files);
    if (fileError) return toast(fileError);
    if (files.length && mediaIds.length !== composerUploadItems.length) return toast("Wait for every media item to finish uploading.");
    composerPublishing = true;
    updateComposerPublishState();
    try {
      const hasVideo = files.some(composerFileIsVideo);
      window.PulseUploadManager?.render(composerProgress, { stage: hasVideo ? "processing" : "publishing", percent: hasVideo ? 90 : 96, message: hasVideo ? "Preparing video playback..." : "Publishing..." });
      await api("/api/pulse/posts", {
        method: "POST",
        body: JSON.stringify({
          title,
          body,
          post_type: hasVideo ? "video" : mediaIds.length ? "image" : selectedType,
          media_ids: mediaIds,
          visibility: composerAudience?.value || "public",
          music_track_id: composerMusicTrackId,
        }),
      });
      document.getElementById("postTitle").value = "";
      document.getElementById("postBody").value = "";
      if (postMedia) postMedia.value = "";
      clearComposerUploads();
      composerMusicTrackId = "";
      composerMusicLabel = "";
      renderComposerPreview();
      setComposerType("text");
      toast("Posted successfully.");
      publish.textContent = "Published ✓";
      await load(true);
    } catch (error) {
      toast(error.message);
    } finally {
      composerPublishing = false;
      updateComposerPublishState();
    }
  });

  document.getElementById("postTitle")?.addEventListener("input", updateComposerPublishState);
  document.getElementById("postBody")?.addEventListener("input", () => {
    updateComposerCounter();
    syncComposerIntelligence();
    updateComposerPublishState();
  });
  syncComposerIntelligence();
  updateComposerCounter();
  updateComposerPublishState();

  document.getElementById("drawerOpen")?.addEventListener("click", () => document.body.classList.add("drawer-open"));
  document.getElementById("drawerClose")?.addEventListener("click", () => document.body.classList.remove("drawer-open"));
  document.getElementById("drawerBackdrop")?.addEventListener("click", () => document.body.classList.remove("drawer-open"));

  const pulseSearchOverlay = document.getElementById("pulseSearchOverlay");
  const pulseSearchInput = document.getElementById("pulseSearchOverlayInput");
  const pulseSearchResults = document.querySelector("[data-pulse-search-results]");
  const pulseSearchMobileButton = document.getElementById("pulseMobileSearch");

  function openCorePulseSearch(query = "") {
    if (!pulseSearchOverlay) {
      window.location.assign("/pulse/search");
      return;
    }
    pulseSearchOverlay.classList.add("open");
    pulseSearchOverlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("pulse-search-open");
    if (pulseSearchInput) {
      pulseSearchInput.value = String(query || "");
      setTimeout(() => pulseSearchInput.focus(), 30);
    }
    if (pulseSearchResults && !pulseSearchResults.textContent.trim()) {
      pulseSearchResults.innerHTML = '<p class="muted">Search across public PulseSoc posts, creators, reels, groups, rooms, and comments.</p>';
    }
  }

  function closeCorePulseSearch() {
    pulseSearchOverlay?.classList.remove("open");
    pulseSearchOverlay?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("pulse-search-open");
  }

  pulseSearchMobileButton?.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    openCorePulseSearch("");
  });
  document.querySelector("[data-close-pulse-search]")?.addEventListener("click", event => {
    event.preventDefault();
    closeCorePulseSearch();
  });
  pulseSearchOverlay?.addEventListener("click", event => {
    if (event.target === pulseSearchOverlay) closeCorePulseSearch();
  });

  function bindTouchDiagnostics() {
    const enabled = new URLSearchParams(location.search).get("touch_debug") === "1" || localStorage.getItem("pulseTouchDebug") === "1";
    if (!enabled || window.__pulseTouchDiagnosticsBound) return;
    window.__pulseTouchDiagnosticsBound = true;
    const inspectPoint = (label, x, y) => {
      const target = document.elementFromPoint(x, y);
      console.warn("[PulseTouchDebug]", label, { x, y, target, tag: target?.tagName, id: target?.id, className: target?.className });
    };
    const warnLargeBlockingOverlays = () => {
      const width = window.innerWidth || document.documentElement.clientWidth || 0;
      const height = window.innerHeight || document.documentElement.clientHeight || 0;
      document.querySelectorAll("body *").forEach(node => {
        const style = getComputedStyle(node);
        if (!["fixed", "absolute"].includes(style.position) || style.pointerEvents === "none" || style.display === "none" || style.visibility === "hidden") return;
        const rect = node.getBoundingClientRect();
        const coversViewport = rect.width >= width * 0.8 && rect.height >= height * 0.8;
        const invisible = Number(style.opacity || 1) < 0.05 || node.getAttribute("aria-hidden") === "true";
        if (coversViewport && invisible) console.warn("[PulseTouchDebug] Large invisible interactive overlay", node, { rect, zIndex: style.zIndex, pointerEvents: style.pointerEvents, opacity: style.opacity });
      });
    };
    window.addEventListener("touchstart", event => {
      const touch = event.touches?.[0];
      if (touch) inspectPoint("touchstart", touch.clientX, touch.clientY);
    }, { passive: true });
    window.addEventListener("click", event => inspectPoint("click", event.clientX, event.clientY), true);
    window.addEventListener("load", warnLargeBlockingOverlays);
    window.setTimeout(warnLargeBlockingOverlays, 1200);
  }

  bindTouchDiagnostics();
  document.getElementById("pulseFab")?.addEventListener("click", event => {
    const sheet = document.getElementById("createSheet");
    if (!sheet) return;
    event.preventDefault();
    sheet.classList.toggle("open");
  });

  load(true);
})();
