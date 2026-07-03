(() => {
  const API = "/api/calls";
  const state = {
    activeCall: null,
    localStream: null,
    mutedAudio: false,
    mutedVideo: false,
    minimized: false,
  };

  const qs = (sel) => document.querySelector(sel);

  function stopStream(stream = state.localStream) {
    try {
      stream?.getTracks?.().forEach((track) => track.stop());
    } catch (_) {
      /* no-op */
    }
    if (stream === state.localStream) state.localStream = null;
  }

  function iconLabel(type) {
    return type === "video" ? "Video call" : "Audio call";
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
      <div class="pulsesoc-call-card" role="dialog" aria-modal="false" aria-label="PulseSoc call">
        <div class="pulsesoc-call-orb" aria-hidden="true"></div>
        <div class="pulsesoc-call-copy">
          <strong data-call-title>PulseSoc Call</strong>
          <span data-call-status>Preparing secure room...</span>
        </div>
        <div class="pulsesoc-call-controls">
          <button type="button" data-call-toggle-mic aria-label="Toggle microphone">Mic</button>
          <button type="button" data-call-toggle-camera aria-label="Toggle camera">Cam</button>
          <button type="button" data-call-minimize aria-label="Minimize call">−</button>
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
      if (target.closest("[data-call-end]")) return endCall();
      if (target.closest("[data-call-minimize]")) return minimizeCall(true);
      if (target.closest("[data-call-restore]")) return minimizeCall(false);
      if (target.closest("[data-call-toggle-mic]")) return toggleMicrophone();
      if (target.closest("[data-call-toggle-camera]")) return toggleCamera();
    });
    return shell;
  }

  function setStatus(message, mode = "info") {
    const shell = ensureShell();
    shell.hidden = false;
    shell.dataset.mode = mode;
    const status = shell.querySelector("[data-call-status]");
    if (status) status.textContent = message;
    const title = shell.querySelector("[data-call-title]");
    if (title && state.activeCall) title.textContent = `${iconLabel(state.activeCall.call_type)} · ${state.activeCall.status || "ready"}`;
    const pill = shell.querySelector("[data-call-pill-title]");
    if (pill && state.activeCall) pill.textContent = `${iconLabel(state.activeCall.call_type)} active`;
  }

  function minimizeCall(value) {
    const shell = ensureShell();
    state.minimized = Boolean(value);
    shell.classList.toggle("is-minimized", state.minimized);
    const pill = shell.querySelector("[data-call-restore]");
    if (pill) pill.hidden = !state.minimized;
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

  function deviceInfo() {
    return {
      user_agent: navigator.userAgent || "",
      platform: navigator.platform || "",
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      touch: navigator.maxTouchPoints || 0,
    };
  }

  async function requestPermissions(callType) {
    if (!navigator.mediaDevices?.getUserMedia) {
      return { ok: false, status: "unsupported", message: "This browser cannot access microphone or camera calls." };
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: callType === "video" });
      stopStream(stream);
      return { ok: true };
    } catch (error) {
      return {
        ok: false,
        status: "permission_denied",
        message: callType === "video" ? "Camera or microphone permission is required for video calls." : "Microphone permission is required for audio calls.",
        error: error?.name || "permission_error",
      };
    }
  }

  function normalizeOptions(options = {}) {
    const conversationId = Number(options.conversationId || options.conversation_id || qs(".comm-shell")?.dataset.initialConversationId || 0);
    return {
      conversation_id: conversationId,
      recipient_user_ids: Array.isArray(options.recipientUserIds) ? options.recipientUserIds : [],
      call_scope: options.callScope || "direct",
    };
  }

  async function startCall(callType, options = {}) {
    const normalized = normalizeOptions(options);
    if (!normalized.conversation_id) {
      setStatus("Choose a conversation before starting a call.", "error");
      return { ok: false, status: "missing_conversation" };
    }
    setStatus(`Checking ${callType === "video" ? "camera and microphone" : "microphone"} permission...`);
    const permissions = await requestPermissions(callType);
    if (!permissions.ok) {
      setStatus(permissions.message, "error");
      return permissions;
    }
    try {
      setStatus("Creating secure PulseSoc call room...");
      const data = await postJson(`${API}/start`, { ...normalized, call_type: callType, device_info: deviceInfo() });
      state.activeCall = data.call || data;
      setStatus(data.join?.token || data.call?.join?.token ? "Call room ready. LiveKit token issued." : "Call request created.", "success");
      minimizeCall(false);
      return data;
    } catch (error) {
      const payload = error.payload || {};
      const missing = payload.livekit?.missing?.length ? ` Missing: ${payload.livekit.missing.join(", ")}.` : "";
      setStatus(`${payload.message || error.message}${missing}`, payload.status === "config_missing" ? "warn" : "error");
      return payload.ok === false ? payload : { ok: false, status: "error", message: error.message };
    }
  }

  async function startAudioCall(options = {}) {
    return startCall("audio", options);
  }

  async function startVideoCall(options = {}) {
    return startCall("video", options);
  }

  async function acceptCall(callId) {
    const data = await postJson(`${API}/${encodeURIComponent(callId)}/accept`, { device_info: deviceInfo() });
    state.activeCall = data.call || state.activeCall;
    setStatus("Call accepted.", "success");
    return data;
  }

  async function declineCall(callId) {
    const data = await postJson(`${API}/${encodeURIComponent(callId)}/decline`, {});
    setStatus("Call declined.", "info");
    return data;
  }

  async function endCall(callId = state.activeCall?.public_id || state.activeCall?.call_id) {
    if (!callId) {
      hideCallShell();
      return { ok: true };
    }
    try {
      const data = await postJson(`${API}/${encodeURIComponent(callId)}/end`, { reason: "ended_by_user" });
      hideCallShell();
      return data;
    } catch (error) {
      setStatus(error.payload?.message || "Could not end call cleanly.", "error");
      return error.payload || { ok: false, message: error.message };
    }
  }

  async function joinCallRoom(callId) {
    const data = await postJson(`${API}/${encodeURIComponent(callId)}/join-token`, {});
    state.activeCall = data.call || state.activeCall;
    setStatus("Call token ready.", "success");
    return data;
  }

  function hideCallShell() {
    stopStream();
    state.activeCall = null;
    const shell = ensureShell();
    shell.hidden = true;
    shell.classList.remove("is-minimized");
  }

  function toggleMicrophone() {
    state.mutedAudio = !state.mutedAudio;
    setStatus(state.mutedAudio ? "Microphone muted." : "Microphone ready.", "info");
    return state.mutedAudio;
  }

  function toggleCamera() {
    state.mutedVideo = !state.mutedVideo;
    setStatus(state.mutedVideo ? "Camera off." : "Camera ready.", "info");
    return state.mutedVideo;
  }

  function switchCamera() {
    setStatus("Camera switching will activate when the LiveKit client is connected.", "warn");
    return { ok: false, status: "client_not_connected" };
  }

  function switchSpeaker() {
    setStatus("Speaker output follows your device settings in this browser.", "info");
    return { ok: true, status: "device_controlled" };
  }

  async function submitQualityReport(callId, report = {}) {
    return postJson(`${API}/${encodeURIComponent(callId)}/quality`, { ...report, device_info: deviceInfo() });
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
    submitQualityReport,
    _state: state,
  };
})();
