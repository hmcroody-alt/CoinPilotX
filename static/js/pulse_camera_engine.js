(async function () {
  const root = document.querySelector("[data-pulse-camera-engine]");
  if (!root) return;

  document.body.classList.add("pulse-camera-active");

  const configEl = document.getElementById("pulseCameraConfig");
  let config = configEl ? JSON.parse(configEl.textContent || "{}") : {};
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
  const previewFlow = document.getElementById("pulseCameraPreviewFlow");
  const previewStage = document.getElementById("pulsePreviewStage");
  const previewTitle = document.getElementById("pulsePreviewTitle");
  const previewMeta = document.getElementById("pulsePreviewMeta");
  const previewStatus = document.getElementById("pulsePreviewStatus");
  const previewPublish = document.getElementById("pulsePreviewPublish");
  const previewCaption = document.getElementById("pulsePreviewCaption");
  const previewPrivacy = document.getElementById("pulsePreviewPrivacy");

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
  let stagedDestination = "";
  let stagedMedia = null;
  let stagedPreviewToken = "";
  let localPreviewUrl = "";
  let busy = false;
  let idleTimer = null;
  let smoothedLight = 0.62;
  let smoothedMotion = 0;
  let lastFrameSample = null;

  function setStatus(message) {
    if (statusEl) statusEl.textContent = message || "";
  }

  function mergeCameraConfig(nextConfig) {
    if (!nextConfig || typeof nextConfig !== "object") return;
    config = {
      ...config,
      ...nextConfig,
      banuba: { ...(config.banuba || {}), ...(nextConfig.banuba || {}) },
      fallback: { ...(config.fallback || {}), ...(nextConfig.fallback || {}) },
    };
    root.dataset.cameraProvider = config.provider || "native_fallback";
    root.dataset.banubaEnabled = config.banuba?.enabled ? "true" : "false";
    root.dataset.deviceFilePickerFallback = config.fallback?.enabled === false ? "false" : "true";
  }

  async function hydrateCameraConfig() {
    const endpoint = config.configEndpoint || "/api/pulse/camera/config";
    const params = new URLSearchParams({ target: config.target || "feed", mode: config.mode || activeMode || "photo" });
    try {
      const response = await fetch(`${endpoint}?${params.toString()}`, { credentials: "same-origin", cache: "no-store" });
      const payload = await response.json().catch(() => null);
      if (response.ok && payload?.camera) mergeCameraConfig(payload.camera);
    } catch (_) {
      mergeCameraConfig({ provider: "native_fallback", fallback: { enabled: true, type: "device_file_picker" } });
    }
  }

  function activateFallbackPicker(message) {
    mergeCameraConfig({ provider: "native_fallback", fallback: { enabled: true, type: "device_file_picker" } });
    permissionTip?.classList.remove("is-hidden");
    if (message) setStatus(message);
  }

  function banubaSdkReady() {
    return Boolean(window.Banuba || window.BanubaSDK || window.BanubaPlayer);
  }

  function setPreviewStatus(message) {
    if (previewStatus) previewStatus.textContent = message || "";
  }

  function setBusy(isBusy, message = "") {
    busy = Boolean(isBusy);
    document.querySelectorAll("[data-preview-destination], [data-publish-destination], #pulsePreviewPublish").forEach((button) => {
      if (button) button.disabled = busy;
    });
    if (message) {
      setStatus(message);
      setPreviewStatus(message);
    }
  }

  function wakeHud() {
    root.classList.remove("is-idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => root.classList.add("is-idle"), 2600);
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
    if (config.banuba?.enabled && !banubaSdkReady()) {
      activateFallbackPicker("Camera effects are unavailable here. Native camera and gallery are ready.");
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      activateFallbackPicker("Camera is unavailable in this browser. Upload from gallery instead.");
      return;
    }
    try {
      stopStream();
      setStatus("Opening camera...");
      stream = await navigator.mediaDevices.getUserMedia(mediaConstraints());
      video.srcObject = stream;
      root.classList.toggle("is-front", facingMode === "user");
      permissionTip?.classList.add("is-hidden");
      startIntelligenceLoop();
      setStatus("Ready");
      setTimeout(() => setStatus(""), 800);
    } catch (error) {
      activateFallbackPicker("Camera permission blocked. Upload from gallery is ready.");
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
    const lowLightBoost = smoothedLight < 0.42 ? ` brightness(${1.08 + (0.42 - smoothedLight) * 0.42}) contrast(1.08)` : "";
    const motionSharpness = smoothedMotion > 24 ? " blur(.18px)" : " sharpen(0)";
    root.style.setProperty("--pulse-camera-filter", `${beauty} ${lens}${lowLightBoost} ${motionSharpness} opacity(${0.78 + intensity * 0.22})`);
    root.style.setProperty("--pulse-lens-overlay", activeLens?.overlay || "transparent");
    root.style.setProperty("--pulse-light-level", String(smoothedLight.toFixed(3)));
    if (particles) particles.hidden = !activeLens || activeLens.particles === "none";
  }

  function startIntelligenceLoop() {
    const intelligenceCanvas = document.createElement("canvas");
    intelligenceCanvas.width = 28;
    intelligenceCanvas.height = 28;
    const ctx = intelligenceCanvas.getContext("2d", { willReadFrequently: true });
    let lastRun = 0;
    function tick(ts) {
      if (!stream || document.hidden) return;
      if (ts - lastRun > 700 && video.readyState >= 2) {
        lastRun = ts;
        try {
          ctx.drawImage(video, 0, 0, intelligenceCanvas.width, intelligenceCanvas.height);
          const data = ctx.getImageData(0, 0, intelligenceCanvas.width, intelligenceCanvas.height).data;
          let total = 0;
          let diff = 0;
          for (let i = 0; i < data.length; i += 4) {
            const luma = (data[i] * 0.2126 + data[i + 1] * 0.7152 + data[i + 2] * 0.0722) / 255;
            total += luma;
            if (lastFrameSample) diff += Math.abs(luma - lastFrameSample[i / 4]);
          }
          const sample = [];
          for (let i = 0; i < data.length; i += 4) sample.push((data[i] * 0.2126 + data[i + 1] * 0.7152 + data[i + 2] * 0.0722) / 255);
          smoothedLight = smoothedLight * 0.82 + (total / sample.length) * 0.18;
          smoothedMotion = smoothedMotion * 0.78 + (lastFrameSample ? diff / sample.length * 100 : 0) * 0.22;
          lastFrameSample = sample;
          root.classList.toggle("is-low-light", smoothedLight < 0.38);
          root.classList.toggle("is-motion", smoothedMotion > 18);
          applyEffect();
        } catch (_) {}
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
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
    localPreviewUrl = URL.createObjectURL(capturedBlob);
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
      localPreviewUrl = URL.createObjectURL(capturedBlob);
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

  async function uploadCapture(retries = 1) {
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
    try {
      setBusy(true, isVideo ? "Uploading video... 0%" : "Uploading image... 0%");
      const data = window.PulseUploadManager
        ? await window.PulseUploadManager.upload({
            url: config.uploadEndpoint || "/api/pulse/media/upload",
            formData: fd,
            file,
            lockKey: "pulse-camera-upload",
            progressTarget: processing,
            mediaType: isVideo ? "video/mp4" : "image/jpeg",
            onProgress: (state) => {
              setStatus(state.message);
              setPreviewStatus(state.message);
            },
          })
        : await fetch(config.uploadEndpoint || "/api/pulse/media/upload", { method: "POST", credentials: "same-origin", body: fd }).then(async (response) => {
            const payload = await response.json().catch(() => ({ ok: false, message: "Upload returned an unreadable response." }));
            if (!response.ok || payload.ok === false) throw new Error(payload.message || "Upload failed.");
            return payload;
          });
      const media = data.media || {};
      if (!media.media_url) throw new Error("Upload did not return a media URL.");
      setStatus("Processing media...");
      return media;
    } catch (error) {
      if (retries > 0) {
        setStatus("Upload interrupted. Retrying...");
        await new Promise((resolve) => setTimeout(resolve, 900));
        return uploadCapture(retries - 1);
      }
      throw error;
    }
  }

  function previewMarkup(destination, sourceUrl, isVideo) {
    const safeCaption = (previewCaption?.value || "Created with Pulse Camera").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
    const mediaHtml = isVideo
      ? `<video src="${sourceUrl}" autoplay loop playsinline controls></video>`
      : `<img src="${sourceUrl}" alt="Pulse Camera preview" decoding="async">`;
    if (destination === "status") {
      return `<article class="pulse-preview-status">${mediaHtml}<div class="pulse-preview-progress"></div><div class="pulse-preview-copy"><strong>Your Pulse Status</strong><span>${safeCaption}</span></div></article>`;
    }
    if (destination === "reel") {
      return `<article class="pulse-preview-reel">${mediaHtml}<aside><span>🔥</span><span>💬</span><span>↗</span></aside><div class="pulse-preview-copy"><strong>@you · Pulse Reel</strong><span>${safeCaption}</span><small>Original Pulse sound</small></div></article>`;
    }
    return `<article class="pulse-preview-feed-card"><header><span class="pulse-preview-avatar">P</span><div><strong>Pulse Camera</strong><small>Public · preview</small></div></header>${mediaHtml}<p>${safeCaption}</p><footer><span>Like</span><span>Comment</span><span>Share</span></footer></article>`;
  }

  async function openPreview(destination) {
    if (busy) return;
    const fallback = fileInput.files?.[0];
    if (!capturedBlob && !fallback) {
      setStatus("Capture or choose media first.");
      return;
    }
    stagedDestination = destination;
    stagedMedia = null;
    stagedPreviewToken = "";
    sheet?.classList.remove("is-on");
    const file = fallback || capturedBlob;
    if (!localPreviewUrl || fallback) localPreviewUrl = URL.createObjectURL(file);
    const isVideo = (fallback?.type || capturedMime || "").startsWith("video/");
    previewFlow?.classList.add("is-on");
    previewFlow?.setAttribute("aria-hidden", "false");
    previewTitle.textContent = destination === "status" ? "Status Preview" : destination === "reel" ? "Reel Preview" : "Pulse Post Preview";
    previewMeta.textContent = destination === "status" ? "Fullscreen story preview with privacy and duration." : destination === "reel" ? "Immersive vertical preview matching Reels placement." : "Feed card preview before publishing.";
    previewStage.innerHTML = previewMarkup(destination, localPreviewUrl, isVideo);
    setPreviewStatus("Preview ready. Publish when it looks right.");
  }

  async function publish(destination) {
    try {
      processing?.classList.add("is-on");
      setBusy(true, "Uploading...");
      const media = await uploadCapture();
      if (!media?.id) throw new Error("Media upload did not return a valid asset.");
      stagedMedia = media;
      setBusy(true, "Preparing preview record...");
      const previewResponse = await fetch("/api/pulse/camera/preview", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          destination,
          media,
          caption: previewCaption?.value || "Created with Pulse Camera",
          privacy: previewPrivacy?.value || "public",
          effect_key: activeLens?.key || "",
          beauty_key: activeBeauty?.key || "",
        }),
      });
      const previewData = await previewResponse.json().catch(() => ({ ok: false, message: "Preview could not be saved." }));
      if (!previewResponse.ok || previewData.ok === false) throw new Error(previewData.message || "Preview could not be saved.");
      stagedPreviewToken = previewData.preview_token || "";
      setBusy(true, "Publishing...");
      window.PulseUploadManager?.render(processing, { stage: "publishing", percent: 96, message: "Publishing..." });
      const mediaType = media.media_type === "video" ? "video" : "image";
      if (destination === "status") {
        const payload = { status_type: mediaType, body: previewCaption?.value || "Captured with Pulse Camera", media_ids: [media.id], visibility: previewPrivacy?.value || "public", duration_hours: 24 };
        const response = await fetch("/api/pulse/status", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || "Status publish failed.");
        await markPreviewPublished("status", data.status?.id || 0);
        setBusy(true, "Posted successfully");
        window.PulseUploadManager?.render(processing, { stage: "success", percent: 100, message: "Posted successfully" });
        setPreviewStatus("Published.");
        location.href = "/pulse/status";
        return;
      }
      if (destination === "reel") {
        const payload = { title: "Camera Reel", caption: previewCaption?.value || "Created with Pulse Camera", category: "Community", visibility: previewPrivacy?.value || "public", post_type: mediaType, media_ids: [media.id] };
        const response = await fetch("/api/pulse/reels/create", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || "Reel publish failed.");
        await markPreviewPublished("reel", data.reel?.id || data.reel_id || 0);
        setBusy(true, "Posted successfully");
        window.PulseUploadManager?.render(processing, { stage: "success", percent: 100, message: "Posted successfully" });
        setPreviewStatus("Published.");
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
      const payload = { media_id: media.id, media_url: media.media_url, title: "Pulse Camera", body: previewCaption?.value || "Created with Pulse Camera", post_type: mediaType };
      const response = await fetch("/api/pulse/posts/create-from-camera", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.message || "Pulse post failed.");
      await markPreviewPublished("pulse_post", data.post_id || data.id || 0);
      setBusy(true, "Posted successfully");
      window.PulseUploadManager?.render(processing, { stage: "success", percent: 100, message: "Posted successfully" });
      setPreviewStatus("Published.");
      location.href = data.next_url || "/pulse";
    } catch (error) {
      processing?.classList.remove("is-on");
      setBusy(false);
      setStatus(error.message);
      setPreviewStatus(error.message + " You can retry without losing the capture.");
    }
  }

  async function markPreviewPublished(entityType, entityId) {
    if (!stagedPreviewToken) return;
    await fetch("/api/pulse/camera/preview/mark-published", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preview_token: stagedPreviewToken, entity_type: entityType, entity_id: entityId || 0 }),
    }).catch(() => {});
  }

  root.addEventListener("touchstart", (event) => {
    wakeHud();
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
    wakeHud();
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
  document.getElementById("pulseCameraGallery")?.addEventListener("click", () => {
    activateFallbackPicker("Choose a photo or video from your device.");
    fileInput.click();
  });
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
  document.querySelectorAll("[data-preview-destination]").forEach((button) => button.addEventListener("click", () => openPreview(button.dataset.previewDestination)));
  document.querySelectorAll("[data-publish-destination]").forEach((button) => button.addEventListener("click", () => publish(button.dataset.publishDestination)));
  document.querySelectorAll("[data-close-camera-sheet]").forEach((button) => button.addEventListener("click", () => sheet?.classList.remove("is-on")));
  document.querySelectorAll("[data-close-preview]").forEach((button) => button.addEventListener("click", () => {
    previewFlow?.classList.remove("is-on");
    previewFlow?.setAttribute("aria-hidden", "true");
    sheet?.classList.add("is-on");
  }));
  previewPublish?.addEventListener("click", () => publish(stagedDestination || "feed"));

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    capturedBlob = null;
    capturedMime = file.type || "application/octet-stream";
    localPreviewUrl = URL.createObjectURL(file);
    useBtn.disabled = false;
    openPreview(activeMode === "reel" ? "reel" : config.target === "status" ? "status" : "feed");
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
  await hydrateCameraConfig();
  selectMode(activeMode);
  wakeHud();
  startCamera();
})();
