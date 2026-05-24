(function () {
  const root = document.querySelector("[data-pulse-camera-engine]");
  if (!root) return;

  document.body.classList.add("pulse-camera-active");

  const configEl = document.getElementById("pulseCameraConfig");
  const config = configEl ? JSON.parse(configEl.textContent || "{}") : {};
  const video = document.getElementById("pulseCameraPreview");
  const canvas = document.getElementById("pulseCameraCanvas");
  const fileInput = document.getElementById("pulseCameraFile");
  const statusEl = document.getElementById("pulseCameraStatus");
  const permissionTip = document.getElementById("pulseCameraPermission");
  const focusRing = document.getElementById("pulseCameraFocusRing");
  const processing = document.getElementById("pulseCameraProcessing");
  const sheet = document.getElementById("pulseCameraSheet");
  const timerEl = document.getElementById("pulseCameraTimer");
  const captureBtn = document.getElementById("pulseCameraCapture");
  const useBtn = document.getElementById("pulseCameraUse");
  const particles = document.getElementById("pulseCameraParticles");

  let stream = null;
  let recorder = null;
  let chunks = [];
  let capturedBlob = null;
  let capturedMime = "image/jpeg";
  let facingMode = "user";
  let activeLens = config.lenses?.[0] || null;
  let activeBeauty = config.beautyModes?.[0] || null;
  let activeMode = config.mode || "photo";
  let zoom = 1;
  let initialPinchDistance = 0;
  let initialZoom = 1;
  let touchStart = null;
  let lastTap = 0;
  let recording = false;
  let recordingStartedAt = 0;
  let recordingTimer = null;
  let holdStartY = 0;

  function setStatus(message) {
    if (statusEl) statusEl.textContent = message || "";
  }

  function vibrate(ms = 12) {
    if (navigator.vibrate) navigator.vibrate(ms);
  }

  function stopStream() {
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
  }

  function mediaConstraints() {
    return {
      video: {
        facingMode,
        width: { ideal: 1080 },
        height: { ideal: 1920 },
        frameRate: { ideal: 30, max: 60 },
      },
      audio: activeMode === "photo" ? false : {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    };
  }

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("Camera is unavailable in this browser. Upload from gallery instead.");
      return;
    }
    try {
      stopStream();
      setStatus("Opening camera...");
      stream = await navigator.mediaDevices.getUserMedia(mediaConstraints());
      video.srcObject = stream;
      root.classList.toggle("is-front", facingMode === "user");
      permissionTip?.classList.add("is-hidden");
      setStatus("Ready");
      setTimeout(() => setStatus(""), 800);
    } catch (error) {
      setStatus("Camera permission blocked. Upload from gallery is ready.");
      permissionTip?.classList.remove("is-hidden");
    }
  }

  function setZoom(nextZoom) {
    zoom = Math.min(4, Math.max(1, nextZoom));
    root.style.setProperty("--pulse-camera-zoom", String(zoom.toFixed(2)));
    const track = stream?.getVideoTracks?.()[0];
    const caps = track?.getCapabilities?.();
    if (caps?.zoom) {
      track.applyConstraints({ advanced: [{ zoom: Math.min(caps.zoom.max, Math.max(caps.zoom.min, zoom)) }] }).catch(() => {});
    }
  }

  function distance(a, b) {
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  }

  function applyEffect() {
    const intensity = Number(document.getElementById("pulseCameraIntensity")?.value || 72) / 100;
    const beauty = activeBeauty?.css_filter || "brightness(1.04) contrast(1.04) saturate(1.05)";
    const lens = activeLens?.css_filter || "";
    root.style.setProperty("--pulse-camera-filter", `${beauty} ${lens} opacity(${0.78 + intensity * 0.22})`);
    root.style.setProperty("--pulse-lens-overlay", activeLens?.overlay || "transparent");
    if (particles) particles.hidden = !activeLens || activeLens.particles === "none";
  }

  function selectLens(key) {
    const next = (config.lenses || []).find((lens) => lens.key === key);
    if (!next) return;
    if (next.locked) {
      setStatus("That lens unlocks with Pulse Premium.");
      return;
    }
    activeLens = next;
    document.querySelectorAll("[data-lens-key]").forEach((button) => button.classList.toggle("is-active", button.dataset.lensKey === key));
    applyEffect();
    setStatus(`${next.label} lens ready`);
  }

  function selectBeauty(key) {
    const next = (config.beautyModes || []).find((mode) => mode.key === key);
    if (!next) return;
    activeBeauty = next;
    document.querySelectorAll("[data-beauty-key]").forEach((button) => button.classList.toggle("is-active", button.dataset.beautyKey === key));
    applyEffect();
  }

  function selectMode(mode) {
    activeMode = mode;
    document.querySelectorAll("[data-camera-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.cameraMode === mode));
    if (stream) startCamera();
    setStatus(mode === "photo" ? "Tap to capture" : "Hold to record");
  }

  async function flipCamera() {
    facingMode = facingMode === "user" ? "environment" : "user";
    vibrate(16);
    await startCamera();
  }

  function showFocus(clientX, clientY) {
    const rect = root.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    root.style.setProperty("--pulse-focus-x", `${(x / rect.width) * 100}%`);
    root.style.setProperty("--pulse-focus-y", `${(y / rect.height) * 100}%`);
    if (focusRing) {
      focusRing.style.left = `${x}px`;
      focusRing.style.top = `${y}px`;
      focusRing.classList.remove("is-on");
      void focusRing.offsetWidth;
      focusRing.classList.add("is-on");
    }
    const track = stream?.getVideoTracks?.()[0];
    track?.applyConstraints?.({ advanced: [{ focusMode: "continuous", exposureMode: "continuous" }] }).catch(() => {});
  }

  async function capturePhotoBlob() {
    const w = video.videoWidth || 1080;
    const h = video.videoHeight || 1920;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.filter = getComputedStyle(root).getPropertyValue("--pulse-camera-filter") || "none";
    if (facingMode === "user" && !document.getElementById("pulseUnmirrorFinal")?.checked) {
      ctx.translate(w, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(video, 0, 0, w, h);
    ctx.restore();
    if (activeLens?.overlay && activeLens.effect_type !== "segmentation_hook") {
      const gradient = ctx.createRadialGradient(w * 0.5, h * 0.32, 10, w * 0.5, h * 0.32, Math.max(w, h) * 0.48);
      gradient.addColorStop(0, "rgba(110,223,246,.12)");
      gradient.addColorStop(1, "rgba(110,223,246,0)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, w, h);
    }
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.94));
  }

  async function capturePhoto() {
    if (!stream) {
      await startCamera();
      return;
    }
    capturedBlob = await capturePhotoBlob();
    capturedMime = "image/jpeg";
    useBtn.disabled = false;
    vibrate(18);
    setStatus("Captured. Choose a destination.");
    sheet?.classList.add("is-on");
  }

  function startRecording() {
    if (!stream || recording) return;
    const mime = MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm";
    chunks = [];
    recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 4500000 });
    recorder.ondataavailable = (event) => {
      if (event.data?.size) chunks.push(event.data);
    };
    recorder.onstop = () => {
      capturedBlob = new Blob(chunks, { type: "video/webm" });
      capturedMime = "video/webm";
      useBtn.disabled = false;
      sheet?.classList.add("is-on");
      setStatus("Video ready. Choose a destination.");
    };
    recorder.start(250);
    recording = true;
    recordingStartedAt = Date.now();
    captureBtn.classList.add("is-recording");
    timerEl?.classList.add("is-on");
    recordingTimer = setInterval(() => {
      const elapsed = Math.floor((Date.now() - recordingStartedAt) / 1000);
      if (timerEl) timerEl.textContent = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
      if (elapsed >= 60) stopRecording();
    }, 250);
    vibrate(22);
    setStatus("Recording...");
  }

  function stopRecording() {
    if (!recording) return;
    recording = false;
    captureBtn.classList.remove("is-recording");
    timerEl?.classList.remove("is-on");
    clearInterval(recordingTimer);
    if (recorder?.state === "recording") recorder.stop();
    vibrate(18);
  }

  async function uploadCapture() {
    const fallback = fileInput.files?.[0];
    const file = fallback || capturedBlob;
    if (!file) {
      setStatus("Capture or choose media first.");
      return null;
    }
    const fd = new FormData();
    const isVideo = (fallback?.type || capturedMime || "").startsWith("video/");
    fd.append("file", file, fallback?.name || (isVideo ? "pulse-camera.webm" : "pulse-camera.jpg"));
    fd.append("context_type", "pulse_camera");
    fd.append("context_id", config.target || "feed");
    fd.append("target", config.target || "feed");
    fd.append("mode", activeMode);
    fd.append("filter_name", activeBeauty?.key || "natural");
    fd.append("effect_key", activeLens?.key || "pulse_glow");
    const response = await fetch("/api/pulse/media/upload", { method: "POST", credentials: "same-origin", body: fd });
    const data = await response.json().catch(() => ({ ok: false, message: "Upload returned an unreadable response." }));
    if (!response.ok || data.ok === false) throw new Error(data.message || "Upload failed.");
    return data.media || {};
  }

  async function publish(destination) {
    try {
      processing?.classList.add("is-on");
      const media = await uploadCapture();
      if (!media?.id) throw new Error("Media upload did not return a valid asset.");
      const mediaType = media.media_type === "video" ? "video" : "image";
      if (destination === "status") {
        const payload = { status_type: mediaType, body: "Captured with Pulse Camera", media_ids: [media.id], visibility: "public", duration_hours: 24 };
        const response = await fetch("/api/pulse/status", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || "Status publish failed.");
        location.href = "/pulse/status";
        return;
      }
      if (destination === "reel") {
        const payload = { title: "Camera Reel", caption: "Created with Pulse Camera", category: "Community", visibility: "public", post_type: mediaType, media_ids: [media.id] };
        const response = await fetch("/api/pulse/reels/create", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || "Reel publish failed.");
        location.href = data.next_url || "/pulse/reels";
        return;
      }
      if (destination === "message" && Number(config.conversationId || 0) > 0) {
        const payload = { conversation_id: Number(config.conversationId), message_type: mediaType, media_url: media.media_url, thumbnail_url: media.thumbnail_url || media.media_url, body: "" };
        const response = await fetch("/api/pulse/messages/send", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || "Message send failed.");
        location.href = `/pulse/messages/${Number(config.conversationId)}`;
        return;
      }
      if (destination === "draft") {
        setStatus("Saved as an uploaded media draft.");
        sheet?.classList.remove("is-on");
        processing?.classList.remove("is-on");
        return;
      }
      const payload = { media_id: media.id, media_url: media.media_url, title: "Pulse Camera", body: "Created with Pulse Camera", post_type: mediaType };
      const response = await fetch("/api/pulse/posts/create-from-camera", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.message || "Pulse post failed.");
      location.href = data.next_url || "/pulse";
    } catch (error) {
      processing?.classList.remove("is-on");
      setStatus(error.message);
    }
  }

  root.addEventListener("touchstart", (event) => {
    if (event.touches.length === 2) {
      initialPinchDistance = distance(event.touches[0], event.touches[1]);
      initialZoom = zoom;
    } else if (event.touches.length === 1) {
      touchStart = { x: event.touches[0].clientX, y: event.touches[0].clientY, t: Date.now() };
      holdStartY = event.touches[0].clientY;
    }
  }, { passive: true });

  root.addEventListener("touchmove", (event) => {
    if (event.touches.length === 2 && initialPinchDistance) {
      setZoom(initialZoom * (distance(event.touches[0], event.touches[1]) / initialPinchDistance));
    } else if (recording && event.touches.length === 1) {
      setZoom(zoom + ((holdStartY - event.touches[0].clientY) / 420));
      holdStartY = event.touches[0].clientY;
    }
  }, { passive: true });

  root.addEventListener("touchend", (event) => {
    if (!touchStart) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - touchStart.x;
    const dy = touch.clientY - touchStart.y;
    if (Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dy)) {
      const lenses = config.lenses || [];
      const index = Math.max(0, lenses.findIndex((lens) => lens.key === activeLens?.key));
      const next = lenses[(index + (dx < 0 ? 1 : -1) + lenses.length) % lenses.length];
      if (next) selectLens(next.key);
    } else if (dy < -90) {
      fileInput.click();
    } else if (dy > 110) {
      closeCamera();
    }
    touchStart = null;
  }, { passive: true });

  root.addEventListener("click", (event) => {
    if (event.target.closest("button,a,input,label,.pulse-camera-sheet")) return;
    const now = Date.now();
    if (now - lastTap < 320) {
      flipCamera();
      lastTap = 0;
      return;
    }
    lastTap = now;
    showFocus(event.clientX, event.clientY);
  });

  captureBtn.addEventListener("pointerdown", (event) => {
    if (activeMode === "photo") return;
    event.preventDefault();
    startRecording();
  });
  captureBtn.addEventListener("pointerup", (event) => {
    if (activeMode === "photo") return;
    event.preventDefault();
    stopRecording();
  });
  captureBtn.addEventListener("click", (event) => {
    if (activeMode !== "photo") return;
    event.preventDefault();
    capturePhoto();
  });

  document.getElementById("pulseCameraFlip")?.addEventListener("click", flipCamera);
  document.getElementById("pulseCameraClose")?.addEventListener("click", closeCamera);
  document.getElementById("pulseCameraGallery")?.addEventListener("click", () => fileInput.click());
  document.getElementById("pulseCameraUse")?.addEventListener("click", () => sheet?.classList.add("is-on"));
  document.getElementById("pulseCameraMic")?.addEventListener("click", () => {
    stream?.getAudioTracks().forEach((track) => { track.enabled = !track.enabled; });
    setStatus("Microphone toggled.");
  });
  document.getElementById("pulseCameraLight")?.addEventListener("click", async () => {
    const track = stream?.getVideoTracks?.()[0];
    try {
      await track?.applyConstraints?.({ advanced: [{ torch: true }] });
      setStatus("Light adjusted.");
    } catch (_) {
      setStatus("Flash is not available on this device.");
    }
  });
  document.getElementById("pulseCameraIntensity")?.addEventListener("input", applyEffect);
  document.querySelectorAll("[data-lens-key]").forEach((button) => button.addEventListener("click", () => selectLens(button.dataset.lensKey)));
  document.querySelectorAll("[data-beauty-key]").forEach((button) => button.addEventListener("click", () => selectBeauty(button.dataset.beautyKey)));
  document.querySelectorAll("[data-camera-mode]").forEach((button) => button.addEventListener("click", () => selectMode(button.dataset.cameraMode)));
  document.querySelectorAll("[data-publish-destination]").forEach((button) => button.addEventListener("click", () => publish(button.dataset.publishDestination)));
  document.querySelectorAll("[data-close-camera-sheet]").forEach((button) => button.addEventListener("click", () => sheet?.classList.remove("is-on")));

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    capturedBlob = null;
    capturedMime = file.type || "application/octet-stream";
    useBtn.disabled = false;
    sheet?.classList.add("is-on");
    setStatus("Gallery media ready.");
  });

  function closeCamera() {
    stopStream();
    location.href = config.closeUrl || "/pulse";
  }

  window.addEventListener("pagehide", stopStream);
  window.addEventListener("visibilitychange", () => {
    if (document.hidden) stopStream();
  });

  selectBeauty(activeBeauty?.key || "natural");
  selectLens(activeLens?.key || "pulse_glow");
  selectMode(activeMode);
  startCamera();
})();
