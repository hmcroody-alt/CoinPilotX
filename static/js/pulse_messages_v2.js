(() => {
  const API = "/api/pulse/communications/v2";
  const state = { conversations: [], active: null, messages: [], filter: "all", members: [] };
  const el = (sel) => document.querySelector(sel);
  const root = el(".comm-shell");
  const currentUserId = Number(root?.dataset.currentUserId || 0);
  const list = el("[data-conversations]");
  const messages = el("[data-messages]");
  const status = el("[data-status]");

  function setStatus(text, kind = "info") {
    if (!status) return;
    status.textContent = text || "";
    status.dataset.kind = kind;
  }

  async function api(path, options = {}) {
    const res = await fetch(API + path, {
      credentials: "same-origin",
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text || "{}"); } catch (_) { data = { ok: false, message: "The server returned an unexpected response." }; }
    if (!res.ok || data.ok === false) {
      const message = data.message || (data.status === "disabled" ? "Pulse Communications 2.0 is not public yet." : "This request could not be completed.");
      throw Object.assign(new Error(message), { data, status: res.status });
    }
    return data;
  }

  function initials(title) {
    return String(title || "P").trim().slice(0, 2).toUpperCase();
  }

  function renderConversations() {
    if (!list) return;
    if (!state.conversations.length) {
      list.innerHTML = `<div class="empty-state">No v2 conversations yet. Create a DM, group, or room to test the staged system.</div>`;
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
    if (sub) sub.textContent = state.active ? `${state.active.conversation_type} / ${state.active.privacy}` : "DMs, groups, rooms, and channels are isolated in v2.";
    if (!state.active) {
      messages.innerHTML = `<div class="empty-state">Choose a chat or create one to start testing v2 safely.</div>`;
      return;
    }
    if (!state.messages.length) {
      messages.innerHTML = `<div class="empty-state">No messages here yet. Send the first one when v2 is enabled.</div>`;
      return;
    }
    messages.innerHTML = state.messages.map((item) => messageHtml(item)).join("");
    messages.scrollTop = messages.scrollHeight;
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
    const url = item.url || item.thumbnail_url || "";
    if (!url) return "";
    if ((item.media_type || "").match(/image|gif/)) return `<img src="${escapeAttr(url)}" alt="Attached media">`;
    if ((item.media_type || "").match(/video/)) return `<video src="${escapeAttr(url)}" controls preload="metadata"></video>`;
    return `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">Open attachment</a>`;
  }

  function renderMembers() {
    const target = el("[data-members]");
    const intel = el("[data-intel-status]");
    if (intel) intel.textContent = state.active ? `${state.active.title} is ready for v2 validation.` : "Waiting for a conversation.";
    if (!target) return;
    if (!state.members.length) {
      target.innerHTML = `<div class="empty-state">No member details loaded.</div>`;
      return;
    }
    target.innerHTML = state.members.map((m) => `
      <div class="member"><span class="avatar">${initials(m.display_name)}</span><span><strong>${escapeHtml(m.display_name)}</strong><small>${escapeHtml(m.role || "member")}</small></span></div>
    `).join("");
  }

  async function loadConversations() {
    try {
      const query = state.filter === "all" ? "" : `?type=${encodeURIComponent(state.filter)}`;
      const data = await api(`/conversations${query}`);
      state.conversations = data.items || data.conversations || [];
      if (!state.active && state.conversations.length) state.active = state.conversations[0];
      renderConversations();
      if (state.active) await loadMessages(state.active.conversation_id);
      setStatus(state.conversations.length ? "" : "No v2 conversations yet.");
    } catch (err) {
      renderConversations();
      setStatus(err.message, err.data?.status === "disabled" ? "disabled" : "error");
    }
  }

  async function loadMessages(conversationId) {
    const data = await api(`/conversations/${conversationId}/messages?limit=80`);
    state.active = data.conversation || state.conversations.find((c) => Number(c.conversation_id) === Number(conversationId)) || state.active;
    state.messages = data.messages || [];
    renderConversations();
    renderMessages();
    await loadMembers(conversationId);
  }

  async function loadMembers(conversationId) {
    try {
      const data = await api(`/conversations/${conversationId}/members`);
      state.members = data.members || [];
      renderMembers();
    } catch (_) {
      state.members = [];
      renderMembers();
    }
  }

  async function uploadSelectedFile(file) {
    if (!file) return 0;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("context_type", "pulse_comm_v2");
    fd.append("context_id", state.active?.conversation_id || "draft");
    const res = await fetch("/api/pulse/media/upload", { method: "POST", credentials: "same-origin", body: fd });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.message || "Attachment upload failed.");
    return Number(data.media?.id || 0);
  }

  function bind() {
    document.addEventListener("click", async (event) => {
      const conversation = event.target.closest("[data-conversation-id]");
      if (conversation) {
        const id = conversation.dataset.conversationId;
        try { await loadMessages(id); } catch (err) { setStatus(err.message, "error"); }
      }
      const filter = event.target.closest("[data-filter]");
      if (filter) {
        state.filter = filter.dataset.filter;
        document.querySelectorAll("[data-filter]").forEach((btn) => btn.classList.toggle("is-active", btn === filter));
        await loadConversations();
      }
      if (event.target.closest("[data-open-dm]")) await openDm();
      if (event.target.closest("[data-create-room]")) await createRoom();
      if (event.target.closest("[data-pick-file]")) el("[data-file]")?.click();
      const react = event.target.closest("[data-react]");
      if (react) await reactToMessage(react.dataset.messageId, react.dataset.react);
      if (event.target.closest("[data-report-last]")) await reportLast();
      if (event.target.closest("[data-block-peer]")) await blockPeer();
      if (event.target.closest("[data-voice]") || event.target.closest("[data-video]")) setStatus("Voice and video are Phase 2 placeholders.");
    });
    el("[data-composer]")?.addEventListener("submit", sendMessage);
  }

  async function openDm() {
    const input = el("[data-target-user]");
    const target = Number(input?.value || 0);
    if (!target) return setStatus("Enter a user ID to open a DM.", "error");
    const data = await api("/direct/open", { method: "POST", body: JSON.stringify({ target_user_id: target }) });
    state.active = data.conversation;
    await loadConversations();
  }

  async function createRoom() {
    const title = el("[data-room-title]")?.value || "";
    const privacy = el("[data-room-privacy]")?.value || "public";
    if (!title.trim()) return setStatus("Name the room before creating it.", "error");
    const data = await api("/rooms", { method: "POST", body: JSON.stringify({ title, privacy }) });
    state.active = data.conversation;
    await loadConversations();
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
      await api(`/conversations/${state.active.conversation_id}/messages`, {
        method: "POST",
        body: JSON.stringify({ body, media_ids: mediaId ? [mediaId] : [] }),
      });
      if (input) input.value = "";
      if (fileInput) fileInput.value = "";
      await loadMessages(state.active.conversation_id);
      setStatus("");
    } catch (err) {
      setStatus(err.message, "error");
    }
  }

  async function reactToMessage(messageId, reaction) {
    await api(`/messages/${messageId}/reactions`, { method: "POST", body: JSON.stringify({ reaction }) });
    if (state.active) await loadMessages(state.active.conversation_id);
  }

  async function reportLast() {
    const last = state.messages[state.messages.length - 1];
    if (!last) return setStatus("No message is available to report.", "error");
    await api(`/messages/${last.id}/report`, { method: "POST", body: JSON.stringify({ reason: "Reported from v2 test UI" }) });
    setStatus("Report sent to moderation.");
  }

  async function blockPeer() {
    const peer = state.members.find((m) => Number(m.user_id) !== currentUserId);
    if (!peer) return setStatus("No peer is available to block.", "error");
    await api("/blocks", { method: "POST", body: JSON.stringify({ blocked_user_id: peer.user_id, reason: "Blocked from v2 test UI" }) });
    setStatus("Member blocked.");
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
  loadConversations();
})();
