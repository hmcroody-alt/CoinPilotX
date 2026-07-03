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
    statusSequenceTimers: [],
    seenIncomingCalls: new Set(),
    facingMode: "user",
    speakerMode: "device",
    audioOutputDeviceId: "",
    reconnectCount: 0,
    visibilityWasHidden: false,
    toneTimer: null,
    toneContext: null,
    lastQualityAt: 0,
    lastFailure: null,
    controlsVisible: false,
    controlsTimer: null,
    durationTimer: null,
    pointerOverControls: false,
    ending: false,
  };

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function t(key, fallback = "", vars = {}) {
    const value = window.PulseI18n?.t ? window.PulseI18n.t(key, fallback || key) : (fallback || key);
    return String(value || fallback || key).replace(/\{([a-z0-9_]+)\}/gi, (_, name) => vars[name] ?? "");
  }

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
    const other = otherParticipant(call);
    return other?.display_name || (call?.call_type === "video" ? "Video call" : "Audio call");
  }

  function otherParticipant(call = state.activeCall) {
    const participants = Array.isArray(call?.participants) ? call.participants : [];
    return participants.find((item) => String(item.role || "") !== roleFor(call) || String(item.user_id) !== String(currentUserParticipant(call)?.user_id || "")) || {};
  }

  function normalizeCallPayload(data = {}) {
    const call = data.call || data;
    const join = data.join || call.join || {};
    if (call && !call.join && join?.token) call.join = join;
    return { call, join };
  }

  function outgoingDeliveryMessage(data = {}) {
    const notifications = Array.isArray(data.notifications) ? data.notifications : Array.isArray(data.call?.notifications) ? data.call.notifications : [];
    if (!notifications.length) return t("pulse.call.notification_failed", "Pulse started, but recipient could not be notified.");
    const created = notifications.some((item) => item?.notification_id || item?.deduped);
    const suppressed = notifications.every((item) => item?.suppressed || item?.reason || item?.status === "suppressed");
    if (!created || suppressed) return t("pulse.call.notification_failed", "Pulse started, but recipient could not be notified.");
    const jobs = notifications.flatMap((item) => Array.isArray(item?.delivery_jobs) ? item.delivery_jobs : []);
    const pushJob = jobs.find((job) => job?.channel === "push");
    if (!pushJob) return t("pulse.call.push_unavailable", "Waiting for recipient. Push delivery unavailable.");
    if (["skipped_no_device", "skipped_by_preference", "config_missing"].includes(String(pushJob.status || ""))) {
      return t("pulse.call.push_unavailable", "Waiting for recipient. Push delivery unavailable.");
    }
    return t("pulse.call.recipient_notified", "Recipient notified.");
  }

  function diagnosticsAllowed() {
    const host = String(window.location.hostname || "").toLowerCase();
    return ["localhost", "127.0.0.1", "::1"].includes(host) || new URLSearchParams(window.location.search || "").get("call_debug") === "1" || localStorage.getItem("pulsesocCallDiagnostics") === "1";
  }

  function callDiagnosticsUrl(call = state.activeCall) {
    const id = callId(call);
    return id ? `/admin/calls/${encodeURIComponent(id)}/delivery` : "";
  }

  function structuredFailure(payload = {}, fallback = {}) {
    return {
      ok: false,
      status: payload.status || fallback.status || "error",
      error_code: payload.error_code || fallback.error_code || "UNKNOWN_ERROR",
      error_title: payload.error_title || fallback.error_title || "Call could not start",
      error_description: payload.error_description || payload.message || fallback.error_description || "PulseSoc could not complete this call request.",
      remediation: payload.remediation || fallback.remediation || "Try again. If this repeats, inspect the correlation ID in Calls Command Center.",
      correlation_id: payload.correlation_id || payload.trace_id || fallback.correlation_id || "",
      call: payload.call || fallback.call || state.activeCall || null,
    };
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
      const error = new Error(data.error_title || data.message || "Call request failed.");
      error.payload = data;
      throw error;
    }
    return data;
  }

  async function getJson(url) {
    const response = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error_title || data.message || "Call request failed.");
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
    shell.setAttribute("tabindex", "-1");
    shell.innerHTML = `
      <div class="pulsesoc-call-backdrop" aria-hidden="true"></div>
      <div class="pulsesoc-call-card" role="dialog" aria-modal="false" aria-label="PulseSoc call" data-call-interaction-zone>
        <header class="pulsesoc-call-head" aria-live="polite">
          <span class="pulsesoc-call-orb" aria-hidden="true"></span>
          <div class="pulsesoc-call-copy">
            <strong data-call-title>${t("pulse.call.default_title", "PulseSoc")}</strong>
            <span data-call-meta>${t("pulse.call.audio_meta", "Voice Connection")} · 00:00</span>
            <small data-call-status>${t("pulse.call.preparing_voice", "Preparing communication channel...")}</small>
          </div>
          <span class="pulsesoc-call-quality" data-call-quality>Standby</span>
        </header>
        <div class="pulsesoc-call-stage" data-call-stage>
          <div class="pulsesoc-call-remote" data-call-remote>
            <div class="pulsesoc-call-audio-visual" data-call-audio-visual aria-hidden="true">
              <span></span><span></span><span></span>
            </div>
            <span data-call-remote-fallback>${t("pulse.call.waiting", "Waiting for response...")}</span>
          </div>
          <div class="pulsesoc-call-audio" data-call-audio></div>
          <div class="pulsesoc-call-local-wrap" data-call-local-wrap hidden>
            <video class="pulsesoc-call-local" data-call-local muted playsinline autoplay></video>
            <span data-call-local-fallback hidden>${t("pulse.call.local_camera_off", "Camera off")}</span>
          </div>
        </div>
        <div class="pulsesoc-call-actions" data-call-incoming-actions hidden>
          <button type="button" class="is-decline" data-call-decline aria-label="Decline call"><span aria-hidden="true">&#9742;</span><b>Decline</b></button>
          <button type="button" class="is-accept" data-call-accept aria-label="Accept call"><span aria-hidden="true">&#9742;</span><b>Accept</b></button>
        </div>
        <button type="button" class="pulsesoc-call-end-primary" data-call-end aria-label="End call"><span aria-hidden="true">&#9742;</span><b>End</b></button>
        <div class="pulsesoc-call-controls" data-call-active-controls data-call-controls-panel hidden>
          <button type="button" data-call-toggle-mic aria-label="Mute microphone"><span aria-hidden="true">&#127908;</span><b>Mic</b></button>
          <button type="button" data-call-toggle-camera aria-label="Turn camera off"><span aria-hidden="true">&#128247;</span><b>Camera</b></button>
          <button type="button" data-call-switch-camera aria-label="Flip camera"><span aria-hidden="true">&#8635;</span><b>Flip</b></button>
          <button type="button" data-call-switch-speaker aria-label="Speaker"><span aria-hidden="true">&#128266;</span><b>Speaker</b></button>
          <button type="button" data-call-diagnostics hidden aria-label="View call diagnostics">View Diagnostics</button>
          <button type="button" data-call-minimize aria-label="Minimize call"><span aria-hidden="true">&#8722;</span><b>Minimize</b></button>
          <button type="button" data-call-more aria-label="More call options"><span aria-hidden="true">&#8942;</span><b>More</b></button>
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
      if (target.closest("[data-call-controls-panel]")) state.pointerOverControls = true;
      if (target.closest("[data-call-accept]")) return acceptCall(callId());
      if (target.closest("[data-call-decline]")) return declineCall(callId());
      if (target.closest("[data-call-end]")) return endCall();
      if (target.closest("[data-call-minimize]")) return minimizeCall(true);
      if (target.closest("[data-call-restore]")) return minimizeCall(false);
      if (target.closest("[data-call-toggle-mic]")) return toggleMicrophone();
      if (target.closest("[data-call-toggle-camera]")) return toggleCamera();
      if (target.closest("[data-call-switch-camera]")) return switchCamera();
      if (target.closest("[data-call-switch-speaker]")) return switchSpeaker();
      if (target.closest("[data-call-diagnostics]")) return openDiagnostics();
      if (target.closest("[data-call-more]")) return showControls(true, true);
      if (target.closest("[data-call-interaction-zone]")) return toggleControls();
    });
    shell.addEventListener("mousemove", () => showControls(true));
    shell.addEventListener("keydown", (event) => {
      const tag = String(event.target?.tagName || "").toLowerCase();
      if (["input", "textarea", "select"].includes(tag) || event.target?.isContentEditable) return;
      if (event.key === "Escape") return showControls(false, true);
      if (event.key?.toLowerCase?.() === "m") {
        event.preventDefault();
        return toggleMicrophone();
      }
      if (event.key?.toLowerCase?.() === "v" && callType() === "video") {
        event.preventDefault();
        return toggleCamera();
      }
      if (event.key === " " && event.target === shell) {
        event.preventDefault();
        toggleControls();
      }
    });
    const controls = qs("[data-call-controls-panel]", shell);
    controls?.addEventListener("pointerenter", () => {
      state.pointerOverControls = true;
      showControls(true, true);
    });
    controls?.addEventListener("pointerleave", () => {
      state.pointerOverControls = false;
      scheduleControlsHide();
    });
    return shell;
  }

  function clearControlsTimer() {
    if (state.controlsTimer) window.clearTimeout(state.controlsTimer);
    state.controlsTimer = null;
  }

  function scheduleControlsHide() {
    clearControlsTimer();
    if (!state.activeCall || state.pointerOverControls) return;
    state.controlsTimer = window.setTimeout(() => {
      if (!state.pointerOverControls) showControls(false, true);
    }, 3000);
  }

  function showControls(visible = true, sticky = false) {
    const shell = ensureShell();
    const panel = qs("[data-call-controls-panel]", shell);
    state.controlsVisible = Boolean(visible);
    if (state.controlsVisible && shell.dataset.callMode !== "incoming") {
      if (panel) panel.hidden = false;
      window.requestAnimationFrame(() => shell.classList.add("controls-visible"));
      if (!sticky) scheduleControlsHide();
    } else {
      shell.classList.remove("controls-visible");
      window.setTimeout(() => {
        if (!state.controlsVisible && panel) panel.hidden = true;
      }, 220);
      clearControlsTimer();
    }
  }

  function toggleControls() {
    showControls(!state.controlsVisible, state.controlsVisible ? true : false);
  }

  function durationLabel() {
    const call = state.activeCall || {};
    const source = call.answered_at || call.started_at || call.created_at || "";
    const started = source ? Date.parse(String(source).replace("Z", "+00:00")) : 0;
    const elapsed = started ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
    const mins = Math.floor(elapsed / 60);
    const secs = elapsed % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }

  function updateDurationDisplay() {
    const shell = qs("[data-pulsesoc-call-shell]");
    if (!shell || shell.hidden) return;
    const meta = qs("[data-call-meta]", shell);
    const key = callType() === "video" ? "pulse.call.video_meta" : "pulse.call.audio_meta";
    const fallback = callType() === "video" ? "Video Connection" : "Voice Connection";
    if (meta) meta.textContent = `${t(key, fallback)} · ${durationLabel()}`;
  }

  function startDurationTimer() {
    stopDurationTimer();
    updateDurationDisplay();
    state.durationTimer = window.setInterval(updateDurationDisplay, 1000);
  }

  function stopDurationTimer() {
    if (state.durationTimer) window.clearInterval(state.durationTimer);
    state.durationTimer = null;
  }

  function setControlButton(button, icon, label) {
    if (!button) return;
    button.innerHTML = `<span aria-hidden="true">${icon}</span><b>${label}</b>`;
  }

  function setStatus(message, mode = "info", quality = "") {
    const shell = ensureShell();
    shell.hidden = false;
    shell.dataset.mode = mode;
    shell.dataset.callType = callType();
    const status = qs("[data-call-status]", shell);
    if (status) status.textContent = message || "";
    const title = qs("[data-call-title]", shell);
    if (title) {
      const subject = state.activeCall ? displayNameFor(state.activeCall) : "PulseSoc";
      title.textContent = subject;
    }
    updateDurationDisplay();
    const q = qs("[data-call-quality]", shell);
    if (q) q.textContent = quality || qualityLabel();
    const pill = qs("[data-call-pill-title]", shell);
    if (pill) pill.textContent = state.activeCall ? `${t("pulse.call.connected", "Pulse Connected")} · ${durationLabel()}` : t("pulse.call.default_title", "PulseSoc");
  }

  function qualityLabel() {
    const shell = qs("[data-pulsesoc-call-shell]");
    if (shell?.dataset.callMode === "failed") return "Unavailable";
    if (!navigator.onLine) return "Offline";
    const roomState = String(state.room?.state || state.room?.connectionState || "").toLowerCase();
    if (roomState.includes("reconnect")) return t("pulse.call.restoring", "Restoring Pulse...");
    if (roomState.includes("connected")) return t("pulse.call.excellent", "Excellent Connection");
    if (state.activeCall?.status === "ringing") return t("pulse.call.waiting", "Waiting for response...");
    if (state.activeCall?.status === "connecting") return t("pulse.call.synchronizing", "Synchronizing...");
    return state.activeCall ? t("pulse.call.outgoing", "Pulsing...") : "Idle";
  }

  function clearStatusSequence() {
    state.statusSequenceTimers.forEach((timer) => window.clearTimeout(timer));
    state.statusSequenceTimers = [];
  }

  function statusSequence(steps = []) {
    clearStatusSequence();
    let delay = 0;
    steps.forEach((step) => {
      delay += Number(step.delay || 0);
      const timer = window.setTimeout(() => {
        if (state.activeCall) setStatus(step.message, step.mode || "info", step.quality || "");
      }, delay);
      state.statusSequenceTimers.push(timer);
    });
  }

  function renderMode(mode, message) {
    const shell = ensureShell();
    shell.dataset.callMode = mode;
    shell.dataset.callType = callType();
    const incoming = qs("[data-call-incoming-actions]", shell);
    const controls = qs("[data-call-active-controls]", shell);
    if (incoming) incoming.hidden = mode !== "incoming";
    if (controls) controls.hidden = mode === "incoming" || !state.controlsVisible;
    const primaryEnd = qs(".pulsesoc-call-end-primary", shell);
    if (primaryEnd) {
      primaryEnd.hidden = mode === "incoming";
      primaryEnd.disabled = Boolean(state.ending);
      primaryEnd.classList.toggle("is-ending", Boolean(state.ending));
      primaryEnd.setAttribute("aria-label", state.ending ? "Ending call" : "End call");
    }
    const cameraButton = qs("[data-call-toggle-camera]", shell);
    const flipButton = qs("[data-call-switch-camera]", shell);
    const isVideo = callType() === "video";
    if (cameraButton) cameraButton.hidden = !isVideo;
    if (flipButton) flipButton.hidden = !isVideo;
    const audioVisual = qs("[data-call-audio-visual]", shell);
    if (audioVisual) audioVisual.hidden = isVideo;
    const fallback = qs("[data-call-remote-fallback]", shell);
    fallback?.classList.remove("pulsesoc-call-incoming-copy");
    if (fallback && mode === "failed") {
      fallback.hidden = false;
      fallback.textContent = message || t("pulse.call.interrupted", "Pulse Interrupted");
    } else if (fallback && mode === "outgoing") {
      fallback.hidden = false;
      fallback.textContent = t("pulse.call.waiting", "Waiting for response...");
    } else if (fallback && mode === "incoming") {
      fallback.hidden = false;
      renderIncomingFallback(fallback);
    } else if (fallback && !isVideo) {
      fallback.hidden = false;
      fallback.textContent = displayNameFor(state.activeCall) || "Audio call";
    } else if (fallback && isVideo && !state.remoteTrackEls.size) {
      fallback.hidden = false;
      fallback.textContent = t("pulse.call.waiting_video", "Waiting for video...");
    }
    setStatus(message || "", mode === "failed" ? "error" : mode === "incoming" ? "success" : "info", mode === "failed" ? "Unavailable" : "");
    if (["active", "outgoing"].includes(mode)) {
      startDurationTimer();
      scheduleControlsHide();
    }
    if (isVideo && ["active", "outgoing"].includes(mode)) syncLocalCameraSurface();
  }

  function renderIncomingFallback(fallback) {
    if (!fallback) return;
    const name = displayNameFor(state.activeCall);
    const typeText = callType() === "video" ? t("pulse.call.incoming_video", "Video Connection") : t("pulse.call.incoming_voice", "Voice Connection");
    fallback.textContent = "";
    fallback.classList.add("pulsesoc-call-incoming-copy");
    const pulse = document.createElement("strong");
    pulse.textContent = t("pulse.call.outgoing", "Pulsing...");
    const caller = document.createElement("span");
    caller.textContent = name;
    const kind = document.createElement("small");
    kind.textContent = typeText;
    fallback.append(pulse, caller, kind);
  }

  function renderFailure(payload = {}, fallback = {}) {
    stopCallTone();
    const failure = structuredFailure(payload, fallback);
    state.lastFailure = failure;
    if (failure.call) state.activeCall = failure.call;
    const shell = ensureShell();
    renderMode("failed", failure.error_title);
    const fallbackEl = qs("[data-call-remote-fallback]", shell);
    if (fallbackEl) {
      fallbackEl.hidden = false;
      fallbackEl.textContent = "";
      const title = document.createElement("strong");
      title.textContent = failure.error_title;
      const description = document.createElement("span");
      description.textContent = failure.error_description;
      const remediation = document.createElement("small");
      remediation.textContent = `Next step: ${failure.remediation}`;
      fallbackEl.append(title, document.createElement("br"), description, document.createElement("br"), remediation);
      if (failure.correlation_id) {
        const correlation = document.createElement("small");
        correlation.textContent = `Correlation ID: ${failure.correlation_id}`;
        fallbackEl.append(document.createElement("br"), correlation);
      }
    }
    const diagnostics = qs("[data-call-diagnostics]", shell);
    const url = callDiagnosticsUrl(failure.call);
    if (diagnostics) diagnostics.hidden = !(diagnosticsAllowed() && url);
    if (failure.correlation_id || failure.error_code) {
      console.warn("PulseSoc call failure", {
        error_code: failure.error_code,
        correlation_id: failure.correlation_id,
        status: failure.status,
      });
    }
    return failure;
  }

  function openDiagnostics() {
    const url = callDiagnosticsUrl(state.lastFailure?.call || state.activeCall);
    if (!url || !diagnosticsAllowed()) return;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  function minimizeCall(value) {
    const shell = ensureShell();
    state.minimized = Boolean(value);
    shell.classList.toggle("is-minimized", state.minimized);
    const pill = qs("[data-call-restore]", shell);
    if (pill) pill.hidden = !state.minimized;
    setControl(state.minimized ? "minimize" : "restore", { minimized: state.minimized })?.catch?.(() => {});
  }

  function stopCallTone() {
    if (state.toneTimer) window.clearInterval(state.toneTimer);
    state.toneTimer = null;
  }

  function toneContext() {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return null;
    if (!state.toneContext) state.toneContext = new AudioContext();
    if (state.toneContext.state === "suspended") state.toneContext.resume?.().catch(() => {});
    return state.toneContext;
  }

  function playPulseTone(frequencies = [440], duration = 0.34, volume = 0.035) {
    const ctx = toneContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    frequencies.forEach((frequency, index) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = index % 2 ? "triangle" : "sine";
      osc.frequency.setValueAtTime(frequency, now);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(volume, now + 0.035);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + duration + 0.04);
    });
  }

  function startCallTone(kind) {
    stopCallTone();
    if (kind === "incoming") {
      playPulseTone([620, 930], 0.28, 0.032);
      state.toneTimer = window.setInterval(() => playPulseTone([620, 930], 0.28, 0.032), 1450);
      if (navigator.vibrate) navigator.vibrate([120, 70, 120]);
      return;
    }
    playPulseTone([350], 0.46, 0.028);
    state.toneTimer = window.setInterval(() => playPulseTone([350], 0.46, 0.028), 1850);
  }

  function showRemoteFallback(message = "") {
    const host = qs("[data-call-remote]");
    if (!host) return;
    if (callType() === "video" && remoteCameraIsLive()) return;
    const fallback = qs("[data-call-remote-fallback]", host);
    if (fallback) {
      fallback.hidden = false;
      if (callType() === "video") renderCameraOffFallback(fallback, otherParticipant(), message || t("pulse.call.remote_camera_off", "Camera Off"));
      else fallback.textContent = message || displayNameFor(state.activeCall);
    }
    const audioVisual = qs("[data-call-audio-visual]", host);
    if (audioVisual) audioVisual.hidden = callType() === "video";
  }

  function clearRemoteTracks() {
    state.remoteTrackEls.forEach((el) => {
      try { el.remove(); } catch (_) {}
    });
    state.remoteTrackEls.clear();
    showRemoteFallback(callType() === "video" ? t("pulse.call.remote_camera_off", "Camera Off") : displayNameFor(state.activeCall));
  }

  async function unpublishLocalTrack(track, stop = false) {
    try {
      const participant = state.room?.localParticipant;
      if (participant?.unpublishTrack) {
        const raw = track?.mediaStreamTrack || track;
        await Promise.resolve(participant.unpublishTrack(track)).catch(() => {});
        if (raw !== track) await Promise.resolve(participant.unpublishTrack(raw)).catch(() => {});
      }
    } catch (_) {}
    try { track.detach?.().forEach((el) => el.remove()); } catch (_) {}
    if (stop) {
      try { track.stop?.(); } catch (_) {}
      try { track.mediaStreamTrack?.stop?.(); } catch (_) {}
      try { if (track instanceof MediaStreamTrack) track.stop(); } catch (_) {}
    }
  }

  function mediaTrack(track) {
    return track?.mediaStreamTrack || (track instanceof MediaStreamTrack ? track : null);
  }

  function collectionValues(collection) {
    if (!collection) return [];
    if (Array.isArray(collection)) return collection;
    if (collection instanceof Map) return Array.from(collection.values());
    if (typeof collection.forEach === "function") {
      const values = [];
      try { collection.forEach((value) => values.push(value)); } catch (_) {}
      return values;
    }
    if (typeof collection === "object") return Object.values(collection);
    return [];
  }

  function videoPublicationsFor(participant) {
    const values = [
      ...collectionValues(participant?.videoTrackPublications),
      ...collectionValues(participant?.trackPublications),
      ...collectionValues(participant?.tracks),
    ];
    const seen = new Set();
    return values.filter((publication) => {
      if (!publication || !isVideoPublication(publication)) return false;
      const key = publication.sid || publication.trackSid || publication.track?.sid || publication.source || publication.track?.source || publication;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function remoteParticipants() {
    return collectionValues(state.room?.remoteParticipants);
  }

  function publicationTrack(publication) {
    return publication?.track || publication?.videoTrack || publication?.audioTrack || publication;
  }

  function publicationSource(publication) {
    const track = publicationTrack(publication);
    return String(publication?.source || track?.source || "").toLowerCase();
  }

  function isVideoPublication(publication) {
    const track = publicationTrack(publication);
    const raw = mediaTrack(track);
    const kind = String(publication?.kind || track?.kind || raw?.kind || "").toLowerCase();
    const source = publicationSource(publication);
    return kind === "video" || raw?.kind === "video" || source === "camera" || source.includes("video") || source.includes("screen");
  }

  function isCameraPublication(publication) {
    const source = publicationSource(publication);
    return !source || source === "camera" || source.includes("camera") || source === "unknown";
  }

  function trackIsLive(track) {
    if (track?.isMuted === true || track?.muted === true) return false;
    const raw = mediaTrack(track);
    if (!raw) return false;
    return raw.readyState !== "ended" && raw.enabled !== false;
  }

  function publicationVideoIsLive(publication, requireSubscribed = false) {
    if (!publication || !isVideoPublication(publication)) return false;
    if (publication.isMuted === true || publication.muted === true) return false;
    if (requireSubscribed && publication.isSubscribed === false) return false;
    const track = publicationTrack(publication);
    return trackIsLive(track);
  }

  function videoElementIsLive(video) {
    if (!video || video.hidden || video.closest?.(".is-camera-off")) return false;
    const stream = video?.srcObject;
    if (!stream?.getVideoTracks) return false;
    return stream.getVideoTracks().some((track) => track.readyState !== "ended" && track.enabled !== false);
  }

  function anyVideoElementIsLive(selector, root = document) {
    return qsa(selector, root).some((video) => videoElementIsLive(video));
  }

  function participantCameraEnabled(participant) {
    if (!participant) return null;
    const value = participant.isCameraEnabled;
    if (typeof value === "boolean") return value;
    if (typeof value === "function") {
      try {
        const result = value.call(participant);
        if (typeof result === "boolean") return result;
      } catch (_) {}
    }
    return null;
  }

  function liveLocalVideoTrack() {
    return tracksByKind("video").find(trackIsLive) || null;
  }

  function rememberLocalTrack(track) {
    if (!track) return;
    const raw = mediaTrack(track);
    const exists = state.localTracks.some((item) => {
      if (item === track) return true;
      const itemRaw = mediaTrack(item);
      return raw?.id && itemRaw?.id && raw.id === itemRaw.id;
    });
    if (!exists) state.localTracks.push(track);
  }

  function liveLocalVideoPublication() {
    return videoPublicationsFor(state.room?.localParticipant)
      .filter(isCameraPublication)
      .find((publication) => publicationVideoIsLive(publication, false)) || null;
  }

  function localCameraIsLive() {
    const participant = state.room?.localParticipant;
    const cameraEnabled = participantCameraEnabled(participant);
    if (cameraEnabled === false) return false;
    const publications = videoPublicationsFor(participant).filter(isCameraPublication);
    const domLive = anyVideoElementIsLive("[data-call-local]");
    if (publications.length) {
      if (publications.some((publication) => publicationVideoIsLive(publication, false))) return true;
      if (cameraEnabled === true && liveLocalVideoTrack()) return true;
      return false;
    }
    if (liveLocalVideoTrack()) return true;
    return cameraEnabled === true && domLive;
  }

  function remoteCameraIsLive() {
    const publications = remoteParticipants().flatMap((participant) => videoPublicationsFor(participant));
    const domLive = anyVideoElementIsLive("[data-call-remote] video");
    if (publications.length) {
      const explicitlyMuted = publications.some((publication) => publication.isMuted === true || publication.muted === true);
      return publications.some((publication) => publicationVideoIsLive(publication, true)) || (!explicitlyMuted && domLive);
    }
    return domLive;
  }

  function clearVideoElement(video) {
    if (!video) return;
    try { video.pause?.(); } catch (_) {}
    try { video.srcObject = null; } catch (_) {}
    try { video.removeAttribute?.("src"); } catch (_) {}
    try { video.load?.(); } catch (_) {}
  }

  function renderCameraOffFallback(fallback, participant = {}, label = "") {
    if (!fallback) return;
    const name = String(participant.display_name || participant.username || participant.name || "").trim();
    const avatar = String(participant.avatar_url || participant.profile_photo_url || participant.photo_url || "").trim();
    fallback.classList.add("pulsesoc-call-camera-off");
    fallback.textContent = "";
    const orb = document.createElement("span");
    orb.className = "pulsesoc-call-camera-orb";
    if (avatar) {
      const image = document.createElement("img");
      image.alt = "";
      image.src = avatar;
      orb.appendChild(image);
    } else {
      orb.textContent = name ? name.slice(0, 1).toUpperCase() : "P";
    }
    const title = document.createElement("strong");
    title.textContent = label || t("pulse.call.local_camera_off", "Camera off");
    const caption = document.createElement("small");
    caption.textContent = name || "PulseSoc";
    fallback.append(orb, title, caption);
  }

  function syncLocalCameraSurface() {
    if (callType() !== "video") return false;
    const isLive = localCameraIsLive();
    state.mutedVideo = !isLive;
    const local = qs("[data-call-local]");
    const wrap = qs("[data-call-local-wrap]");
    const fallback = qs("[data-call-local-fallback]");
    if (wrap) {
      wrap.hidden = false;
      wrap.classList.toggle("is-camera-off", !isLive);
      wrap.classList.toggle("is-camera-live", isLive);
      wrap.dataset.cameraState = isLive ? "live" : "off";
    }
    if (fallback) {
      fallback.hidden = isLive;
      if (!isLive) renderCameraOffFallback(fallback, currentUserParticipant(), t("pulse.call.local_camera_off", "Camera off"));
      else fallback.classList.remove("pulsesoc-call-camera-off");
    }
    if (local) {
      local.hidden = !isLive;
      local.classList.toggle("is-live", isLive);
      local.classList.toggle("is-off", !isLive);
      if (!isLive) clearVideoElement(local);
      else if (!videoElementIsLive(local)) {
        const publication = liveLocalVideoPublication();
        const track = publicationTrack(publication) || liveLocalVideoTrack();
        if (track) attachLocalPreview(track, { skipSync: true });
      }
    }
    renderCameraButtonState();
    return isLive;
  }

  function syncRemoteCameraSurface(message = "") {
    if (callType() !== "video") return false;
    const host = qs("[data-call-remote]");
    if (!host) return false;
    const isLive = remoteCameraIsLive();
    const fallback = qs("[data-call-remote-fallback]", host);
    const audioVisual = qs("[data-call-audio-visual]", host);
    if (audioVisual) audioVisual.hidden = true;
    if (fallback) {
      fallback.hidden = isLive;
      if (!isLive) renderCameraOffFallback(fallback, otherParticipant(), message || t("pulse.call.remote_camera_off", "Camera Off"));
      else fallback.classList.remove("pulsesoc-call-camera-off");
    }
    if (!isLive) {
      qsa("video", host).forEach((video) => {
        state.remoteTrackEls.delete(video);
        try { video.remove(); } catch (_) {}
      });
    }
    return isLive;
  }

  function syncCameraSurfaces() {
    syncLocalCameraSurface();
    syncRemoteCameraSurface();
  }

  function renderCameraButtonState() {
    const btn = qs("[data-call-toggle-camera]");
    if (!btn) return;
    const cameraOff = callType() === "video" && !localCameraIsLive();
    btn.classList.toggle("is-muted", cameraOff);
    btn.setAttribute("aria-label", cameraOff ? "Turn camera on" : "Turn camera off");
    setControlButton(btn, "&#128247;", cameraOff ? "Camera On" : "Camera");
  }

  function tracksByKind(kind) {
    return state.localTracks.filter((item) => localTrackKind(item) === kind || mediaTrack(item)?.kind === kind);
  }

  async function stopLocalTracks(kind = "") {
    const selected = kind ? tracksByKind(kind) : [...state.localTracks];
    await Promise.all(selected.map((track) => unpublishLocalTrack(track, true)));
    state.localTracks = kind ? state.localTracks.filter((track) => !selected.includes(track)) : [];
    const local = qs("[data-call-local]");
    if (local && (!kind || kind === "video")) {
      clearVideoElement(local);
    }
    const wrap = qs("[data-call-local-wrap]");
    const fallback = qs("[data-call-local-fallback]");
    if (!kind || kind === "video") {
      if (wrap) wrap.hidden = kind === "video" ? false : true;
      if (kind === "video") {
        detachLocalPreview();
        syncLocalCameraSurface();
      }
      else if (fallback) fallback.hidden = true;
    }
  }

  async function disconnectRoom(reason = "client_disconnect") {
    stopCallTone();
    clearStatusSequence();
    stopQualityTimer();
    stopStatusPolling();
    stopDurationTimer();
    clearControlsTimer();
    await stopLocalTracks();
    clearRemoteTracks();
    try { state.room?.removeAllListeners?.(); } catch (_) {}
    try { await state.room?.disconnect?.(); } catch (_) {}
    state.room = null;
    state.connecting = null;
    if (reason !== "minimize") state.mutedAudio = false;
    state.mutedVideo = false;
  }

  async function hideCallShell() {
    await disconnectRoom("hide");
    state.activeCall = null;
    state.lastFailure = null;
    state.ending = false;
    state.minimized = false;
    state.controlsVisible = false;
    state.pointerOverControls = false;
    const shell = ensureShell();
    shell.hidden = true;
    shell.classList.remove("is-minimized");
    showControls(false, true);
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

  async function createSingleLocalTrack(kind) {
    const LK = livekitClient();
    const audio = kind === "audio" ? { echoCancellation: true, noiseSuppression: true, autoGainControl: true } : false;
    const video = kind === "video" ? { facingMode: state.facingMode, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } } : false;
    if (kind === "video" && LK?.createLocalVideoTrack) return LK.createLocalVideoTrack(video);
    if (kind === "audio" && LK?.createLocalAudioTrack) return LK.createLocalAudioTrack(audio);
    if (LK?.createLocalTracks) {
      const tracks = await LK.createLocalTracks({ audio, video });
      return tracks.find((track) => localTrackKind(track) === kind || mediaTrack(track)?.kind === kind) || tracks[0];
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio, video });
    return stream.getTracks().find((track) => track.kind === kind);
  }

  function detachLocalPreview() {
    const local = qs("[data-call-local]");
    const wrap = qs("[data-call-local-wrap]");
    const fallback = qs("[data-call-local-fallback]");
    tracksByKind("video").forEach((track) => {
      try {
        const detached = track.detach ? track.detach(local || undefined) : [];
        (Array.isArray(detached) ? detached : [detached]).forEach((el) => {
          if (el && el !== local) el.remove?.();
        });
      } catch (_) {}
    });
    if (local) {
      clearVideoElement(local);
      local.hidden = true;
      local.classList.remove("is-live");
      local.classList.add("is-off");
    }
    if (wrap) {
      wrap.hidden = false;
      wrap.classList.remove("is-camera-live");
      wrap.classList.add("is-camera-off");
      wrap.dataset.cameraState = "off";
    }
    if (fallback) fallback.hidden = false;
  }

  function attachLocalPreview(track, options = {}) {
    if (localTrackKind(track) !== "video") return;
    const local = qs("[data-call-local]");
    const wrap = qs("[data-call-local-wrap]");
    const fallback = qs("[data-call-local-fallback]");
    if (!local) return;
    try {
      clearVideoElement(local);
      if (track.attach) {
        let attached = null;
        try {
          attached = track.attach(local);
        } catch (_) {
          attached = track.attach();
        }
        if (attached instanceof HTMLVideoElement) {
          local.srcObject = attached.srcObject;
          if (attached !== local) attached.remove?.();
        }
      } else if (track instanceof MediaStreamTrack) {
        local.srcObject = new MediaStream([track]);
      } else if (track.mediaStreamTrack) {
        local.srcObject = new MediaStream([track.mediaStreamTrack]);
      }
      if (!local.srcObject && mediaTrack(track)) {
        local.srcObject = new MediaStream([mediaTrack(track)]);
      }
      rememberLocalTrack(track);
      if (wrap) {
        wrap.hidden = false;
        wrap.classList.remove("is-camera-off");
        wrap.classList.add("is-camera-live");
        wrap.dataset.cameraState = "live";
      }
      if (fallback) {
        fallback.hidden = true;
        fallback.classList.remove("pulsesoc-call-camera-off");
      }
      local.hidden = false;
      local.classList.remove("is-off");
      local.classList.add("is-live");
      local.play?.().catch(() => {});
      if (!options.skipSync) syncLocalCameraSurface();
    } catch (_) {
      if (wrap) wrap.hidden = true;
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

  async function publishSingleLocalTrack(kind) {
    if (!state.room?.localParticipant) throw new Error("Call room is not connected.");
    const track = await createSingleLocalTrack(kind);
    if (!track) throw new Error(`${kind} track could not be created.`);
    if (kind === "video") attachLocalPreview(track);
    await state.room.localParticipant.publishTrack?.(track);
    rememberLocalTrack(track);
    return track;
  }

  async function ensureLocalAudioTrack() {
    const existing = tracksByKind("audio").find((track) => mediaTrack(track)?.readyState !== "ended");
    if (existing) {
      const raw = mediaTrack(existing);
      if (raw && !state.mutedAudio) raw.enabled = true;
      return existing;
    }
    if (!state.room) return null;
    const track = await publishSingleLocalTrack("audio");
    if (state.mutedAudio) {
      try { await track.mute?.(); } catch (_) {}
      const raw = mediaTrack(track);
      if (raw) raw.enabled = false;
    }
    return track;
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
        if (fallback) {
          fallback.hidden = true;
          fallback.classList.remove("pulsesoc-call-camera-off");
        }
        const audioVisual = qs("[data-call-audio-visual]", host);
        if (audioVisual) audioVisual.hidden = true;
        el.classList.add("pulsesoc-call-remote-video");
      }
      if (kind === "audio") {
        el.hidden = true;
        if (state.audioOutputDeviceId && typeof el.setSinkId === "function") {
          el.setSinkId(state.audioOutputDeviceId).catch(() => {});
        }
      }
      host.appendChild(el);
      state.remoteTrackEls.add(el);
      el.play?.().catch(() => {});
      if (kind === "video") syncRemoteCameraSurface();
    } catch (error) {
      console.warn("PulseSoc call remote attach failed", error);
    }
  }

  function detachRemoteTrack(track) {
    try {
      const kind = localTrackKind(track);
      const els = track.detach ? track.detach() : [];
      els.forEach((el) => {
        state.remoteTrackEls.delete(el);
        el.remove();
      });
      if (kind === "video") syncRemoteCameraSurface(t("pulse.call.remote_camera_off", "Camera Off"));
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
      stopCallTone();
      setStatus(t("pulse.call.connected", "Pulse Connected"), "success", t("pulse.call.excellent", "Excellent Connection"));
      startQualityTimer();
    });
    on(event.Reconnecting || "reconnecting", () => {
      state.reconnectCount += 1;
      setStatus(t("pulse.call.restoring", "Restoring Pulse..."), "warn", t("pulse.call.restoring", "Restoring Pulse..."));
    });
    on(event.Reconnected || "reconnected", () => setStatus(t("pulse.call.restored", "Pulse Restored"), "success", t("pulse.call.excellent", "Excellent Connection")));
    on(event.Disconnected || "disconnected", () => setStatus(t("pulse.call.lost", "Pulse Lost"), "warn", "Offline"));
    on(event.ParticipantConnected || "participantConnected", () => setStatus(t("pulse.call.accepted", "Pulse Accepted"), "success", t("pulse.call.excellent", "Excellent Connection")));
    on(event.ParticipantDisconnected || "participantDisconnected", () => {
      setStatus("Participant left.", "info", qualityLabel());
      syncRemoteCameraSurface(t("pulse.call.remote_camera_off", "Camera Off"));
    });
    on(event.TrackSubscribed || "trackSubscribed", (track) => attachRemoteTrack(track));
    on(event.TrackUnsubscribed || "trackUnsubscribed", (track) => detachRemoteTrack(track));
    on(event.TrackMuted || "trackMuted", () => syncCameraSurfaces());
    on(event.TrackUnmuted || "trackUnmuted", () => syncCameraSurfaces());
    on(event.TrackPublished || "trackPublished", () => syncCameraSurfaces());
    on(event.TrackUnpublished || "trackUnpublished", () => syncCameraSurfaces());
    on(event.LocalTrackPublished || "localTrackPublished", () => syncLocalCameraSurface());
    on(event.LocalTrackUnpublished || "localTrackUnpublished", () => syncLocalCameraSurface());
  }

  async function connectCallRoom(data, options = {}) {
    const { call, join } = normalizeCallPayload(data);
    state.activeCall = call;
    if (!join?.token || !join?.livekit_url) {
      return renderFailure(data, {
        status: "missing_join_token",
        error_code: "LIVEKIT_TOKEN_FAILED",
        error_title: "Call token missing",
        error_description: "The backend created a call response without a usable LiveKit token or URL.",
        remediation: "Open Calls Command Center and inspect this call's startup diagnostics.",
        call,
      });
    }
    const LK = livekitClient();
    if (!LK?.Room) {
      return renderFailure({}, {
        status: "livekit_client_missing",
        error_code: "LIVEKIT_CLIENT_NOT_LOADED",
        error_title: "Call client is still loading",
        error_description: "The LiveKit browser client was not available when the call started.",
        remediation: "Refresh the app or check whether the LiveKit client bundle loaded successfully.",
        call,
      });
    }
    if (state.connecting) return state.connecting;
    state.connecting = (async () => {
      try {
        await disconnectRoom("reconnect");
        renderMode(options.mode || "active", options.connectingMessage || t("pulse.call.establishing", "Establishing Secure Connection..."));
        const room = new LK.Room({ adaptiveStream: true, dynacast: true });
        state.room = room;
        wireRoomEvents(room, LK);
        await room.connect(join.livekit_url, join.token);
        await publishLocalTracks(room, callType(call));
        if (options.markConnected !== false) {
          const connected = await postJson(`${API}/${encodeURIComponent(callId(call))}/connected`, { device_info: deviceInfo() });
          state.activeCall = connected.call || state.activeCall;
        }
        renderMode(options.mode === "outgoing" ? "outgoing" : "active", options.readyMessage || (options.mode === "outgoing" ? t("pulse.call.waiting", "Waiting for response...") : t("pulse.call.connected", "Pulse Connected")));
        startStatusPolling();
        startQualityTimer();
        return { ok: true, call: state.activeCall };
      } catch (error) {
        const name = error?.name || "";
        const permissionDenied = name === "NotAllowedError";
        const failure = renderFailure({}, {
          status: "connect_failed",
          error_code: permissionDenied ? "MEDIA_PERMISSION_DENIED" : "LIVEKIT_ROOM_CONNECT_FAILED",
          error_title: permissionDenied ? "Microphone or camera permission blocked" : "Could not connect to LiveKit room",
          error_description: permissionDenied
            ? (callType(call) === "video" ? "Camera or microphone permission is required for video calls." : "Microphone permission is required for audio calls.")
            : (error?.message || "The browser could not connect to the LiveKit room."),
          remediation: permissionDenied
            ? "Allow microphone/camera access in the browser or app settings, then start the call again."
            : "Run the LiveKit config test, check provider connectivity, and inspect this call in Calls Command Center.",
          call,
        });
        await postJson(`${API}/${encodeURIComponent(callId(call))}/end`, { reason: "client_connect_failed", error: name, error_code: failure.error_code }).catch(() => {});
        return failure;
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
      return renderFailure({}, {
        status: "missing_conversation",
        error_code: "MISSING_CONVERSATION",
        error_title: "No conversation selected",
        error_description: "PulseSoc could not identify which conversation should receive this call.",
        remediation: "Open a conversation and try the call again.",
      });
    }
    try {
      startCallTone("outgoing");
      renderMode("outgoing", t("pulse.call.outgoing", "Pulsing..."));
      statusSequence([
        { delay: 700, message: t("pulse.call.searching", "Searching for secure connection...") },
        { delay: 1000, message: type === "video" ? t("pulse.call.preparing_video", "Synchronizing video channel...") : t("pulse.call.preparing_voice", "Preparing communication channel...") },
        { delay: 1200, message: t("pulse.call.waiting", "Waiting for response...") },
      ]);
      const data = await postJson(`${API}/start`, { ...normalized, call_type: type, device_info: deviceInfo() });
      const { call } = normalizeCallPayload(data);
      state.activeCall = call;
      const readyMessage = outgoingDeliveryMessage(data);
      const connected = await connectCallRoom(data, {
        mode: "outgoing",
        markConnected: false,
        connectingMessage: type === "video" ? t("pulse.call.preparing_video", "Synchronizing video channel...") : t("pulse.call.preparing_voice", "Preparing communication channel..."),
        readyMessage,
      });
      if (connected?.ok === false) return connected;
      minimizeCall(false);
      return data;
    } catch (error) {
      const payload = error.payload || {};
      return renderFailure(payload, {
        status: payload.status || "error",
        error_code: "UNKNOWN_ERROR",
        error_title: error.message || "Call could not start",
        error_description: payload.message || "PulseSoc could not start this call.",
        remediation: "Inspect the correlation ID in Calls Command Center.",
      });
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
    startCallTone("incoming");
    const name = displayNameFor(call);
    renderMode("incoming", `${name} ${t("pulse.call.incoming_suffix", "is Pulsing You...")}`);
    const meta = qs("[data-call-meta]", ensureShell());
    if (meta) meta.textContent = callType(call) === "video" ? t("pulse.call.incoming_video", "Video Connection") : t("pulse.call.incoming_voice", "Voice Connection");
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
      stopCallTone();
      renderMode("active", t("pulse.call.accepted", "Pulse Accepted"));
      statusSequence([
        { delay: 550, message: t("pulse.call.synchronizing", "Synchronizing...") },
        { delay: 900, message: t("pulse.call.establishing", "Establishing Secure Connection...") },
      ]);
      const data = await postJson(`${API}/${encodeURIComponent(id)}/accept`, { device_info: deviceInfo() });
      const connected = await connectCallRoom(data, { mode: "active", markConnected: true, connectingMessage: t("pulse.call.synchronizing", "Synchronizing..."), readyMessage: t("pulse.call.connected", "Pulse Connected") });
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
      stopCallTone();
      await hideCallShell();
      return data;
    } catch (error) {
      renderMode("failed", error.payload?.message || "Could not decline this call.");
      return error.payload || { ok: false, message: error.message };
    }
  }

  async function endCall(id = callId()) {
    state.ending = true;
    const endButton = qs("[data-call-end]");
    if (endButton) {
      endButton.disabled = true;
      endButton.classList.add("is-ending");
      endButton.setAttribute("aria-label", "Ending call");
    }
    if (!id) {
      await hideCallShell();
      state.ending = false;
      return { ok: true };
    }
    const endRequest = postJson(`${API}/${encodeURIComponent(id)}/end`, { reason: "ended_by_user" }).catch((error) => error.payload || { ok: false, message: error.message });
    try {
      await disconnectRoom("ended_by_user");
      state.activeCall = null;
      const shell = ensureShell();
      shell.hidden = true;
      shell.classList.remove("is-minimized", "controls-visible");
      showControls(false, true);
      state.ending = false;
      const data = await endRequest;
      return data?.ok === false ? data : { ok: true, ...(data || {}) };
    } catch (error) {
      state.ending = false;
      await hideCallShell();
      return error.payload || { ok: false, message: error.message };
    }
  }

  async function joinCallRoom(id) {
    const data = await postJson(`${API}/${encodeURIComponent(id)}/join-token`, { device_info: deviceInfo() });
    const connected = await connectCallRoom(data, { mode: "active", markConnected: true, connectingMessage: t("pulse.call.synchronizing", "Synchronizing..."), readyMessage: t("pulse.call.connected", "Pulse Connected") });
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
    await setControl(state.mutedAudio ? "mute-audio" : "unmute-audio", {
      local_track_count: tracksByKind("audio").length,
    });
    setStatus(state.mutedAudio ? t("pulse.call.mic_muted", "Microphone muted.") : t("pulse.call.mic_on", "Microphone on."), "info");
    const btn = qs("[data-call-toggle-mic]");
    if (btn) {
      btn.classList.toggle("is-muted", state.mutedAudio);
      btn.setAttribute("aria-label", state.mutedAudio ? "Unmute microphone" : "Mute microphone");
      setControlButton(btn, state.mutedAudio ? "&#127908;" : "&#127908;", state.mutedAudio ? "Unmute" : "Mic");
    }
    return state.mutedAudio;
  }

  async function toggleCamera() {
    if (callType() !== "video") return false;
    const turningOff = syncLocalCameraSurface();
    state.mutedVideo = turningOff;
    if (turningOff) {
      try { await state.room?.localParticipant?.setCameraEnabled?.(false); } catch (_) {}
      await stopLocalTracks("video");
      await setControl("disable-video", { unpublished: true });
    } else {
      try {
        await publishSingleLocalTrack("video");
        await setControl("enable-video", { republished: true, facing_mode: state.facingMode });
        syncLocalCameraSurface();
      } catch (error) {
        state.mutedVideo = true;
        await setControl("disable-video", { republish_failed: true, error: error?.name || error?.message || "video_failed" });
        setStatus(error?.name === "NotAllowedError" ? "Camera permission needed." : "Camera could not turn on.", "error");
      }
    }
    const live = syncLocalCameraSurface();
    setStatus(live ? t("pulse.call.camera_on", "Camera on.") : t("pulse.call.camera_off", "Camera off."), "info");
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
        syncLocalCameraSurface();
        await setControl("switch-camera", { facing_mode: state.facingMode, method: "restartTrack" });
        setStatus(t("pulse.call.camera_switched", "Camera switched."), "success");
        return { ok: true };
      }
      if (state.room?.localParticipant) {
        await stopLocalTracks("video");
        await publishSingleLocalTrack("video");
        syncLocalCameraSurface();
        await setControl("switch-camera", { facing_mode: state.facingMode, method: "republish" });
        setStatus(t("pulse.call.camera_switched", "Camera switched."), "success");
        return { ok: true };
      }
      setStatus(t("pulse.call.camera_switch_unsupported", "Camera switching is not supported by this browser session."), "warn");
      await setControl("switch-camera", { facing_mode: state.facingMode, unsupported: true });
      return { ok: false, status: "unsupported" };
    } catch (error) {
      setStatus("Camera could not switch.", "error");
      return { ok: false, message: error.message };
    }
  }

  async function switchSpeaker() {
    const audioEls = qsa("[data-call-audio] audio");
    const canSink = audioEls.some((el) => typeof el.setSinkId === "function");
    if (!canSink || !navigator.mediaDevices?.enumerateDevices) {
      state.speakerMode = "device";
      await setControl("speaker", { mode: "device_controlled", supported: false });
      setStatus(t("pulse.call.speaker_device", "Audio output follows this device."), "info");
      return { ok: true, status: "device_controlled" };
    }
    try {
      const outputs = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "audiooutput");
      if (!outputs.length) {
        await setControl("speaker", { mode: "device_controlled", supported: true, outputs: 0 });
        setStatus(t("pulse.call.speaker_device", "Audio output follows this device."), "info");
        return { ok: true, status: "device_controlled" };
      }
      const currentIndex = outputs.findIndex((device) => device.deviceId === state.audioOutputDeviceId);
      const next = outputs[(currentIndex + 1) % outputs.length];
      await Promise.all(audioEls.map((el) => el.setSinkId(next.deviceId).catch(() => {})));
      state.audioOutputDeviceId = next.deviceId;
      state.speakerMode = next.label || "selected";
      await setControl("speaker", { mode: "selected_output", device_label: next.label || "audiooutput" });
      setStatus(t("pulse.call.speaker_changed", "Audio output switched."), "success");
      return { ok: true, status: "selected", device_id: next.deviceId };
    } catch (error) {
      await setControl("speaker", { mode: "failed", error: error?.name || error?.message || "speaker_failed" });
      setStatus("Speaker output could not switch on this device.", "warn");
      return { ok: false, status: "speaker_failed", message: error.message };
    }
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
      const roomState = String(state.room?.state || state.room?.connectionState || "").toLowerCase();
      const score = roomState.includes("connected") ? 92 : roomState.includes("reconnect") ? 42 : 70;
      submitQualityReport(callId(), {
        quality_score: score,
        network_type: navigator.connection?.effectiveType || "",
        resolution: `${window.innerWidth}x${window.innerHeight}`,
        reconnect_count: state.reconnectCount,
        speaker_mode: state.speakerMode,
        muted_audio: state.mutedAudio,
        muted_video: callType() === "video" ? !localCameraIsLive() : false,
        hidden: document.hidden,
        local_audio_tracks: tracksByKind("audio").length,
        local_video_tracks: tracksByKind("video").length,
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
          stopCallTone();
          renderMode("failed", status === "declined" ? t("pulse.call.declined", "Pulse Declined") : status === "missed" ? t("pulse.call.missed", "Missed Pulse") : status === "failed" ? t("pulse.call.interrupted", "Pulse Interrupted") : t("pulse.call.ended", "Pulse Ended"));
          window.setTimeout(() => hideCallShell(), 1400);
          return;
        }
        if (isIncoming(call)) showIncoming(call);
        else if (status === "ringing") renderMode("outgoing", t("pulse.call.waiting", "Waiting for response..."));
        else renderMode("active", status === "reconnecting" ? t("pulse.call.restoring", "Restoring Pulse...") : t("pulse.call.connected", "Pulse Connected"));
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
        renderMode("outgoing", call.status === "ringing" ? t("pulse.call.waiting", "Waiting for response...") : t("pulse.call.outgoing", "Pulsing..."));
      }
    }).catch(() => {});
  }

  async function handleAppVisibility() {
    if (!state.activeCall) return;
    if (document.hidden) {
      state.visibilityWasHidden = true;
      await setControl("visibility", { state: "background", tracks: state.localTracks.map((track) => ({ kind: localTrackKind(track), ready_state: mediaTrack(track)?.readyState || "" })) });
      setStatus(t("pulse.call.background_limited", "Device background mode may pause microphone. PulseSoc will restore it when possible."), "info");
      return;
    }
    if (!state.visibilityWasHidden) return;
    state.visibilityWasHidden = false;
    await setControl("visibility", { state: "foreground" });
    if (!state.mutedAudio) await ensureLocalAudioTrack().catch(() => {});
    if (callType() === "video" && !state.mutedVideo && !tracksByKind("video").length) await publishSingleLocalTrack("video").catch(() => {});
    setStatus(t("pulse.call.restored", "Pulse Restored"), "success", qualityLabel());
    wakeCallPolling();
  }

  document.addEventListener("visibilitychange", () => {
    handleAppVisibility().catch(() => {});
  });

  window.addEventListener("focus", wakeCallPolling);
  window.addEventListener("pageshow", wakeCallPolling);
  window.addEventListener("online", () => {
    setStatus(t("pulse.call.restored", "Pulse Restored"), "success", qualityLabel());
    wakeCallPolling();
  });
  window.addEventListener("offline", () => setStatus(`${t("pulse.call.lost", "Pulse Lost")}. Attempting recovery...`, "warn", "Offline"));

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
