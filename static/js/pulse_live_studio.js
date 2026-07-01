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
    setText(root, "[data-live-health]", health.level || data.status || "ready");
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
    const interval = Math.max(2200, Number(root.dataset.livePollMs || 4500));
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
    const LIVE_HOST_PUBLISHER = "LiveStudioCameraOwner";
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
    function claimLiveStudioCameraOwner() {
      const existing = window.__PulseSocLiveStudioCameraOwner;
      if (existing?.root && existing.root !== root) {
        try { existing.stop?.("camera_owner_replaced_by_live_studio_camera_owner"); } catch (_) {}
      }
      window.__PulseSocLiveStudioCameraOwner = { name: LIVE_HOST_PUBLISHER, root, stop: (reason) => stop(reason) };
      root.dataset.liveCameraOwner = LIVE_HOST_PUBLISHER;
    }
    function releaseLiveStudioCameraOwner() {
      if (window.__PulseSocLiveStudioCameraOwner?.root === root) {
        delete window.__PulseSocLiveStudioCameraOwner;
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
      releaseLiveStudioCameraOwner();
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
            claimLiveStudioCameraOwner();
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
    const stop = (reason = "manual_stop") => cleanupPublisher({ disconnect: true, reason });
    const start = async () => {
      if (cameraStartPromise) return cameraStartPromise;
      cameraStartPromise = (async () => {
      try {
        setConnectButtonsBusy(true);
        claimLiveStudioCameraOwner();
        pauseCompetingLiveMedia();
        await stop("start_camera_restart");
        claimLiveStudioCameraOwner();
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
    qsa(root, "[data-check-mux-status]").forEach((button) => {
        button.addEventListener("click", () => checkMuxStatus(root));
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
