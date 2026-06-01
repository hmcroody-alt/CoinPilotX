(function () {
  "use strict";

  const locks = new Map();

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
    const rawBody = safeRawBody(xhr.responseText || "");
    const diagnostic = {
      status: xhr.status,
      contentType,
      headers,
      rawBody,
      url: xhr.responseURL || "",
    };
    console.warn("Pulse upload response parse failed", diagnostic);
    const lower = rawBody.toLowerCase();
    let message = "Upload returned a non-JSON response from the server.";
    if (xhr.status === 401 || lower.includes("/login") || lower.includes("login")) {
      message = "Login expired. Please sign in and try the upload again.";
    } else if (xhr.status === 413) {
      message = "Upload is too large. Please choose a smaller video or photo.";
    } else if (contentType.includes("text/html")) {
      message = "Upload returned an HTML page instead of JSON. Please retry after refreshing.";
    } else if (!rawBody.trim()) {
      message = "Upload returned an empty response. Please retry.";
    }
    return { ok: false, message, upload_debug: diagnostic };
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
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(opts.method || "POST", opts.url || "/api/pulse/media/upload", true);
      xhr.withCredentials = opts.credentials !== false;
      xhr.upload.onprogress = function (event) {
        if (!event.lengthComputable) return;
        const percent = Math.min(84, Math.max(3, Math.round((event.loaded / event.total) * 84)));
        const state = { stage: "uploading", percent, message: textFor("uploading", percent, type), type };
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
          console.debug("Pulse upload response", {
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
