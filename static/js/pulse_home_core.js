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

  function toast(message) {
    if (!toastNode) return;
    toastNode.textContent = String(message || "PulseSoc updated.");
    toastNode.classList.add("show");
    clearTimeout(toastNode._pulseTimer);
    toastNode._pulseTimer = setTimeout(() => toastNode.classList.remove("show"), 3200);
  }

  async function api(url, options = {}) {
    const controller = typeof AbortController === "undefined" ? null : new AbortController();
    const timer = controller ? setTimeout(() => controller.abort(), 10000) : null;
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

  function mediaUrl(item) {
    return item?.playback_url || item?.media_url || item?.url || item?.source_url || "";
  }

  function renderMedia(items) {
    const grid = element("div", "media-grid");
    (items || []).slice(0, 4).forEach(item => {
      const url = mediaUrl(item);
      if (!url) return;
      const type = String(item.media_type || item.type || item.mime_type || "").toLowerCase();
      const isVideo = type.includes("video") || /\.(mp4|mov|webm|m4v)(\?|$)/i.test(url);
      const media = document.createElement(isVideo ? "video" : "img");
      media.src = url;
      if (isVideo) {
        media.controls = true;
        media.preload = "metadata";
        media.playsInline = true;
        if (item.thumbnail_url || item.poster_url) media.poster = item.thumbnail_url || item.poster_url;
      } else {
        media.alt = item.alt_text || "PulseSoc post media";
        media.loading = "lazy";
      }
      grid.appendChild(media);
    });
    return grid;
  }

  function reactionButton(post, reactionType, count) {
    const labels = { like: "Like", love: "Love", fire: "Fire" };
    const button = element("button", "reaction-pill", `${labels[reactionType]} ${Number(count || 0)}`);
    button.type = "button";
    button.dataset.coreReact = reactionType;
    button.dataset.postId = post.id;
    button.setAttribute("aria-pressed", post.viewer_reaction === reactionType ? "true" : "false");
    if (post.viewer_reaction === reactionType) button.classList.add("active");
    return button;
  }

  function renderPost(post) {
    const card = element("article", "card post");
    card.dataset.postId = post.id;

    const author = element("header", "author");
    const authorMain = element("div", "author-main");
    const avatar = element("span", "avatar", (post.author?.display_name || post.author_public_name || "P").slice(0, 1).toUpperCase());
    const avatarUrl = post.author?.avatar_url || post.author_avatar;
    if (avatarUrl) {
      avatar.textContent = "";
      const image = document.createElement("img");
      image.src = avatarUrl;
      image.alt = "";
      image.loading = "lazy";
      avatar.appendChild(image);
    }
    const identity = element("div");
    identity.appendChild(element("div", "author-name", post.author?.display_name || post.author_public_name || "PulseSoc member"));
    identity.appendChild(element("small", "muted", post.author?.primary_label || post.author?.rank || "Member"));
    authorMain.append(avatar, identity);
    const time = document.createElement("a");
    time.className = "muted";
    time.href = post.permalink || `/pulse/post/${post.id}`;
    time.textContent = post.created_at ? new Date(post.created_at).toLocaleString() : "View post";
    author.append(authorMain, time);
    card.appendChild(author);

    if (post.title) card.appendChild(element("h2", "", post.title));
    if (post.body) card.appendChild(element("p", "", post.body));
    if (post.live?.live_url) {
      const live = element("a", "button primary", "Join live");
      live.href = post.live.live_url;
      card.appendChild(live);
    }
    const media = renderMedia(post.media);
    if (media.childElementCount) card.appendChild(media);

    if ((post.tags || []).length) {
      const tags = element("div", "tags");
      post.tags.slice(0, 6).forEach(tag => {
        const link = element("a", "tag", `#${tag}`);
        link.href = `/pulse?topic=${encodeURIComponent(tag)}`;
        tags.appendChild(link);
      });
      card.appendChild(tags);
    }

    const reactions = element("div", "reactions");
    ["like", "love", "fire"].forEach(type => reactions.appendChild(reactionButton(post, type, post.reaction_counts?.[type])));
    card.appendChild(reactions);

    const actions = element("div", "actions");
    const comments = element("a", "button", `Comments ${Number(post.comments_count || post.comment_count || 0)}`);
    comments.href = post.permalink || `/pulse/post/${post.id}`;
    const view = element("a", "button", "View post");
    view.href = post.permalink || `/pulse/post/${post.id}`;
    actions.append(comments, view);
    card.appendChild(actions);
    return card;
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
        posts.forEach(post => feed.appendChild(renderPost(post)));
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
    const reaction = event.target.closest("[data-core-react]");
    if (!reaction || reaction.disabled) return;
    reaction.disabled = true;
    try {
      const data = await api(`/api/pulse/posts/${reaction.dataset.postId}/react`, {
        method: "POST",
        body: JSON.stringify({ reaction_type: reaction.dataset.coreReact }),
      });
      const count = Number(data.reaction_counts?.[reaction.dataset.coreReact] || 0);
      const label = reaction.dataset.coreReact[0].toUpperCase() + reaction.dataset.coreReact.slice(1);
      reaction.textContent = `${label} ${count}`;
      reaction.classList.toggle("active", !data.removed);
      reaction.setAttribute("aria-pressed", data.removed ? "false" : "true");
    } catch (error) {
      toast(error.message);
    } finally {
      reaction.disabled = false;
    }
  });

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
