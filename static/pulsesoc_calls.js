(() => {
  const API = "/api/calls";
  const POLL_MS = 6500;
  const STATUS_MS = 2800;
  const QUALITY_MS = 30000;

  const state = {
    activeCall: null,
    room: null,
    localTracks: [],
    remoteTrackEls: new Set(),
    mutedAudio: false,
    mutedVideo: false,
    minimized: false,
    connecting: null,
    activePollTimer: null,
    realtimeBindTimer: null,
    statusTimer: null,
    qualityTimer: null,
    seenIncomingCalls: new Set(),
    facingMode: "user",
    lastQualityAt: 0,
  };

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function livekitClient() {
    return window.LivekitClient || window.LiveKitClient || window.livekitClient || null;
  }

  function callId(call = state.activeCall) {
    return call?.public_id || call?.call_id || call?.id || "";
  }

  function callType(call = state.activeCall) {
    return String(call?.call_type || "audio").toLowerCase() === "video" ? "video" : "audio";
  }

  function currentUserParticipant(call = state.activeCall) {
    if (call?.participant?.user_id) return call.participant;
    const participants = Array.isArray(call?.participants) ? call.participants : [];
    return participants.find((item) => String(item.user_id) === String(call?.current_user_id || "")) || {};
  }

  function roleFor(call = state.activeCall) {
    return String(currentUserParticipant(call)?.role || "").toLowerCase();
  }

  function isIncoming(call) {
    return roleFor(call) === "callee" && String(call?.status || "") === "ringing";
  }

  function displayNameFor(call) {
    const participants = Array.isArray(call?.participants) ? call.participants : [];
    const other = participants.find((item) => String(item.role || "") !== roleFor(call) || String(item.user_id) !== String(currentUserParticipant(call)?.user_id || ""));
    return other?.display_name || (call?.call_type === "video" ? "Video call" : "Audio call");
  }

  function normalizeCallPayload(data = {}) {
    const call = data.call || data;
    const join = data.join || call.join || {};
    if (call && !call.join && join?.token) call.join = join;
    return { call, join };
  }

  function outgoingDeliveryMessage(data = {}) {
    const notifications = Array.isArray(data.notifications) ? data.notifications : Array.isArray(data.call?.notifications) ? data.call.notifications : [];
    if (!notifications.length) return "Call started, but recipient could not be notified.";
    const created = notifications.some((item) => item?.notification_id || item?.deduped);
    const suppressed = notifications.every((item) => item?.suppressed || item?.reason || item?.status === "suppressed");
    if (!created || suppressed) return "Call started, but recipient could not be notified.";
    const jobs = notifications.flatMap((item) => Array.isArray(item?.delivery_jobs) ? item.delivery_jobs : []);
    const pushJob = jobs.find((job) => job?.channel === "push");
    if (!pushJob) return "Waiting for recipient. Push delivery unavailable.";
    if (["skipped_no_device", "skipped_by_preference", "config_missing"].includes(String(pushJob.status || ""))) {
      return "Waiting for recipient. Push delivery unavailable.";
    }
    return "Ringing...";
  }

  async function postJson(url, body = {}) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.message || "Call request failed.");
      error.payload = data;
      throw error;
    }
    return data;
  }

  async function getJson(url) {
    const response = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.message || "Call request failed.");
      error.payload = data;
      throw error;
    }
    return data;
  }

  function deviceInfo() {
    return {
      user_agent: navigator.userAgent || "",
      platform: navigator.platform || "",
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      touch: navigator.maxTouchPoints || 0,
      online: navigator.onLine !== false,
    };
  }

  function ensureShell() {
    let shell = qs("[data-pulsesoc-call-shell]");
    if (shell) return shell;
    shell = document.createElement("section");
    shell.className = "pulsesoc-call-shell";
    shell.hidden = true;
    shell.setAttribute("data-pulsesoc-call-shell", "");
    shell.setAttribute("aria-live", "polite");
    shell.innerHTML = `
      <div class="pulsesoc-call-backdrop" data-call-minimize aria-hidden="true"></div>
      <div class="pulsesoc-call-card" role="dialog" aria-modal="false" aria-label="PulseSoc call">
        <header class="pulsesoc-call-head">
          <span class="pulsesoc-call-orb" aria-hidden="true"></span>
          <div class="pulsesoc-call-copy">
            <strong data-call-title>PulseSoc Call</strong>
            <span data-call-status>Preparing secure room...</span>
          </div>
          <span class="pulsesoc-call-quality" data-call-quality>Standby</span>
        </header>
        <div class="pulsesoc-call-stage" data-call-stage>
          <div class="pulsesoc-call-remote" data-call-remote>
            <span data-call-remote-fallback>Waiting for the other person...</span>
          </div>
          <div class="pulsesoc-call-audio" data-call-audio></div>
          <video class="pulsesoc-call-local" data-call-local muted playsinline autoplay hidden></video>
        </div>
        <div class="pulsesoc-call-actions" data-call-incoming-actions hidden>
          <button type="button" class="is-accept" data-call-accept aria-label="Accept call">Accept</button>
          <button type="button" class="is-decline" data-call-decline aria-label="Decline call">Decline</button>
        </div>
        <div class="pulsesoc-call-controls" data-call-active-controls>
          <button type="button" data-call-toggle-mic aria-label="Mute microphone">Mic</button>
          <button type="button" data-call-toggle-camera aria-label="Turn camera off">Camera</button>
          <button type="button" data-call-switch-camera aria-label="Switch camera">Flip</button>
          <button type="button" data-call-switch-speaker aria-label="Switch speaker">Speaker</button>
          <button type="button" data-call-minimize aria-label="Minimize call">Minimize</button>
          <button type="button" data-call-end aria-label="End call">End</button>
        </div>
      </div>
      <button class="pulsesoc-call-pill" type="button" data-call-restore hidden aria-label="Restore call">
        <span aria-hidden="true"></span>
        <strong data-call-pill-title>PulseSoc call</strong>
      </button>`;
    document.body.appendChild(shell);
    shell.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest("[data-call-accept]")) return acceptCall(callId());
      if (target.closest("[data-call-decline]")) return declineCall(callId());
      if (target.closest("[data-call-end]")) return endCall();
      if (target.closest("[data-call-minimize]")) return minimizeCall(true);
      if (target.closest("[data-call-restore]")) return minimizeCall(false);
      if (target.closest("[data-call-toggle-mic]")) return toggleMicrophone();
      if (target.closest("[data-call-toggle-camera]")) return toggleCamera();
      if (target.closest("[data-call-switch-camera]")) return switchCamera();
      if (target.closest("[data-call-switch-speaker]")) return switchSpeaker();
    });
    return shell;
  }

  function setStatus(message, mode = "info", quality = "") {
    const shell = ensureShell();
    shell.hidden = false;
    shell.dataset.mode = mode;
    const status = qs("[data-call-status]", shell);
    if (status) status.textContent = message || "";
    const title = qs("[data-call-title]", shell);
    if (title) {
      const label = callType() === "video" ? "Video call" : "Audio call";
      const subject = state.activeCall ? displayNameFor(state.activeCall) : "PulseSoc";
      title.textContent = `${label} - ${subject}`;
    }
    const q = qs("[data-call-quality]", shell);
    if (q) q.textContent = quality || qualityLabel();
    const pill = qs("[data-call-pill-title]", shell);
    if (pill) pill.textContent = state.activeCall ? `${callType() === "video" ? "Video" : "Audio"} call active` : "PulseSoc call";
  }

  function qualityLabel() {
    const shell = qs("[data-pulsesoc-call-shell]");
    if (shell?.dataset.callMode === "failed") return "Unavailable";
    if (!navigator.onLine) return "Offline";
    const roomState = String(state.room?.state || state.room?.connectionState || "").toLowerCase();
    if (roomState.includes("reconnect")) return "Reconnecting";
    if (roomState.includes("connected")) return "Good";
    if (state.activeCall?.status === "ringing") return "Ringing";
    if (state.activeCall?.status === "connecting") return "Connecting";
    return state.activeCall ? "Standby" : "Idle";
  }

  function renderMode(mode, message) {
    const shell = ensureShell();
    shell.dataset.callMode = mode;
    const incoming = qs("[data-call-incoming-actions]", shell);
    const controls = qs("[data-call-active-controls]", shell);
    if (incoming) incoming.hidden = mode !== "incoming";
    if (controls) controls.hidden = mode === "incoming";
    const cameraButton = qs("[data-call-toggle-camera]", shell);
    const flipButton = qs("[data-call-switch-camera]", shell);
    const isVideo = callType() === "video";
    if (cameraButton) cameraButton.hidden = !isVideo;
    if (flipButton) flipButton.hidden = !isVideo;
    const fallback = qs("[data-call-remote-fallback]", shell);
    if (fallback && mode === "failed") {
      fallback.hidden = false;
      fallback.textContent = message || "Call unavailable.";
    } else if (fallback && mode === "outgoing") {
      fallback.hidden = false;
      fallback.textContent = "Waiting for recipient to answer...";
    } else if (fallback && mode === "incoming") {
      fallback.hidden = false;
      fallback.textContent = "Incoming call.";
    }
    setStatus(message || "", mode === "failed" ? "error" : mode === "incoming" ? "success" : "info", mode === "failed" ? "Unavailable" : "");
  }

  function minimizeCall(value) {
    const shell = ensureShell();
    state.minimized = Boolean(value);
    shell.classList.toggle("is-minimized", state.minimized);
    const pill = qs("[data-call-restore]", shell);
    if (pill) pill.hidden = !state.minimized;
  }

  function clearRemoteTracks() {
    state.remoteTrackEls.forEach((el) => {
      try { el.remove(); } catch (_) {}
    });
    state.remoteTrackEls.clear();
  }

  function stopLocalTracks() {
    state.localTracks.forEach((track) => {
      try { track.stop?.(); } catch (_) {}
      try { track.mediaStreamTrack?.stop?.(); } catch (_) {}
      try { track.detach?.().forEach((el) => el.remove()); } catch (_) {}
    });
    state.localTracks = [];
    const local = qs("[data-call-local]");
    if (local) {
      local.srcObject = null;
      local.hidden = true;
    }
  }

  async function disconnectRoom(reason = "client_disconnect") {
    stopQualityTimer();
    stopStatusPolling();
    stopLocalTracks();
    clearRemoteTracks();
    try { await state.room?.disconnect?.(); } catch (_) {}
    state.room = null;
    state.connecting = null;
    if (reason !== "minimize") state.mutedAudio = false;
    state.mutedVideo = false;
  }

  function hideCallShell() {
    disconnectRoom("hide");
    state.activeCall = null;
    const shell = ensureShell();
    shell.hidden = true;
    shell.classList.remove("is-minimized");
  }

  function localTrackKind(track) {
    return track?.kind || track?.source || track?.mediaStreamTrack?.kind || "";
  }

  async function createLocalTracks(type) {
    const LK = livekitClient();
    if (!navigator.mediaDevices?.getUserMedia && !LK?.createLocalTracks) {
      throw new Error("Your browser does not support calling.");
    }
    const constraints = {
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: type === "video" ? { facingMode: state.facingMode, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } } : false,
    };
    if (LK?.createLocalTracks) {
      return LK.createLocalTracks(constraints);
    }
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    return stream.getTracks();
  }

  function attachLocalPreview(track) {
    if (localTrackKind(track) !== "video") return;
    const local = qs("[data-call-local]");
    if (!local) return;
    try {
      if (track.attach) {
        const attached = track.attach();
        if (attached instanceof HTMLVideoElement) {
          local.srcObject = attached.srcObject;
        }
      } else if (track instanceof MediaStreamTrack) {
        local.srcObject = new MediaStream([track]);
      } else if (track.mediaStreamTrack) {
        local.srcObject = new MediaStream([track.mediaStreamTrack]);
      }
      local.hidden = false;
      local.play?.().catch(() => {});
    } catch (_) {
      local.hidden = true;
    }
  }

  async function publishLocalTracks(room, type) {
    state.localTracks = await createLocalTracks(type);
    for (const track of state.localTracks) {
      attachLocalPreview(track);
      try {
        if (room?.localParticipant?.publishTrack) {
          await room.localParticipant.publishTrack(track);
        }
      } catch (error) {
        console.warn("PulseSoc call publish failed", error);
      }
    }
  }

  function attachRemoteTrack(track) {
    const kind = localTrackKind(track);
    const host = kind === "audio" ? qs("[data-call-audio]") : qs("[data-call-remote]");
    if (!host) return;
    try {
      const el = track.attach ? track.attach() : null;
      if (!el) return;
      if (kind === "video") {
        qsa("video", host).forEach((node) => node.remove());
        const fallback = qs("[data-call-remote-fallback]", host);
        if (fallback) fallback.hidden = true;
        el.classList.add("pulsesoc-call-remote-video");
      }
      if (kind === "audio") el.hidden = true;
      host.appendChild(el);
      state.remoteTrackEls.add(el);
      el.play?.().catch(() => {});
    } catch (error) {
      console.warn("PulseSoc call remote attach failed", error);
    }
  }

  function detachRemoteTrack(track) {
    try {
      const els = track.detach ? track.detach() : [];
      els.forEach((el) => {
        state.remoteTrackEls.delete(el);
        el.remove();
      });
    } catch (_) {}
  }

  function wireRoomEvents(room, LK) {
    if (!room?.on || room.__pulseSocWired) return;
    room.__pulseSocWired = true;
    const event = LK?.RoomEvent || {};
    const on = (name, handler) => {
      if (!name) return;
      try { room.on(name, handler); } catch (_) {}
    };
    on(event.Connected || "connected", () => {
      setStatus("Connected to the secure call room.", "success", "Good");
      startQualityTimer();
    });
    on(event.Reconnecting || "reconnecting", () => setStatus("Connection is recovering...", "warn", "Reconnecting"));
    on(event.Reconnected || "reconnected", () => setStatus("Connection restored.", "success", "Good"));
    on(event.Disconnected || "disconnected", () => setStatus("Call disconnected.", "warn", "Disconnected"));
    on(event.ParticipantConnected || "participantConnected", () => setStatus("Participant joined.", "success", "Good"));
    on(event.ParticipantDisconnected || "participantDisconnected", () => setStatus("Participant left.", "info", qualityLabel()));
    on(event.TrackSubscribed || "trackSubscribed", (track) => attachRemoteTrack(track));
    on(event.TrackUnsubscribed || "trackUnsubscribed", (track) => detachRemoteTrack(track));
  }

  async function connectCallRoom(data, options = {}) {
    const { call, join } = normalizeCallPayload(data);
    state.activeCall = call;
    if (!join?.token || !join?.livekit_url) {
      renderMode("failed", "Calling is temporarily unavailable. Please try again later.");
      return { ok: false, status: "missing_join_token" };
    }
    const LK = livekitClient();
    if (!LK?.Room) {
      renderMode("failed", "Calling is still loading. Try again in a moment.");
      return { ok: false, status: "livekit_client_missing" };
    }
    if (state.connecting) return state.connecting;
    state.connecting = (async () => {
      try {
        await disconnectRoom("reconnect");
        renderMode(options.mode || "active", options.connectingMessage || "Connecting secure call room...");
        const room = new LK.Room({ adaptiveStream: true, dynacast: true });
        state.room = room;
        wireRoomEvents(room, LK);
        await room.connect(join.livekit_url, join.token);
        await publishLocalTracks(room, callType(call));
        if (options.markConnected !== false) {
          const connected = await postJson(`${API}/${encodeURIComponent(callId(call))}/connected`, { device_info: deviceInfo() });
          state.activeCall = connected.call || state.activeCall;
        }
        renderMode(options.mode === "outgoing" ? "outgoing" : "active", options.readyMessage || (options.mode === "outgoing" ? "Ringing..." : "Call connected."));
        startStatusPolling();
        startQualityTimer();
        return { ok: true, call: state.activeCall };
      } catch (error) {
        const name = error?.name || "";
        const message = name === "NotAllowedError"
          ? (callType(call) === "video" ? "Camera or microphone permission is required for video calls." : "Microphone permission is required for calls.")
          : (error?.message || "Could not connect this call.");
        renderMode("failed", message);
        await postJson(`${API}/${encodeURIComponent(callId(call))}/end`, { reason: "client_connect_failed", error: name }).catch(() => {});
        return { ok: false, status: "connect_failed", message };
      } finally {
        state.connecting = null;
      }
    })();
    return state.connecting;
  }

  function normalizeOptions(options = {}) {
    const conversationId = Number(options.conversationId || options.conversation_id || qs(".comm-shell")?.dataset.initialConversationId || 0);
    return {
      conversation_id: conversationId,
      recipient_user_ids: Array.isArray(options.recipientUserIds) ? options.recipientUserIds : [],
      call_scope: options.callScope || "direct",
    };
  }

  async function startCall(type, options = {}) {
    const normalized = normalizeOptions(options);
    if (!normalized.conversation_id) {
      renderMode("failed", "Choose a conversation before starting a call.");
      return { ok: false, status: "missing_conversation" };
    }
    try {
      renderMode("outgoing", `Starting ${type} call...`);
      const data = await postJson(`${API}/start`, { ...normalized, call_type: type, device_info: deviceInfo() });
      const { call } = normalizeCallPayload(data);
      state.activeCall = call;
      const readyMessage = outgoingDeliveryMessage(data);
      const connected = await connectCallRoom(data, {
        mode: "outgoing",
        markConnected: false,
        connectingMessage: "Preparing secure room...",
        readyMessage,
      });
      if (connected?.ok === false) return connected;
      minimizeCall(false);
      return data;
    } catch (error) {
      const payload = error.payload || {};
      const message = payload.status === "config_missing"
        ? "Calling is not configured yet."
        : payload.message || error.message || "Call could not start.";
      renderMode("failed", message);
      return payload.ok === false ? payload : { ok: false, status: "error", message: error.message };
    }
  }

  async function startAudioCall(options = {}) {
    return startCall("audio", options);
  }

  async function startVideoCall(options = {}) {
    return startCall("video", options);
  }

  function showIncoming(call) {
    state.activeCall = call;
    minimizeCall(false);
    renderMode("incoming", `Incoming ${callType(call)} call from ${displayNameFor(call)}.`);
    const id = callId(call);
    if (id && !state.seenIncomingCalls.has(id)) {
      state.seenIncomingCalls.add(id);
      postJson(`${API}/${encodeURIComponent(id)}/ring-seen`, { device_info: deviceInfo() }).catch(() => {});
    }
  }

  function realtimePayload(event) {
    return event?.payload || event?.detail || event || {};
  }

  function handleIncomingRealtime(event) {
    const payload = realtimePayload(event);
    const call = payload.call || payload.data?.call || payload;
    if (!call || !callId(call)) {
      pollActiveCalls();
      return;
    }
    if (isIncoming(call)) {
      showIncoming(call);
      return;
    }
    if (String(call.status || "") === "ringing") pollActiveCalls();
  }

  function wakeCallPolling() {
    pollActiveCalls();
    if (state.activeCall && !state.statusTimer) startStatusPolling();
  }

  async function acceptCall(id) {
    if (!id) return { ok: false, status: "missing_call" };
    try {
      renderMode("active", "Accepting call...");
      const data = await postJson(`${API}/${encodeURIComponent(id)}/accept`, { device_info: deviceInfo() });
      const connected = await connectCallRoom(data, { mode: "active", markConnected: true, connectingMessage: "Joining call...", readyMessage: "Call connected." });
      if (connected?.ok === false) return connected;
      return data;
    } catch (error) {
      const payload = error.payload || {};
      renderMode("failed", payload.message || "Could not accept this call.");
      return payload.ok === false ? payload : { ok: false, status: "error", message: error.message };
    }
  }

  async function declineCall(id) {
    if (!id) return { ok: false, status: "missing_call" };
    try {
      const data = await postJson(`${API}/${encodeURIComponent(id)}/decline`, {});
      hideCallShell();
      return data;
    } catch (error) {
      renderMode("failed", error.payload?.message || "Could not decline this call.");
      return error.payload || { ok: false, message: error.message };
    }
  }

  async function endCall(id = callId()) {
    if (!id) {
      hideCallShell();
      return { ok: true };
    }
    try {
      const data = await postJson(`${API}/${encodeURIComponent(id)}/end`, { reason: "ended_by_user" });
      hideCallShell();
      return data;
    } catch (error) {
      renderMode("failed", error.payload?.message || "Could not end call cleanly.");
      return error.payload || { ok: false, message: error.message };
    }
  }

  async function joinCallRoom(id) {
    const data = await postJson(`${API}/${encodeURIComponent(id)}/join-token`, { device_info: deviceInfo() });
    const connected = await connectCallRoom(data, { mode: "active", markConnected: true, connectingMessage: "Joining call...", readyMessage: "Call connected." });
    if (connected?.ok === false) return connected;
    return data;
  }

  async function setControl(action, body = {}) {
    const id = callId();
    if (!id) return null;
    return postJson(`${API}/${encodeURIComponent(id)}/${action}`, body).catch(() => null);
  }

  async function toggleMicrophone() {
    state.mutedAudio = !state.mutedAudio;
    for (const track of state.localTracks.filter((item) => localTrackKind(item) === "audio")) {
      try {
        if (state.mutedAudio && track.mute) await track.mute();
        else if (!state.mutedAudio && track.unmute) await track.unmute();
        else if (track.mediaStreamTrack) track.mediaStreamTrack.enabled = !state.mutedAudio;
        else if (track instanceof MediaStreamTrack) track.enabled = !state.mutedAudio;
      } catch (_) {}
    }
    await setControl(state.mutedAudio ? "mute-audio" : "unmute-audio");
    setStatus(state.mutedAudio ? "Microphone muted." : "Microphone on.", "info");
    const btn = qs("[data-call-toggle-mic]");
    if (btn) {
      btn.classList.toggle("is-muted", state.mutedAudio);
      btn.setAttribute("aria-label", state.mutedAudio ? "Unmute microphone" : "Mute microphone");
      btn.textContent = state.mutedAudio ? "Unmute" : "Mic";
    }
    return state.mutedAudio;
  }

  async function toggleCamera() {
    if (callType() !== "video") return false;
    state.mutedVideo = !state.mutedVideo;
    for (const track of state.localTracks.filter((item) => localTrackKind(item) === "video")) {
      try {
        if (state.mutedVideo && track.mute) await track.mute();
        else if (!state.mutedVideo && track.unmute) await track.unmute();
        else if (track.mediaStreamTrack) track.mediaStreamTrack.enabled = !state.mutedVideo;
        else if (track instanceof MediaStreamTrack) track.enabled = !state.mutedVideo;
      } catch (_) {}
    }
    await setControl(state.mutedVideo ? "disable-video" : "enable-video");
    setStatus(state.mutedVideo ? "Camera off." : "Camera on.", "info");
    const local = qs("[data-call-local]");
    if (local) local.hidden = state.mutedVideo;
    const btn = qs("[data-call-toggle-camera]");
    if (btn) {
      btn.classList.toggle("is-muted", state.mutedVideo);
      btn.setAttribute("aria-label", state.mutedVideo ? "Turn camera on" : "Turn camera off");
      btn.textContent = state.mutedVideo ? "Camera On" : "Camera";
    }
    return state.mutedVideo;
  }

  async function switchCamera() {
    if (callType() !== "video") return { ok: false, status: "not_video" };
    state.facingMode = state.facingMode === "user" ? "environment" : "user";
    const video = state.localTracks.find((track) => localTrackKind(track) === "video");
    try {
      if (video?.restartTrack) {
        await video.restartTrack({ facingMode: state.facingMode, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } });
        attachLocalPreview(video);
        setStatus("Camera switched.", "success");
        return { ok: true };
      }
      setStatus("Camera switching is not supported by this browser session.", "warn");
      return { ok: false, status: "unsupported" };
    } catch (error) {
      setStatus("Camera could not switch.", "error");
      return { ok: false, message: error.message };
    }
  }

  function switchSpeaker() {
    setStatus("Speaker output follows your device settings in this browser.", "info");
    return { ok: true, status: "device_controlled" };
  }

  async function startScreenShare() {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setStatus("Screen sharing is not supported on this device.", "warn");
      return { ok: false, status: "unsupported" };
    }
    await setControl("screen-share/start");
    setStatus("Screen sharing is ready for the next call layer.", "info");
    return { ok: true };
  }

  async function stopScreenShare() {
    await setControl("screen-share/stop");
    setStatus("Screen sharing stopped.", "info");
    return { ok: true };
  }

  async function submitQualityReport(id = callId(), report = {}) {
    if (!id) return { ok: false, status: "missing_call" };
    return postJson(`${API}/${encodeURIComponent(id)}/quality`, { ...report, device_info: deviceInfo() });
  }

  function startQualityTimer() {
    stopQualityTimer();
    state.qualityTimer = window.setInterval(() => {
      const now = Date.now();
      if (!callId() || now - state.lastQualityAt < QUALITY_MS - 1000) return;
      state.lastQualityAt = now;
      const score = qualityLabel() === "Good" ? 92 : qualityLabel() === "Reconnecting" ? 42 : 70;
      submitQualityReport(callId(), {
        quality_score: score,
        network_type: navigator.connection?.effectiveType || "",
        resolution: `${window.innerWidth}x${window.innerHeight}`,
      }).catch(() => {});
    }, QUALITY_MS);
  }

  function stopQualityTimer() {
    if (state.qualityTimer) window.clearInterval(state.qualityTimer);
    state.qualityTimer = null;
  }

  function startStatusPolling() {
    stopStatusPolling();
    state.statusTimer = window.setInterval(async () => {
      const id = callId();
      if (!id) return;
      try {
        const data = await getJson(`${API}/${encodeURIComponent(id)}/status`);
        const call = data.call;
        if (!call) return;
        state.activeCall = call;
        const status = String(call.status || "");
        if (["ended", "missed", "declined", "failed", "canceled"].includes(status)) {
          renderMode("failed", status === "declined" ? "Call declined." : status === "missed" ? "No answer." : "Call ended.");
          window.setTimeout(hideCallShell, 1400);
          return;
        }
        if (isIncoming(call)) showIncoming(call);
        else if (status === "ringing") renderMode("outgoing", "Ringing...");
        else renderMode("active", status === "reconnecting" ? "Reconnecting..." : "Call active.");
      } catch (_) {}
    }, STATUS_MS);
  }

  function stopStatusPolling() {
    if (state.statusTimer) window.clearInterval(state.statusTimer);
    state.statusTimer = null;
  }

  async function pollActiveCalls() {
    try {
      const data = await getJson(`${API}/active`);
      const calls = Array.isArray(data.calls) ? data.calls : [];
      if (!state.activeCall) {
        const incoming = calls.find((call) => isIncoming(call));
        if (incoming) showIncoming(incoming);
      }
    } catch (_) {
      /* polling must never break Messenger */
    }
  }

  function startActivePolling() {
    if (state.activePollTimer) return;
    pollActiveCalls();
    state.activePollTimer = window.setInterval(pollActiveCalls, POLL_MS);
  }

  function bindRealtimeCalls() {
    if (window.PulseRealtime?.on) {
      window.PulseRealtime.on("incoming_call", handleIncomingRealtime);
      window.PulseRealtime.on("communication_call_incoming", handleIncomingRealtime);
      window.PulseRealtime.on("call_started", handleIncomingRealtime);
      window.PulseRealtime.on("notification_created", (event) => {
        const payload = realtimePayload(event);
        const type = String(payload.type || payload.notification_type || payload.notification?.type || payload.event_type || "").toLowerCase();
        if (type === "incoming_call") handleIncomingRealtime(event);
      });
      return;
    }
    window.clearTimeout(state.realtimeBindTimer);
    state.realtimeBindTimer = window.setTimeout(bindRealtimeCalls, 1200);
  }

  function handleDeepLinkedCall() {
    const params = new URLSearchParams(window.location.search || "");
    const id = params.get("call_id");
    if (!id) return;
    getJson(`${API}/${encodeURIComponent(id)}/status`).then((data) => {
      const call = data.call;
      if (!call) return;
      if (isIncoming(call)) showIncoming(call);
      else if (["connecting", "connected", "reconnecting"].includes(String(call.status || ""))) joinCallRoom(id).catch(() => {});
      else {
        state.activeCall = call;
        renderMode("outgoing", call.status === "ringing" ? "Ringing..." : `Call ${call.status || "ready"}.`);
      }
    }).catch(() => {});
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    wakeCallPolling();
  });

  window.addEventListener("focus", wakeCallPolling);
  window.addEventListener("pageshow", wakeCallPolling);
  window.addEventListener("online", () => {
    setStatus("Connection restored.", "success", qualityLabel());
    wakeCallPolling();
  });
  window.addEventListener("offline", () => setStatus("Network offline. Call will reconnect when possible.", "warn", "Offline"));

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      startActivePolling();
      bindRealtimeCalls();
      handleDeepLinkedCall();
    }, { once: true });
  } else {
    startActivePolling();
    bindRealtimeCalls();
    handleDeepLinkedCall();
  }

  window.PulseSocCalls = {
    startAudioCall,
    startVideoCall,
    acceptCall,
    declineCall,
    endCall,
    joinCallRoom,
    toggleMicrophone,
    toggleCamera,
    switchCamera,
    switchSpeaker,
    startScreenShare,
    stopScreenShare,
    minimizeCall,
    restoreCall: () => minimizeCall(false),
    submitQualityReport,
    _state: state,
  };
})();
