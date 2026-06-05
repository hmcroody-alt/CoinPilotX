(() => {
  const API = "/api/pulse/communications/v2";
  const INITIAL_MESSAGE_LIMIT = 40;
  const state = {
    conversations: [],
    conversationCache: new Map(),
    peopleCache: new Map(),
    active: null,
    messages: [],
    members: [],
    rooms: [],
    typing: [],
    presence: [],
    groupMembers: [],
    replyTo: null,
    searchTimer: 0,
    groupSearchTimer: 0,
    filter: "all",
    hasOlder: false,
    oldestMessageId: 0,
    loadingThread: false,
    initialThreadLoaded: false,
    typingTimer: 0,
    typingSentAt: 0,
    detailsOpen: false,
    actionPending: false,
    mobileMode: "list",
    conversationSearch: "",
    attachmentQueue: [],
    attachmentSeq: 0,
    attachmentSheetOpen: false,
    maxAttachments: 8,
    uploadLimits: {
      image: 25 * 1024 * 1024,
      video: 250 * 1024 * 1024,
      audio: 25 * 1024 * 1024,
      file: 50 * 1024 * 1024,
    },
    voice: {
      stream: null,
      recorder: null,
      chunks: [],
      blob: null,
      url: "",
      startedAt: 0,
      elapsedMs: 0,
      timer: 0,
      analyserTimer: 0,
      waveform: [],
      state: "idle",
    },
  };
  const el = (sel) => document.querySelector(sel);
  const root = el(".comm-shell");
  const currentUserId = Number(root?.dataset.currentUserId || 0);
  const list = el("[data-conversations]");
  const messages = el("[data-messages]");
  const status = el("[data-status]");
  const mobileQuery = window.matchMedia("(max-width: 768px)");

  function isMobile() {
    return mobileQuery.matches;
  }

  function setMobileMode(mode) {
    state.mobileMode = mode;
    root?.setAttribute("data-mobile-mode", mode);
    document.body.dataset.mobileChatMode = mode;
  }

  function setStatus(text, kind = "info") {
    if (status) {
      status.textContent = text || "";
      status.dataset.kind = kind;
    }
    const modalStatus = document.querySelector("[data-modal]:not([hidden]) [data-modal-status]");
    if (modalStatus) {
      modalStatus.textContent = text || "";
      modalStatus.dataset.kind = kind;
    }
  }

  async function api(path, options = {}, metric = "request") {
    const started = performance.now();
    const res = await fetch(API + path, {
      credentials: "same-origin",
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text || "{}"); } catch (_) { data = { ok: false, message: "The server returned an unexpected response." }; }
    const durationMs = Math.round(performance.now() - started);
    console.info("Pulse Communications V2 timing", { metric, path, status: res.status, durationMs, serverTimingMs: data.timing_ms });
    if (!res.ok || data.ok === false) {
      const trace = data.trace_id ? ` Trace: ${data.trace_id}` : "";
      const mislabeledServerError = res.status >= 500 && /upload failed/i.test(String(data.message || ""));
      const message = mislabeledServerError
        ? `Messenger is temporarily unavailable. Refresh and try again.${trace}`
        : data.message || (data.status === "disabled" ? "Pulse Communications 2.0 is not public yet." : `This request could not be completed.${trace}`);
      throw Object.assign(new Error(message), { data, status: res.status, durationMs });
    }
    return data;
  }

  function rememberConversation(item) {
    if (!item) return null;
    const id = Number(item.conversation_id || item.id || 0);
    if (!id) return item;
    const merged = { ...(state.conversationCache.get(id) || {}), ...item, conversation_id: id };
    state.conversationCache.set(id, merged);
    return merged;
  }

  function initials(title) {
    return String(title || "P").trim().slice(0, 2).toUpperCase();
  }

  function presenceForUser(userId) {
    return (state.presence || []).find((item) => Number(item.user_id || 0) === Number(userId || 0)) || {};
  }

  function presenceForConversation(item) {
    const peerId = Number(item?.peer_user_id || item?.other_user_id || item?.target_user_id || 0);
    const directPeer = peerId ? presenceForUser(peerId) : null;
    if (directPeer?.user_id) return directPeer;
    const activePeer = (state.presence || []).find((presence) => Number(presence.user_id || 0) !== currentUserId && presence.active_now);
    return activePeer || {};
  }

  function presenceLabel(presence) {
    if (!presence || presence.presence_visible === false || presence.status === "hidden") return "Presence hidden";
    if (presence.active_now || presence.status === "online") return "Online";
    if (presence.status === "away") return "Away";
    if (presence.last_seen_at) return `Last active ${relativeTime(presence.last_seen_at)}`;
    return "Offline";
  }

  function presenceClass(presence) {
    if (presence?.active_now || presence?.status === "online") return "online";
    if (presence?.status === "away") return "away";
    return "offline";
  }

  function conversationPreview(item) {
    return item.last_message_preview || item.last_message_body || item.last_message_text || item.description || `${item.conversation_type || "Conversation"} / ${Number(item.member_count || 0)} members`;
  }

  function renderConversations() {
    if (!list) return;
    const filtered = state.conversations.filter((item) => {
      const query = state.conversationSearch.toLowerCase();
      return !query || `${item.title || ""} ${item.conversation_type || ""}`.toLowerCase().includes(query);
    });
    if (!filtered.length) {
      list.innerHTML = `<div class="empty-state">No conversations yet. Start a DM, create a group, or open a room.</div>`;
      return;
    }
    list.innerHTML = filtered.map((item) => {
      const presence = presenceForConversation(item);
      const typingNames = state.active && Number(state.active.conversation_id) === Number(item.conversation_id)
        ? (state.typing || []).map((user) => user.display_name || "Someone")
        : [];
      const preview = typingNames.length ? typingSummary(typingNames) : conversationPreview(item);
      return `
      <button class="conversation ${state.active && Number(state.active.conversation_id) === Number(item.conversation_id) ? "is-active" : ""}" type="button" data-conversation-id="${item.conversation_id}">
        <span class="avatar presence-${presenceClass(presence)}">${initials(item.title)}</span>
        <span class="conversation-main">
          <strong>${escapeHtml(item.title || "Untitled chat")}</strong>
          <small class="${typingNames.length ? "is-typing" : ""}">${escapeHtml(preview)}</small>
        </span>
        <span class="conversation-meta"><time>${escapeHtml(shortTime(item.last_message_at || item.last_activity_at || item.updated_at || item.created_at))}</time>${Number(item.unread_count || 0) ? `<span class="badge">${Number(item.unread_count)}</span>` : ""}</span>
      </button>
    `; }).join("");
  }

  function renderMessages() {
    if (!messages) return;
    const title = el("[data-thread-title]");
    const sub = el("[data-thread-subtitle]");
    const avatar = el("[data-thread-avatar]");
    if (title) title.textContent = state.active ? state.active.title : "Select a conversation";
    const threadPresence = presenceForConversation(state.active || {});
    if (sub) sub.textContent = state.active ? `${presenceLabel(threadPresence)} / ${state.active.conversation_type || "conversation"}` : "Search for someone or create a group to start chatting.";
    if (avatar) {
      avatar.textContent = state.active ? initials(state.active.title) : "P";
      avatar.className = `thread-avatar presence-${presenceClass(threadPresence)}`;
    }
    renderTypingPill();
    if (!state.active) {
      messages.innerHTML = `<div class="empty-state">Search for someone or create a group to start chatting.</div>`;
      return;
    }
    if (!state.messages.length) {
      messages.innerHTML = `<div class="empty-state">No messages here yet. Send the first one.</div>`;
      return;
    }
    const older = state.hasOlder ? `<button class="load-older" type="button" data-load-older>Load older messages</button>` : "";
    messages.innerHTML = `${older}<div class="message-stack">${state.messages.map((item) => messageHtml(item)).join("")}</div>`;
    if (!state.preserveScroll) smoothScrollToBottom();
    state.preserveScroll = false;
  }

  function smoothScrollToBottom() {
    if (!messages) return;
    messages.scrollTo({ top: messages.scrollHeight, behavior: state.messages.length > 5 ? "smooth" : "auto" });
  }

  function renderTypingPill() {
    const pill = el("[data-typing-pill]");
    if (!pill) return;
    const names = (state.typing || []).map((item) => item.display_name || "Someone").filter(Boolean);
    pill.textContent = typingSummary(names);
    pill.hidden = !names.length;
    pill.classList.toggle("is-visible", names.length > 0);
  }

  function renderMembers() {
    const target = el("[data-members]");
    const summary = el("[data-details-summary]");
    const typing = el("[data-typing-state]");
    if (summary) {
      summary.textContent = state.active
        ? `${state.active.title || "Active chat"} / ${state.active.conversation_type || "conversation"} / ${Number(state.active.member_count || state.members.length || 0)} members`
        : "Choose a chat to see members, safety, and rooms.";
    }
    if (typing) {
      const names = (state.typing || []).map((item) => item.display_name || "Someone").filter(Boolean);
      typing.textContent = typingSummary(names);
      typing.classList.toggle("is-visible", names.length > 0);
    }
    if (!target) return;
    if (!state.active) {
      target.innerHTML = `<div class="empty-state">No active conversation selected.</div>`;
      return;
    }
    if (!state.members.length) {
      target.innerHTML = `<div class="empty-state">Members load with the selected thread.</div>`;
      return;
    }
    const presenceByUser = new Map((state.presence || []).map((item) => [Number(item.user_id || 0), item]));
    target.innerHTML = state.members.map((member) => {
      const presence = presenceByUser.get(Number(member.user_id || 0)) || {};
      const label = presenceLabel(presence);
      return `
      <article class="member-row">
        <span class="avatar presence-${presenceClass(presence)}">${initials(member.display_name || member.username)}</span>
        <span><strong>${escapeHtml(member.display_name || "Pulse member")}</strong><small>${escapeHtml(member.role || "member")} / ${escapeHtml(label)}</small></span>
      </article>
    `; }).join("");
  }

  function renderRooms() {
    const target = el("[data-room-list]");
    if (!target) return;
    const rooms = (state.rooms || []).filter((item) => item.conversation_type === "room");
    if (!rooms.length) {
      target.innerHTML = `<div class="empty-state">No public rooms yet. Create one to start the space.</div>`;
      return;
    }
    target.innerHTML = rooms.map((room) => `
      <button class="room-row" type="button" data-room-id="${Number(room.conversation_id || 0)}">
        <strong>${escapeHtml(room.title || "Pulse room")}</strong>
        <small>${escapeHtml(room.privacy || "public")} / ${Number(room.member_count || 0)} members</small>
      </button>
    `).join("");
  }

  function messageHtml(item) {
    const mine = Number(item.sender_user_id || 0) === currentUserId || item.is_mine;
    const attachments = (item.attachments || []).map(attachmentHtml).join("");
    const reactions = ["heart", "fire", "check"].map((reaction) => `<button type="button" data-react="${reaction}" data-message-id="${item.id}">${reaction}</button>`).join("");
    const reactionSummary = (item.reactions || []).map((reaction) => `<span>${escapeHtml(reaction.reaction_type)} ${Number(reaction.count || 0)}</span>`).join("");
    const reply = item.reply_preview ? `<button class="reply-preview" type="button" data-jump-message="${Number(item.reply_preview.id || 0)}">Replying to ${escapeHtml(item.reply_preview.sender?.display_name || "message")}: ${escapeHtml(item.reply_preview.body || item.reply_preview.message_type || "")}</button>` : "";
    return `
      <article class="message ${mine ? "is-mine" : ""}" data-message-id="${item.id}">
        ${!mine ? `<strong>${escapeHtml(item.sender?.display_name || "Pulse member")}</strong>` : ""}
        ${reply}
        ${item.body ? `<p>${escapeHtml(item.body)}</p>` : ""}
        ${attachments ? `<div class="attachments">${attachments}</div>` : ""}
        ${reactionSummary ? `<div class="reaction-summary">${reactionSummary}</div>` : ""}
        <small class="message-meta"><time>${escapeHtml(shortTime(item.created_at))}</time>${item.is_edited ? " / Edited" : ""}${mine ? ` / ${escapeHtml(item.delivery_status || "sent")}` : ""}</small>
        <button class="message-menu-trigger" type="button" data-message-actions="${item.id}" aria-label="Message actions">...</button>
        <div class="reaction-row" data-reaction-menu="${item.id}" hidden>${reactions}<button type="button" data-reply-message="${item.id}">Reply</button>${mine ? `<button type="button" data-edit-message="${item.id}">Edit</button><button type="button" data-delete-message="${item.id}" data-delete-for="everyone">Delete</button>` : `<button type="button" data-delete-message="${item.id}" data-delete-for="self">Remove</button>`}<button type="button" data-forward-message="${item.id}">Forward</button></div>
      </article>
    `;
  }

  function attachmentHtml(item) {
    const url = item.playback_url || item.url || item.cdn_url || item.thumbnail_url || "";
    if (!url) return "";
    if (item.voice_note || (item.media_type || "").match(/audio|voice/)) return voiceAttachmentHtml(item, url);
    if (window.PulseMediaRenderer && (item.media_type || "").match(/image|gif|video|audio/)) {
      return window.PulseMediaRenderer.renderMedia({
        ...item,
        media_url: item.url || item.cdn_url || url,
        valid_url: item.cdn_url || item.url || url,
        playback_url: item.playback_url || url,
        poster_url: item.poster_url || item.thumbnail_url || "",
        media_type: item.media_type || "file",
        mime_type: item.mime_type || (String(url).includes(".m3u8") ? "application/vnd.apple.mpegurl" : ""),
        title: item.filename || "Pulse attachment",
      }, { surface: "messages-v2", className: "comm-v2-media-attachment" });
    }
    if ((item.media_type || "").match(/image|gif/)) return `<img src="${escapeAttr(url)}" alt="Attached media">`;
    if ((item.media_type || "").match(/video/)) return `<video src="${escapeAttr(url)}" controls playsinline webkit-playsinline preload="metadata" poster="${escapeAttr(item.thumbnail_url || "")}"></video>`;
    return `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">Open attachment</a>`;
  }

  function voiceAttachmentHtml(item, url) {
    const waveform = Array.isArray(item.waveform) && item.waveform.length ? item.waveform : Array.from({ length: 36 }, (_, index) => 22 + ((index * 17) % 54));
    const bars = waveform.slice(0, 56).map((level) => `<i style="--level:${Math.max(8, Math.min(100, Number(level) || 18))}"></i>`).join("");
    const duration = Number(item.duration_seconds || item.duration || 0);
    return `
      <div class="voice-message" data-voice-message>
        <div class="voice-message-controls">
          <button type="button" data-voice-play aria-label="Play voice note">Play</button>
          <progress data-voice-progress max="100" value="0"></progress>
          <select data-voice-speed aria-label="Playback speed">
            <option value="1">1x</option>
            <option value="1.5">1.5x</option>
            <option value="2">2x</option>
          </select>
        </div>
        <div class="voice-waveform" data-voice-playback-waveform>${bars}</div>
        <small><span data-voice-current>0:00</span> / <span data-voice-duration>${formatDuration(duration)}</span></small>
        <audio data-voice-audio preload="metadata" src="${escapeAttr(url)}"></audio>
      </div>
    `;
  }

  async function loadConversations({ selectFirst = !isMobile() } = {}) {
    try {
      const query = state.filter === "all" ? "" : `?type=${encodeURIComponent(state.filter)}`;
      const data = await api(`/conversations${query}`, {}, "conversations_list");
      state.conversations = (data.items || data.conversations || []).map(rememberConversation);
      if (selectFirst && !state.active && state.conversations.length) {
        state.active = state.conversations[0];
      } else if (state.active) {
        state.active = rememberConversation(state.conversations.find((c) => Number(c.conversation_id) === Number(state.active.conversation_id)) || state.active);
      }
      renderConversations();
      setStatus(state.conversations.length ? "" : "No v2 conversations yet.");
      if (state.active && !state.initialThreadLoaded && !isMobile()) {
        state.initialThreadLoaded = true;
        window.requestAnimationFrame(() => loadMessages(state.active.conversation_id).catch((err) => setStatus(err.message, "error")));
      }
    } catch (err) {
      renderConversations();
      setStatus(err.message, err.data?.status === "disabled" ? "disabled" : "error");
    }
  }

  async function loadMessages(conversationId, { beforeId = 0, appendOlder = false } = {}) {
    if (state.loadingThread) return;
    state.loadingThread = true;
    try {
      const query = `limit=${INITIAL_MESSAGE_LIMIT}${beforeId ? `&before_id=${beforeId}` : ""}`;
      const data = await api(`/conversations/${conversationId}/messages?${query}`, {}, "selected_thread_messages");
      state.active = rememberConversation(data.conversation || state.conversationCache.get(Number(conversationId)) || state.active);
      const nextMessages = data.messages || [];
      if (appendOlder) {
        state.preserveScroll = true;
        const seen = new Set(nextMessages.map((m) => Number(m.id)));
        state.messages = [...nextMessages, ...state.messages.filter((m) => !seen.has(Number(m.id)))];
      } else {
        state.messages = nextMessages;
      }
      state.hasOlder = Boolean(data.has_older);
      state.oldestMessageId = Number(data.oldest_message_id || state.messages[0]?.id || 0);
      state.members = data.members || state.members;
      state.typing = data.typing || [];
      await loadPresence(state.active?.conversation_id || conversationId);
      renderConversations();
      renderMessages();
      renderMembers();
      if (window.PulseMediaRenderer) window.PulseMediaRenderer.hydrate(messages);
      document.querySelectorAll("[data-voice-message]").forEach(bindVoiceAudio);
    } finally {
      state.loadingThread = false;
    }
  }

  async function loadOlderMessages() {
    if (!state.active || !state.oldestMessageId || !state.hasOlder) return;
    const previousHeight = messages?.scrollHeight || 0;
    await loadMessages(state.active.conversation_id, { beforeId: state.oldestMessageId, appendOlder: true });
    if (messages) messages.scrollTop = Math.max(0, messages.scrollHeight - previousHeight);
  }

  async function loadRooms() {
    try {
      const data = await api("/rooms", {}, "rooms_list");
      state.rooms = (data.items || data.conversations || []).map(rememberConversation);
      renderRooms();
    } catch (err) {
      const target = el("[data-room-list]");
      if (target) target.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
    }
  }

  async function uploadSelectedFile(file, metadata = {}) {
    if (!file) return 0;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("context_type", "pulse_comm_v2");
    fd.append("conversation_id", state.active?.conversation_id || "");
    Object.entries(metadata || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null) fd.append(key, typeof value === "string" ? value : JSON.stringify(value));
    });
    const started = performance.now();
    const res = await fetch(`${API}/attachments/upload`, { method: "POST", credentials: "same-origin", body: fd });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text || "{}"); } catch (_) { data = { ok: false, message: res.status === 403 ? "Attachment upload was blocked by site security." : "Attachment upload returned an unexpected response." }; }
    console.info("Pulse Communications V2 timing", { metric: "attachment_upload", status: res.status, durationMs: Math.round(performance.now() - started) });
    if (!res.ok || data.ok === false) throw new Error(data.message || "Attachment upload failed.");
    return Number(data.media?.id || 0);
  }

  function attachmentKind(file) {
    const type = (file?.type || "").toLowerCase();
    const name = (file?.name || "").toLowerCase();
    if (type.startsWith("image/")) return "image";
    if (type.startsWith("video/")) return "video";
    if (type.startsWith("audio/")) return "audio";
    if (/\.(jpg|jpeg|png|gif|webp|avif)$/i.test(name)) return "image";
    if (/\.(mp4|mov|m4v|webm)$/i.test(name)) return "video";
    if (/\.(mp3|m4a|wav|ogg|webm)$/i.test(name)) return "audio";
    return "file";
  }

  function validateAttachment(file) {
    if (!file) return "Choose a file first.";
    const name = file.name || "attachment";
    const kind = attachmentKind(file);
    const blocked = /\.(exe|dll|bat|cmd|com|scr|js|jar|msi|ps1|sh)$/i;
    if (blocked.test(name)) return "That file type is blocked for safety.";
    const limit = state.uploadLimits[kind] || state.uploadLimits.file;
    if (file.size > limit) return `${name} is too large. Limit: ${formatBytes(limit)}.`;
    if (state.attachmentQueue.length >= state.maxAttachments) return `You can send up to ${state.maxAttachments} attachments at once.`;
    return "";
  }

  function addAttachmentFiles(files) {
    const incoming = Array.from(files || []);
    if (!incoming.length) return;
    const next = [];
    for (const file of incoming) {
      const error = validateAttachment(file);
      if (error) {
        setStatus(error, "error");
        continue;
      }
      next.push({
        id: `att-${Date.now()}-${++state.attachmentSeq}`,
        file,
        kind: attachmentKind(file),
        status: "queued",
        progress: 0,
        mediaId: 0,
        error: "",
        previewUrl: file.type?.startsWith("image/") || file.type?.startsWith("video/") ? URL.createObjectURL(file) : "",
      });
    }
    state.attachmentQueue = [...state.attachmentQueue, ...next].slice(0, state.maxAttachments);
    renderAttachmentPreview();
    if (next.length) setStatus(`${next.length} attachment${next.length === 1 ? "" : "s"} ready.`);
  }

  function removeAttachment(id) {
    const item = state.attachmentQueue.find((entry) => entry.id === id);
    if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
    state.attachmentQueue = state.attachmentQueue.filter((entry) => entry.id !== id);
    renderAttachmentPreview();
  }

  function moveAttachment(id, direction) {
    const index = state.attachmentQueue.findIndex((entry) => entry.id === id);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= state.attachmentQueue.length) return;
    const copy = [...state.attachmentQueue];
    [copy[index], copy[nextIndex]] = [copy[nextIndex], copy[index]];
    state.attachmentQueue = copy;
    renderAttachmentPreview();
  }

  function renderAttachmentPreview() {
    const rail = el("[data-attachment-preview]");
    if (!rail) return;
    rail.hidden = !state.attachmentQueue.length;
    rail.innerHTML = state.attachmentQueue.map((item, index) => {
      const media = item.kind === "image"
        ? `<img src="${escapeAttr(item.previewUrl)}" alt="">`
        : item.kind === "video"
          ? `<video src="${escapeAttr(item.previewUrl)}" muted playsinline preload="metadata"></video>`
          : item.kind === "audio"
            ? `<div class="attachment-file-icon">♪</div>`
            : `<div class="attachment-file-icon">FILE</div>`;
      const progress = item.status === "uploading" ? `<progress max="100" value="${Number(item.progress || 0)}"></progress>` : "";
      return `
        <article class="attachment-preview-card" data-attachment-id="${escapeAttr(item.id)}" data-state="${escapeAttr(item.status)}">
          ${media}
          <div>
            <strong>${escapeHtml(item.file.name || "Attachment")}</strong>
            <small>${escapeHtml(item.error || `${item.kind} / ${formatBytes(item.file.size || 0)} / ${item.status}`)}</small>
            ${progress}
          </div>
          <div class="attachment-preview-actions">
            <button type="button" data-attachment-move="${escapeAttr(item.id)}" data-direction="-1" ${index === 0 ? "disabled" : ""}>↑</button>
            <button type="button" data-attachment-move="${escapeAttr(item.id)}" data-direction="1" ${index === state.attachmentQueue.length - 1 ? "disabled" : ""}>↓</button>
            ${item.status === "failed" ? `<button type="button" data-attachment-retry="${escapeAttr(item.id)}">Retry</button>` : ""}
            <button type="button" data-attachment-remove="${escapeAttr(item.id)}">${item.status === "uploading" ? "Cancel" : "Remove"}</button>
          </div>
        </article>
      `;
    }).join("");
  }

  async function uploadAttachmentItem(item) {
    if (!item || item.status === "uploaded") return item?.mediaId || 0;
    item.status = "uploading";
    item.progress = 10;
    item.error = "";
    renderAttachmentPreview();
    try {
      const mediaId = await uploadSelectedFile(item.file, { attachment_kind: item.kind });
      item.status = "uploaded";
      item.progress = 100;
      item.mediaId = mediaId;
      renderAttachmentPreview();
      return mediaId;
    } catch (error) {
      item.status = "failed";
      item.error = error.message || "Upload failed.";
      renderAttachmentPreview();
      throw error;
    }
  }

  async function uploadAttachmentQueue() {
    const ids = [];
    for (const item of state.attachmentQueue) {
      if (item.status === "cancelled") continue;
      ids.push(await uploadAttachmentItem(item));
    }
    return ids.filter(Boolean);
  }

  function clearAttachmentQueue() {
    state.attachmentQueue.forEach((item) => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    state.attachmentQueue = [];
    renderAttachmentPreview();
    ["[data-file]", "[data-camera-file]", "[data-photo-file]", "[data-video-file]", "[data-generic-file]"].forEach((selector) => {
      const input = el(selector);
      if (input) input.value = "";
    });
  }

  function toggleAttachmentSheet(force) {
    const sheet = el("[data-attachment-sheet]");
    state.attachmentSheetOpen = typeof force === "boolean" ? force : !state.attachmentSheetOpen;
    if (sheet) {
      sheet.hidden = !state.attachmentSheetOpen;
      sheet.classList.toggle("is-open", state.attachmentSheetOpen);
    }
  }

  function openAttachmentOption(option) {
    toggleAttachmentSheet(false);
    if (option === "voice") return startVoiceRecording();
    if (option === "camera") return el("[data-camera-file]")?.click();
    if (option === "photo") return el("[data-photo-file]")?.click();
    if (option === "video") return el("[data-video-file]")?.click();
    return el("[data-generic-file]")?.click();
  }

  function recorderMimeType() {
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    return candidates.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || "";
  }

  function formatDuration(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const mins = Math.floor(value / 60);
    const secs = Math.floor(value % 60);
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }

  function updateVoicePanel() {
    const panel = el("[data-voice-panel]");
    const stateLabel = el("[data-voice-state]");
    const timer = el("[data-voice-timer]");
    const pause = el("[data-voice-pause]");
    const resume = el("[data-voice-resume]");
    const stop = el("[data-voice-stop]");
    const preview = el("[data-voice-preview]");
    const wave = el("[data-voice-waveform]");
    const voiceState = state.voice.state;
    if (!panel) return;
    panel.hidden = voiceState === "idle";
    panel.dataset.state = voiceState;
    el("[data-voice-start]")?.classList.toggle("is-recording", voiceState === "recording");
    if (stateLabel) stateLabel.textContent = voiceState === "recording" ? "Recording..." : voiceState === "paused" ? "Paused" : voiceState === "ready" ? "Ready to send" : "Ready to record";
    if (timer) timer.textContent = formatDuration((state.voice.elapsedMs || 0) / 1000);
    if (pause) pause.hidden = voiceState !== "recording";
    if (resume) resume.hidden = voiceState !== "paused";
    if (stop) stop.hidden = !["recording", "paused"].includes(voiceState);
    if (preview) {
      preview.hidden = voiceState !== "ready";
      if (state.voice.url && preview.src !== state.voice.url) preview.src = state.voice.url;
    }
    if (wave) wave.innerHTML = (state.voice.waveform.length ? state.voice.waveform : Array.from({ length: 32 }, () => 12)).slice(-56).map((level) => `<i style="--level:${Math.max(8, Math.min(100, Number(level) || 12))}"></i>`).join("");
  }

  function startVoiceTimer() {
    window.clearTimeout(state.voice.timer);
    state.voice.startedAt = Date.now();
    const tick = () => {
      if (state.voice.state === "recording") {
        state.voice.elapsedMs += Date.now() - state.voice.startedAt;
        state.voice.startedAt = Date.now();
        updateVoicePanel();
        state.voice.timer = window.setTimeout(tick, 300);
      }
    };
    state.voice.timer = window.setTimeout(tick, 300);
  }

  async function startVoiceRecording() {
    if (!state.active) return setStatus("Choose a conversation before recording.", "error");
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      return setStatus("Voice recording is not supported in this browser.", "error");
    }
    discardVoiceRecording({ silent: true });
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const mimeType = recorderMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      state.voice.stream = stream;
      state.voice.recorder = recorder;
      state.voice.chunks = [];
      state.voice.waveform = [];
      state.voice.elapsedMs = 0;
      state.voice.state = "recording";
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) state.voice.chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        if (recorder._discarded) return;
        finalizeVoiceRecording(mimeType || recorder.mimeType || "audio/webm");
      });
      recorder.start(500);
      startVoiceAnalyser(stream);
      startVoiceTimer();
      updateVoicePanel();
      setStatus("Recording voice note...");
    } catch (error) {
      discardVoiceRecording({ silent: true });
      setStatus(error?.name === "NotAllowedError" ? "Microphone permission was denied." : "Microphone could not start. Try again.", "error");
    }
  }

  function startVoiceAnalyser(stream) {
    window.clearTimeout(state.voice.analyserTimer);
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const context = new AudioCtx();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 128;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const sample = () => {
        if (state.voice.state !== "recording") return;
        analyser.getByteTimeDomainData(data);
        const peak = data.reduce((max, value) => Math.max(max, Math.abs(value - 128)), 0);
        state.voice.waveform.push(Math.max(8, Math.min(100, Math.round((peak / 128) * 100))));
        if (state.voice.waveform.length > 80) state.voice.waveform.shift();
        updateVoicePanel();
        state.voice.analyserTimer = window.setTimeout(sample, 180);
      };
      state.voice.analyserTimer = window.setTimeout(sample, 180);
    } catch (_) {}
  }

  function pauseVoiceRecording() {
    if (state.voice.recorder?.state === "recording") {
      state.voice.elapsedMs += Date.now() - state.voice.startedAt;
      state.voice.recorder.pause();
      state.voice.state = "paused";
      updateVoicePanel();
      setStatus("Voice recording paused.");
    }
  }

  function resumeVoiceRecording() {
    if (state.voice.recorder?.state === "paused") {
      state.voice.startedAt = Date.now();
      state.voice.recorder.resume();
      state.voice.state = "recording";
      updateVoicePanel();
      setStatus("Recording voice note...");
    }
  }

  function stopVoiceRecording() {
    if (!state.voice.recorder || !["recording", "paused"].includes(state.voice.recorder.state)) return;
    if (state.voice.recorder.state === "recording") state.voice.elapsedMs += Date.now() - state.voice.startedAt;
    state.voice.recorder.stop();
    window.clearTimeout(state.voice.timer);
    window.clearTimeout(state.voice.analyserTimer);
    state.voice.stream?.getTracks?.().forEach((track) => track.stop());
  }

  function finalizeVoiceRecording(mimeType) {
    const blob = new Blob(state.voice.chunks, { type: mimeType || "audio/webm" });
    if (blob.size < 64) {
      discardVoiceRecording({ silent: true });
      return setStatus("Voice note was too short. Try again.", "error");
    }
    state.voice.blob = blob;
    state.voice.url = URL.createObjectURL(blob);
    state.voice.state = "ready";
    if (!state.voice.waveform.length) state.voice.waveform = Array.from({ length: 36 }, (_, index) => 18 + ((index * 13) % 58));
    updateVoicePanel();
    setStatus("Voice note ready to send.");
  }

  function discardVoiceRecording(options = {}) {
    window.clearTimeout(state.voice.timer);
    window.clearTimeout(state.voice.analyserTimer);
    try {
      if (state.voice.recorder && ["recording", "paused"].includes(state.voice.recorder.state)) {
        state.voice.recorder._discarded = true;
        state.voice.recorder.stop();
      }
    } catch (_) {}
    state.voice.stream?.getTracks?.().forEach((track) => track.stop());
    if (state.voice.url) URL.revokeObjectURL(state.voice.url);
    state.voice = { stream: null, recorder: null, chunks: [], blob: null, url: "", startedAt: 0, elapsedMs: 0, timer: 0, analyserTimer: 0, waveform: [], state: "idle" };
    updateVoicePanel();
    if (!options.silent) setStatus("Voice note discarded.");
  }

  async function uploadVoiceDraft() {
    if (!state.voice.blob || state.voice.state !== "ready") return 0;
    const ext = state.voice.blob.type.includes("mp4") ? "m4a" : "ogg";
    const file = new File([state.voice.blob], `pulse-voice-note-${Date.now()}.${ext}`, { type: state.voice.blob.type || "audio/webm" });
    return uploadSelectedFile(file, {
      attachment_kind: "voice_note",
      duration_seconds: Math.max(1, Math.round((state.voice.elapsedMs || 0) / 1000)),
      waveform_json: JSON.stringify(state.voice.waveform || []),
    });
  }

  function toggleVoicePlayback(container) {
    const audio = container?.querySelector("[data-voice-audio]");
    const button = container?.querySelector("[data-voice-play]");
    if (!audio || !button) return;
    document.querySelectorAll("[data-voice-audio]").forEach((item) => {
      if (item !== audio) {
        item.pause();
        const otherButton = item.closest("[data-voice-message]")?.querySelector("[data-voice-play]");
        if (otherButton) {
          otherButton.textContent = "Play";
          otherButton.dataset.playing = "false";
        }
      }
    });
    if (audio.paused) {
      audio.play().catch(() => setStatus("Tap again to play this voice note.", "error"));
      button.textContent = "Pause";
      button.dataset.playing = "true";
    } else {
      audio.pause();
      button.textContent = "Play";
      button.dataset.playing = "false";
    }
    bindVoiceAudio(container);
  }

  function setVoicePlaybackSpeed(container, speed) {
    const audio = container?.querySelector("[data-voice-audio]");
    if (audio) audio.playbackRate = Number(speed || 1);
  }

  function bindVoiceAudio(container) {
    const audio = container?.querySelector("[data-voice-audio]");
    if (!audio || audio.dataset.bound === "1") return;
    audio.dataset.bound = "1";
    const progress = container.querySelector("[data-voice-progress]");
    const current = container.querySelector("[data-voice-current]");
    const duration = container.querySelector("[data-voice-duration]");
    audio.addEventListener("loadedmetadata", () => {
      if (duration) duration.textContent = formatDuration(audio.duration || 0);
    });
    audio.addEventListener("timeupdate", () => {
      if (current) current.textContent = formatDuration(audio.currentTime || 0);
      if (progress) progress.value = audio.duration ? Math.round((audio.currentTime / audio.duration) * 100) : 0;
    });
    audio.addEventListener("ended", () => {
      const button = container.querySelector("[data-voice-play]");
      if (button) {
        button.textContent = "Play";
        button.dataset.playing = "false";
      }
    });
    progress?.addEventListener("click", (event) => {
      if (!audio.duration) return;
      const box = progress.getBoundingClientRect();
      audio.currentTime = ((event.clientX - box.left) / Math.max(1, box.width)) * audio.duration;
    });
  }

  function bind() {
    document.addEventListener("click", async (event) => {
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (!target) return;
      try {
        const conversation = target.closest("[data-conversation-id]");
        if (conversation) {
          const id = Number(conversation.dataset.conversationId || 0);
          if (state.active && Number(state.active.conversation_id) === id) return;
          state.active = rememberConversation(state.conversationCache.get(id) || state.conversations.find((item) => Number(item.conversation_id) === id));
          state.messages = [];
          state.members = [];
          state.hasOlder = false;
          renderMessages();
          renderMembers();
          await loadMessages(id);
          setMobileMode("thread");
          el("[data-message-input]")?.focus();
          return;
        }
        const filter = target.closest("[data-filter]");
        if (filter) {
          state.filter = filter.dataset.filter;
          state.active = null;
          state.messages = [];
          state.members = [];
          state.initialThreadLoaded = false;
          document.querySelectorAll("[data-filter]").forEach((btn) => btn.classList.toggle("is-active", btn === filter));
          await loadConversations();
          return;
        }
        if (target.closest("[data-open-new-chat]")) return openModal("new-chat");
        if (target.closest("[data-open-new-group]")) return openModal("new-group");
        if (target.closest("[data-open-new-room]")) return openModal("new-room");
        if (target.closest("[data-close-modal]")) return closeModals();
        if (target.closest("[data-toggle-details]")) return toggleDetails();
        if (target.closest("[data-mobile-list]")) {
          setMobileMode("list");
          return;
        }
        const messageActions = target.closest("[data-message-actions]");
        if (messageActions) {
          const menu = el(`[data-reaction-menu="${messageActions.dataset.messageActions}"]`);
          document.querySelectorAll("[data-reaction-menu]").forEach((item) => {
            if (item !== menu) item.hidden = true;
          });
          if (menu) menu.hidden = !menu.hidden;
          return;
        }
        const person = target.closest("[data-person-id]");
        if (person?.closest("[data-person-results]")) return await runAction(person, "Opening chat...", () => openDm(Number(person.dataset.personId || 0)));
        if (person?.closest("[data-group-person-results]")) return addGroupMember(Number(person.dataset.personId || 0));
        const removeMember = target.closest("[data-remove-group-member]");
        if (removeMember) return removeGroupMember(Number(removeMember.dataset.removeGroupMember || 0));
        const createGroupButton = target.closest("[data-create-group]");
        if (createGroupButton) return await runAction(createGroupButton, "Creating group...", createGroup);
        const createRoomButton = target.closest("[data-create-room]");
        if (createRoomButton) return await runAction(createRoomButton, "Creating room...", createRoom);
        if (target.closest("[data-toggle-attachments]")) return toggleAttachmentSheet();
        const attachmentOption = target.closest("[data-attachment-option]");
        if (attachmentOption) return openAttachmentOption(attachmentOption.dataset.attachmentOption || "file");
        const removeAttachmentButton = target.closest("[data-attachment-remove]");
        if (removeAttachmentButton) return removeAttachment(removeAttachmentButton.dataset.attachmentRemove);
        const retryAttachmentButton = target.closest("[data-attachment-retry]");
        if (retryAttachmentButton) {
          const item = state.attachmentQueue.find((entry) => entry.id === retryAttachmentButton.dataset.attachmentRetry);
          if (item) await uploadAttachmentItem(item);
          return;
        }
        const moveAttachmentButton = target.closest("[data-attachment-move]");
        if (moveAttachmentButton) return moveAttachment(moveAttachmentButton.dataset.attachmentMove, Number(moveAttachmentButton.dataset.direction || 0));
        if (target.closest("[data-voice-start]")) return await startVoiceRecording();
        if (target.closest("[data-voice-pause]")) return pauseVoiceRecording();
        if (target.closest("[data-voice-resume]")) return resumeVoiceRecording();
        if (target.closest("[data-voice-stop]")) return stopVoiceRecording();
        if (target.closest("[data-voice-discard]")) return discardVoiceRecording();
        const voicePlay = target.closest("[data-voice-play]");
        if (voicePlay) return toggleVoicePlayback(voicePlay.closest("[data-voice-message]"));
        const speed = target.closest("[data-voice-speed]");
        if (speed) return setVoicePlaybackSpeed(speed.closest("[data-voice-message]"), speed.value);
        if (target.closest("[data-load-older]")) return await loadOlderMessages();
        const room = target.closest("[data-room-id]");
        if (room) return await runAction(room, "Opening room...", () => openRoom(Number(room.dataset.roomId || 0)));
        const react = target.closest("[data-react]");
        if (react) return await reactToMessage(react.dataset.messageId, react.dataset.react);
        const reply = target.closest("[data-reply-message]");
        if (reply) return startReply(Number(reply.dataset.replyMessage || 0));
        const jump = target.closest("[data-jump-message]");
        if (jump) return jumpToMessage(Number(jump.dataset.jumpMessage || 0));
        const edit = target.closest("[data-edit-message]");
        if (edit) return await editMessage(Number(edit.dataset.editMessage || 0));
        const del = target.closest("[data-delete-message]");
        if (del) return await deleteMessage(Number(del.dataset.deleteMessage || 0), del.dataset.deleteFor || "self");
        const forward = target.closest("[data-forward-message]");
        if (forward) return await forwardMessage(Number(forward.dataset.forwardMessage || 0));
        if (target.closest("[data-report-last]")) return await reportLast();
        if (target.closest("[data-block-peer]")) return await blockPeer();
      } catch (err) {
        console.error("Pulse Communications V2 action failed", err);
        setStatus(err?.message || "That action could not be completed. Please try again.", "error");
      }
    });
    el("[data-composer]")?.addEventListener("submit", sendMessage);
    el("[data-message-input]")?.addEventListener("input", debounceTyping);
    el("[data-person-search]")?.addEventListener("input", () => debouncePeopleSearch("direct"));
    el("[data-group-person-search]")?.addEventListener("input", () => debouncePeopleSearch("group"));
    el("[data-conversation-search]")?.addEventListener("input", (event) => {
      state.conversationSearch = event.target.value || "";
      renderConversations();
    });
    document.addEventListener("change", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const speed = target?.closest("[data-voice-speed]");
      if (speed) setVoicePlaybackSpeed(speed.closest("[data-voice-message]"), speed.value);
      if (target?.matches("[data-file], [data-camera-file], [data-photo-file], [data-video-file], [data-generic-file]")) {
        addAttachmentFiles(target.files);
      }
    });
    document.addEventListener("keydown", async (event) => {
      if (event.key === "Escape") return closeModals();
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
      const roomModal = event.target.closest?.('[data-modal="new-room"]');
      const groupModal = event.target.closest?.('[data-modal="new-group"]');
      if (roomModal) {
        event.preventDefault();
        await runAction(el("[data-create-room]"), "Creating room...", createRoom);
      } else if (groupModal && !event.target.closest?.("[data-group-person-search]")) {
        event.preventDefault();
        await runAction(el("[data-create-group]"), "Creating group...", createGroup);
      }
    });
    document.querySelectorAll("[data-modal]").forEach((modal) => {
      modal.addEventListener("click", (event) => {
        if (event.target === modal) closeModals();
      });
    });
  }

  function debounceTyping() {
    if (!state.active) return;
    window.clearTimeout(state.typingTimer);
    state.typingTimer = window.setTimeout(sendTypingIndicator, 450);
  }

  async function sendTypingIndicator() {
    if (!state.active) return;
    const now = Date.now();
    if (now - state.typingSentAt < 2500) return;
    state.typingSentAt = now;
    try {
      await api(`/conversations/${state.active.conversation_id}/typing`, {
        method: "POST",
        body: JSON.stringify({ is_typing: true }),
      }, "typing_indicator");
    } catch (_) {}
  }

  async function sendPresenceHeartbeat() {
    try {
      await api("/presence/heartbeat", { method: "POST", body: JSON.stringify({ status: "online" }) }, "presence_heartbeat");
    } catch (_) {}
  }

  function schedulePresenceHeartbeat() {
    window.setTimeout(async () => {
      await sendPresenceHeartbeat();
      schedulePresenceHeartbeat();
    }, 30000);
  }

  async function loadPresence(conversationId) {
    if (!conversationId) return;
    try {
      const data = await api(`/conversations/${conversationId}/presence`, {}, "conversation_presence");
      state.presence = data.presence || [];
    } catch (_) {}
  }

  function openModal(name) {
    closeModals();
    const modal = el(`[data-modal="${name}"]`);
    if (!modal) {
      setStatus("That creation panel is unavailable. Refresh and try again.", "error");
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    if (isMobile()) setMobileMode("create");
    window.setTimeout(() => modal.querySelector("input")?.focus(), 30);
  }

  function closeModals() {
    document.querySelectorAll("[data-modal]").forEach((modal) => {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
    });
    if (isMobile()) setMobileMode(state.active ? "thread" : "list");
  }

  function resetCreationModal(name) {
    const modal = el(`[data-modal="${name}"]`);
    if (!modal) return;
    modal.querySelectorAll("input:not([type=file])").forEach((input) => { input.value = ""; });
    modal.querySelectorAll("[data-person-results], [data-group-person-results]").forEach((target) => { target.innerHTML = ""; });
    modal.querySelectorAll("[data-modal-status]").forEach((target) => {
      target.textContent = "";
      target.dataset.kind = "info";
    });
  }

  async function runAction(button, pendingText, action) {
    if (state.actionPending) return;
    state.actionPending = true;
    const previousText = button?.textContent || "";
    if (button) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    setStatus(pendingText);
    try {
      const result = await action();
      if (result !== false) setStatus("");
      return result;
    } catch (err) {
      console.error("Pulse Communications V2 action failed", err);
      setStatus(err?.message || "That action could not be completed. Please try again.", "error");
      return false;
    } finally {
      state.actionPending = false;
      if (button?.isConnected) {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        if (previousText && !button.textContent.trim()) button.textContent = previousText;
      }
    }
  }

  function debouncePeopleSearch(kind) {
    const key = kind === "group" ? "groupSearchTimer" : "searchTimer";
    window.clearTimeout(state[key]);
    state[key] = window.setTimeout(() => searchPeople(kind), 260);
  }

  async function searchPeople(kind) {
    const input = kind === "group" ? el("[data-group-person-search]") : el("[data-person-search]");
    const target = kind === "group" ? el("[data-group-person-results]") : el("[data-person-results]");
    const query = String(input?.value || "").trim();
    if (!target) return;
    if (query.length < 2) {
      target.innerHTML = `<div class="empty-state">Type at least two characters.</div>`;
      return;
    }
    try {
      target.innerHTML = `<div class="empty-state">Searching...</div>`;
      const data = await api(`/people/search?q=${encodeURIComponent(query)}`, {}, "people_search");
      const people = data.people || data.items || [];
      people.forEach(rememberPerson);
      target.innerHTML = people.length ? people.map((person) => personResultHtml(person)).join("") : `<div class="empty-state">No people found.</div>`;
    } catch (err) {
      target.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
    }
  }

  function personResultHtml(person) {
    const remembered = rememberPerson({
      user_id: Number(person.user_id || 0),
      display_name: person.display_name || "Pulse member",
      username: person.username || "",
      avatar_url: person.avatar_url || "",
    });
    return `
      <button class="person-result" type="button" data-person-id="${Number(remembered.user_id || 0)}">
        <span class="avatar">${initials(remembered.display_name || remembered.username)}</span>
        <span><strong>${escapeHtml(remembered.display_name || "Pulse member")}</strong><small>${escapeHtml(remembered.username ? `@${remembered.username}` : person.matched_email ? "Email match" : "Pulse member")}</small></span>
        <span aria-hidden="true">+</span>
      </button>
    `;
  }

  function rememberPerson(person) {
    const userId = Number(person?.user_id || 0);
    if (!userId) return person || {};
    const remembered = { ...(state.peopleCache.get(userId) || {}), ...person, user_id: userId };
    state.peopleCache.set(userId, remembered);
    return remembered;
  }

  async function openDm(target) {
    if (!target) {
      setStatus("Choose someone to message.", "error");
      return false;
    }
    const data = await api("/direct/open", { method: "POST", body: JSON.stringify({ target_user_id: target }) }, "create_direct");
    state.active = rememberConversation(data.conversation);
    if (!state.active?.conversation_id) throw new Error("The chat opened without a conversation ID. Please retry.");
    state.initialThreadLoaded = false;
    closeModals();
    resetCreationModal("new-chat");
    await loadConversations({ selectFirst: false });
    await loadMessages(state.active.conversation_id);
    setMobileMode("thread");
    el("[data-message-input]")?.focus();
  }

  function addGroupMember(selectedUserId) {
    const person = state.peopleCache.get(Number(selectedUserId)) || {};
    const userId = Number(person.user_id || 0);
    if (!userId) return setStatus("That member could not be selected. Search again and retry.", "error");
    if (state.groupMembers.some((item) => Number(item.user_id) === userId)) return setStatus("That person is already selected.");
    state.groupMembers = [...state.groupMembers, person];
    renderSelectedPeople();
    setStatus(`${person.display_name || "Member"} added to the group.`);
  }

  function removeGroupMember(userId) {
    state.groupMembers = state.groupMembers.filter((item) => Number(item.user_id) !== Number(userId));
    renderSelectedPeople();
  }

  function renderSelectedPeople() {
    const target = el("[data-selected-people]");
    if (!target) return;
    target.innerHTML = state.groupMembers.length ? state.groupMembers.map((person) => `
      <div class="selected-person">
        <span class="avatar">${initials(person.display_name || person.username)}</span>
        <span><strong>${escapeHtml(person.display_name || "Pulse member")}</strong><small>${escapeHtml(person.username ? `@${person.username}` : "Selected")}</small></span>
        <button type="button" data-remove-group-member="${Number(person.user_id || 0)}">Remove</button>
      </div>
    `).join("") : `<div class="empty-state">Select at least one person.</div>`;
  }

  async function createGroup() {
    const title = String(el("[data-group-title]")?.value || "").trim();
    const memberIds = state.groupMembers.map((item) => Number(item.user_id || 0)).filter(Boolean);
    if (!title) {
      setStatus("Name the group before creating it.", "error");
      el("[data-group-title]")?.focus();
      return false;
    }
    if (!memberIds.length) {
      setStatus("Add at least one person to create a group.", "error");
      el("[data-group-person-search]")?.focus();
      return false;
    }
    const data = await api("/groups", { method: "POST", body: JSON.stringify({ title, member_ids: memberIds }) }, "create_group");
    state.active = rememberConversation(data.conversation);
    if (!state.active?.conversation_id) throw new Error("The group was created without a conversation ID. Please retry.");
    state.initialThreadLoaded = false;
    state.groupMembers = [];
    renderSelectedPeople();
    closeModals();
    resetCreationModal("new-group");
    await loadConversations({ selectFirst: false });
    await loadMessages(state.active.conversation_id);
    setMobileMode("thread");
    el("[data-message-input]")?.focus();
  }

  async function createRoom() {
    const title = String(el("[data-room-title]")?.value || "").trim();
    const privacy = String(el("[data-room-privacy]")?.value || "public");
    const description = String(el("[data-room-description]")?.value || "").trim();
    if (!title) {
      setStatus("Name the room before creating it.", "error");
      el("[data-room-title]")?.focus();
      return false;
    }
    const data = await api("/rooms", { method: "POST", body: JSON.stringify({ title, privacy, description }) }, "create_room");
    state.active = rememberConversation(data.conversation);
    if (!state.active?.conversation_id) throw new Error("The room was created without a conversation ID. Please retry.");
    state.initialThreadLoaded = false;
    closeModals();
    resetCreationModal("new-room");
    await loadConversations({ selectFirst: false });
    await loadRooms();
    await loadMessages(state.active.conversation_id);
    el("[data-message-input]")?.focus();
  }

  async function openRoom(roomId) {
    if (!roomId) return;
    state.active = rememberConversation(state.conversationCache.get(roomId) || state.rooms.find((item) => Number(item.conversation_id) === roomId));
    state.messages = [];
    state.members = [];
    state.typing = [];
    renderConversations();
    renderMessages();
    renderMembers();
    await loadMessages(roomId);
  }

  async function sendMessage(event) {
    event.preventDefault();
    if (!state.active) return setStatus("Choose a conversation first.", "error");
    const input = el("[data-message-input]");
    const body = input?.value || "";
    const hasVoice = state.voice.state === "ready" && !!state.voice.blob;
    const clientMessageId = `comm-v2-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    try {
      setStatus(hasVoice ? "Uploading voice note..." : state.attachmentQueue.length ? "Uploading attachments..." : "Sending...");
      const mediaIds = state.attachmentQueue.length ? await uploadAttachmentQueue() : [];
      const voiceId = hasVoice ? await uploadVoiceDraft() : 0;
      const data = await api(`/conversations/${state.active.conversation_id}/messages`, {
        method: "POST",
        body: JSON.stringify({
          body,
          message_type: hasVoice ? "voice" : mediaIds.length ? "media" : "text",
          media_ids: [...mediaIds, ...(voiceId ? [voiceId] : [])],
          reply_to_message_id: state.replyTo?.id || 0,
          client_message_id: clientMessageId,
        }),
      }, "send_message");
      if (input) input.value = "";
      clearAttachmentQueue();
      if (hasVoice) discardVoiceRecording({ silent: true });
      state.replyTo = null;
      if (data.message) {
        state.messages = [...state.messages, data.message];
        renderMessages();
        document.querySelectorAll("[data-voice-message]").forEach(bindVoiceAudio);
      } else {
        await loadMessages(state.active.conversation_id);
      }
      setStatus("");
    } catch (err) {
      setStatus(err.message, "error");
    }
  }

  async function reactToMessage(messageId, reaction) {
    const data = await api(`/messages/${messageId}/reactions`, { method: "POST", body: JSON.stringify({ reaction }) }, "reaction");
    if (data.message) {
      state.messages = state.messages.map((item) => Number(item.id) === Number(messageId) ? data.message : item);
      renderMessages();
    }
  }

  function startReply(messageId) {
    const item = state.messages.find((message) => Number(message.id) === Number(messageId));
    if (!item) return setStatus("That message is no longer available.", "error");
    state.replyTo = { id: Number(item.id), body: item.body || item.message_type || "attachment" };
    setStatus(`Replying to: ${state.replyTo.body}`);
    el("[data-message-input]")?.focus();
  }

  function jumpToMessage(messageId) {
    const target = document.querySelector(`[data-message-id="${Number(messageId)}"]`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("is-highlighted");
      window.setTimeout(() => target.classList.remove("is-highlighted"), 1400);
    }
  }

  async function editMessage(messageId) {
    const item = state.messages.find((message) => Number(message.id) === Number(messageId));
    if (!item) return;
    const nextBody = window.prompt("Edit message", item.body || "");
    if (nextBody === null) return;
    const data = await api(`/messages/${messageId}`, { method: "PATCH", body: JSON.stringify({ body: nextBody }) }, "edit_message");
    if (data.message) {
      state.messages = state.messages.map((message) => Number(message.id) === Number(messageId) ? data.message : message);
      renderMessages();
    }
  }

  async function deleteMessage(messageId, deleteFor) {
    if (!window.confirm(deleteFor === "everyone" ? "Delete this message for everyone?" : "Remove this message from your view?")) return;
    await api(`/messages/${messageId}`, { method: "DELETE", body: JSON.stringify({ delete_for: deleteFor }) }, "delete_message");
    state.messages = state.messages.filter((message) => Number(message.id) !== Number(messageId));
    renderMessages();
    setStatus("Message deleted.");
  }

  async function forwardMessage(messageId) {
    if (!state.conversations.length) await loadConversations({ selectFirst: false });
    const choices = state.conversations.filter((item) => Number(item.conversation_id) !== Number(state.active?.conversation_id || 0));
    if (!choices.length) return setStatus("Create another conversation before forwarding.", "error");
    const names = choices.map((item, index) => `${index + 1}. ${item.title}`).join("\\n");
    const selected = Number(window.prompt(`Forward to which conversation?\\n${names}`, "1") || 0);
    const target = choices[selected - 1];
    if (!target) return;
    const data = await api(`/messages/${messageId}/forward`, { method: "POST", body: JSON.stringify({ conversation_ids: [target.conversation_id] }) }, "forward_message");
    setStatus(data.message || "Message forwarded.");
  }

  function typingSummary(names) {
    if (!names.length) return "No one is typing right now.";
    if (names.length === 1) return `${names[0]} is typing...`;
    if (names.length === 2) return `${names[0]} and ${names[1]} are typing...`;
    return `${names[0]} and ${names.length - 1} others are typing...`;
  }

  async function reportLast() {
    const last = state.messages[state.messages.length - 1];
    if (!last) return setStatus("No message is available to report.", "error");
    await api(`/messages/${last.id}/report`, { method: "POST", body: JSON.stringify({ reason: "Reported from v2 test UI" }) }, "report");
    setStatus("Report sent to moderation.");
  }

  async function blockPeer() {
    const peer = state.members.find((m) => Number(m.user_id) !== currentUserId);
    if (!peer) return setStatus("No peer is available to block.", "error");
    await api("/blocks", { method: "POST", body: JSON.stringify({ blocked_user_id: peer.user_id, reason: "Blocked from v2 test UI" }) }, "block");
    setStatus("Member blocked.");
  }

  function toggleDetails() {
    state.detailsOpen = !state.detailsOpen;
    root?.classList.toggle("details-open", state.detailsOpen);
  }

  function shortTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function relativeTime(value) {
    if (!value) return "unknown";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return "just now";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    return `${days}d ago`;
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(value > 20 * 1024 * 1024 ? 0 : 1)} MB`;
    if (value >= 1024) return `${Math.round(value / 1024)} KB`;
    return `${value || 0} B`;
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#96;");
  }

  bind();
  setMobileMode(isMobile() ? "list" : "desktop");
  sendPresenceHeartbeat();
  schedulePresenceHeartbeat();
  mobileQuery.addEventListener?.("change", () => setMobileMode(isMobile() ? (state.active ? "thread" : "list") : "desktop"));
  renderMessages();
  renderMembers();
  renderRooms();
  loadConversations();
  loadRooms();
})();
