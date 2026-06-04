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
    groupMembers: [],
    searchTimer: 0,
    groupSearchTimer: 0,
    filter: "all",
    hasOlder: false,
    oldestMessageId: 0,
    loadingThread: false,
    initialThreadLoaded: false,
    typingTimer: 0,
    typingSentAt: 0,
    detailsCollapsed: false,
    actionPending: false,
  };
  const el = (sel) => document.querySelector(sel);
  const root = el(".comm-shell");
  const currentUserId = Number(root?.dataset.currentUserId || 0);
  const list = el("[data-conversations]");
  const messages = el("[data-messages]");
  const status = el("[data-status]");

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

  function renderConversations() {
    if (!list) return;
    if (!state.conversations.length) {
      list.innerHTML = `<div class="empty-state">No conversations yet. Start a DM, create a group, or open a room.</div>`;
      return;
    }
    list.innerHTML = state.conversations.map((item) => `
      <button class="conversation ${state.active && Number(state.active.conversation_id) === Number(item.conversation_id) ? "is-active" : ""}" type="button" data-conversation-id="${item.conversation_id}">
        <span class="avatar">${initials(item.title)}</span>
        <span>
          <strong>${escapeHtml(item.title || "Untitled chat")}</strong>
          <small>${escapeHtml(item.conversation_type || "")} / ${Number(item.member_count || 0)} members</small>
        </span>
        ${Number(item.unread_count || 0) ? `<span class="badge">${Number(item.unread_count)}</span>` : ""}
      </button>
    `).join("");
  }

  function renderMessages() {
    if (!messages) return;
    const title = el("[data-thread-title]");
    const sub = el("[data-thread-subtitle]");
    if (title) title.textContent = state.active ? state.active.title : "Select a conversation";
    if (sub) sub.textContent = state.active ? `${state.active.conversation_type} / ${state.active.privacy}` : "Search for someone or create a group to start chatting.";
    if (!state.active) {
      messages.innerHTML = `<div class="empty-state">Search for someone or create a group to start chatting.</div>`;
      return;
    }
    if (!state.messages.length) {
      messages.innerHTML = `<div class="empty-state">No messages here yet. Send the first one.</div>`;
      return;
    }
    const older = state.hasOlder ? `<button class="load-older" type="button" data-load-older>Load older messages</button>` : "";
    messages.innerHTML = `${older}${state.messages.map((item) => messageHtml(item)).join("")}`;
    if (!state.preserveScroll) messages.scrollTop = messages.scrollHeight;
    state.preserveScroll = false;
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
      typing.textContent = names.length ? `${names.join(", ")} ${names.length === 1 ? "is" : "are"} typing...` : "No one is typing right now.";
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
    target.innerHTML = state.members.map((member) => `
      <article class="member-row">
        <span class="avatar">${initials(member.display_name || member.username)}</span>
        <span><strong>${escapeHtml(member.display_name || "Pulse member")}</strong><small>${escapeHtml(member.role || "member")}</small></span>
      </article>
    `).join("");
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
    return `
      <article class="message ${mine ? "is-mine" : ""}" data-message-id="${item.id}">
        <strong>${escapeHtml(item.sender?.display_name || "Pulse member")}</strong>
        <time>${escapeHtml(shortTime(item.created_at))}</time>
        ${item.reply_to_message_id ? `<small>Reply to #${Number(item.reply_to_message_id)}</small>` : ""}
        ${item.body ? `<p>${escapeHtml(item.body)}</p>` : ""}
        ${attachments ? `<div class="attachments">${attachments}</div>` : ""}
        <div class="reaction-row">${reactions}</div>
      </article>
    `;
  }

  function attachmentHtml(item) {
    const url = item.playback_url || item.url || item.cdn_url || item.thumbnail_url || "";
    if (!url) return "";
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

  async function loadConversations({ selectFirst = true } = {}) {
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
      if (state.active && !state.initialThreadLoaded) {
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
      renderConversations();
      renderMessages();
      renderMembers();
      if (window.PulseMediaRenderer) window.PulseMediaRenderer.hydrate(messages);
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

  async function uploadSelectedFile(file) {
    if (!file) return 0;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("context_type", "pulse_comm_v2");
    fd.append("conversation_id", state.active?.conversation_id || "");
    const started = performance.now();
    const res = await fetch(`${API}/attachments/upload`, { method: "POST", credentials: "same-origin", body: fd });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text || "{}"); } catch (_) { data = { ok: false, message: res.status === 403 ? "Attachment upload was blocked by site security." : "Attachment upload returned an unexpected response." }; }
    console.info("Pulse Communications V2 timing", { metric: "attachment_upload", status: res.status, durationMs: Math.round(performance.now() - started) });
    if (!res.ok || data.ok === false) throw new Error(data.message || "Attachment upload failed.");
    return Number(data.media?.id || 0);
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
        if (target.closest("[data-mobile-list]")) return list?.scrollIntoView({ behavior: "smooth", block: "start" });
        const person = target.closest("[data-person-id]");
        if (person?.closest("[data-person-results]")) return await runAction(person, "Opening chat...", () => openDm(Number(person.dataset.personId || 0)));
        if (person?.closest("[data-group-person-results]")) return addGroupMember(Number(person.dataset.personId || 0));
        const removeMember = target.closest("[data-remove-group-member]");
        if (removeMember) return removeGroupMember(Number(removeMember.dataset.removeGroupMember || 0));
        const createGroupButton = target.closest("[data-create-group]");
        if (createGroupButton) return await runAction(createGroupButton, "Creating group...", createGroup);
        const createRoomButton = target.closest("[data-create-room]");
        if (createRoomButton) return await runAction(createRoomButton, "Creating room...", createRoom);
        if (target.closest("[data-pick-file]")) return el("[data-file]")?.click();
        if (target.closest("[data-load-older]")) return await loadOlderMessages();
        const room = target.closest("[data-room-id]");
        if (room) return await runAction(room, "Opening room...", () => openRoom(Number(room.dataset.roomId || 0)));
        const react = target.closest("[data-react]");
        if (react) return await reactToMessage(react.dataset.messageId, react.dataset.react);
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

  function openModal(name) {
    closeModals();
    const modal = el(`[data-modal="${name}"]`);
    if (!modal) {
      setStatus("That creation panel is unavailable. Refresh and try again.", "error");
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    window.setTimeout(() => modal.querySelector("input")?.focus(), 30);
  }

  function closeModals() {
    document.querySelectorAll("[data-modal]").forEach((modal) => {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
    });
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
    const fileInput = el("[data-file]");
    const body = input?.value || "";
    const file = fileInput?.files?.[0];
    try {
      setStatus(file ? "Uploading attachment..." : "Sending...");
      const mediaId = await uploadSelectedFile(file);
      const data = await api(`/conversations/${state.active.conversation_id}/messages`, {
        method: "POST",
        body: JSON.stringify({ body, media_ids: mediaId ? [mediaId] : [] }),
      }, "send_message");
      if (input) input.value = "";
      if (fileInput) fileInput.value = "";
      if (data.message) {
        state.messages = [...state.messages, data.message];
        renderMessages();
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
    state.detailsCollapsed = !state.detailsCollapsed;
    root?.classList.toggle("details-collapsed", state.detailsCollapsed);
  }

  function shortTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#96;");
  }

  bind();
  renderMessages();
  renderMembers();
  renderRooms();
  loadConversations();
  loadRooms();
})();
