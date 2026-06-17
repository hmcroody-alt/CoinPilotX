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
        const overlay = element("span", "post-media-engagement", "");
        overlay.append(
          element("span", "post-media-reactions", `❤️ ${compactNumber(reactionTotal(post))}   🔥 ${compactNumber(post.reaction_counts?.fire || 0)}`),
          element("span", "post-media-views", `👁 ${compactNumber(post.view_count || post.views_count)}`)
        );
        frame.appendChild(overlay);
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

    const media = renderMedia(post, mediaItems);
    if (kind === "video" && media) card.appendChild(media);
    renderCreatorHeader(card, post, author, authorName, label);
    renderMenu(card, post, author);
    renderCaption(card, post);
    if (post.live?.live_url) {
      const live = element("a", "button primary", "Join live");
      live.href = post.live.live_url;
      card.appendChild(live);
    }
    if (kind !== "video" && media) card.appendChild(media);
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

  const composer = document.getElementById("pulseComposer");
  const postType = document.getElementById("postType");
  const postMedia = document.getElementById("postMedia");
  const postMediaPreview = document.getElementById("postMediaPreview");
  const composeMsg = document.getElementById("composeMsg");
  const composerAudience = document.getElementById("postAudience");
  const composerProgress = document.querySelector("#pulseComposer [data-upload-progress]");
  let composerFiles = [];
  let composerObjectUrls = [];
  let composerMusicTrackId = "";
  let composerMusicLabel = "";
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
    return (composerFiles.length ? composerFiles : Array.from(postMedia?.files || [])).some(composerFileIsVideo);
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
    updateComposerMusicVisibility();
    if (composeMsg) {
      composeMsg.textContent = composerMusicLabel
        ? `Music attached: ${composerMusicLabel}`
        : next === "video"
          ? "Reel mode selected. Choose a video to preview it before publishing."
          : next === "poll"
            ? "Write the question you want the community to answer."
            : next === "scam_report"
              ? "Add the who, what, where, and why so the warning is useful."
              : "Ready to publish.";
    }
  }

  function clearComposerObjectUrls() {
    composerObjectUrls.forEach(url => URL.revokeObjectURL(url));
    composerObjectUrls = [];
  }

  function renderComposerPreview() {
    if (!postMediaPreview) return;
    clearComposerObjectUrls();
    if (!composerFiles.length) {
      postMediaPreview.innerHTML = "";
      updateComposerMusicVisibility();
      window.PulseUploadManager?.render(composerProgress, { stage: "idle", percent: 0, message: "Ready to publish." });
      return;
    }
    postMediaPreview.innerHTML = composerFiles.map((file, index) => {
      const url = URL.createObjectURL(file);
      composerObjectUrls.push(url);
      const isVideo = composerFileIsVideo(file);
      const media = isVideo
        ? `<video src="${url}" controls playsinline webkit-playsinline preload="metadata"></video>`
        : `<img src="${url}" alt="${esc(file.name || "Selected media preview")}" loading="eager" decoding="async">`;
      return `<span class="pulse-selected-media ${isVideo ? "is-video" : "is-image"}" data-selected-media="${index}">${media}<footer><span><strong>${esc(file.name || "PulseSoc media")}</strong><small>${isVideo ? "Video" : "Image"} ready</small></span><button type="button" data-remove-composer-media="${index}">Remove</button></footer></span>`;
    }).join("");
    updateComposerMusicVisibility();
    window.PulseUploadManager?.render(composerProgress, { stage: "complete", percent: 100, message: `${composerFiles.length} media item${composerFiles.length === 1 ? "" : "s"} ready to publish.` });
  }

  function openComposerPicker(type = "") {
    if (!postMedia) {
      toast("Media picker is not available. Refresh and try again.");
      return;
    }
    const next = type || (postType?.value === "video" ? "video" : "");
    if (next) setComposerType(next);
    const accept = next === "video"
      ? "video/mp4,video/webm,video/quicktime,.mp4,.mov,.webm"
      : "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.webm";
    postMedia.setAttribute("accept", accept);
    postMedia.click();
  }

  postMedia?.addEventListener("change", event => {
    composerFiles = Array.from(event.target.files || []).slice(0, 4);
    const hasVideo = composerFiles.some(composerFileIsVideo);
    if (composerFiles.length) {
      setComposerType(hasVideo ? "video" : (postType?.value === "video" ? "video" : "text"));
      renderComposerPreview();
      toast(`${composerFiles.length} media item${composerFiles.length === 1 ? "" : "s"} attached.`);
    } else {
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
    } else if (action === "hashtags") {
      const tags = type === "scam_report" ? "#ScamAlert #CryptoSafety #PulseSoc" : type === "poll" ? "#Question #PulseSoc" : type === "video" ? "#Reel #PulseSoc #Creator" : "#PulseSoc";
      bodyInput.value = body ? `${body}\n\n${tags}` : tags;
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
    toast("Composer enhanced. Review before publishing.");
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
    const audienceTrigger = event.target.closest("[data-composer-audience]");
    if (audienceTrigger) {
      event.preventDefault();
      const panel = document.querySelector("#pulseComposer [data-composer-audience-panel]");
      if (panel) panel.hidden = !panel.hidden;
      composerAudience?.focus();
      toast(`Audience: ${composerAudience?.selectedOptions?.[0]?.textContent || "Public"}`);
      return;
    }
    const enhanceTrigger = event.target.closest("[data-composer-enhance]");
    if (enhanceTrigger) {
      event.preventDefault();
      const panel = document.querySelector("#pulseComposer [data-composer-enhance-panel]");
      if (panel) panel.hidden = !panel.hidden;
      enhanceComposer("improve");
      return;
    }
    const enhanceAction = event.target.closest("[data-enhance-action]");
    if (enhanceAction) {
      event.preventDefault();
      enhanceComposer(enhanceAction.dataset.enhanceAction || "improve");
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
    const removeComposerMedia = event.target.closest("[data-remove-composer-media]");
    if (removeComposerMedia) {
      const index = Number(removeComposerMedia.dataset.removeComposerMedia || -1);
      composerFiles = composerFiles.filter((_, itemIndex) => itemIndex !== index);
      if (postMedia && !composerFiles.length) postMedia.value = "";
      renderComposerPreview();
      toast(composerFiles.length ? "Media removed." : "Media cleared.");
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

  const publish = document.getElementById("publishBtn");
  publish?.addEventListener("click", async () => {
    const titleInput = document.getElementById("postTitle");
    const bodyInput = document.getElementById("postBody");
    const title = titleInput?.value || "";
    let body = bodyInput?.value || "";
    const files = composerFiles.length ? composerFiles : Array.from(document.getElementById("postMedia")?.files || []);
    if (!title.trim() && !body.trim() && !files.length) return toast("Write something or attach media before publishing.");
    const selectedType = postType?.value || "text";
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
    publish.disabled = true;
    try {
      const mediaIds = [];
      for (const file of files.slice(0, 4)) {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("context_type", "pulse");
        formData.append("context_id", "draft");
        const isVideo = composerFileIsVideo(file);
        toast(isVideo ? "Uploading video..." : "Uploading media...");
        const uploaded = window.PulseUploadManager
          ? await window.PulseUploadManager.upload({ url: "/api/pulse/media/upload", formData, file, button: publish, progressTarget: composerProgress, lockKey: `pulse-feed-upload-${file.name}`, onProgress: step => toast(step.message) })
          : await api("/api/pulse/media/upload", { method: "POST", body: formData, timeoutMs: isVideo ? 120000 : 60000 });
        const mediaId = uploaded.media?.id || uploaded.media_id || uploaded.id;
        if (mediaId) mediaIds.push(mediaId);
      }
      const hasVideo = files.some(composerFileIsVideo);
      if (files.length && mediaIds.length !== files.slice(0, 4).length) throw new Error("Upload completed but media did not attach. Please retry.");
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
      composerFiles = [];
      composerMusicTrackId = "";
      composerMusicLabel = "";
      renderComposerPreview();
      setComposerType("text");
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
