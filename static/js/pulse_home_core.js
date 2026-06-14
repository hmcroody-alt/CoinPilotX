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

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
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
    const nameLink = element("a", "post-card-name", authorName);
    nameLink.href = profile;
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
    main.append(avatarNode(author, authorName), identity);
    header.append(main, controls);
    card.appendChild(header);
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
      const frame = element("button", `post-card-media-frame ${video ? "is-video" : "is-image"}`);
      frame.type = "button";
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
    const label = author.primary_label || author.bio || author.rank || (author.badges || ["Member"])[0] || "PulseSoc member";
    const mediaItems = post.media || [];
    const kind = mediaTypeLabel(post, mediaItems);
    const card = element("article", `card post post-card-modern post-card-${kind}`);
    card.dataset.postId = post.id;
    card.dataset.postType = post.post_type || kind;
    card.dataset.mediaKind = kind;

    renderCreatorHeader(card, post, author, authorName, label);
    renderMenu(card, post, author);
    renderCaption(card, post);
    if (post.live?.live_url) {
      const live = element("a", "button primary", "Join live");
      live.href = post.live.live_url;
      card.appendChild(live);
    }
    const media = renderMedia(post, mediaItems);
    if (media) card.appendChild(media);
    renderEngagement(card, post);
    renderActions(card, post);
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

  document.addEventListener("click", async event => {
    const mediaTrigger = event.target.closest("[data-pulse-media-trigger]");
    const mediaType = event.target.closest("[data-type='image'], [data-type='video']");
    if (mediaTrigger || mediaType) {
      event.preventDefault();
      window.location.href = "/pulse/create";
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

    const mediaOpen = event.target.closest("[data-open-media-lightbox]");
    if (mediaOpen) {
      event.preventDefault();
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

  const publish = document.getElementById("publishBtn");
  publish?.addEventListener("click", async () => {
    const title = document.getElementById("postTitle")?.value || "";
    const body = document.getElementById("postBody")?.value || "";
    const files = document.getElementById("postMedia")?.files || [];
    if (files.length) {
      toast("For media posts, use the full Create page.");
      window.location.href = "/pulse/create";
      return;
    }
    if (!title.trim() && !body.trim()) return toast("Write something before publishing.");
    publish.disabled = true;
    try {
      await api("/api/pulse/posts", { method: "POST", body: JSON.stringify({ title, body, post_type: "text", media_ids: [] }) });
      document.getElementById("postTitle").value = "";
      document.getElementById("postBody").value = "";
      toast("Posted successfully.");
      await load(true);
    } catch (error) {
      toast(error.message);
    } finally {
      publish.disabled = false;
    }
  });

  document.getElementById("drawerOpen")?.addEventListener("click", () => document.body.classList.add("drawer-open"));
  document.getElementById("drawerClose")?.addEventListener("click", () => document.body.classList.remove("drawer-open"));
  document.getElementById("drawerBackdrop")?.addEventListener("click", () => document.body.classList.remove("drawer-open"));
  document.getElementById("pulseFab")?.addEventListener("click", event => {
    const sheet = document.getElementById("createSheet");
    if (!sheet) return;
    event.preventDefault();
    sheet.classList.toggle("open");
  });

  load(true);
})();
