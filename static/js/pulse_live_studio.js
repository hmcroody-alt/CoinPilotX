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

  function chatMessageHtml(message) {
    const name = message.display_name || message.username || (message.message_type === "system" ? "Pulse" : "Viewer");
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
    feed.innerHTML = messages.map(chatMessageHtml).join("") || `<article class="live-chat-message"><div class="live-chat-avatar">P</div><div><strong>Pulse</strong><p>Chat is ready.</p></div></article>`;
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
      console.warn("Pulse Live state recovery", error);
    }
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
      if (window.toast) window.toast(error.message);
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
      console.warn("Pulse Live reaction failed", error);
    }
  }

  function bootCamera(root) {
    const video = qs(root, "[data-live-camera]");
    if (!video || !navigator.mediaDevices?.getUserMedia) return;
    let stream = null;
    const stop = () => {
      if (stream) stream.getTracks().forEach((track) => track.stop());
      stream = null;
    };
    const start = async () => {
      try {
        stop();
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 60 } },
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        video.srcObject = stream;
        root.classList.add("is-camera-active");
        setText(root, "[data-live-camera-state]", "Camera live-ready");
      } catch (error) {
        setText(root, "[data-live-camera-state]", "Camera needs permission");
        if (window.toast) window.toast(error.message);
      }
    };
    qs(root, "[data-live-start-camera]")?.addEventListener("click", start);
    qs(root, "[data-live-mute]")?.addEventListener("click", (event) => {
      stream?.getAudioTracks().forEach((track) => { track.enabled = !track.enabled; });
      const enabled = stream?.getAudioTracks().some((track) => track.enabled);
      event.currentTarget.textContent = enabled ? "Mute Mic" : "Unmute Mic";
    });
    qs(root, "[data-live-screen]")?.addEventListener("click", async () => {
      try {
        stop();
        stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
        video.srcObject = stream;
        video.style.transform = "none";
        root.classList.add("is-camera-active");
        setText(root, "[data-live-camera-state]", "Screen share live-ready");
      } catch {
        if (window.toast) window.toast("Screen share was not started.");
      }
    });
    window.addEventListener("pagehide", stop);
  }

  function init(root) {
    bootCamera(root);
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
  }

  document.addEventListener("DOMContentLoaded", () => {
    qsa(document, "[data-pulse-live-shell]").forEach(init);
  });

  window.PulseLiveStudio = { init, fetchState, sendReaction };
})();
