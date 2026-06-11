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

  function applyState(root, data) {
    const health = data.health || {};
    const pulse = data.presence?.pulse || data.presence || {};
    setText(root, "[data-live-viewers]", data.viewer_count ?? 0);
    setText(root, "[data-live-health]", health.level || data.status || "ready");
    setText(root, "[data-live-score]", health.score ?? 0);
    setText(root, "[data-live-bitrate]", `${health.bitrate_kbps || 0} kbps`);
    setText(root, "[data-live-fps]", `${health.fps || 0} FPS`);
    setText(root, "[data-live-latency]", health.latency_ms ? `${health.latency_ms} ms` : "ready");
    setText(root, "[data-live-pulse]", pulse.label || "ready");
    if (data.mux?.live_status) setText(root, "[data-mux-live-status]", `Status ${data.mux.live_status}`);
    if (data.livekit?.egress_status) setText(root, "[data-live-camera-state]", data.livekit.egress_error || `LiveKit egress ${data.livekit.egress_status}`);
    const score = qs(root, ".live-health-score");
    if (score) score.style.setProperty("--score", Math.max(0, Math.min(100, Number(health.score || 0))));
    renderChat(root, data.messages || []);
    renderReactionBurst(root, data.reaction_cloud || []);
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

  async function checkMuxStatus(root) {
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
      }
      notify(`Mux status: ${data.mux_live_status || "idle"}`);
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
      await checkMuxStatus(root);
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
    try {
      await fetch(`/api/pulse/live/${id}/react`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reaction_type: reaction }),
      });
      renderReactionBurst(root, [{ emoji: reaction, x: 64, delay_ms: 0 }]);
    } catch (error) {
      console.warn("PulseSoc Live reaction failed", error);
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
    return tracks.map((track) => ({
      kind: track.kind,
      id: track.id,
      label: track.label,
      readyState: track.readyState,
      enabled: track.enabled,
      muted: track.muted,
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
    player.defaultMuted = false;
    player.removeAttribute("muted");
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
    let stream = null;
    let livekitRoom = null;
    let livekitTracks = [];
    function livekitClient() {
      return window.LivekitClient || window.LiveKitClient || window.livekitClient || null;
    }
    function liveStartMessage(error) {
      const name = error?.name || "";
      const message = error?.message || "Browser Live could not start.";
      if (name === "NotAllowedError" || name === "SecurityError") return "Camera/microphone permission is blocked in Chrome.";
      if (name === "NotFoundError" || name === "OverconstrainedError") return "No available camera or microphone matched this device.";
      if (error?.pulseStage === "livekit-connect") return `LiveKit connection failed: ${message}`;
      if (error?.pulseStage === "livekit-publish") return `LiveKit track publish failed: ${message}`;
      if (error?.pulseStage === "media-capture") return `Camera/microphone capture failed: ${message}`;
      return message;
    }
    function tracksToMediaStream(tracks) {
      const mediaStream = new MediaStream();
      tracks.forEach((track) => {
        const mediaTrack = track?.mediaStreamTrack || track?.track;
        if (mediaTrack) mediaStream.addTrack(mediaTrack);
      });
      return mediaStream;
    }
    function detachLiveKitTracks() {
      livekitTracks.forEach((track) => {
        try { track.detach?.().forEach((element) => element.remove?.()); } catch (_) {}
        try { track.stop?.(); } catch (_) {}
      });
      livekitTracks = [];
    }
    async function connectLiveKitRoom() {
      const LK = livekitClient();
      const id = root?.dataset?.liveId;
      if (!LK) throw new Error("LiveKit browser client is still loading. Try Start Camera again in a moment.");
      if (!id) throw new Error("Live session is missing.");
      if (livekitRoom?.state === "connected") return livekitRoom;
      const tokenResponse = await fetch(`/api/pulse/live/${id}/livekit/token`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "publisher" }),
      });
      const tokenData = await tokenResponse.json().catch(() => ({}));
      if (!tokenResponse.ok || tokenData.ok === false) throw new Error(tokenData.message || "LiveKit host token could not be created.");
      livekitRoom = new LK.Room({ adaptiveStream: true, dynacast: true });
      await livekitRoom.connect(tokenData.livekit_url, tokenData.token);
      root.dataset.livekitRoom = tokenData.room || "";
      return livekitRoom;
    }
    async function publishToLiveKit(kind) {
      const LK = livekitClient();
      if (!LK) throw new Error("LiveKit browser client is still loading. Try Start Camera again in a moment.");
      detachLiveKitTracks();
      setText(root, "[data-live-camera-state]", kind === "screen_share" ? "Requesting screen share permission..." : "Requesting camera/microphone permission...");
      let trackOptions;
      try {
        trackOptions = kind === "screen_share"
          ? await LK.createLocalScreenTracks({ audio: true, video: true })
          : await LK.createLocalTracks({
              audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
              video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 60 } },
            });
      } catch (error) {
        error.pulseStage = "media-capture";
        throw error;
      }
      livekitTracks = trackOptions;
      stream = tracksToMediaStream(livekitTracks);
      const videoTrack = livekitTracks.find((track) => track.kind === "video");
      if (videoTrack?.attach) {
        try {
          videoTrack.attach(video);
        } catch (_) {
          video.srcObject = stream;
        }
      } else {
        video.srcObject = stream;
      }
      root.classList.add("is-camera-active");
      setText(root, "[data-live-camera-state]", "Camera ready. Connecting to LiveKit...");
      let room;
      try {
        room = await connectLiveKitRoom();
      } catch (error) {
        error.pulseStage = "livekit-connect";
        throw error;
      }
      setText(root, "[data-live-camera-state]", "Publishing camera and microphone to LiveKit...");
      try {
        await Promise.all(livekitTracks.map((track) => room.localParticipant.publishTrack(track)));
      } catch (error) {
        console.warn("PulseSoc LiveKit publishTrack failed", error);
        error.pulseStage = "livekit-publish";
        throw error;
      }
      return stream;
    }
    async function publishTracks(kind) {
      const id = root?.dataset?.liveId;
      if (!id || !stream) return;
      const audioTracks = stream.getAudioTracks().filter((track) => track.readyState === "live").length;
      const videoTracks = stream.getVideoTracks().filter((track) => track.readyState === "live").length;
      try {
        await fetch(`/api/pulse/live/${id}/browser-publish`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: kind || "browser_camera",
            audio_tracks: audioTracks,
            video_tracks: videoTracks,
            muted: !stream.getAudioTracks().some((track) => track.enabled),
          }),
        }).then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok || data.ok === false) throw new Error(data.message || "Live media publish failed.");
          return data;
        });
        console.info("PulseSoc Live publisher publish acknowledged", { live_id: id, tracks: trackDiagnostics(stream) });
        setText(root, "[data-live-camera-state]", audioTracks || videoTracks ? "Browser Live is publishing through LiveKit and forwarding to Mux." : "No tracks detected");
        await fetchState(root);
      } catch (error) {
        setText(root, "[data-live-camera-state]", "Publishing needs attention");
        notify(error.message);
      }
    }
    const stop = () => {
      detachLiveKitTracks();
      try { livekitRoom?.disconnect?.(); } catch (_) {}
      livekitRoom = null;
      if (stream) stream.getTracks().forEach((track) => track.stop());
      stream = null;
    };
    const start = async () => {
      try {
        stop();
        root.classList.add("is-connecting");
        setText(root, "[data-live-camera-state]", "Connecting Browser Live to LiveKit...");
        stream = await publishToLiveKit("browser_camera");
        root.classList.add("is-camera-active");
        setText(root, "[data-live-camera-state]", "Browser Live is publishing through LiveKit and forwarding to Mux.");
        console.info("PulseSoc Live publisher local stream", { live_id: root.dataset.liveId, tracks: trackDiagnostics(stream) });
        await publishTracks("browser_camera");
      } catch (error) {
        const message = liveStartMessage(error);
        setText(root, "[data-live-camera-state]", message);
        console.warn("PulseSoc Browser Live start failed", { stage: error?.pulseStage || error?.name || "unknown", message: error?.message || String(error) });
        notify(message);
      } finally {
        root.classList.remove("is-connecting");
      }
    };
    qs(root, "[data-live-start-camera]")?.addEventListener("click", start);
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
      try {
        stop();
        root.classList.add("is-connecting");
        setText(root, "[data-live-camera-state]", "Connecting screen share through LiveKit...");
        stream = await publishToLiveKit("screen_share");
        video.style.transform = "none";
        root.classList.add("is-camera-active");
        setText(root, "[data-live-camera-state]", "Screen is publishing through LiveKit and forwarding to Mux.");
        console.info("PulseSoc Live publisher screen stream", { live_id: root.dataset.liveId, tracks: trackDiagnostics(stream) });
        await publishTracks("screen_share");
      } catch {
        notify("Screen share was not started.");
      } finally {
        root.classList.remove("is-connecting");
      }
    });
    window.addEventListener("pagehide", stop);
  }

  function init(root) {
    bootCamera(root);
    if (!qs(root, "[data-live-camera]")) initViewerTransport(root);
    fetchState(root);
    const interval = Number(root.dataset.livePollMs || 4500);
    setInterval(() => fetchState(root), Math.max(2200, interval));
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
    qs(root, "[data-live-unmute]")?.addEventListener("click", async (event) => {
      const player = qs(root, "[data-live-player]");
      if (!player) return;
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
