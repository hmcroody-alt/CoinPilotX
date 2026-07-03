(() => {
  const root = document.querySelector("[data-intelligence-root]");
  const adminForm = document.querySelector("[data-admin-intel-collect]");
  const adminTestForm = document.querySelector("[data-admin-intel-test-alert]");
  const adminSendForm = document.querySelector("[data-admin-intel-send-event]");
  const adminCadenceForm = document.querySelector("[data-admin-intel-cadence]");
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  let state = {};

  function initialState() {
    const node = document.getElementById("pulse-intelligence-state");
    if (!node) return {};
    try {
      return JSON.parse(node.textContent || "{}");
    } catch (_) {
      return {};
    }
  }

  function currentView() {
    return String(root?.dataset.intelligenceView || "alerts");
  }

  function streamLabel(streamKey) {
    return {
      pulsesoc_discoveries: "PulseSoc Discoveries",
      crypto_pulse: "Crypto Pulse",
      market_pulse: "Market Pulse",
      world_pulse: "World Pulse",
      security_pulse: "Security Pulse",
      technology_pulse: "Technology Pulse",
      pulsesoc_pulse: "PulseSoc Pulse",
      creator_pulse: "Creator Pulse",
      music_pulse: "Music Pulse",
      system_pulse: "System Pulse",
    }[String(streamKey || "")] || String(streamKey || "Pulse").replaceAll("_", " ");
  }

  async function json(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.message || data.error || "Request failed.");
    return data;
  }

  function setStatus(message, kind = "info") {
    const node = document.querySelector("[data-intelligence-status]");
    if (!node) return;
    node.textContent = message;
    node.dataset.kind = kind;
  }

  function isSafeExternalUrl(url) {
    try {
      const parsed = new URL(String(url || ""), window.location.origin);
      return parsed.protocol === "https:" && ["apps.apple.com", "pulsesoc.com"].includes(parsed.hostname);
    } catch (_) {
      return false;
    }
  }

  function isSafeInternalUrl(url) {
    const value = String(url || "");
    return value.startsWith("/") && !value.startsWith("//") && !value.toLowerCase().startsWith("/javascript:");
  }

  function actionIcon(icon) {
    return {
      apple: "▣",
      share: "↗",
      spark: "✦",
      music: "♪",
      chart: "▥",
      trend: "⌁",
      shield: "◇",
      globe: "◎",
      chip: "◈",
      creator: "✧",
      system: "◌",
    }[String(icon || "").toLowerCase()] || "✦";
  }

  function renderSignalCtas(actions = [], eventId = 0) {
    const safe = actions.filter((action) => {
      if (!action || !action.label || !action.type) return false;
      if (action.type === "deep_link") return isSafeInternalUrl(action.url);
      if (action.type === "app_store") return isSafeExternalUrl(action.url);
      if (action.type === "share") return isSafeExternalUrl(action.share_url || action.url);
      return false;
    }).slice(0, 3);
    if (!safe.length) return "";
    return `
      <div class="signal-cta-row" aria-label="Pulse actions">
        ${safe.map((action, index) => `
          <button type="button"
            class="signal-cta ${esc(action.style || "primary")}"
            data-signal-action="${esc(action.type)}"
            data-action-index="${index}"
            data-event-id="${Number(eventId || 0)}"
            aria-label="${esc(action.label)}">
            <span aria-hidden="true">${esc(actionIcon(action.icon))}</span>
            ${esc(action.label)}
          </button>
        `).join("")}
      </div>
    `;
  }

  function renderSummary(summary = {}) {
    document.querySelectorAll("[data-summary]").forEach((node) => {
      const key = node.dataset.summary;
      const value = Number(summary[key] || 0);
      node.textContent = key === "avg_confidence" ? `${value}%` : String(value);
    });
  }

  function renderStreams(streams = []) {
    const list = document.querySelector("[data-stream-list]");
    if (!list) return;
    list.innerHTML = streams.map((stream) => `
      <article class="stream-card" data-stream-key="${esc(stream.stream_key)}" data-enabled="${stream.enabled ? "true" : "false"}">
        <header>
          <div>
            <span class="signal-pill">${esc(stream.category || "signal")}</span>
            <h3>${esc(stream.display_name)}</h3>
          </div>
          <button class="toggle" type="button" data-stream-toggle="${esc(stream.stream_key)}" aria-pressed="${stream.enabled ? "true" : "false"}" aria-label="${stream.enabled ? "Disable" : "Enable"} ${esc(stream.display_name)}"></button>
        </header>
        <p>${esc(stream.purpose)}</p>
        <label>Frequency
          <select data-stream-setting="${esc(stream.stream_key)}" data-field="frequency">
            ${["realtime", "digest", "morning", "afternoon", "evening", "weekly", "monthly", "muted"].map((item) => `<option value="${item}" ${stream.frequency === item ? "selected" : ""}>${item.replace("_", " ")}</option>`).join("")}
          </select>
        </label>
        <label>Confidence threshold
          <input type="range" min="40" max="95" value="${Number(stream.confidence_threshold || 70)}" data-stream-setting="${esc(stream.stream_key)}" data-field="confidence_threshold" aria-label="${esc(stream.display_name)} confidence threshold">
        </label>
        <div class="stream-actions">
          <button type="button" data-stream-push="${esc(stream.stream_key)}" aria-pressed="${stream.push_enabled ? "true" : "false"}">${stream.push_enabled ? "Push On" : "Push Off"}</button>
          <small>${Number(stream.confidence_threshold || 70)}% · ${esc(stream.priority_filter || "normal")}</small>
        </div>
      </article>
    `).join("") || `<p class="empty">No streams are available yet.</p>`;
  }

  function renderSignals(events = []) {
    const list = document.querySelector("[data-signal-list]");
    if (!list) return;
    list.innerHTML = events.map((event) => {
      const copy = event.alert_copy || {};
      const headline = copy.card_headline || event.headline;
      const summary = copy.card_summary || event.summary;
      const category = copy.card_category || streamLabel(event.stream_key);
      const priority = copy.card_priority_badge || String(event.priority || "watch").toUpperCase();
      const label = copy.card_label || "PULSESOC ALERT";
      const icon = copy.card_icon || "spark";
      const accent = copy.accent || "cyan";
      return `
      <article class="signal-card signal-card-v2" data-event-id="${Number(event.id || 0)}" data-signal-accent="${esc(accent)}">
        <div class="signal-icon" aria-hidden="true">${esc(actionIcon(icon))}</div>
        <div class="signal-card-body">
          <div class="signal-kicker">
            <span class="signal-source-label">${esc(label)}</span>
            <span class="signal-category">${esc(category)}</span>
            <span class="signal-priority">${esc(priority)}</span>
          </div>
          <div class="signal-meta">
            <span class="signal-pill">${esc(streamLabel(event.stream_key))}</span>
            <span class="confidence-ring">${Number(event.confidence_score || 0)}%</span>
          </div>
          <h3>${esc(headline)}</h3>
          <p>${esc(summary)}</p>
          <small>${esc(event.confidence_label)} confidence · ${Number(event.read_time_seconds || 30)}s read</small>
          <p><strong>Why it matters:</strong> ${esc(event.why_it_matters)}</p>
          ${event.expected_impact ? `<p><strong>Expected impact:</strong> ${esc(event.expected_impact)}</p>` : ""}
          ${renderSignalCtas(event.actions || [], event.id)}
          <div class="signal-actions">
            <button type="button" data-feedback="opened" data-event-id="${Number(event.id || 0)}" data-stream-key="${esc(event.stream_key)}">Mark read</button>
            <button type="button" data-feedback="saved" data-event-id="${Number(event.id || 0)}" data-stream-key="${esc(event.stream_key)}">Save</button>
            <button type="button" data-ask-pulse-ai="${Number(event.id || 0)}">Ask Pulse AI</button>
            <button type="button" data-feedback="not_helpful" data-event-id="${Number(event.id || 0)}" data-stream-key="${esc(event.stream_key)}">Not helpful</button>
            <button type="button" data-feedback="wrong" data-event-id="${Number(event.id || 0)}" data-stream-key="${esc(event.stream_key)}">Wrong</button>
            <button type="button" data-feedback="outdated" data-event-id="${Number(event.id || 0)}" data-stream-key="${esc(event.stream_key)}">Outdated</button>
            <button type="button" data-feedback="too_frequent" data-event-id="${Number(event.id || 0)}" data-stream-key="${esc(event.stream_key)}">Too frequent</button>
            <button type="button" data-feedback="dismissed" data-delete-signal="true" data-event-id="${Number(event.id || 0)}" data-stream-key="${esc(event.stream_key)}">Delete</button>
          </div>
        </div>
      </article>
    `; }).join("") || `<p class="empty">No Pulses yet. Your streams are ready and waiting for high-confidence signals.</p>`;
  }

  function renderForecasts(forecasts = []) {
    const list = document.querySelector("[data-forecast-list]");
    if (!list) return;
    list.innerHTML = forecasts.map((forecast) => `
      <article class="forecast-card">
        <strong>${esc(forecast.title)}</strong>
        <p>${esc(forecast.forecast_body)}</p>
        <small>${esc(forecast.confidence_label)} · ${Number(forecast.confidence_score || 0)}% · ${esc(forecast.horizon)}</small>
      </article>
    `).join("") || `<p class="empty">Forecasts appear when enough trusted signals support them.</p>`;
  }

  function render(nextState) {
    state = nextState || {};
    renderSummary(state.summary || {});
    renderStreams(state.streams || []);
    renderSignals(state.events || []);
    renderForecasts(state.forecasts || []);
  }

  async function refresh() {
    setStatus("Refreshing signals...");
    const data = await json(`/api/pulse/intelligence/state?view=${encodeURIComponent(currentView())}`);
    render(data);
    setStatus("Signals updated.");
  }

  async function patchStream(streamKey, payload) {
    const data = await json(`/api/pulse/intelligence/streams/${encodeURIComponent(streamKey)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    const streams = (state.streams || []).map((item) => item.stream_key === streamKey ? { ...item, ...(data.stream || {}) } : item);
    render({ ...state, streams });
    setStatus("Signal preference saved.");
  }

  root?.addEventListener("click", async (event) => {
    const refreshButton = event.target.closest("[data-refresh-intelligence]");
    if (refreshButton) {
      try { await refresh(); } catch (error) { setStatus(error.message, "error"); }
      return;
    }
    const toggle = event.target.closest("[data-stream-toggle]");
    if (toggle) {
      const streamKey = toggle.dataset.streamToggle;
      const enabled = toggle.getAttribute("aria-pressed") !== "true";
      toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
      try { await patchStream(streamKey, { enabled }); } catch (error) { setStatus(error.message, "error"); }
      return;
    }
    const push = event.target.closest("[data-stream-push]");
    if (push) {
      const streamKey = push.dataset.streamPush;
      const enabled = push.getAttribute("aria-pressed") !== "true";
      push.setAttribute("aria-pressed", enabled ? "true" : "false");
      try { await patchStream(streamKey, { push_enabled: enabled }); } catch (error) { setStatus(error.message, "error"); }
      return;
    }
    const feedback = event.target.closest("[data-feedback]");
    if (feedback) {
      try {
        await json("/api/pulse/intelligence/feedback", {
          method: "POST",
          body: JSON.stringify({
            event_id: feedback.dataset.eventId,
            stream_key: feedback.dataset.streamKey,
            feedback_type: feedback.dataset.feedback,
          }),
        });
        if (feedback.dataset.deleteSignal === "true") {
          feedback.closest(".signal-card")?.remove();
          setStatus("Signal deleted from your view.");
        } else {
          setStatus(feedback.dataset.feedback === "opened" ? "Signal marked read." : "Feedback saved.");
        }
      } catch (error) {
        setStatus(error.message, "error");
      }
      return;
    }
    const askAi = event.target.closest("[data-ask-pulse-ai]");
    if (askAi) {
      const eventId = Number(askAi.dataset.askPulseAi || 0);
      window.location.assign(`/pulse/messages?pulse_ai=1&signal_id=${encodeURIComponent(String(eventId || ""))}`);
      return;
    }
    const signalAction = event.target.closest("[data-signal-action]");
    if (signalAction) {
      const eventId = Number(signalAction.dataset.eventId || 0);
      const index = Number(signalAction.dataset.actionIndex || 0);
      const signal = (state.events || []).find((item) => Number(item.id || 0) === eventId);
      const action = signal && Array.isArray(signal.actions) ? signal.actions[index] : null;
      if (!action) return;
      if (action.type === "deep_link" && isSafeInternalUrl(action.url)) {
        window.location.assign(action.url);
        return;
      }
      if (action.type === "app_store" && isSafeExternalUrl(action.url)) {
        window.open(action.url, "_blank", "noopener,noreferrer");
        return;
      }
      if (action.type === "share" && isSafeExternalUrl(action.share_url || action.url)) {
        const shareUrl = action.share_url || action.url;
        if (navigator.share) {
          try {
            await navigator.share({ title: action.share_title || "PulseSoc", text: action.share_text || "Join me on PulseSoc.", url: shareUrl });
            setStatus("Share sheet opened.");
          } catch (_) {
            setStatus("Share canceled.");
          }
          return;
        }
        try {
          await navigator.clipboard.writeText(shareUrl);
          setStatus("PulseSoc link copied.");
        } catch (_) {
          setStatus("Share link could not be copied.", "error");
        }
      }
    }
  });

  root?.addEventListener("change", async (event) => {
    const setting = event.target.closest("[data-stream-setting]");
    if (!setting) return;
    const streamKey = setting.dataset.streamSetting;
    const field = setting.dataset.field;
    const value = field === "confidence_threshold" ? Number(setting.value || 70) : setting.value;
    try { await patchStream(streamKey, { [field]: value }); } catch (error) { setStatus(error.message, "error"); }
  });

  adminForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      stream_key: form.elements.stream_key.value,
      target_user_id: form.elements.target_user_id.value || 0,
      limit: Number(form.elements.limit.value || 20),
      all_streams: Boolean(form.elements.all_streams.checked),
      dry_run: Boolean(form.elements.dry_run.checked),
      deliver: Boolean(form.elements.deliver.checked),
    };
    const result = document.querySelector("[data-admin-intel-result]");
    if (result) result.textContent = "Running collector...";
    try {
      const data = await json("/api/admin/intelligence/collect", { method: "POST", body: JSON.stringify(payload) });
      if (result) result.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      if (result) result.textContent = error.message;
    }
  });

  adminTestForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      stream_key: form.elements.stream_key.value,
      target_user_id: form.elements.target_user_id.value || 0,
    };
    const result = document.querySelector("[data-admin-intel-delivery-result]");
    if (result) result.textContent = "Sending test alert...";
    try {
      const data = await json("/api/admin/intelligence/delivery/test", { method: "POST", body: JSON.stringify(payload) });
      if (result) result.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      if (result) result.textContent = error.message;
    }
  });

  adminSendForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      event_id: form.elements.event_id.value,
      mode: form.elements.mode.value,
      target_user_id: form.elements.target_user_id.value || 0,
      delivery_type: form.elements.delivery_type.value,
      schedule_at: form.elements.schedule_at.value,
      process_now: true,
    };
    const result = document.querySelector("[data-admin-intel-delivery-result]");
    if (result) result.textContent = "Queueing alert...";
    try {
      const data = await json("/api/admin/intelligence/delivery/send", { method: "POST", body: JSON.stringify(payload) });
      if (result) result.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      if (result) result.textContent = error.message;
    }
  });

  adminCadenceForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      target_user_id: form.elements.target_user_id.value || 0,
      limit: Number(form.elements.limit.value || 500),
    };
    const result = document.querySelector("[data-admin-intel-delivery-result]");
    if (result) result.textContent = "Sending next cadence alert...";
    try {
      const data = await json("/api/admin/intelligence/cadence/send-now", { method: "POST", body: JSON.stringify(payload) });
      if (result) result.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      if (result) result.textContent = error.message;
    }
  });

  document.addEventListener("click", async (event) => {
    const result = document.querySelector("[data-admin-intel-delivery-result]");
    const process = event.target.closest("[data-admin-intel-process-delivery]");
    if (process) {
      if (result) result.textContent = "Processing Intelligence delivery queue...";
      try {
        const data = await json("/api/admin/intelligence/delivery/process", { method: "POST", body: JSON.stringify({ limit: 100, digest_limit: 50 }) });
        if (result) result.textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        if (result) result.textContent = error.message;
      }
      return;
    }
    const digest = event.target.closest("[data-admin-intel-generate-digest]");
    if (digest) {
      if (result) result.textContent = "Generating digest jobs...";
      try {
        const data = await json("/api/admin/intelligence/delivery/digests", { method: "POST", body: JSON.stringify({ limit: 500, digest_type: "daily" }) });
        if (result) result.textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        if (result) result.textContent = error.message;
      }
      return;
    }
    const cancel = event.target.closest("[data-admin-intel-cancel-job]");
    if (cancel) {
      if (result) result.textContent = "Canceling delivery job...";
      try {
        const data = await json("/api/admin/intelligence/delivery/cancel", { method: "POST", body: JSON.stringify({ job_id: cancel.dataset.adminIntelCancelJob }) });
        if (result) result.textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        if (result) result.textContent = error.message;
      }
    }
  });

  if (root) render(initialState());
})();
