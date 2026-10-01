(function () {
  "use strict";

  const locks = new Map();
  const DIRECT_VIDEO_THRESHOLD_BYTES = 20 * 1024 * 1024;

  function textFor(stage, percent, type) {
    const media = type && type.startsWith("video") ? "video" : type && type.startsWith("image") ? "image" : "media";
    if (stage === "starting") return "Upload starting...";
    if (stage === "uploading" && media === "video") return `Uploading video... ${Math.max(0, Math.min(100, Math.round(percent || 0)))}%`;
    if (stage === "uploading" && media === "image") return `Uploading image... ${Math.max(0, Math.min(100, Math.round(percent || 0)))}%`;
    if (stage === "uploading") return `Uploading media... ${Math.max(0, Math.min(100, Math.round(percent || 0)))}%`;
    if (stage === "processing") return "Processing media...";
    if (stage === "publishing") return "Publishing...";
    if (stage === "success" || stage === "complete") return "Posted successfully";
    if (stage === "failed") return "Upload failed. Tap to retry.";
    return "Preparing media...";
  }

  function findProgressRoot(target) {
    if (!target) return null;
    if (target.nodeType === 1) return target.closest("[data-upload-progress]") || target;
    if (typeof target === "string") return document.querySelector(target);
    return null;
  }

  function render(root, state) {
    if (!root) return;
    const percent = Math.max(0, Math.min(100, Math.round(state.percent || 0)));
    root.dataset.uploadState = state.stage || "idle";
    root.classList.toggle("is-uploading", ["starting", "uploading", "processing", "publishing"].includes(state.stage));
    root.classList.toggle("is-success", state.stage === "success" || state.stage === "complete");
    root.classList.toggle("is-failed", state.stage === "failed");
    const bar = root.querySelector("[data-upload-progress-bar]");
    if (bar) {
      bar.style.width = `${percent}%`;
      bar.setAttribute("aria-valuenow", String(percent));
    }
    const text = root.querySelector("[data-upload-progress-text]");
    if (text) text.textContent = state.message || textFor(state.stage, percent, state.type);
  }

  function setButtonDisabled(button, disabled, label) {
    if (!button) return;
    button.disabled = !!disabled;
    if (label) {
      if (!button.dataset.originalText) button.dataset.originalText = button.textContent || "";
      button.textContent = label;
    } else if (!disabled && button.dataset.originalText) {
      button.textContent = button.dataset.originalText;
    }
  }

  function responseHeaders(xhr) {
    const out = {};
    String(xhr.getAllResponseHeaders() || "").trim().split(/[\r\n]+/).forEach((line) => {
      const index = line.indexOf(":");
      if (index > 0) out[line.slice(0, index).toLowerCase()] = line.slice(index + 1).trim();
    });
    return out;
  }

  function safeRawBody(text) {
    const body = String(text || "");
    return body.length > 1200 ? `${body.slice(0, 1200)}...` : body;
  }

  function uploadParseError(xhr) {
    const headers = responseHeaders(xhr);
    const contentType = xhr.getResponseHeader("content-type") || headers["content-type"] || "";
    const edgeRay = xhr.getResponseHeader("cf-ray") || headers["cf-ray"] || "";
    const rawBody = safeRawBody(xhr.responseText || "");
    const diagnostic = {
      status: xhr.status,
      contentType,
      endpoint: xhr.responseURL || "",
      edgeRay,
      headers,
      first500: rawBody.slice(0, 500),
      rawBody,
    };
    console.warn("PulseSoc upload response parse failed", diagnostic);
    const lower = rawBody.toLowerCase();
    let message = "Upload returned a non-JSON response from the server.";
    if (xhr.status === 403 && (contentType.includes("text/html") || lower.includes("cloudflare") || lower.includes("attention required"))) {
      message = "Upload was blocked by site security. Please try again or contact support.";
    } else if (xhr.status === 524 || lower.includes("cloudflare error 524") || lower.includes("a timeout occurred")) {
      message = "Upload timed out before PulseSoc received it. Try a smaller video and retry.";
    } else if (xhr.status >= 500 && contentType.includes("text/html")) {
      message = "Upload failed on the server. Please retry or contact support if it continues.";
    } else if (xhr.status === 401 || lower.includes("/login") || lower.includes("login")) {
      message = "Session expired. Please sign in and retry the upload.";
    } else if (xhr.status === 413) {
      message = "This upload is too large for the standard upload lane. Large videos should use direct Mux upload.";
    } else if (lower.includes("request entity too large") || lower.includes("payload too large")) {
      message = "This upload is too large for the standard upload lane. Large videos should use direct Mux upload.";
    } else if (contentType.includes("text/html")) {
      message = "Upload returned an HTML page instead of JSON. Please retry after refreshing.";
    } else if (!rawBody.trim()) {
      message = "Upload returned an empty response. Please retry.";
    }
    return { ok: false, success: false, message, error: "non_json_upload_response", upload_debug: diagnostic };
  }

  function isVideoFile(file, type) {
    return !!file && (String(type || file.type || "").toLowerCase().startsWith("video/") || /\.(mp4|mov|webm|m4v)$/i.test(file.name || ""));
  }

  function fieldFromForm(form, name, fallback) {
    try {
      const value = form && form.get ? form.get(name) : "";
      return value == null || value === "" ? fallback : value;
    } catch (_) {
      return fallback;
    }
  }

  function shouldUseDirectMux(file, type, opts) {
    if (!isVideoFile(file, type)) return false;
    if (opts.directMux === false || opts.disableDirectMux) return false;
    if (opts.directMux === true || opts.preferDirectMux) return true;
    if (!/Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent || "") && Number(file.size || 0) >= 8 * 1024 * 1024) return true;
    const threshold = Number(opts.directMuxThresholdBytes || DIRECT_VIDEO_THRESHOLD_BYTES);
    return Number(file.size || 0) >= threshold;
  }

  function uploadMuxDirect(opts, file, root, type, key, form) {
    return new Promise(async (resolve, reject) => {
      let mediaId = 0;
      let uploadId = "";
      try {
        const startState = { stage: "starting", percent: 2, message: "Preparing large video upload...", type };
        render(root, startState);
        opts.onProgress && opts.onProgress(startState);
        const startResponse = await fetch("/api/pulse/media/mux/direct-upload", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename: file.name || "pulse-video.mp4",
            mime_type: file.type || opts.mediaType || "video/mp4",
            size: Number(file.size || 0),
            context_type: fieldFromForm(form, "context_type", opts.contextType || "pulse_video"),
            context_id: fieldFromForm(form, "context_id", opts.contextId || "direct_mux"),
            origin: window.location.origin,
          }),
        });
        const startText = await startResponse.text();
        let startData = {};
        try { startData = JSON.parse(startText || "{}"); } catch (_) { startData = { ok: false, message: "Large video upload setup returned an unreadable response." }; }
        if (!startResponse.ok || startData.ok === false || !startData.upload_url) {
          throw new Error(startData.message || "Large video upload could not start.");
        }
        mediaId = startData.media_id || startData.media?.id || 0;
        uploadId = startData.upload_id || "";
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", startData.upload_url, true);
        xhr.withCredentials = false;
        if (file.type) xhr.setRequestHeader("Content-Type", file.type);
        xhr.upload.onprogress = function (event) {
          if (!event.lengthComputable) return;
          const percent = Math.min(88, Math.max(4, Math.round((event.loaded / event.total) * 88)));
          const state = { stage: "uploading", percent, loaded: event.loaded, total: event.total, message: `Uploading video directly to Mux... ${percent}%`, type };
          render(root, state);
          opts.onProgress && opts.onProgress(state);
        };
        xhr.onerror = function () {
          const error = new Error("Large video upload was interrupted. Your video is still selected; retry when ready.");
          render(root, { stage: "failed", percent: 0, message: error.message, type });
          setButtonDisabled(opts.button, false);
          locks.delete(key);
          reject(error);
        };
        xhr.onload = async function () {
          try {
            if (xhr.status < 200 || xhr.status >= 300) {
              throw new Error(`Mux upload failed with status ${xhr.status}. Please retry.`);
            }
            const processingState = { stage: "processing", percent: 92, message: "Video uploaded. Preparing playback...", type };
            render(root, processingState);
            opts.onProgress && opts.onProgress(processingState);
            const completeResponse = await fetch("/api/pulse/media/mux/direct-upload/complete", {
              method: "POST",
              credentials: "same-origin",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ media_id: mediaId, upload_id: uploadId }),
            });
            const completeText = await completeResponse.text();
            let completeData = {};
            try { completeData = JSON.parse(completeText || "{}"); } catch (_) { completeData = { ok: false, message: "Video upload completion returned an unreadable response." }; }
            if (!completeResponse.ok || completeData.ok === false) {
              throw new Error(completeData.message || "Video uploaded, but PulseSoc could not finish the media record.");
            }
            const done = { stage: "complete", percent: 100, message: "Video uploaded. Playback is processing.", type };
            render(root, done);
            setButtonDisabled(opts.button, false);
            locks.delete(key);
            resolve({ ...completeData, media: completeData.media || startData.media, media_id: mediaId, direct_upload: true });
          } catch (error) {
            render(root, { stage: "failed", percent: 0, message: error.message, type });
            setButtonDisabled(opts.button, false);
            locks.delete(key);
            reject(error);
          }
        };
        xhr.send(file);
      } catch (error) {
        render(root, { stage: "failed", percent: 0, message: error.message, type });
        setButtonDisabled(opts.button, false);
        locks.delete(key);
        reject(error);
      }
    });
  }

  function upload(options) {
    const opts = options || {};
    const file = opts.file;
    if (!file) return Promise.reject(new Error("Choose media before publishing."));
    const key = opts.lockKey || opts.url || "pulse-upload";
    if (locks.get(key)) return Promise.reject(new Error("Upload already in progress."));
    locks.set(key, true);
    const root = findProgressRoot(opts.progressTarget);
    const type = file.type || opts.mediaType || "";
    const form = opts.formData || new FormData();
    if (!opts.formData) {
      form.append(opts.fileField || "file", file, opts.filename || file.name || (type.startsWith("video") ? "pulse-video.webm" : "pulse-image.jpg"));
      Object.entries(opts.fields || {}).forEach(([name, value]) => form.append(name, value == null ? "" : value));
    }
    setButtonDisabled(opts.button, true, "Uploading...");
    render(root, { stage: "starting", percent: 1, message: textFor("starting", 1, type), type });
    if (shouldUseDirectMux(file, type, opts)) {
      return uploadMuxDirect(opts, file, root, type, key, form);
    }
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(opts.method || "POST", opts.url || "/api/pulse/media/upload", true);
      xhr.withCredentials = opts.credentials !== false;
      xhr.upload.onprogress = function (event) {
        if (!event.lengthComputable) return;
        const percent = Math.min(84, Math.max(3, Math.round((event.loaded / event.total) * 84)));
        const state = { stage: "uploading", percent, loaded: event.loaded, total: event.total, message: textFor("uploading", percent, type), type };
        render(root, state);
        opts.onProgress && opts.onProgress(state);
      };
      xhr.onerror = function () {
        const error = new Error("Network interrupted. Your media is still selected; retry when ready.");
        render(root, { stage: "failed", percent: 0, message: error.message, type });
        setButtonDisabled(opts.button, false);
        locks.delete(key);
        reject(error);
      };
      xhr.onload = function () {
        let data = {};
        try { data = JSON.parse(xhr.responseText || "{}"); } catch (_) { data = uploadParseError(xhr); }
        if (window.PULSE_UPLOAD_DEBUG) {
          console.debug("PulseSoc upload response", {
            status: xhr.status,
            contentType: xhr.getResponseHeader("content-type") || "",
            headers: responseHeaders(xhr),
            body: safeRawBody(xhr.responseText || ""),
          });
        }
        if (xhr.status < 200 || xhr.status >= 300 || data.ok === false) {
          const error = new Error(data.message || "Upload failed. Tap to retry.");
          render(root, { stage: "failed", percent: 0, message: error.message, type });
          setButtonDisabled(opts.button, false);
          locks.delete(key);
          reject(error);
          return;
        }
        render(root, { stage: "processing", percent: 90, message: "Processing media...", type });
        window.setTimeout(() => {
          render(root, { stage: "complete", percent: 100, message: "Upload complete. Ready to publish.", type });
          setButtonDisabled(opts.button, false);
          locks.delete(key);
          resolve(data);
        }, 120);
      };
      xhr.send(form);
    });
  }

  async function publish(options) {
    const opts = options || {};
    const root = findProgressRoot(opts.progressTarget);
    setButtonDisabled(opts.button, true, "Publishing...");
    render(root, { stage: "publishing", percent: 96, message: "Publishing..." });
    try {
      const response = await fetch(opts.url, {
        method: opts.method || "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
        body: JSON.stringify(opts.body || {}),
      });
      const data = await response.json().catch(() => ({ ok: false, message: "Publishing returned an unreadable response." }));
      if (!response.ok || data.ok === false) throw new Error(data.message || "Publishing failed.");
      render(root, { stage: "success", percent: 100, message: opts.successMessage || "Posted successfully" });
      return data;
    } catch (error) {
      render(root, { stage: "failed", percent: 0, message: `${error.message} Tap to retry.` });
      throw error;
    } finally {
      setButtonDisabled(opts.button, false);
    }
  }

  window.PulseUploadManager = {
    upload,
    publish,
    render,
    textFor,
  };
})();
