(function () {
  const escapeHtml = (value) =>
    String(value || "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));

  function qs(root, selector) {
    return (root || document).querySelector(selector);
  }

  function qsa(root, selector) {
    return Array.from((root || document).querySelectorAll(selector));
  }

  function setText(root, selector, value) {
    const node = qs(root, selector);
    if (node) node.textContent = value;
  }

  function setCameraSurfaceActive(root, active) {
    if (!root) return;
    root.classList.toggle("is-camera-active", Boolean(active));
    qsa(root, "[data-live-idle-overlay]").forEach((node) => {
      node.hidden = Boolean(active);
      node.setAttribute("aria-hidden", active ? "true" : "false");
    });
    const state = qs(root, "[data-live-camera-state]");
    if (state && active && !state.classList.contains("is-error")) {
      state.hidden = true;
      state.textContent = "";
    }
  }

  function showCameraState(root, message, mode = "info") {
    const state = qs(root, "[data-live-camera-state]");
    if (!state) return;
    state.textContent = message || "";
    const forceVisible = mode === "status" || mode === "warning" || mode === "error";
    state.hidden = !message || (root.classList.contains("is-camera-active") && !forceVisible);
    state.classList.toggle("is-error", mode === "error");
  }

  function notify(message) {
    if (typeof window.toast === "function") window.toast(message);
  }

  function chatMessageHtml(message) {
    const name = message.display_name || message.username || (message.message_type === "system" ? "PulseSoc" : "Viewer");
    const initial = String(name || "P").trim().slice(0, 1).toUpperCase() || "P";
    const role = message.message_type === "system" ? "System" : message.pinned ? "Pinned" : "Live";
    return `<article class="live-chat-message" data-message-id="${Number(message.id || 0)}">
      <div class="live-chat-avatar">${escapeHtml(initial)}</div>
      <div>
        <strong>${escapeHtml(name)}</strong> <span class="pill">${escapeHtml(role)}</span>
        <p>${escapeHtml(message.body || "")}</p>
      </div>
    </article>`;
  }

  function renderChat(root, messages) {
    const feed = qs(root, "[data-live-chat-feed]");
    if (!feed || !Array.isArray(messages)) return;
    const oldScroll = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 80;
    feed.innerHTML = messages.map(chatMessageHtml).join("") || `<article class="live-chat-message"><div class="live-chat-avatar">P</div><div><strong>PulseSoc</strong><p>Chat is ready.</p></div></article>`;
    if (oldScroll) feed.scrollTop = feed.scrollHeight;
  }

  function renderReactionBurst(root, reactions) {
    const layer = qs(root, "[data-live-reactions]");
    if (!layer) return;
    const safe = Array.isArray(reactions) ? reactions.slice(0, 12) : [];
    layer.innerHTML = safe.map((item, index) => {
      const x = Number(item.x || 58 + index * 3);
      const delay = Number(item.delay_ms || index * 140);
      return `<span style="--x:${x}%;--delay:${delay}ms">${escapeHtml(item.emoji || "🔥")}</span>`;
    }).join("");
  }

  function friendlyStreamHealth(value, status) {
    const raw = String(value || status || "").toLowerCase();
    if (["ended", "offline", "complete", "finished"].includes(raw)) return "Offline";
    if (["excellent", "stable", "live", "active"].includes(raw)) return "Excellent";
    if (["good", "ready", "broadcasting"].includes(raw)) return "Good";
    if (["fair", "warning", "starting", "reconnecting"].includes(raw)) return "Fair";
    return raw ? "Connection Issue" : "Good";
  }

  function ensurePublicPlayback(root, data) {
    if (!root || root.dataset.liveRole !== "viewer") return;
    const player = qs(root, ".live-public-player");
    if (!player) return;
    const playback = data.playback || {};
    const mux = data.mux || {};
    const playbackUrl = playback.playback_url || playback.hls_url || mux.playback_url || "";
    const hasDirectRoom = Boolean(playback.webrtc_room_id || data.livekit?.room);
    const muxStatus = String(mux.live_status || "").toLowerCase();
    const active = ["active", "live"].includes(muxStatus) && Boolean(playbackUrl);
    if (!active || (!playbackUrl && !hasDirectRoom)) return;
    const existing = qs(player, "[data-live-player]");
    if (existing) {
      if (playbackUrl && !existing.getAttribute("src") && !existing.srcObject) existing.setAttribute("src", playbackUrl);
      if (!playbackUrl && existing.getAttribute("src")) existing.removeAttribute("src");
      initViewerTransport(root);
      return;
    }
    const reactions = qs(player, "[data-live-reactions]")?.outerHTML || "<div class='live-floating-reactions' data-live-reactions></div>";
    const srcAttr = playbackUrl ? ` src="${escapeHtml(playbackUrl)}"` : "";
    player.innerHTML = `<video class="live-public-video" data-live-player${srcAttr} autoplay muted playsinline webkit-playsinline preload="metadata" controlsList="nodownload noplaybackrate noremoteplayback" disablepictureinpicture></video><button class="live-unmute-button" type="button" data-live-unmute aria-label="Enable live audio">Tap to unmute</button>${reactions}`;
    initViewerTransport(root);
  }

  function applyState(root, data) {
    const health = data.health || {};
    const pulse = data.presence?.pulse || data.presence || {};
    setText(root, "[data-live-viewers]", data.viewer_count ?? 0);
    setText(root, "[data-live-health]", friendlyStreamHealth(health.level, data.status));
    setText(root, "[data-live-score]", health.score ?? 0);
    setText(root, "[data-live-bitrate]", health.bitrate_label || `${health.bitrate_kbps || 0} kbps`);
    setText(root, "[data-live-fps]", health.fps_label || `${health.fps || 0} FPS`);
    setText(root, "[data-live-latency]", health.latency_ms ? `${health.latency_ms} ms` : "ready");
    setText(root, "[data-live-pulse]", pulse.label || "ready");
    if (data.mux?.live_status) setText(root, "[data-mux-live-status]", `Status ${data.mux.live_status}`);
    if (data.livekit?.egress_error) {
      const muxStatus = String(data.mux?.live_status || "").toLowerCase();
      const publishState = String(data.publish_state || "").toLowerCase();
      const egressStatus = String(data.livekit?.egress_status || "").toLowerCase();
      const hasDirectRoom = Boolean(data.playback?.webrtc_room_id || data.livekit?.room);
      const direct = data.direct_mode || data.mux?.quota_exhausted || ["egress_quota_exhausted", "livekit_direct"].includes(muxStatus) || ["browser_live_livekit_direct", "livekit_direct"].includes(publishState) || ["quota_exhausted", "livekit_direct"].includes(egressStatus);
      const directCopy = "LiveKit direct mode is active. Mux replay resumes when egress minutes are available.";
      const fallbackCopy = hasDirectRoom ? "LiveKit publishing is connected, but Mux is not active yet. Start Camera again if Mux stays idle." : "Mux replay needs attention. Start Camera again to reconnect LiveKit publishing.";
      setText(root, "[data-live-egress-message]", direct ? directCopy : fallbackCopy);
      setText(root, "[data-live-transport-summary]", direct ? "LiveKit direct is a fallback only; public Mux playback is unavailable until egress recovers." : hasDirectRoom ? "LiveKit is connected, but public playback waits for Mux active." : "Mux bridge needs attention. LiveKit publishing can reconnect from Start Camera.");
    }
    const score = qs(root, ".live-health-score");
    if (score) score.style.setProperty("--score", Math.max(0, Math.min(100, Number(health.score || 0))));
    renderChat(root, data.messages || []);
    renderReactionBurst(root, data.reaction_cloud || []);
    renderLiveGuests(root, data.guests || []);
    renderBackstage(root, data.join_requests || [], data.guests || []);
    renderCoHostStatus(root, data);
    renderJoinPanel(root, data.viewer_join_request, data.guest);
    ensurePublicPlayback(root, data);
  }

  async function fetchState(root) {
    const id = root?.dataset?.liveId;
    if (!id) return;
    try {
      const response = await fetch(`/api/pulse/live/${id}/state`, { credentials: "same-origin", cache: "no-store" });
      const data = await response.json();
      if (data && data.ok !== false) applyState(root, data);
    } catch (error) {
      setText(root, "[data-live-health]", "reconnecting");
      console.warn("PulseSoc Live state recovery", error);
    }
  }

  function scheduleLiveStatePolling(root) {
    const interval = Math.max(900, Number(root.dataset.livePollMs || 1200));
    let inFlight = false;
    const poll = async () => {
      if (document.hidden || inFlight) return;
      inFlight = true;
      try {
        await fetchState(root);
      } finally {
        inFlight = false;
      }
    };
    setInterval(poll, interval);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) poll();
    }, { passive: true });
  }

  async function checkMuxStatus(root, options = {}) {
    const muxId = qs(root, "[data-check-mux-status]")?.dataset?.muxLiveId || "";
    if (!muxId) {
      notify("Mux Live stream id is not available for this session.");
      return null;
    }
    try {
      const response = await fetch(`/api/pulse/live/mux/${encodeURIComponent(muxId)}`, { credentials: "same-origin", cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.message || "Mux status could not be checked.");
      setText(root, "[data-mux-live-status]", `Status ${data.mux_live_status || "idle"}`);
      if (data.mux_live_status === "active" || data.mux_live_status === "live") {
        setText(root, "[data-live-health]", "live");
        setText(root, "[data-live-pulse]", "broadcasting");
      setText(root, "[data-live-bitrate]", data.bitrate_label || "Live");
      setText(root, "[data-live-fps]", data.fps_label || "Live");
      }
      if (!options.silent) notify(`Mux status: ${data.mux_live_status || "idle"}`);
      return data;
    } catch (error) {
      notify(error.message);
      return null;
    }
  }

  function scheduleMuxPolling(root) {
    const muxId = qs(root, "[data-check-mux-status]")?.dataset?.muxLiveId || "";
    if (!muxId) return;
    const interval = Math.max(10000, Number(root?.dataset?.muxPollMs || 12000));
    const tick = async () => {
      if (!root?.isConnected) return;
      await checkMuxStatus(root, { silent: true });
      setTimeout(tick, interval);
    };
    setTimeout(tick, interval);
  }

  async function copyLiveValue(button) {
    const valueNode = button?.parentElement?.querySelector("[data-copy-value]");
    const value = valueNode?.dataset?.copyValue || valueNode?.textContent || "";
    if (!value) return;
    await navigator.clipboard?.writeText(value).catch(() => {});
    notify("Copied.");
  }

  function toggleAdvancedStreaming(root, force) {
    const panel = qs(root, "[data-live-advanced-panel]");
    if (!panel) return;
    const shouldOpen = typeof force === "boolean" ? force : panel.hidden;
    panel.hidden = !shouldOpen;
    panel.style.display = shouldOpen ? "" : "none";
    panel.setAttribute("aria-hidden", shouldOpen ? "false" : "true");
    if (shouldOpen) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function revealLiveSecret(button) {
    const valueNode = button?.parentElement?.querySelector("[data-secret-live-value]");
    if (!valueNode) return;
    const isRevealed = valueNode.dataset.secretRevealed === "1";
    const masked = valueNode.dataset.secretMasked || "••••••";
    const raw = valueNode.dataset.copyValue || "";
    valueNode.textContent = isRevealed ? masked : raw || masked;
    valueNode.dataset.secretRevealed = isRevealed ? "0" : "1";
    button.textContent = isRevealed ? "Reveal" : "Hide";
  }

  async function sendChat(root) {
    const id = root?.dataset?.liveId;
    const input = qs(root, "[data-live-chat-input]");
    const body = input?.value?.trim();
    if (!id || !body) return;
    const button = qs(root, "[data-live-chat-send]");
    if (button) button.disabled = true;
    try {
      await fetch(`/api/pulse/live/${id}/chat`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body }),
      }).then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) throw new Error(data.message || "Chat could not send.");
        return data;
      });
      input.value = "";
      await fetchState(root);
    } catch (error) {
      notify(error.message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function sendReaction(root, reaction) {
    const id = root?.dataset?.liveId;
    if (!id) return;
    const button = root.querySelector(`[data-live-reaction="${CSS.escape(String(reaction))}"]`);
    if (button?.dataset.busy === "1") return;
    if (button) {
      button.dataset.busy = "1";
      button.classList.add("active", "is-popping", "is-pending");
      button.setAttribute("aria-pressed", "true");
    }
    renderReactionBurst(root, [{ emoji: reaction, x: 64, delay_ms: 0 }]);
    try {
      const response = await fetch(`/api/pulse/live/${id}/react`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reaction_type: reaction }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.message || "Reaction failed.");
    } catch (error) {
      if (button) {
        button.classList.remove("active");
        button.setAttribute("aria-pressed", "false");
      }
      console.warn("PulseSoc Live reaction failed", error);
    } finally {
      if (button) {
        button.classList.remove("is-popping", "is-pending");
        delete button.dataset.busy;
      }
    }
  }

  function liveGuestTileHtml(guest) {
    const name = guest.display_name || "Co-host";
    const initial = String(name).trim().slice(0, 1).toUpperCase() || "G";
    const role = guest.role_label || (guest.role === "cohost" ? "Co-host" : "Guest");
    const muted = guest.audio_muted ? `Muted ${role.toLowerCase()}` : `${role} live`;
    return `<article class="live-guest-tile" data-live-guest-id="${Number(guest.id || 0)}">
      <span class="live-guest-avatar">${escapeHtml(initial)}</span>
      <div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(muted)}</small></div>
    </article>`;
  }

  function liveGuestControlHtml(guest) {
    const action = guest.audio_muted ? "unmute" : "mute";
    const role = guest.role_label || (guest.role === "cohost" ? "Co-host" : "Guest");
    return `<article class="live-guest-control" data-live-guest-id="${Number(guest.id || 0)}">
      <div><strong>${escapeHtml(guest.display_name || role)}</strong><small>${escapeHtml(role)} · ${guest.audio_muted ? "Muted by host" : "Audio active"} · Video ${guest.video_enabled ? "on" : "off"}</small></div>
      <div class="live-join-request-actions">
        <button type="button" data-live-guest-action="${action}" data-live-guest-id="${Number(guest.id || 0)}">${action === "unmute" ? "Unmute" : "Mute"}</button>
        <button type="button" data-live-guest-action="remove" data-live-guest-id="${Number(guest.id || 0)}">Remove</button>
      </div>
    </article>`;
  }

  function liveJoinRequestHtml(request) {
    const name = request.display_name || "Viewer";
    const initial = String(name).trim().slice(0, 1).toUpperCase() || "V";
    const requestId = Number(request.id || 0);
    const message = request.request_message ? `<p class="muted">${escapeHtml(request.request_message)}</p>` : "";
    return `<article class="live-join-request" data-live-request-id="${requestId}">
      <div class="live-join-request-main">
        <span class="live-guest-avatar">${escapeHtml(initial)}</span>
        <div><strong>${escapeHtml(name)}</strong><small>Camera ${request.camera_ready ? "ready" : "blocked"} · Mic ${request.mic_ready ? "ready" : "blocked"} · ${escapeHtml(request.network_quality || "unknown")}</small></div>
      </div>
      ${message}
      <div class="live-join-request-actions">
        <button type="button" data-live-request-action="accept" data-live-request-id="${requestId}">Accept Co-host</button>
        <button type="button" data-live-request-action="deny" data-live-request-id="${requestId}">Deny</button>
      </div>
    </article>`;
  }

  function renderLiveGuests(root, guests) {
    const list = Array.isArray(guests) ? guests : [];
    const html = list.length ? list.map(liveGuestTileHtml).join("") : `<article class="live-guest-tile is-empty"><span class="live-guest-avatar">+</span><div><strong>No co-hosts</strong><small>Approved co-hosts appear here.</small></div></article>`;
    qsa(root, "[data-live-guest-stack]").forEach((node) => { node.innerHTML = html; });
    qsa(root, "[data-live-guest-sidecar]").forEach((node) => { node.innerHTML = list.length ? html : `<p class="muted">No approved co-hosts yet.</p>`; });
  }

  function renderBackstage(root, requests, guests) {
    const requestList = qs(root, "[data-live-join-request-list]");
    const guestList = qs(root, "[data-live-guest-control-list]");
    const safeRequests = Array.isArray(requests) ? requests : [];
    const safeGuests = Array.isArray(guests) ? guests : [];
    qsa(root, "[data-live-request-count]").forEach((node) => { node.textContent = String(safeRequests.length); });
    qsa(root, "[data-live-guest-count]").forEach((node) => { node.textContent = String(safeGuests.length); });
    if (requestList) {
      requestList.innerHTML = safeRequests.length ? safeRequests.map(liveJoinRequestHtml).join("") : `<p class="muted">No co-host requests. Viewers can request camera/mic access from the Live room.</p>`;
    }
    if (guestList) {
      guestList.innerHTML = safeGuests.length ? safeGuests.map(liveGuestControlHtml).join("") : `<p class="muted">No active co-hosts.</p>`;
    }
  }

  function renderCoHostStatus(root, data) {
    const cohost = data.cohost || {};
    const accepting = Boolean(cohost.enabled ?? data.accepting_guests ?? data.cohost_enabled);
    const active = Number(data.guest_count ?? cohost.active_count ?? 0);
    const pending = Number(data.join_request_count ?? cohost.pending_count ?? 0);
    const state = active > 0 ? `${active} co-host${active === 1 ? "" : "s"} live` : accepting ? "Available" : "Closed";
    const copy = accepting
      ? "Viewers can request camera and microphone access. Accept them from Backstage to create a real multi-host LiveKit publisher."
      : "This Live is not accepting new co-host requests.";
    qsa(root, "[data-live-cohost-card]").forEach((card) => {
      card.classList.toggle("is-active", active > 0);
      card.classList.toggle("is-closed", !accepting);
    });
    qsa(root, "[data-live-cohost-state]").forEach((node) => { node.textContent = state; });
    qsa(root, "[data-live-cohost-copy]").forEach((node) => { node.textContent = copy; });
    qsa(root, "[data-live-guest-count]").forEach((node) => { node.textContent = String(active); });
    qsa(root, "[data-live-request-count]").forEach((node) => { node.textContent = String(pending); });
  }

  function renderJoinPanel(root, request, guest) {
    const panel = qs(root, "[data-live-join-panel]");
    if (!panel || root.dataset.liveViewerKind === "host") return;
    const status = guest ? "accepted" : (request?.status || "none");
    panel.dataset.liveRequestStatus = status;
    if (guest) {
      const published = guest.status === "live" && Boolean(root.__pulseLiveGuestPublished);
      const joining = ["joining", "joined", "publishing"].includes(String(guest.status || ""));
      panel.innerHTML = `<button class="live-primary-action is-accepted" type="button" data-live-guest-join disabled>${published ? "Joined" : joining ? "Publishing..." : "Accepted — Joining..."}</button><button type="button" data-live-guest-leave data-live-guest-id="${Number(guest.id || 0)}">Leave co-host seat</button><p class="muted" data-live-join-status>${published ? "Joined as co-host. Camera and microphone are live." : joining ? "Waiting for LiveKit participant and track confirmation." : "Publishing is server-approved for this co-host slot."}</p>`;
      if (published) return;
      publishGuestToLiveKit(root).catch((error) => {
        qsa(root, "[data-live-guest-join]").forEach((button) => { button.textContent = "Retry publish"; button.disabled = false; });
        const diagnostic = error.error_code ? ` ${error.error_code} at ${error.step || "unknown_stage"}.` : "";
        const message = `${error.message || "Co-host publishing could not start."}${diagnostic}${error.trace_id ? ` (Trace ${error.trace_id})` : ""}`;
        console.error("[PulseSoc cohost publish failure]", { error_code: error.error_code || "LIVEKIT_PUBLISH_FAILED", step: error.step || "publish_failed", trace_id: error.trace_id || "", message: error.message || "" });
        setText(root, "[data-live-join-status]", message);
      });
      return;
    }
    if (status === "pending") {
      panel.innerHTML = `<button class="live-primary-action" type="button" data-live-join-request disabled>Waiting for Host</button><button type="button" data-live-cancel-request data-live-request-id="${Number(request?.id || 0)}">Cancel co-host request</button><p class="muted" data-live-join-status>Request sent. The host can accept or deny from Backstage.</p>`;
      return;
    }
    if (status === "denied") {
      panel.innerHTML = `<button class="live-primary-action" type="button" data-live-join-request>Denied</button><p class="muted" data-live-join-status>Your last co-host request was denied. Tap Denied to request again.</p>`;
      return;
    }
    if (status === "cancelled") {
      panel.innerHTML = `<button class="live-primary-action" type="button" data-live-join-request>Request to Co-host</button><p class="muted" data-live-join-status>Request cancelled. You can request again.</p>`;
      return;
    }
    panel.innerHTML = `<button class="live-primary-action" type="button" data-live-join-request>Request to Co-host</button><p class="muted" data-live-join-status>Camera and microphone readiness will be checked before the host sees your request.</p>`;
  }

  function livekitClient() {
    return window.LivekitClient || window.LiveKitClient || window.livekitClient || null;
  }

  function stopGuestMedia(root) {
    root.__pulseLiveGuestTracks?.forEach((track) => {
      try { track.stop?.(); } catch (_) {}
      try { track.mediaStreamTrack?.stop?.(); } catch (_) {}
    });
    root.__pulseLiveGuestStream?.getTracks?.().forEach((track) => {
      try { track.stop(); } catch (_) {}
    });
    root.__pulseLiveGuestAudioStream?.getTracks?.().forEach((track) => {
      try { track.stop(); } catch (_) {}
    });
    root.__pulseLiveGuestTracks = [];
    root.__pulseLiveGuestStream = null;
    root.__pulseLiveGuestAudioStream = null;
    root.__pulseLiveGuestToken = null;
    root.__pulseLiveGuestTokenClaims = null;
  }

  async function checkGuestReadiness(root) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera and microphone are unavailable in this browser.");
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    const cameraReady = stream.getVideoTracks().some((track) => track.readyState === "live");
    const micReady = stream.getAudioTracks().some((track) => track.readyState === "live");
    const settings = stream.getVideoTracks()[0]?.getSettings?.() || {};
    const payload = {
      camera_ready: cameraReady,
      mic_ready: micReady,
      network_quality: navigator.onLine === false ? "offline" : "ready",
      connection: {
        camera_ready: cameraReady,
        mic_ready: micReady,
        width: Number(settings.width || 0),
        height: Number(settings.height || 0),
        frameRate: Number(settings.frameRate || 0),
        effectiveType: navigator.connection?.effectiveType || "",
        downlink: navigator.connection?.downlink || "",
      },
    };
    stream.getTracks().forEach((track) => track.stop());
    return payload;
  }

  function cohostTraceId(root) {
    const liveId = String(root?.dataset?.liveId || "");
    const key = `pulseCohostTrace:${liveId}`;
    let traceId = root?.dataset?.liveCohostTraceId || "";
    try { traceId = traceId || sessionStorage.getItem(key) || ""; } catch (_) {}
    if (!traceId) traceId = window.crypto?.randomUUID?.() || `cohost-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    if (root) root.dataset.liveCohostTraceId = traceId;
    try { sessionStorage.setItem(key, traceId); } catch (_) {}
    return traceId;
  }

  function cohostStageError(code, message, step, traceId) {
    const error = new Error(message);
    Object.assign(error, { error_code: code, step, trace_id: traceId });
    return error;
  }

  async function traceCohostStage(root, step, details = {}) {
    const liveId = String(root?.dataset?.liveId || "");
    if (!liveId) return;
    const payload = { trace_id: cohostTraceId(root), step, request_id: Number(details.request_id || root?.dataset?.liveRequestId || 0), error_code: details.error_code || "", details };
    console.info("[PulseSoc cohost stage]", payload);
    try {
      await fetch(`/api/pulse/live/${encodeURIComponent(liveId)}/cohost-trace`, { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), keepalive: true });
    } catch (_) {}
  }

  async function requestJoinLive(root) {
    const id = String(root?.dataset?.liveId || "").trim();
    const button = qs(root, "[data-live-join-request]");
    if (button?.dataset.busy === "1") return;
    if (!/^[1-9]\d*$/.test(id)) {
      const invalid = { error_code: "INVALID_LIVE_ID", step: "entry_validation", trace_id: window.crypto?.randomUUID?.() || String(Date.now()), message: "This Live has an invalid identifier. Refresh and try again." };
      console.error("[PulseSoc cohost request blocked]", { live_id: id, endpoint: null, auth_context: "same-origin-session", payload: null, ...invalid });
      setText(root, "[data-live-join-status]", `${invalid.message} (Trace ${invalid.trace_id})`);
      notify(`${invalid.message} (Trace ${invalid.trace_id})`);
      return;
    }
    button.dataset.busy = "1";
    button.disabled = true;
    const setStatus = (message) => setText(root, "[data-live-join-status]", message);
    try {
      button.textContent = "Checking...";
      setStatus("Checking camera and microphone permission...");
      const readiness = await checkGuestReadiness(root);
      button.textContent = "Checking...";
      const endpoint = `/api/pulse/live/${encodeURIComponent(id)}/cohost/request`;
      const requestPayload = { ...readiness, requested_role: "cohost", request_message: readiness.request_message || "Requesting co-host camera/mic access.", trace_id: cohostTraceId(root) };
      console.info("[PulseSoc cohost request]", { live_id: id, endpoint, auth_context: "same-origin-session", payload: requestPayload });
      const response = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestPayload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        const error = new Error(data.message || "The co-host request did not complete.");
        Object.assign(error, data);
        throw error;
      }
      console.info("[PulseSoc cohost response]", { live_id: id, request_id: data.request_id || data.request?.id || 0, state: data.state || data.status || "", step: data.step || "", trace_id: data.trace_id || requestPayload.trace_id });
      root.dataset.liveRequestId = String(data.request_id || data.request?.id || 0);
      button.textContent = data.status === "accepted" ? "Accepted — Joining..." : "Request Sent";
      setStatus(data.message || "Request sent. Waiting for host approval.");
      await fetchState(root);
    } catch (error) {
      button.disabled = false;
      button.textContent = "Retry Co-host";
      const diagnostic = error.error_code ? ` ${error.error_code} at ${error.step || "unknown_stage"}.` : "";
      const message = `${error.message || "The co-host request did not complete."}${diagnostic}${error.trace_id ? ` (Trace ${error.trace_id})` : ""}`;
      console.error("[PulseSoc cohost failure]", { live_id: id, error_code: error.error_code || "UNKNOWN_COHOST_ERROR", step: error.step || "client_request", trace_id: error.trace_id || "", message: error.message || "" });
      setStatus(message);
      notify(message);
    } finally {
      delete button.dataset.busy;
    }
  }

  async function cancelJoinRequest(root, requestId) {
    const id = root?.dataset?.liveId;
    if (!id || !requestId) return;
    const response = await fetch(`/api/pulse/live/${id}/join-requests/${encodeURIComponent(requestId)}/cancel`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.message || "Request could not be cancelled.");
    stopGuestMedia(root);
    await fetchState(root);
  }

  async function hostJoinRequestAction(root, requestId, action) {
    const id = root?.dataset?.liveId;
    if (!id || !requestId || !action) return;
    const response = await fetch(`/api/pulse/live/${id}/join-requests/${encodeURIComponent(requestId)}/${encodeURIComponent(action)}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.message || "Join request action failed.");
      Object.assign(error, data);
      throw error;
    }
    notify(data.message || `Join request ${action}ed.`);
    await fetchState(root);
  }

  async function hostGuestAction(root, guestId, action) {
    const id = root?.dataset?.liveId;
    if (!id || !guestId || !action) return;
    const response = await fetch(`/api/pulse/live/${id}/guests/${encodeURIComponent(guestId)}/${encodeURIComponent(action)}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.message || "Guest action failed.");
    if (action === "leave") {
      stopGuestMedia(root);
      root.__pulseLiveGuestPublished = false;
      try { await root.__pulseLiveGuestRoom?.disconnect?.(); } catch (_) {}
      root.__pulseLiveGuestRoom = null;
    }
    await fetchState(root);
  }

  async function publishGuestToLiveKit(root) {
    if (root.__pulseLiveGuestPublished || root.__pulseLiveGuestPublishing) return;
    const id = String(root?.dataset?.liveId || "");
    if (!id || root.dataset.liveViewerKind === "host") return;
    const LK = livekitClient();
    let traceId = cohostTraceId(root);
    if (!LK) throw cohostStageError("LIVEKIT_ROOM_JOIN_FAILED", "LiveKit is not available in this browser.", "livekit_client", traceId);
    root.__pulseLiveGuestPublishing = true;
    let room = null;
    const pendingTracks = [];
    const started = new Map();
    const begin = async (step, details = {}) => {
      started.set(step, performance.now());
      await traceCohostStage(root, step, { ...details, result: "started", started_at: new Date().toISOString() });
    };
    const complete = async (step, details = {}) => {
      const began = started.get(step) || performance.now();
      await traceCohostStage(root, step, { ...details, result: "completed", completed_at: new Date().toISOString(), duration_ms: Math.max(0, Math.round(performance.now() - began)) });
    };
    try {
      setText(root, "[data-live-join-status]", "Accepted — Joining with camera and microphone...");
      await begin("cohost_token_request_started", { stage_number: 13, stage_name: "livekit_token_generation_started" });
      const tokenResponse = await fetch(`/api/pulse/live/${id}/livekit/token`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "cohost", trace_id: traceId }),
      });
      const tokenText = await tokenResponse.text();
      let tokenData = {};
      try { tokenData = JSON.parse(tokenText || "{}"); }
      catch (_) { throw cohostStageError("TOKEN_DELIVERY_FAILED", "The server returned an unreadable co-host token response.", "token_delivery", traceId); }
      if (!tokenResponse.ok || tokenData.ok === false) {
        const error = cohostStageError(tokenData.error_code || "TOKEN_DELIVERY_FAILED", tokenData.message || "Co-host token could not be created.", tokenData.step || "token_delivery", tokenData.trace_id || traceId);
        Object.assign(error, tokenData);
        throw error;
      }
      if (tokenData.trace_id) {
        traceId = String(tokenData.trace_id);
        root.dataset.liveCohostTraceId = traceId;
        try { sessionStorage.setItem(`pulseCohostTrace:${id}`, traceId); } catch (_) {}
      }
      const requiredClaims = tokenData.token && tokenData.livekit_url && tokenData.identity && tokenData.room && tokenData.participant_name && tokenData.role === "cohost" && tokenData.can_publish === true && tokenData.can_subscribe === true && tokenData.can_publish_data === true && tokenData.room_join === true && Number(tokenData.expires_at || 0) * 1000 > Date.now();
      if (!requiredClaims) throw cohostStageError("TOKEN_DELIVERY_FAILED", "The co-host token response is missing required verified claims.", "token_delivery", tokenData.trace_id || traceId);
      root.__pulseLiveGuestToken = tokenData.token;
      root.__pulseLiveGuestTokenClaims = tokenData.token_claims || {};
      root.dataset.liveGuestId = String(tokenData.guest_id || "");
      root.dataset.liveRequestId = String(tokenData.request_id || root.dataset.liveRequestId || "");
      await complete("cohost_token_request_started", { stage_number: 13, stage_name: "livekit_token_generated", request_id: tokenData.request_id || 0 });
      await traceCohostStage(root, "cohost_token_delivered", { stage_number: 15, stage_name: "token_delivered_to_viewer", result: "completed", http_status: tokenResponse.status, json_parsed: true, memory_assigned: root.__pulseLiveGuestToken === tokenData.token, request_id: tokenData.request_id || 0 });
      room = new LK.Room({ adaptiveStream: true, dynacast: true });
      const roomEvents = LK.RoomEvent || {};
      if (room.on) {
        if (roomEvents.Reconnecting) room.on(roomEvents.Reconnecting, () => traceCohostStage(root, "cohost_room_join_started", { stage_number: 16, stage_name: "websocket_reconnecting", result: "started" }));
        if (roomEvents.Reconnected) room.on(roomEvents.Reconnected, () => traceCohostStage(root, "cohost_room_joined", { stage_number: 16, stage_name: "ice_reconnected", result: "completed" }));
        if (roomEvents.Disconnected) room.on(roomEvents.Disconnected, (reason) => traceCohostStage(root, "cohost_failure", { stage_number: 16, stage_name: "room_disconnected", result: "failed", error_code: "LIVEKIT_ROOM_JOIN_FAILED", error_message: String(reason || "Room disconnected.") }));
      }
      await begin("cohost_room_join_started", { stage_number: 16, stage_name: "livekit_room_join_started", dns_target: new URL(tokenData.livekit_url).hostname, websocket_url: tokenData.livekit_url.replace(/\?.*$/, "") });
      try {
        await Promise.race([
          room.connect(tokenData.livekit_url, root.__pulseLiveGuestToken, { autoSubscribe: true }),
          new Promise((_, reject) => setTimeout(() => reject(cohostStageError("ROOM_JOIN_TIMEOUT", "The LiveKit room connection timed out.", "room_join", traceId)), 12000)),
        ]);
      } catch (error) {
        if (error?.error_code) throw error;
        throw cohostStageError("LIVEKIT_ROOM_JOIN_FAILED", error?.message || "LiveKit room join failed.", "room_join", traceId);
      }
      await complete("cohost_room_join_started", { stage_number: 16, stage_name: "livekit_room_joined", connection_state: String(room.state || room.connectionState || "connected"), participant_identity: room.localParticipant?.identity || tokenData.identity });
      await traceCohostStage(root, "cohost_room_joined", { stage_number: 16, stage_name: "participant_connected", result: "completed", participant_identity: room.localParticipant?.identity || tokenData.identity });

      let videoTrack;
      let audioTrack;
      try {
        await begin("cohost_camera_publish_started", { stage_number: 17, stage_name: "camera_get_user_media" });
        if (LK.createLocalVideoTrack) videoTrack = await LK.createLocalVideoTrack({ resolution: { width: 1280, height: 720 }, facingMode: "user" });
        else if (LK.createLocalTracks) videoTrack = (await LK.createLocalTracks({ video: true, audio: false })).find((track) => track.kind === "video");
        else {
          const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
          root.__pulseLiveGuestStream = stream;
          videoTrack = stream.getVideoTracks()[0];
        }
        if (!videoTrack) throw new Error("No camera track was created.");
        pendingTracks.push(videoTrack);
        await traceCohostStage(root, "cohost_camera_track_created", { stage_number: 17, stage_name: "camera_track_created", result: "completed", track_id: videoTrack.mediaStreamTrack?.id || videoTrack.sid || "" });
      } catch (error) {
        throw cohostStageError("VIDEO_TRACK_FAILED", error?.message || "The camera track could not be created.", "video_track", traceId);
      }
      let videoPublication;
      try {
        videoPublication = await room.localParticipant.publishTrack(videoTrack);
        await complete("cohost_camera_publish_started", { stage_number: 17, stage_name: "camera_track_published", publication_sid: videoPublication?.trackSid || videoPublication?.sid || "" });
        await traceCohostStage(root, "cohost_camera_publish_success", { stage_number: 17, stage_name: "camera_track_acknowledged", result: "completed", publication_sid: videoPublication?.trackSid || videoPublication?.sid || "" });
      } catch (error) {
        throw cohostStageError("VIDEO_TRACK_FAILED", error?.message || "The camera track could not be published.", "video_track", traceId);
      }

      try {
        await begin("cohost_microphone_publish_started", { stage_number: 18, stage_name: "microphone_get_user_media" });
        if (LK.createLocalAudioTrack) audioTrack = await LK.createLocalAudioTrack({ echoCancellation: true, noiseSuppression: true });
        else if (LK.createLocalTracks) audioTrack = (await LK.createLocalTracks({ video: false, audio: true })).find((track) => track.kind === "audio");
        else {
          const stream = await navigator.mediaDevices.getUserMedia({ video: false, audio: true });
          root.__pulseLiveGuestAudioStream = stream;
          audioTrack = stream.getAudioTracks()[0];
        }
        if (!audioTrack) throw new Error("No microphone track was created.");
        pendingTracks.push(audioTrack);
        await traceCohostStage(root, "cohost_microphone_track_created", { stage_number: 18, stage_name: "microphone_track_created", result: "completed", track_id: audioTrack.mediaStreamTrack?.id || audioTrack.sid || "" });
      } catch (error) {
        throw cohostStageError("AUDIO_TRACK_FAILED", error?.message || "The microphone track could not be created.", "audio_track", traceId);
      }
      let audioPublication;
      try {
        audioPublication = await room.localParticipant.publishTrack(audioTrack);
        await complete("cohost_microphone_publish_started", { stage_number: 18, stage_name: "microphone_track_published", publication_sid: audioPublication?.trackSid || audioPublication?.sid || "" });
        await traceCohostStage(root, "cohost_microphone_publish_success", { stage_number: 18, stage_name: "microphone_track_acknowledged", result: "completed", publication_sid: audioPublication?.trackSid || audioPublication?.sid || "" });
      } catch (error) {
        throw cohostStageError("AUDIO_TRACK_FAILED", error?.message || "The microphone track could not be published.", "audio_track", traceId);
      }

      root.__pulseLiveGuestRoom = room;
      root.__pulseLiveGuestTracks = [videoTrack, audioTrack];
      setText(root, "[data-live-join-status]", "Camera and microphone published. Confirming co-host with LiveKit...");
      const confirmationDeadline = Date.now() + 15000;
      let confirmation = {};
      while (Date.now() < confirmationDeadline) {
        const response = await fetch(`/api/pulse/live/${encodeURIComponent(id)}/guests/${encodeURIComponent(tokenData.guest_id)}/publish-complete`, { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trace_id: traceId, participant_identity: tokenData.identity, room_connected: true, video_publication_sid: videoPublication?.trackSid || videoPublication?.sid || "", audio_publication_sid: audioPublication?.trackSid || audioPublication?.sid || "" }) });
        confirmation = await response.json().catch(() => ({}));
        if (!response.ok && response.status !== 202) {
          const error = cohostStageError(confirmation.error_code || "COHOST_PROMOTION_FAILED", confirmation.message || "The server could not confirm the active co-host.", confirmation.step || "cohost_promotion", confirmation.trace_id || traceId);
          Object.assign(error, confirmation);
          throw error;
        }
        if (confirmation.state === "live") break;
        setText(root, "[data-live-join-status]", confirmation.message || "Waiting for LiveKit participant confirmation...");
        await new Promise((resolve) => setTimeout(resolve, Number(confirmation.retry_after_ms || 800)));
      }
      if (confirmation.state !== "live") throw cohostStageError("COHOST_PROMOTION_FAILED", `LiveKit did not confirm ${confirmation.missing_event || "participant and tracks"} before timeout.`, "cohost_promotion", traceId);
      root.__pulseLiveGuestPublished = true;
      await traceCohostStage(root, "cohost_live_success", { stage_number: 20, stage_name: "active_cohost_live", result: "completed", guest_id: tokenData.guest_id });
      setText(root, "[data-live-join-status]", "Joined as co-host. Camera and microphone are live.");
      qsa(root, "[data-live-guest-join]").forEach((button) => { button.textContent = "Joined"; button.disabled = true; });
      window.addEventListener("pagehide", () => {
        stopGuestMedia(root);
        try { room.disconnect?.(); } catch (_) {}
      }, { once: true });
    } catch (error) {
      const code = error?.error_code || "COHOST_PROMOTION_FAILED";
      await traceCohostStage(root, "cohost_failure", { stage_number: Number(error?.stage_number || 0), stage_name: error?.step || "cohost_pipeline_failure", result: "failed", error_code: code, error_message: error?.message || `${code} stopped the co-host pipeline.` });
      pendingTracks.forEach((track) => { try { track.stop?.(); } catch (_) {} try { track.mediaStreamTrack?.stop?.(); } catch (_) {} });
      try { await room?.disconnect?.(); } catch (_) {}
      stopGuestMedia(root);
      throw error;
    } finally {
      root.__pulseLiveGuestPublishing = false;
    }
  }

  async function shareLive(root) {
    const url = new URL(`/pulse/live/${encodeURIComponent(root.dataset.liveId || "")}`, window.location.origin).href;
    try {
      if (navigator.share) await navigator.share({ title: "PulseSoc Live", url });
      else {
        await navigator.clipboard?.writeText(url);
        notify("Live link copied.");
      }
    } catch (_) {
      notify("Live share was cancelled.");
    }
  }

  const rtcConfig = {
    iceServers: [
      { urls: "stun:stun.l.google.com:19302" },
      { urls: "stun:stun.cloudflare.com:3478" },
    ],
  };

  function livePeerId(role) {
    const key = `pulseLivePeer:${role}`;
    try {
      const existing = sessionStorage.getItem(key);
      if (existing) return existing;
      const next = `${role}-${Date.now().toString(36)}-${(crypto.randomUUID?.() || Math.random().toString(36).slice(2)).slice(0, 18)}`;
      sessionStorage.setItem(key, next);
      return next;
    } catch (_) {
      return `${role}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    }
  }

  function trackDiagnostics(stream) {
    const tracks = stream ? stream.getTracks() : [];
    const maskDeviceId = value => {
      const raw = String(value || "");
      return raw ? (raw.length <= 8 ? "masked" : `${raw.slice(0, 4)}...${raw.slice(-4)}`) : "";
    };
    return tracks.map((track) => ({
      kind: track.kind,
      id: track.id,
      label: track.label,
      readyState: track.readyState,
      enabled: track.enabled,
      muted: track.muted,
      settings: (() => {
        const settings = track.getSettings?.() || {};
        return {
          width: Number(settings.width || 0),
          height: Number(settings.height || 0),
          frameRate: Number(settings.frameRate || 0),
          facingMode: settings.facingMode || "",
          deviceId: maskDeviceId(settings.deviceId),
        };
      })(),
    }));
  }

  function ensureTransportDiagnostics(root) {
    let node = qs(root, "[data-live-transport-diagnostics]");
    if (!node) {
      node = document.createElement("div");
      node.className = "live-transport-diagnostics";
      node.dataset.liveTransportDiagnostics = "1";
      node.setAttribute("aria-live", "polite");
      root.appendChild(node);
    }
    return node;
  }

  function updateTransportDiagnostics(root, state) {
    if (root?.dataset?.liveDebug !== "1") return;
    const node = ensureTransportDiagnostics(root);
    const parts = [
      `connection ${state.connection || "new"}`,
      `ice ${state.ice || "new"}`,
      `stream ${state.stream || "waiting"}`,
      `audio ${state.audio ? "yes" : "no"}`,
      `video ${state.video ? "yes" : "no"}`,
      `bytes ${Math.round(Number(state.bytes || 0))}`,
      `playback ${state.playback ? "started" : "waiting"}`,
      `autoplay ${state.autoplayBlocked ? "blocked" : "ok"}`,
      `unmute ${state.unmuted ? "yes" : "no"}`,
      `audioOut ${state.audioOut ? "yes" : "waiting"}`,
    ];
    node.textContent = parts.join(" · ");
  }

  async function postSignal(root, role, peerId, eventType, payload, targetPeerId) {
    const id = root?.dataset?.liveId;
    if (!id) return null;
    const response = await fetch(`/api/pulse/live/${id}/webrtc/signal`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        role,
        peer_id: peerId,
        target_peer_id: targetPeerId || (role === "viewer" ? "publisher" : "all"),
        event_type: eventType,
        payload,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.message || "Live signal failed.");
    return data;
  }

  async function fetchSignals(root, role, peerId, afterId) {
    const id = root?.dataset?.liveId;
    if (!id) return { signals: [], last_id: afterId || 0 };
    const params = new URLSearchParams({ role, peer_id: peerId, after_id: String(afterId || 0) });
    const response = await fetch(`/api/pulse/live/${id}/webrtc/signals?${params}`, { credentials: "same-origin", cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.message || "Live signals failed.");
    return data;
  }

  async function initPublisherTransport(root, stream) {
    if (!window.RTCPeerConnection || !stream) return;
    const peerId = root.__pulseLivePublisherPeerId || (root.__pulseLivePublisherPeerId = livePeerId("publisher"));
    const sessions = root.__pulseLivePublisherSessions || (root.__pulseLivePublisherSessions = new Map());
    updateTransportDiagnostics(root, { connection: "publisher", ice: "checking", stream: "local", audio: stream.getAudioTracks().length, video: stream.getVideoTracks().length });
    console.info("PulseSoc Live publisher tracks", { live_id: root.dataset.liveId, peer_id: peerId, tracks: trackDiagnostics(stream) });

    sessions.forEach((pc) => {
      const senders = pc.getSenders();
      stream.getTracks().forEach((track) => {
        const sender = senders.find((item) => item.track?.kind === track.kind);
        if (sender) sender.replaceTrack(track).catch((error) => console.warn("PulseSoc Live replaceTrack failed", error));
        else pc.addTrack(track, stream);
      });
    });

    if (root.__pulseLivePublisherPolling) return;
    root.__pulseLivePublisherPolling = true;
    let afterId = 0;
    const makePeer = (viewerPeerId) => {
      if (sessions.has(viewerPeerId)) return sessions.get(viewerPeerId);
      const pc = new RTCPeerConnection(rtcConfig);
      stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      pc.onicecandidate = (event) => {
        if (event.candidate) postSignal(root, "publisher", peerId, "candidate", { candidate: event.candidate.toJSON() }, viewerPeerId).catch((error) => console.warn("PulseSoc Live publisher ICE signal failed", error));
      };
      pc.onconnectionstatechange = () => updateTransportDiagnostics(root, {
        connection: pc.connectionState,
        ice: pc.iceConnectionState,
        stream: "publishing",
        audio: stream.getAudioTracks().length,
        video: stream.getVideoTracks().length,
      });
      pc.oniceconnectionstatechange = pc.onconnectionstatechange;
      sessions.set(viewerPeerId, pc);
      return pc;
    };
    const poll = async () => {
      if (!root.isConnected) return;
      try {
        const data = await fetchSignals(root, "publisher", peerId, afterId);
        afterId = Number(data.last_id || afterId || 0);
        for (const signal of data.signals || []) {
          const viewerPeerId = signal.peer_id;
          if (!viewerPeerId) continue;
          const pc = makePeer(viewerPeerId);
          if (signal.event_type === "offer" && signal.payload?.sdp) {
            await pc.setRemoteDescription(new RTCSessionDescription(signal.payload.sdp));
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            await postSignal(root, "publisher", peerId, "answer", { sdp: pc.localDescription.toJSON() }, viewerPeerId);
          } else if (signal.event_type === "candidate" && signal.payload?.candidate) {
            await pc.addIceCandidate(new RTCIceCandidate(signal.payload.candidate)).catch((error) => console.warn("PulseSoc Live publisher ICE add failed", error));
          } else if (signal.event_type === "bye") {
            pc.close();
            sessions.delete(viewerPeerId);
          }
        }
      } catch (error) {
        console.warn("PulseSoc Live publisher signaling recovery", error);
      } finally {
        setTimeout(poll, document.hidden ? 2200 : 900);
      }
    };
    poll();
  }

  async function initViewerTransport(root) {
    const player = qs(root, "[data-live-player]");
    if (!player || !window.RTCPeerConnection) return;
    const playerSource = String(player.currentSrc || player.getAttribute("src") || "").toLowerCase();
    if (playerSource.includes("stream.mux.com") || playerSource.includes(".m3u8")) {
      updateTransportDiagnostics(root, {
        connection: "mux-hls",
        ice: "not-needed",
        stream: "mux hls",
        audio: "hls",
        video: "hls",
        playback: !player.paused,
      });
      return;
    }
    const peerId = root.__pulseLiveViewerPeerId || (root.__pulseLiveViewerPeerId = livePeerId("viewer"));
    if (root.__pulseLiveViewerStarted) return;
    root.__pulseLiveViewerStarted = true;
    const pc = new RTCPeerConnection(rtcConfig);
    const remoteStream = new MediaStream();
    let afterId = 0;
    let bytesReceived = 0;
    let playbackStarted = false;
    const playbackPolicy = { autoplayBlocked: false, unmuted: false, audioOut: false };
    root.__pulseLivePlaybackPolicy = playbackPolicy;
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });
    player.defaultMuted = true;
    player.muted = true;
    player.setAttribute("muted", "");
    player.playsInline = true;
    player.preload = "metadata";
    pc.ontrack = async (event) => {
      event.streams?.[0]?.getTracks().forEach((track) => {
        if (!remoteStream.getTracks().some((existing) => existing.id === track.id)) remoteStream.addTrack(track);
      });
      if (!player.srcObject) {
        player.removeAttribute("src");
        player.srcObject = remoteStream;
      }
      console.info("PulseSoc Live viewer remote stream", { live_id: root.dataset.liveId, peer_id: peerId, tracks: trackDiagnostics(remoteStream) });
      try {
        await player.play();
        playbackStarted = true;
        playbackPolicy.audioOut = remoteStream.getAudioTracks().length > 0 && !player.muted;
      } catch (error) {
        playbackPolicy.autoplayBlocked = true;
        console.warn("PulseSoc Live viewer autoplay blocked", error);
      }
      updateTransportDiagnostics(root, {
        connection: pc.connectionState,
        ice: pc.iceConnectionState,
        stream: remoteStream.active ? "remote" : "waiting",
        audio: remoteStream.getAudioTracks().length,
        video: remoteStream.getVideoTracks().length,
        bytes: bytesReceived,
        playback: playbackStarted,
        ...playbackPolicy,
      });
    };
    pc.onicecandidate = (event) => {
      if (event.candidate) postSignal(root, "viewer", peerId, "candidate", { candidate: event.candidate.toJSON() }, "publisher").catch((error) => console.warn("PulseSoc Live viewer ICE signal failed", error));
    };
    const refreshDiagnostics = async () => {
      try {
        const stats = await pc.getStats();
        let nextBytes = 0;
        stats.forEach((report) => {
          if (report.type === "inbound-rtp" && !report.isRemote) nextBytes += Number(report.bytesReceived || 0);
        });
        bytesReceived = nextBytes;
      } catch (_) {}
      updateTransportDiagnostics(root, {
        connection: pc.connectionState,
        ice: pc.iceConnectionState,
        stream: remoteStream.active ? "remote" : "waiting",
        audio: remoteStream.getAudioTracks().length,
        video: remoteStream.getVideoTracks().length,
        bytes: bytesReceived,
        playback: playbackStarted && !player.paused,
        ...playbackPolicy,
      });
    };
    pc.onconnectionstatechange = refreshDiagnostics;
    pc.oniceconnectionstatechange = refreshDiagnostics;
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await postSignal(root, "viewer", peerId, "offer", { sdp: pc.localDescription.toJSON() }, "publisher");
      console.info("PulseSoc Live viewer offer sent", { live_id: root.dataset.liveId, peer_id: peerId });
    } catch (error) {
      console.warn("PulseSoc Live viewer offer failed", error);
      updateTransportDiagnostics(root, { connection: "failed", ice: pc.iceConnectionState, stream: "offer failed", audio: 0, video: 0 });
      return;
    }
    const poll = async () => {
      if (!root.isConnected) return;
      try {
        const data = await fetchSignals(root, "viewer", peerId, afterId);
        afterId = Number(data.last_id || afterId || 0);
        for (const signal of data.signals || []) {
          if (signal.event_type === "answer" && signal.payload?.sdp && !pc.currentRemoteDescription) {
            await pc.setRemoteDescription(new RTCSessionDescription(signal.payload.sdp));
          } else if (signal.event_type === "candidate" && signal.payload?.candidate) {
            await pc.addIceCandidate(new RTCIceCandidate(signal.payload.candidate)).catch((error) => console.warn("PulseSoc Live viewer ICE add failed", error));
          }
        }
      } catch (error) {
        console.warn("PulseSoc Live viewer signaling recovery", error);
      } finally {
        refreshDiagnostics();
        setTimeout(poll, document.hidden ? 2200 : 900);
      }
    };
    poll();
    setInterval(() => {
      if (!document.hidden) refreshDiagnostics();
    }, 15000);
    window.addEventListener("pagehide", () => {
      postSignal(root, "viewer", peerId, "bye", {}, "publisher").catch(() => {});
      pc.close();
    }, { once: true });
  }

  function bootCamera(root) {
    const video = qs(root, "[data-live-camera]");
    if (!video || !navigator.mediaDevices?.getUserMedia) return;
    video.defaultMuted = true;
    video.muted = true;
    video.volume = 0;
    video.playsInline = true;
    video.setAttribute("muted", "");
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    let stream = null;
    let livekitRoom = null;
    let livekitTracks = [];
    let livekitConnectPromise = null;
    let livekitPublishPromise = null;
    let livekitPublishComplete = false;
    let livekitPublishedTrackIds = new Set();
    let cameraStartPromise = null;
    let screenStartPromise = null;
    let currentPublishKind = "";
    let liveHealthManager = null;
    let videoRecoveryPromise = null;
    const liveDebugSessionId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
    const LIVE_HOST_PUBLISHER = "LiveHostPublisher";
    function livekitClient() {
      return window.LivekitClient || window.LiveKitClient || window.livekitClient || null;
    }
    function liveStartMessage(error) {
      const name = error?.name || "";
      const message = error?.message || "Browser Live could not start.";
      if (name === "NotAllowedError" || name === "SecurityError") return "Camera/microphone permission is blocked in Chrome. Click the site controls icon in the address bar, allow camera and microphone, then reload.";
      if (name === "NotFoundError" || name === "OverconstrainedError") return "No available camera or microphone matched this device.";
      if (error?.pulseStage === "livekit-connect") return `LiveKit connection failed: ${message}`;
      if (error?.pulseStage === "livekit-publish") return `LiveKit track publish failed: ${message}`;
      if (error?.pulseStage === "media-capture") return `Camera/microphone capture failed: ${message}`;
      return message;
    }
    function mediaTrackOf(track) {
      return track?.mediaStreamTrack || track?.track || (track && typeof track.kind === "string" && typeof track.stop === "function" ? track : null);
    }
    function localTrackKind(track) {
      return String(track?.kind || mediaTrackOf(track)?.kind || "").toLowerCase();
    }
    function trackStableId(track) {
      const mediaTrack = mediaTrackOf(track);
      return mediaTrack?.id || track?.sid || track?.trackSid || "";
    }
    function tracksToMediaStream(tracks) {
      const mediaStream = new MediaStream();
      tracks.forEach((track) => {
        const mediaTrack = mediaTrackOf(track);
        if (mediaTrack) mediaStream.addTrack(mediaTrack);
      });
      return mediaStream;
    }
    function cameraVideoProfiles() {
      return [
        { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 } },
        { facingMode: "user", width: { ideal: 960 }, height: { ideal: 540 }, frameRate: { ideal: 24, max: 30 } },
        { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 20, max: 24 } },
      ];
    }
    function livekitRoomState(room) {
      return String(room?.state || room?.connectionState || "").toLowerCase();
    }
    function isLiveKitConnected(room) {
      const state = livekitRoomState(room);
      return Boolean(room?.isConnected || state === "connected");
    }
    function livekitLog(event, details = {}) {
      const payload = {
        event,
        live_id: root?.dataset?.liveId || "",
        room: root?.dataset?.livekitRoom || "",
        session_id: liveDebugSessionId,
        owner: root?.dataset?.liveCameraOwner || "",
        device: navigator.userAgentData?.platform || navigator.platform || "",
        browser: navigator.userAgent || "",
        timestamp: new Date().toISOString(),
        ...details,
      };
      console.info("PulseSoc LiveKit", payload);
      sendLiveDebug(event, payload);
    }
    function liveDebugEventName(event, details = {}) {
      const kind = String(details.kind || details.track_kind || details.trackKind || "").toLowerCase();
      if (event === "connected" || event === "connected_ready") return "room_connected";
      if (event === "connecting") return "room_connecting";
      if (event === "disconnected") return "room_disconnected";
      if (event === "reconnecting") return "room_reconnecting";
      if (event === "reconnected") return "room_reconnected";
      if (event === "participant_connected") return "participant_joined";
      if (event === "participant_disconnected") return "participant_disconnected";
      if (event === "local_track_published" || event === "publish_track_resolved") return kind === "audio" ? "audio_track_published" : kind === "video" ? "video_track_published" : "";
      if (event === "local_track_unpublished") return kind === "audio" ? "audio_track_unpublished" : kind === "video" ? "video_track_unpublished" : "";
      if (event === "track_ended") return "track_ended";
      if (event === "track_muted") return "track_muted";
      if (event === "track_unmuted") return "track_unmuted";
      if (event === "cleanup_started") return "publisher_cleanup_started";
      if (event === "cleanup_completed") return "publisher_cleanup_completed";
      if (event === "publish_request_started") return "publish_request_started";
      if (event === "publish_request_acknowledged") return "publish_request_acknowledged";
      if (event === "backend_waiting_for_tracks") return "backend_waiting_for_tracks";
      if (event === "egress_start_response") return "egress_start_response";
      if ([
        "live_camera_started",
        "live_video_frame_seen",
        "live_video_freeze_detected",
        "live_video_recovery_started",
        "live_video_track_reacquired",
        "live_video_republished",
        "live_video_recovery_success",
        "live_video_recovery_failed",
        "live_room_reconnect_started",
        "live_room_reconnect_success",
      ].includes(event)) return event;
      return "";
    }
    function sendLiveDebug(event, details = {}) {
      const id = root?.dataset?.liveId;
      const mapped = liveDebugEventName(event, details);
      if (!id || !mapped) return;
      try {
        fetch(`/api/pulse/live/${id}/debug-event`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ event: mapped, details }),
        }).catch(() => {});
      } catch (_) {}
    }
    function wireLocalTrackDiagnostics(track) {
      const mediaTrack = mediaTrackOf(track);
      if (!mediaTrack || mediaTrack.datasetPulseDiagnostics === "1") return;
      mediaTrack.datasetPulseDiagnostics = "1";
      const kind = localTrackKind(track);
      const trackId = mediaTrack.id || track?.sid || "";
      mediaTrack.addEventListener?.("ended", () => {
        livekitLog("track_ended", { kind, track_id: trackId, ready_state: mediaTrack.readyState || "" });
        if (kind === "video") liveHealthManager?.trackProblem("track_ended", track);
      });
      mediaTrack.addEventListener?.("mute", () => {
        livekitLog("track_muted", { kind, track_id: trackId, ready_state: mediaTrack.readyState || "" });
        if (kind === "video") liveHealthManager?.trackProblem("track_muted", track);
      });
      mediaTrack.addEventListener?.("unmute", () => {
        livekitLog("track_unmuted", { kind, track_id: trackId, ready_state: mediaTrack.readyState || "" });
        if (kind === "video") liveHealthManager?.trackProblem("track_unmuted", track);
      });
    }
    function stopLocalTrack(track) {
      const mediaTrack = mediaTrackOf(track);
      try { track?.detach?.().forEach((element) => element.remove?.()); } catch (_) {}
      try { track?.stop?.(); } catch (_) {}
      try { mediaTrack?.stop?.(); } catch (_) {}
    }
    function attachPreviewFromTracks(tracks) {
      const videoTrack = tracks.find((track) => localTrackKind(track) === "video");
      if (videoTrack?.attach) {
        try {
          videoTrack.attach(video);
        } catch (_) {
          video.srcObject = stream;
        }
      } else {
        video.srcObject = stream;
      }
      video.muted = true;
      video.defaultMuted = true;
      video.volume = 0;
      video.playsInline = true;
      video.setAttribute("muted", "");
      video.setAttribute("playsinline", "");
      video.setAttribute("webkit-playsinline", "");
      try { video.play?.().catch?.(() => {}); } catch (_) {}
    }
    function pauseCompetingLiveMedia() {
      qsa(document, "video").forEach((node) => {
        if (node === video || root.contains(node)) return;
        try { node.pause?.(); } catch (_) {}
        const mediaStream = node.srcObject;
        if (mediaStream?.getTracks && node.dataset.liveCamera !== "1") {
          mediaStream.getVideoTracks?.().forEach((track) => {
            if (track.readyState === "live" && track.label && /camera|front|back|webcam/i.test(track.label)) {
              try { track.stop(); } catch (_) {}
            }
          });
        }
      });
    }
    function claimLiveHostPublisher() {
      const existing = window.__PulseSocLiveHostPublisher;
      if (existing?.root && existing.root !== root) {
        try { existing.stop?.("camera_owner_replaced_by_live_host_publisher"); } catch (_) {}
      }
      window.__PulseSocLiveHostPublisher = { name: LIVE_HOST_PUBLISHER, root, stop: (reason) => stop(reason) };
      root.dataset.liveCameraOwner = LIVE_HOST_PUBLISHER;
    }
    function releaseLiveHostPublisher() {
      if (window.__PulseSocLiveHostPublisher?.root === root) {
        delete window.__PulseSocLiveHostPublisher;
      }
      delete root.dataset.liveCameraOwner;
    }
    async function collectVideoSenderStats() {
      const stats = { fps: 0, bitrate: 0, framesEncoded: null, bytesSent: null, source: "" };
      try {
        for (const publication of localPublications(livekitRoom)) {
          if (publicationKind(publication) !== "video") continue;
          const sender = publication?.track?.sender || publication?.sender || publication?.track?.processorSender;
          if (!sender?.getStats) continue;
          const reports = await sender.getStats();
          reports.forEach((report) => {
            if (report.type === "outbound-rtp" && !report.isRemote && (report.kind === "video" || report.mediaType === "video")) {
              stats.framesEncoded = Number(report.framesEncoded ?? report.framesSent ?? stats.framesEncoded ?? 0);
              stats.bytesSent = Number(report.bytesSent ?? stats.bytesSent ?? 0);
              stats.fps = Number(report.framesPerSecond || report.frameRate || stats.fps || 0);
              stats.source = "sender.getStats";
            }
          });
        }
      } catch (_) {}
      return stats;
    }
    function createLiveHealthManager() {
      const freezeThresholdMs = 4500;
      const checkIntervalMs = 1000;
      let timer = 0;
      let frameCallbackActive = false;
      let lastFrameAt = 0;
      let lastFrameCount = 0;
      let lastFrameLogAt = 0;
      let lastStats = { framesEncoded: null, bytesSent: null, sampledAt: 0 };
      const lifecycleEvents = ["visibilitychange", "pageshow", "focus", "orientationchange", "resize"];
      const stateDetails = (reason = "") => {
        const videoTrack = stream?.getVideoTracks?.()[0] || null;
        const audioTrack = stream?.getAudioTracks?.()[0] || null;
        return {
          reason,
          track_state: videoTrack?.readyState || "missing",
          track_muted: Boolean(videoTrack?.muted),
          track_enabled: Boolean(videoTrack?.enabled),
          audio_state: audioTrack?.readyState || "missing",
          audio_enabled: Boolean(audioTrack?.enabled),
          room_state: livekitRoomState(livekitRoom),
          fps: Number(root.dataset.liveVideoFps || 0),
          bitrate: Number(root.dataset.liveVideoBitrate || 0),
          last_frame_age_ms: lastFrameAt ? Date.now() - lastFrameAt : null,
          published_video_tracks: Number(root.dataset.livekitPublishedVideo || 0),
          published_audio_tracks: Number(root.dataset.livekitPublishedAudio || 0),
        };
      };
      const noteFrame = (metadata = {}) => {
        lastFrameAt = Date.now();
        lastFrameCount = Number(metadata.presentedFrames || lastFrameCount + 1);
        if (lastFrameAt - lastFrameLogAt > 15000) {
          lastFrameLogAt = lastFrameAt;
          livekitLog("live_video_frame_seen", { ...stateDetails("requestVideoFrameCallback"), presented_frames: lastFrameCount });
        }
      };
      const scheduleFrameCallback = () => {
        if (!video.requestVideoFrameCallback || frameCallbackActive) return;
        frameCallbackActive = true;
        const onFrame = (_now, metadata) => {
          if (!timer) {
            frameCallbackActive = false;
            return;
          }
          noteFrame(metadata || {});
          try {
            video.requestVideoFrameCallback(onFrame);
          } catch (_) {
            frameCallbackActive = false;
          }
        };
        try {
          video.requestVideoFrameCallback(onFrame);
        } catch (_) {
          frameCallbackActive = false;
        }
      };
      const check = async (reason = "interval") => {
        if (!timer || document.hidden || currentPublishKind !== "browser_camera" || !stream) return;
        const videoTrack = stream.getVideoTracks().find((track) => track.readyState === "live") || stream.getVideoTracks()[0];
        const audioHealthy = stream.getAudioTracks().some((track) => track.readyState === "live" && track.enabled);
        const roomConnected = isLiveKitConnected(livekitRoom);
        const stats = await collectVideoSenderStats();
        if (stats.framesEncoded !== null) {
          if (lastStats.framesEncoded !== null && stats.framesEncoded > lastStats.framesEncoded) {
            noteFrame({ presentedFrames: stats.framesEncoded });
          }
          if (lastStats.bytesSent !== null && stats.bytesSent !== null && stats.bytesSent >= lastStats.bytesSent) {
            const elapsed = Math.max(1, Date.now() - (lastStats.sampledAt || Date.now()));
            root.dataset.liveVideoBitrate = String(Math.round(((stats.bytesSent - lastStats.bytesSent) * 8 * 1000) / elapsed));
          }
          if (stats.fps) root.dataset.liveVideoFps = String(Math.round(stats.fps));
          lastStats = { framesEncoded: stats.framesEncoded, bytesSent: stats.bytesSent, sampledAt: Date.now() };
        }
        if (!lastFrameAt && video.readyState >= 2) lastFrameAt = Date.now();
        const frameAge = lastFrameAt ? Date.now() - lastFrameAt : Number.POSITIVE_INFINITY;
        const trackBroken = !videoTrack || videoTrack.readyState !== "live" || videoTrack.muted || videoTrack.enabled === false;
        const frozen = roomConnected && audioHealthy && (trackBroken || frameAge > freezeThresholdMs);
        if (!frozen) return;
        livekitLog("live_video_freeze_detected", { ...stateDetails(reason), frame_age_ms: frameAge, track_broken: trackBroken });
        await recoverFrozenVideo(reason, stateDetails(reason));
      };
      const onLifecycle = (event) => {
        if (document.hidden) return;
        showCameraState(root, "Checking camera...", "status");
        setTimeout(() => check(event.type || "mobile_resume"), 350);
        setTimeout(() => check(`${event.type || "mobile_resume"}_settled`), 1600);
      };
      return {
        start(reason = "start") {
          this.stop("restart");
          lastFrameAt = Date.now();
          lastStats = { framesEncoded: null, bytesSent: null, sampledAt: 0 };
          scheduleFrameCallback();
          timer = window.setInterval(() => { check("interval").catch(() => {}); }, checkIntervalMs);
          lifecycleEvents.forEach((eventName) => window.addEventListener(eventName, onLifecycle, { passive: true }));
          livekitLog("live_camera_started", { ...stateDetails(reason), owner: LIVE_HOST_PUBLISHER, supports_request_video_frame_callback: Boolean(video.requestVideoFrameCallback), supports_get_stats: true });
        },
        stop() {
          if (timer) clearInterval(timer);
          timer = 0;
          frameCallbackActive = false;
          lifecycleEvents.forEach((eventName) => window.removeEventListener(eventName, onLifecycle));
        },
        checkNow(reason = "manual_check") {
          return check(reason);
        },
        trackProblem(reason, track) {
          livekitLog(reason === "track_muted" ? "track_muted" : reason === "track_unmuted" ? "track_unmuted" : "track_ended", {
            ...stateDetails(reason),
            kind: localTrackKind(track),
            track_id: trackStableId(track),
          });
          setTimeout(() => check(reason).catch(() => {}), 400);
        },
      };
    }
    function wait(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }
    async function waitForLiveKitConnected(room, timeoutMs = 12000) {
      if (isLiveKitConnected(room)) return room;
      const LK = livekitClient();
      await new Promise((resolve, reject) => {
        let done = false;
        const started = Date.now();
        const cleanup = () => {
          clearInterval(poll);
          clearTimeout(timer);
          try { room?.off?.(LK?.RoomEvent?.Connected, onConnected); } catch (_) {}
          try { room?.off?.(LK?.RoomEvent?.ConnectionStateChanged, onState); } catch (_) {}
        };
        const finish = (error) => {
          if (done) return;
          done = true;
          cleanup();
          if (error) reject(error);
          else resolve();
        };
        const onConnected = () => finish();
        const onState = (state) => {
          livekitLog("connection_state", { state: String(state || livekitRoomState(room)) });
          if (isLiveKitConnected(room)) finish();
        };
        const poll = setInterval(() => {
          if (isLiveKitConnected(room)) finish();
          else if (Date.now() - started > timeoutMs) finish(new Error("LiveKit room did not reach connected state before publishing."));
        }, 250);
        const timer = setTimeout(() => finish(new Error("LiveKit room did not reach connected state before publishing.")), timeoutMs + 250);
        try { room?.on?.(LK?.RoomEvent?.Connected, onConnected); } catch (_) {}
        try { room?.on?.(LK?.RoomEvent?.ConnectionStateChanged, onState); } catch (_) {}
      });
      return room;
    }
    function localPublications(room) {
      const publications = room?.localParticipant?.trackPublications;
      if (!publications) return [];
      if (typeof publications.values === "function") return Array.from(publications.values());
      return Array.isArray(publications) ? publications : Object.values(publications);
    }
    function publicationKind(publication) {
      return String(publication?.kind || publication?.track?.kind || publication?.source || "").toLowerCase();
    }
    function setConnectButtonsBusy(busy) {
      qsa(root, "[data-live-start-camera], [data-live-screen]").forEach((button) => {
        button.disabled = Boolean(busy);
        button.setAttribute("aria-busy", busy ? "true" : "false");
      });
    }
    async function unpublishLocalTracks(room) {
      const participant = room?.localParticipant;
      if (!participant?.unpublishTrack) return;
      for (const publication of localPublications(room)) {
        const track = publication?.track;
        if (!track) continue;
        try {
          await participant.unpublishTrack(track, true);
          livekitLog("local_track_unpublished", { kind: publicationKind(publication), sid: publication?.trackSid || publication?.sid || "" });
        } catch (error) {
          console.warn("PulseSoc LiveKit unpublishTrack recovery", { kind: publicationKind(publication), message: error?.message || String(error) });
        }
      }
    }
    async function unpublishLocalTracksByKind(room, kind) {
      const participant = room?.localParticipant;
      if (!participant?.unpublishTrack) return;
      for (const publication of localPublications(room)) {
        if (publicationKind(publication) !== kind) continue;
        const track = publication?.track;
        if (!track) continue;
        try {
          await participant.unpublishTrack(track, true);
          livekitLog("local_track_unpublished", { kind, sid: publication?.trackSid || publication?.sid || "", reason: "video_recovery" });
        } catch (error) {
          console.warn("PulseSoc LiveKit video unpublish recovery", { kind, message: error?.message || String(error) });
        }
      }
    }
    function detachLiveKitTracks() {
      livekitTracks.forEach((track) => {
        stopLocalTrack(track);
      });
      livekitTracks = [];
      livekitPublishedTrackIds = new Set();
      livekitPublishComplete = false;
    }
    async function cleanupPublisher({ disconnect = false, reason = "unspecified" } = {}) {
      liveHealthManager?.stop(reason);
      livekitLog("cleanup_started", { reason, disconnect, local_track_count: livekitTracks.length, stream_track_count: stream?.getTracks?.().length || 0 });
      await unpublishLocalTracks(livekitRoom);
      detachLiveKitTracks();
      if (stream) stream.getTracks().forEach((track) => track.stop());
      stream = null;
      video.srcObject = null;
      video.defaultMuted = true;
      video.muted = true;
      video.volume = 0;
      if (disconnect) {
        try { await livekitRoom?.disconnect?.(); } catch (_) {}
        livekitRoom = null;
        livekitConnectPromise = null;
      }
      setCameraSurfaceActive(root, false);
      releaseLiveHostPublisher();
      livekitLog("cleanup_completed", { reason, disconnect });
    }
    async function connectLiveKitRoom() {
      const LK = livekitClient();
      const id = root?.dataset?.liveId;
      if (!LK) throw new Error("LiveKit browser client is still loading. Try Start Camera again in a moment.");
      if (!id) throw new Error("Live session is missing.");
      if (isLiveKitConnected(livekitRoom)) return livekitRoom;
      if (livekitConnectPromise) return livekitConnectPromise;
      livekitConnectPromise = (async () => {
        const tokenResponse = await fetch(`/api/pulse/live/${id}/livekit/token`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: "publisher" }),
        });
        const tokenData = await tokenResponse.json().catch(() => ({}));
        if (!tokenResponse.ok || tokenData.ok === false) throw new Error(tokenData.message || "LiveKit host token could not be created.");
        const room = livekitRoom || new LK.Room({ adaptiveStream: true, dynacast: true });
        livekitRoom = room;
        if (!room.datasetPulseHandlers) {
          room.datasetPulseHandlers = "1";
          try { room.on?.(LK.RoomEvent?.Connected, () => livekitLog("connected", { state: livekitRoomState(room) })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.Disconnected, () => livekitLog("disconnected", { state: livekitRoomState(room) })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.ParticipantConnected, (participant) => livekitLog("participant_connected", { identity: participant?.identity || "" })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.ParticipantDisconnected, (participant) => livekitLog("participant_disconnected", { identity: participant?.identity || "" })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.Reconnecting, () => livekitLog("reconnecting", { state: livekitRoomState(room) })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.Reconnected, () => livekitLog("reconnected", { state: livekitRoomState(room) })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.LocalTrackPublished, (publication) => livekitLog("local_track_published", { kind: publication?.kind || publication?.track?.kind || "", sid: publication?.trackSid || publication?.sid || "" })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.LocalTrackUnpublished, (publication) => livekitLog("local_track_unpublished", { kind: publication?.kind || publication?.track?.kind || "", sid: publication?.trackSid || publication?.sid || "" })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.TrackMuted, (publication) => livekitLog("track_muted", { kind: publication?.kind || publication?.track?.kind || "", sid: publication?.trackSid || publication?.sid || "" })); } catch (_) {}
          try { room.on?.(LK.RoomEvent?.TrackUnmuted, (publication) => livekitLog("track_unmuted", { kind: publication?.kind || publication?.track?.kind || "", sid: publication?.trackSid || publication?.sid || "" })); } catch (_) {}
        }
        livekitLog("connecting", { configured: true });
        await room.connect(tokenData.livekit_url, tokenData.token);
        root.dataset.livekitRoom = tokenData.room || "";
        await waitForLiveKitConnected(room);
        livekitLog("connected_ready", { state: livekitRoomState(room) });
        return room;
      })();
      try {
        return await livekitConnectPromise;
      } catch (error) {
        livekitConnectPromise = null;
        throw error;
      }
    }
    async function publishToLiveKit(kind) {
      const LK = livekitClient();
      if (!LK) throw new Error("LiveKit browser client is still loading. Try Start Camera again in a moment.");
      if (livekitPublishPromise) return livekitPublishPromise;
      if (livekitPublishComplete && stream && isLiveKitConnected(livekitRoom)) return stream;
      livekitPublishPromise = (async () => {
      await cleanupPublisher({ disconnect: false, reason: "replace_local_tracks_before_publish" });
      showCameraState(root, kind === "screen_share" ? "Requesting screen share permission..." : "Requesting camera/microphone permission...");
      let trackOptions;
      try {
        if (kind === "screen_share") {
          trackOptions = await LK.createLocalScreenTracks({ audio: true, video: true });
        } else {
          const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
          const videoProfiles = cameraVideoProfiles();
          let lastCaptureError = null;
          for (const videoConstraints of videoProfiles) {
            try {
              trackOptions = await LK.createLocalTracks({ audio, video: videoConstraints });
              break;
            } catch (profileError) {
              lastCaptureError = profileError;
              trackOptions = null;
            }
          }
          if (!trackOptions) throw lastCaptureError || new Error("Camera capture failed");
        }
      } catch (error) {
        error.pulseStage = "media-capture";
        throw error;
      }
      livekitTracks = trackOptions;
      livekitTracks.forEach(wireLocalTrackDiagnostics);
      stream = tracksToMediaStream(livekitTracks);
      attachPreviewFromTracks(livekitTracks);
      setCameraSurfaceActive(root, true);
      showCameraState(root, "Camera ready. Connecting to LiveKit...");
      let room;
      try {
        room = await connectLiveKitRoom();
        await waitForLiveKitConnected(room);
      } catch (error) {
        error.pulseStage = "livekit-connect";
        throw error;
      }
      showCameraState(root, "Publishing camera and microphone to LiveKit...");
      try {
        const publications = [];
        for (const track of livekitTracks) {
          const mediaTrack = mediaTrackOf(track);
          const trackId = trackStableId(track) || `${localTrackKind(track) || "track"}-${publications.length}`;
          if (livekitPublishedTrackIds.has(trackId)) continue;
          const kindName = localTrackKind(track);
          const existingPublication = localPublications(room).find((publication) => {
            const existingTrack = publication?.track;
            return publicationKind(publication) === kindName && existingTrack && existingTrack.mediaStreamTrack?.readyState !== "ended";
          });
          if (existingPublication) {
            try { track.stop?.(); } catch (_) {}
            livekitLog("duplicate_track_skipped", { kind: kindName, sid: existingPublication?.trackSid || existingPublication?.sid || "" });
            continue;
          }
          const publication = await room.localParticipant.publishTrack(track);
          livekitPublishedTrackIds.add(trackId);
          publications.push(publication);
          livekitLog("publish_track_resolved", { kind: track?.kind || mediaTrack?.kind || "", track_id: trackId, sid: publication?.trackSid || publication?.sid || "" });
        }
        if (!publications.length && !livekitPublishedTrackIds.size) throw new Error("LiveKit did not publish any local tracks.");
        livekitPublishComplete = true;
        root.dataset.livekitPublishedAudio = String(stream.getAudioTracks().filter((track) => track.readyState === "live").length);
        root.dataset.livekitPublishedVideo = String(stream.getVideoTracks().filter((track) => track.readyState === "live").length);
      } catch (error) {
        console.warn("PulseSoc LiveKit publishTrack failed", error);
        error.pulseStage = "livekit-publish";
        throw error;
      }
      return stream;
      })();
      try {
        return await livekitPublishPromise;
      } finally {
        livekitPublishPromise = null;
      }
    }
    async function publishTracks(kind) {
      const id = root?.dataset?.liveId;
      if (!id || !stream) return;
      const audioTracks = stream.getAudioTracks().filter((track) => track.readyState === "live").length;
      const videoTracks = stream.getVideoTracks().filter((track) => track.readyState === "live").length;
      try {
        let publishData = null;
        for (let attempt = 1; attempt <= 8; attempt += 1) {
          livekitLog("publish_request_started", { attempt, kind, audio_tracks: audioTracks, video_tracks: videoTracks, room_state: livekitRoomState(livekitRoom) });
          const response = await fetch(`/api/pulse/live/${id}/browser-publish`, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: kind || "browser_camera",
              audio_tracks: audioTracks,
              video_tracks: videoTracks,
              muted: !stream.getAudioTracks().some((track) => track.enabled),
              livekit_room_state: livekitRoomState(livekitRoom),
              livekit_publish_complete: livekitPublishComplete,
              livekit_published_track_count: livekitPublishedTrackIds.size,
              published_track_ids: Array.from(livekitPublishedTrackIds).slice(0, 8),
            }),
          });
          const data = await response.json().catch(() => ({}));
          if (response.ok && data.ok !== false) {
            publishData = data;
            livekitLog("publish_request_acknowledged", { attempt, publish_path: data.publish_path || "", mux_waiting: Boolean(data.mux_waiting), egress_id: data.egress?.egress_id || "", egress_status: data.egress?.status || "", egress_strategy: data.egress?.strategy || "" });
            livekitLog("egress_start_response", { attempt, ok: Boolean(data.egress?.ok), egress_id: data.egress?.egress_id || "", egress_status: data.egress?.status || "", egress_strategy: data.egress?.strategy || "", mux_waiting: Boolean(data.mux_waiting) });
            break;
          }
          if (response.status === 409 && data.retryable) {
            livekitLog("backend_waiting_for_tracks", { attempt, retry_after_ms: Number(data.retry_after_ms || 1500), host_joined: Boolean(data.livekit?.host_joined), video_tracks: Number(data.video_tracks || 0) });
            showCameraState(root, data.message || "Waiting for LiveKit track confirmation...");
            await wait(Math.max(600, Number(data.retry_after_ms || 1500)));
            continue;
          }
          throw new Error(data.message || "Live media publish failed.");
        }
        if (!publishData) throw new Error("LiveKit tracks did not become ready for Mux egress. Try Start Camera again.");
        if (publishData.publish_path === "livekit_direct") {
          notify(publishData.message || "LiveKit direct mode is active. Mux replay will resume when egress minutes are available.");
        } else if (publishData.mux_waiting) {
          notify(publishData.message || "LiveKit egress started. Waiting for Mux to become active before public playback opens.");
        }
        console.info("PulseSoc Live publisher publish acknowledged", { live_id: id, tracks: trackDiagnostics(stream) });
        if (audioTracks || videoTracks) {
          setCameraSurfaceActive(root, true);
          showCameraState(root, "");
        } else {
          showCameraState(root, "No tracks detected", "error");
        }
        await fetchState(root);
      } catch (error) {
        notify(error.message);
      }
    }
    async function createVideoOnlyTracks() {
      const LK = livekitClient();
      let lastCaptureError = null;
      for (const videoConstraints of cameraVideoProfiles()) {
        try {
          if (LK?.createLocalTracks) {
            const tracks = await LK.createLocalTracks({ audio: false, video: videoConstraints });
            const videoTracks = tracks.filter((track) => localTrackKind(track) === "video");
            if (videoTracks.length) return videoTracks;
          }
          const nextStream = await navigator.mediaDevices.getUserMedia({ audio: false, video: videoConstraints });
          const mediaTracks = nextStream.getVideoTracks();
          if (mediaTracks.length) return mediaTracks;
        } catch (error) {
          lastCaptureError = error;
        }
      }
      throw lastCaptureError || new Error("Camera video track could not be reacquired.");
    }
    function rebuildStreamWithVideo(videoTracks) {
      const audioTracks = stream?.getAudioTracks?.().filter((track) => track.readyState === "live") || [];
      const oldVideoTracks = livekitTracks.filter((track) => localTrackKind(track) === "video");
      const oldVideoIds = new Set(oldVideoTracks.map(trackStableId).filter(Boolean));
      oldVideoTracks.forEach(stopLocalTrack);
      livekitPublishedTrackIds = new Set(Array.from(livekitPublishedTrackIds).filter((id) => !oldVideoIds.has(id)));
      livekitTracks = livekitTracks.filter((track) => localTrackKind(track) !== "video").concat(videoTracks);
      livekitTracks.forEach(wireLocalTrackDiagnostics);
      const nextStream = new MediaStream();
      audioTracks.forEach((track) => nextStream.addTrack(track));
      videoTracks.forEach((track) => {
        const mediaTrack = mediaTrackOf(track);
        if (mediaTrack) nextStream.addTrack(mediaTrack);
      });
      stream = nextStream;
      attachPreviewFromTracks(videoTracks);
      return stream;
    }
    async function republishVideoTracks(videoTracks, reason) {
      const room = await connectLiveKitRoom();
      await waitForLiveKitConnected(room);
      await unpublishLocalTracksByKind(room, "video");
      for (const track of videoTracks) {
        const publication = await room.localParticipant.publishTrack(track);
        const mediaTrack = mediaTrackOf(track);
        livekitPublishedTrackIds.add(trackStableId(track) || publication?.trackSid || publication?.sid || "");
        livekitLog("live_video_republished", {
          reason,
          track_id: mediaTrack?.id || "",
          sid: publication?.trackSid || publication?.sid || "",
          track_state: mediaTrack?.readyState || "",
        });
      }
      livekitPublishComplete = true;
      root.dataset.livekitPublishedAudio = String(stream.getAudioTracks().filter((track) => track.readyState === "live").length);
      root.dataset.livekitPublishedVideo = String(stream.getVideoTracks().filter((track) => track.readyState === "live").length);
    }
    async function recoverFrozenVideo(reason = "freeze_detected", snapshot = {}) {
      if (videoRecoveryPromise) return videoRecoveryPromise;
      if (currentPublishKind !== "browser_camera") return null;
      videoRecoveryPromise = (async () => {
        showCameraState(root, "Video recovering...", "status");
        livekitLog("live_video_recovery_started", { reason, ...snapshot });
        try {
          try { video.pause?.(); } catch (_) {}
          video.srcObject = null;
          const videoTracks = await createVideoOnlyTracks();
          livekitLog("live_video_track_reacquired", { reason, tracks: trackDiagnostics(tracksToMediaStream(videoTracks)) });
          rebuildStreamWithVideo(videoTracks);
          await republishVideoTracks(videoTracks, reason);
          await publishTracks("browser_camera_recovery");
          liveHealthManager?.start("video_recovery_success");
          showCameraState(root, "Video recovered", "status");
          notify("Video recovered");
          livekitLog("live_video_recovery_success", { reason, tracks: trackDiagnostics(stream) });
          return stream;
        } catch (error) {
          livekitLog("live_video_recovery_failed", { reason, message: error?.message || String(error), ...snapshot });
          try {
            showCameraState(root, "Video recovery needs reconnect...", "status");
            livekitLog("live_room_reconnect_started", { reason, message: error?.message || String(error) });
            await cleanupPublisher({ disconnect: true, reason: "video_recovery_reconnect" });
            claimLiveHostPublisher();
            currentPublishKind = "browser_camera";
            stream = await publishToLiveKit("browser_camera");
            await publishTracks("browser_camera_reconnect");
            liveHealthManager?.start("room_reconnect_success");
            livekitLog("live_room_reconnect_success", { reason, tracks: trackDiagnostics(stream) });
            showCameraState(root, "Video recovered", "status");
            return stream;
          } catch (reconnectError) {
            const message = liveStartMessage(reconnectError);
            showCameraState(root, "Camera permission lost. Tap to retry camera.", "error");
            notify(message || "Camera recovery failed. Tap Camera to retry.");
            livekitLog("live_video_recovery_failed", { reason: "room_reconnect_failed", message: reconnectError?.message || String(reconnectError) });
            throw reconnectError;
          }
        } finally {
          videoRecoveryPromise = null;
        }
      })();
      return videoRecoveryPromise;
    }
    const stop = (reason = "manual_stop") => cleanupPublisher({ disconnect: true, reason });
    const start = async () => {
      if (cameraStartPromise) return cameraStartPromise;
      cameraStartPromise = (async () => {
      try {
        setConnectButtonsBusy(true);
        claimLiveHostPublisher();
        pauseCompetingLiveMedia();
        await stop("start_camera_restart");
        claimLiveHostPublisher();
        root.classList.add("is-connecting");
        showCameraState(root, "Connecting Browser Live to LiveKit...");
        currentPublishKind = "browser_camera";
        stream = await publishToLiveKit("browser_camera");
        setCameraSurfaceActive(root, true);
        showCameraState(root, "");
        console.info("PulseSoc Live publisher local stream", { live_id: root.dataset.liveId, tracks: trackDiagnostics(stream) });
        await publishTracks("browser_camera");
        liveHealthManager = createLiveHealthManager();
        liveHealthManager.start("browser_camera_started");
      } catch (error) {
        const message = liveStartMessage(error);
        liveHealthManager?.stop("start_failed");
        setCameraSurfaceActive(root, false);
        showCameraState(root, message, "error");
        console.warn("PulseSoc Browser Live start failed", { stage: error?.pulseStage || error?.name || "unknown", message: error?.message || String(error) });
        notify(message);
      } finally {
        root.classList.remove("is-connecting");
        setConnectButtonsBusy(false);
      }
      })();
      try {
        return await cameraStartPromise;
      } finally {
        cameraStartPromise = null;
      }
    };
    qsa(root, "[data-live-start-camera]").forEach((button) => button.addEventListener("click", start));
    qs(root, "[data-live-mute]")?.addEventListener("click", (event) => {
      const currentlyEnabled = stream?.getAudioTracks().some((track) => track.enabled) !== false;
      const nextEnabled = !currentlyEnabled;
      stream?.getAudioTracks().forEach((track) => { track.enabled = nextEnabled; });
      livekitTracks.filter((track) => track.kind === "audio").forEach((track) => {
        try { track.mediaStreamTrack.enabled = nextEnabled; } catch (_) {}
      });
      const enabled = stream?.getAudioTracks().some((track) => track.enabled);
      event.currentTarget.textContent = enabled ? "Mute Mic" : "Unmute Mic";
    });
    qs(root, "[data-live-screen]")?.addEventListener("click", async () => {
      if (screenStartPromise) return screenStartPromise;
      screenStartPromise = (async () => {
      try {
        setConnectButtonsBusy(true);
        await stop("start_screen_restart");
        root.classList.add("is-connecting");
        showCameraState(root, "Connecting screen share through LiveKit...");
        currentPublishKind = "screen_share";
        stream = await publishToLiveKit("screen_share");
        video.style.transform = "none";
        setCameraSurfaceActive(root, true);
        showCameraState(root, "");
        console.info("PulseSoc Live publisher screen stream", { live_id: root.dataset.liveId, tracks: trackDiagnostics(stream) });
        await publishTracks("screen_share");
      } catch {
        notify("Screen share was not started.");
      } finally {
        root.classList.remove("is-connecting");
        setConnectButtonsBusy(false);
      }
      })();
      try {
        return await screenStartPromise;
      } finally {
        screenStartPromise = null;
      }
    });
    window.addEventListener("pagehide", () => { stop("pagehide").catch(() => {}); });
  }

  function init(root) {
    bootCamera(root);
    if (!qs(root, "[data-live-camera]")) initViewerTransport(root);
    fetchState(root);
    scheduleLiveStatePolling(root);
    qs(root, "[data-live-chat-send]")?.addEventListener("click", () => sendChat(root));
    qs(root, "[data-live-chat-input]")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) sendChat(root);
    });
    qsa(root, "[data-live-reaction]").forEach((button) => {
      button.addEventListener("click", () => sendReaction(root, button.dataset.liveReaction || "🔥"));
    });
    qsa(root, "[data-copy-live-value]").forEach((button) => {
      button.addEventListener("click", () => copyLiveValue(button));
    });
    qsa(root, "[data-live-settings-toggle]").forEach((button) => {
      button.addEventListener("click", () => toggleAdvancedStreaming(root));
    });
    qsa(root, "[data-reveal-live-secret]").forEach((button) => {
      button.addEventListener("click", () => revealLiveSecret(button));
    });
    qsa(root, "[data-check-mux-status]").forEach((button) => {
        button.addEventListener("click", () => checkMuxStatus(root));
    });
    root.addEventListener("click", async (event) => {
      const joinButton = event.target?.closest?.("[data-live-join-request]");
      const cancelButton = event.target?.closest?.("[data-live-cancel-request]");
      const requestButton = event.target?.closest?.("[data-live-request-action]");
      const guestButton = event.target?.closest?.("[data-live-guest-action], [data-live-guest-leave]");
      const shareButton = event.target?.closest?.("[data-live-share]");
      const backstageButton = event.target?.closest?.("[data-live-open-backstage]");
      if (!joinButton && !cancelButton && !requestButton && !guestButton && !shareButton && !backstageButton) return;
      if (joinButton) {
        event.preventDefault();
        await requestJoinLive(root);
        return;
      }
      if (cancelButton) {
        event.preventDefault();
        try {
          await cancelJoinRequest(root, cancelButton.dataset.liveRequestId || "");
        } catch (error) {
          notify(error.message || "Request could not be cancelled.");
        }
        return;
      }
      if (requestButton) {
        event.preventDefault();
        requestButton.disabled = true;
        try {
          await hostJoinRequestAction(root, requestButton.dataset.liveRequestId || "", requestButton.dataset.liveRequestAction || "");
        } catch (error) {
          notify(error.message || "Join request action failed.");
        } finally {
          requestButton.disabled = false;
        }
        return;
      }
      if (guestButton) {
        event.preventDefault();
        const action = guestButton.dataset.liveGuestAction || (guestButton.matches("[data-live-guest-leave]") ? "leave" : "");
        const guestId = guestButton.dataset.liveGuestId || "";
        guestButton.disabled = true;
        try {
          await hostGuestAction(root, guestId, action);
        } catch (error) {
          notify(error.message || "Guest action failed.");
        } finally {
          guestButton.disabled = false;
        }
        return;
      }
      if (shareButton) {
        event.preventDefault();
        await shareLive(root);
        return;
      }
      if (backstageButton) {
        event.preventDefault();
        qs(root, "[data-live-backstage-panel]")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
    scheduleMuxPolling(root);
    const unmuteButton = qs(root, "[data-live-unmute]");
    const livePlayer = qs(root, "[data-live-player]");
    root.addEventListener("click", async (event) => {
      const target = event.target?.closest?.("[data-live-unmute]");
      if (!target || !root.contains(target)) return;
      const player = qs(root, "[data-live-player]");
      if (!player) return;
      if (player.dataset.liveHostViewer === "1") return;
      player.defaultMuted = false;
      player.removeAttribute("muted");
      player.muted = false;
      player.volume = 1;
      target.hidden = true;
      await player.play?.().catch(() => {});
    });
    if (livePlayer?.dataset?.liveHostViewer === "1") {
      livePlayer.muted = true;
      livePlayer.defaultMuted = true;
      livePlayer.volume = 0;
      livePlayer.setAttribute("muted", "");
      unmuteButton?.setAttribute("hidden", "");
      livePlayer.addEventListener("volumechange", () => {
        if (!livePlayer.muted || livePlayer.volume !== 0) {
          livePlayer.defaultMuted = true;
          livePlayer.muted = true;
          livePlayer.volume = 0;
          livePlayer.setAttribute("muted", "");
        }
      });
    } else if (unmuteButton) {
      let hideUnmuteTimer = window.setTimeout(() => {
        if (!unmuteButton.hidden) unmuteButton.hidden = true;
      }, 5200);
      const revealUnmute = () => {
        if (!livePlayer?.muted) return;
        unmuteButton.hidden = false;
        unmuteButton.classList.remove("is-subtle");
        window.clearTimeout(hideUnmuteTimer);
        hideUnmuteTimer = window.setTimeout(() => {
          if (livePlayer?.muted) unmuteButton.hidden = true;
        }, 3600);
      };
      livePlayer?.addEventListener("click", revealUnmute);
      livePlayer?.addEventListener("volumechange", () => {
        if (!livePlayer.muted && Number(livePlayer.volume || 0) > 0) unmuteButton.hidden = true;
      });
    }
    unmuteButton?.addEventListener("click", async (event) => {
      const player = qs(root, "[data-live-player]");
      if (!player) return;
      if (player.dataset.liveHostViewer === "1") return;
      player.defaultMuted = false;
      player.removeAttribute("muted");
      player.muted = false;
      root.__pulseLivePlaybackPolicy = root.__pulseLivePlaybackPolicy || {};
      root.__pulseLivePlaybackPolicy.unmuted = true;
      try {
        await player.play();
        root.__pulseLivePlaybackPolicy.audioOut = true;
        console.info("PulseSoc Live viewer unmuted", { live_id: root.dataset.liveId, has_audio: !!player.srcObject?.getAudioTracks?.().length });
      } catch (error) {
        root.__pulseLivePlaybackPolicy.autoplayBlocked = true;
        console.warn("PulseSoc Live viewer unmute failed", error);
      }
      event.currentTarget.hidden = true;
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    qsa(document, "[data-pulse-live-shell]").forEach(init);
  });

  window.PulseLiveStudio = { init, fetchState, sendReaction, checkMuxStatus };
})();
