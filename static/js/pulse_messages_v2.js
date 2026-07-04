(() => {
  const API = "/api/pulse/communications/v2";
  const MEDIA_API = "/api/messages/media";
  const PULSE_AI_API = "/api/pulse-ai";
  const PULSE_AI_CONVERSATION_ID = -9001001;
  const PULSE_AI_USER_ID = -9001001;
  const INITIAL_MESSAGE_LIMIT = 40;
  const el = (sel) => document.querySelector(sel);
  const root = el(".comm-shell");
  const currentUserId = Number(root?.dataset.currentUserId || 0);
  const pathConversationMatch = location.pathname.match(/\/pulse\/messages\/(\d+)/);
  const initialConversationId = Number(root?.dataset.initialConversationId || new URLSearchParams(location.search).get("conversation") || pathConversationMatch?.[1] || 0);
  const list = el("[data-conversations]");
  const messages = el("[data-messages]");
  const status = el("[data-status]");
  const mobileQuery = window.matchMedia("(max-width: 840px)");
  const state = {
    conversations: [],
    conversationCache: new Map(),
    messageCache: new Map(),
    peopleCache: new Map(),
    active: null,
    messages: [],
    members: [],
    rooms: [],
    typing: [],
    presence: [],
    groupMembers: [],
    replyTo: null,
    searchTimer: 0,
    groupSearchTimer: 0,
    filter: "all",
    hasOlder: false,
    oldestMessageId: 0,
    loadingThread: false,
    threadHydrating: false,
    activeRequestToken: 0,
    initialThreadLoaded: false,
    typingTimer: 0,
    typingStopTimer: 0,
    typingSentAt: 0,
    detailsOpen: false,
    actionPending: false,
    composerSending: false,
    mobileMode: "list",
    conversationSearch: "",
    threadSearchQuery: "",
    actionConversationId: 0,
    attachmentQueue: [],
    attachmentSeq: 0,
    attachmentSheetOpen: false,
    emojiOpen: false,
    composerState: "idle",
    reactionOpen: false,
    aiEnabled: root?.dataset.aiEnabled === "true",
    pulseAIEnabled: true,
    pulseAITyping: false,
    pulseAISettings: null,
    aiBusy: false,
    aiOutput: "",
    maxAttachments: 8,
    uploadLimits: {
      image: 15 * 1024 * 1024,
      video: 200 * 1024 * 1024,
      audio: 25 * 1024 * 1024,
      file: 50 * 1024 * 1024,
    },
    voice: {
      stream: null,
      recorder: null,
      chunks: [],
      blob: null,
      url: "",
      startedAt: 0,
      elapsedMs: 0,
      timer: 0,
      analyserTimer: 0,
      audioContext: null,
      waveform: [],
      stopResolve: null,
      state: "idle",
    },
    realtimeAfterId: 0,
    realtimeTimer: 0,
    realtimePolling: false,
    realtimeBound: false,
    realtimeConnected: false,
    controlOpen: false,
    controlLoading: false,
    controlSaving: false,
    controlSearch: "",
    controlData: null,
    controlConversationId: 0,
    controlEntry: "thread",
    controlExpanded: new Set(["conversation", "notifications", "appearance", "privacy"]),
    controlDetail: null,
    controlSettingsCache: new Map(),
    tabChannel: "BroadcastChannel" in window ? new BroadcastChannel("pulse-comm-v2") : null,
  };
  const MEDIA_FOUNDATION_MIME_BY_EXT = {
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    png: "image/png",
    webp: "image/webp",
    heic: "image/heic",
    heif: "image/heif",
    mp4: "video/mp4",
    m4v: "video/mp4",
    webm: "video/webm",
    mp3: "audio/mpeg",
    m4a: "audio/mp4",
    wav: "audio/wav",
    ogg: "audio/ogg",
    oga: "audio/ogg",
  };
  const MEDIA_FOUNDATION_MIMES = new Set(Object.values(MEDIA_FOUNDATION_MIME_BY_EXT));
  const CONTROL_THEME_OPTIONS = [
    ["dark_galaxy", "Dark Galaxy"],
    ["pulse_green", "Pulse Green"],
    ["deep_space", "Deep Space"],
    ["nebula", "Nebula"],
    ["cyber_night", "Cyber Night"],
    ["solar_flame", "Solar Flame"],
    ["ocean_signal", "Ocean Signal"],
    ["royal_purple", "Royal Purple"],
    ["haiti_night", "Haiti Night"],
    ["creator_gold", "Creator Gold"],
  ];
  const CONTROL_WALLPAPER_OPTIONS = [
    ["deep_space", "Deep Space"],
    ["neon_planet", "Neon Planet"],
    ["galaxy_grid", "Galaxy Grid"],
    ["pulse_horizon", "Pulse Horizon"],
    ["alien_city", "Alien City"],
    ["cosmic_ocean", "Cosmic Ocean"],
    ["aurora_signal", "Aurora Signal"],
    ["dark_nebula", "Dark Nebula"],
    ["star_tunnel", "Star Tunnel"],
    ["minimal_black", "Minimal Black"],
  ];
  const CONTROL_BUBBLE_OPTIONS = [
    ["cyan", "Cyan"],
    ["purple", "Purple"],
    ["rose", "Rose"],
    ["orange", "Orange"],
    ["green", "Green"],
    ["gold", "Gold"],
    ["blue", "Blue"],
  ];
  const CONTROL_SECTIONS = [
    {
      id: "conversation",
      icon: "💬",
      title: "Conversation",
      desc: "Info, members, media and search.",
      items: [
        { label: "View Members", icon: "👥", action: "members" },
        { label: "Shared Media", icon: "🖼", action: "shared-media" },
        { label: "Pinned Messages", icon: "📌", action: "pinned-messages" },
        { label: "Search Chat", icon: "⌕", action: "search-chat" },
        { label: "Message Stats", icon: "▥", action: "message-stats" },
        { label: "Media Storage", icon: "◉", action: "storage" },
        { label: "Export Chat", icon: "⇧", action: "export-chat", dangerConfirm: "Export this conversation to a local JSON file?" },
      ],
    },
    {
      id: "notifications",
      icon: "🔔",
      title: "Notifications",
      desc: "Alerts, sounds, previews and badges.",
      items: [
        { label: "Mute Conversation", icon: "🔕", setting: "mute_choice", kind: "select", options: [["off", "Off"], ["1_hour", "1 hour"], ["8_hours", "8 hours"], ["today", "Today"], ["1_week", "1 week"], ["forever", "Forever"]] },
        { label: "Notification Sound", icon: "♪", setting: "sound", kind: "select", options: [["pulse_beam", "Pulse Beam"], ["soft_orbit", "Soft Orbit"], ["deep_signal", "Deep Signal"], ["crystal_ping", "Crystal Ping"], ["silent", "Silent"]] },
        { label: "Show on Lock Screen", icon: "▣", setting: "lock_screen", kind: "toggle" },
        { label: "Show Message Preview", icon: "◉", setting: "message_preview", kind: "toggle" },
        { label: "Mention Notifications", icon: "@", setting: "mentions", kind: "toggle" },
        { label: "Reaction Notifications", icon: "✦", setting: "reactions", kind: "toggle" },
        { label: "Typing Notifications", icon: "...", setting: "typing", kind: "toggle" },
        { label: "Read Receipt Notifications", icon: "✓", setting: "read_receipts", kind: "toggle" },
        { label: "More Notification Settings", icon: "⚙", action: "open-notification-settings" },
      ],
    },
    {
      id: "appearance",
      icon: "🖌",
      title: "Appearance",
      desc: "Themes, colors, density and motion.",
      items: [
        { label: "Theme", icon: "◌", setting: "theme", kind: "select", options: CONTROL_THEME_OPTIONS },
        { label: "Wallpaper", icon: "▧", setting: "wallpaper", kind: "select", options: CONTROL_WALLPAPER_OPTIONS },
        { label: "Bubble Color", icon: "●", setting: "bubble_color", kind: "select", options: CONTROL_BUBBLE_OPTIONS },
        { label: "Font Size", icon: "Aa", setting: "font_size", kind: "select", options: [["small", "Small"], ["medium", "Medium"], ["large", "Large"], ["extra_large", "Extra Large"]] },
        { label: "Chat Density", icon: "↕", setting: "density", kind: "select", options: [["compact", "Compact"], ["balanced", "Balanced"], ["relaxed", "Relaxed"]] },
        { label: "Animation Level", icon: "✺", setting: "animation_level", kind: "select", options: [["full", "Full"], ["balanced", "Balanced"], ["reduced", "Reduced"], ["off", "Off"]] },
        { label: "Reduce Particles", icon: "·", setting: "reduce_particles", kind: "toggle" },
        { label: "High Contrast", icon: "◐", setting: "high_contrast", kind: "toggle" },
      ],
    },
    {
      id: "privacy",
      icon: "🔒",
      title: "Privacy",
      desc: "Visibility and private conversation behavior.",
      items: [
        { label: "Read Receipts", icon: "✓✓", setting: "read_receipts", kind: "toggle" },
        { label: "Typing Indicator", icon: "...", setting: "typing_indicator", kind: "toggle" },
        { label: "Online Status", icon: "●", setting: "online_status", kind: "toggle" },
        { label: "Last Seen", icon: "◷", setting: "last_seen", kind: "toggle" },
        { label: "Show Message Preview", icon: "◉", setting: "message_preview", kind: "toggle" },
      ],
    },
    {
      id: "ai",
      icon: "✦",
      title: "AI Assistant",
      desc: "Private actions only run after you tap.",
      items: [
        { label: "Summarize Conversation", icon: "✦", action: "ai-summary", requiresAi: true },
        { label: "Smart Replies", icon: "↻", action: "ai-replies", requiresAi: true },
      ],
    },
    {
      id: "media",
      icon: "🖼",
      title: "Media",
      desc: "Downloads, uploads, links and files.",
      items: [
        { label: "Auto Download Photos", icon: "▧", setting: "auto_download_photos", kind: "toggle" },
        { label: "Auto Download Videos", icon: "▶", setting: "auto_download_videos", kind: "toggle" },
        { label: "Auto Download Voice Messages", icon: "🎤", setting: "auto_download_voice", kind: "toggle" },
        { label: "Upload Quality", icon: "HD", setting: "upload_quality", kind: "select", options: [["standard", "Standard"], ["high", "High"], ["original", "Original"]] },
        { label: "Auto Save Camera Photos", icon: "◎", setting: "auto_save_camera", kind: "toggle" },
        { label: "Clear Media Cache", icon: "⌫", action: "clear-cache", dangerConfirm: "Clear local media cache for this device?" },
        { label: "Shared Links", icon: "↗", action: "shared-links" },
        { label: "Shared Files", icon: "▤", action: "shared-files" },
      ],
    },
    {
      id: "security",
      icon: "🛡",
      title: "Security",
      desc: "Safety, reports, sessions and trust.",
      items: [
        { label: "Encryption Status", icon: "🛡", action: "security-status" },
        { label: "Verify Contact", icon: "◇", action: "verify-contact", directOnly: true },
        { label: "Trusted Devices", icon: "▣", action: "trusted-devices" },
        { label: "Active Sessions", icon: "◉", action: "active-sessions" },
        { label: "Security Log", icon: "▤", action: "security-log" },
        { label: "Report Conversation", icon: "!", action: "report-conversation", dangerConfirm: "Send the latest message to moderation review?" },
        { label: "Block User", icon: "⊘", action: "block-user", directOnly: true, dangerConfirm: "Block this member?" },
      ],
    },
    {
      id: "productivity",
      icon: "✓",
      title: "Productivity",
      desc: "Pins, archive, reminders and tasks.",
      items: [
        { label: "Pin Conversation", icon: "📌", action: "pin" },
        { label: "Archive Conversation", icon: "▤", action: "archive", dangerConfirm: "Archive this conversation?" },
        { label: "Mark Unread", icon: "◌", action: "mark-unread" },
        { label: "Favorite Conversation", icon: "★", setting: "favorite", kind: "toggle" },
        { label: "Reminder", icon: "⏱", setting: "reminder", kind: "select", options: [["off", "Off"], ["today", "Today"], ["tomorrow", "Tomorrow"], ["next_week", "Next week"]] },
        { label: "Create Note", icon: "✎", action: "create-note" },
        { label: "Create Task", icon: "☑", action: "create-task" },
      ],
    },
    {
      id: "group",
      icon: "👥",
      title: "Group Settings",
      desc: "Members, roles and permissions.",
      groupOnly: true,
      items: [
        { label: "Members", icon: "👥", action: "members" },
      ],
    },
    {
      id: "storage",
      icon: "◉",
      title: "Storage",
      desc: "Conversation size, media and cache.",
      items: [
        { label: "Conversation Size", icon: "◉", stat: "storage_used" },
        { label: "Photos", icon: "▧", stat: "photos" },
        { label: "Videos", icon: "▶", stat: "videos" },
        { label: "Voice Messages", icon: "🎤", stat: "voice" },
        { label: "Files", icon: "▤", stat: "media_files" },
        { label: "Links", icon: "↗", action: "shared-links" },
        { label: "Largest Files", icon: "⇅", action: "largest-files" },
        { label: "Clear Cache", icon: "⌫", action: "clear-cache", dangerConfirm: "Clear local cache for this conversation?" },
      ],
    },
    {
      id: "accessibility",
      icon: "♿",
      title: "Accessibility",
      desc: "Display, motion, audio and haptics.",
      items: [
        { label: "Large Text", icon: "Aa", setting: "large_text", kind: "toggle" },
        { label: "Reduce Motion", icon: "↘", setting: "reduce_motion", kind: "toggle" },
        { label: "High Contrast", icon: "◐", setting: "high_contrast", kind: "toggle" },
        { label: "Haptic Feedback", icon: "✦", setting: "haptic_feedback", kind: "toggle" },
      ],
    },
    {
      id: "danger",
      icon: "!",
      title: "Danger Zone",
      desc: "Destructive actions require confirmation.",
      danger: true,
      items: [
        { label: "Clear Conversation", icon: "⌫", action: "clear-conversation", dangerConfirm: "Clear this conversation from your view? Other members keep their messages." },
        { label: "Delete Conversation", icon: "×", action: "delete-conversation", dangerConfirm: "Remove this conversation from your inbox? Other members keep their messages." },
        { label: "Leave Group", icon: "⇠", action: "leave-group", groupOnly: true, dangerConfirm: "Leave this group conversation?" },
        { label: "Block User", icon: "⊘", action: "block-user", directOnly: true, dangerConfirm: "Block this member?" },
        { label: "Report Spam", icon: "!", action: "report-conversation", dangerConfirm: "Send this conversation to moderation review?" },
        { label: "Delete Media", icon: "⌧", action: "delete-media", dangerConfirm: "Hide shared media from your view in this conversation?" },
        { label: "Reset Conversation Settings", icon: "↺", action: "reset-settings", dangerConfirm: "Reset this conversation's controls to defaults?" },
      ],
    },
  ];

  function isMobile() {
    return mobileQuery.matches;
  }

  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches || root?.classList.contains("control-reduce-motion");
  }

  function setMobileMode(mode) {
    state.mobileMode = mode;
    root?.setAttribute("data-mobile-mode", mode);
    document.body.dataset.mobileChatMode = mode;
  }

  function draftStorageKey(conversationId = state.active?.conversation_id) {
    const id = Number(conversationId || 0);
    return id ? `pulseMessengerDraft:${currentUserId}:${id}` : "";
  }

  function saveActiveDraft() {
    const key = draftStorageKey();
    const input = el("[data-message-input]");
    if (!key || !input) return;
    try {
      if (input.value) sessionStorage.setItem(key, input.value);
      else sessionStorage.removeItem(key);
    } catch (_) {}
  }

  function restoreDraft(conversationId = state.active?.conversation_id) {
    const input = el("[data-message-input]");
    const key = draftStorageKey(conversationId);
    if (!input || !key) return;
    try {
      input.value = sessionStorage.getItem(key) || "";
    } catch (_) {
      input.value = "";
    }
  }

  function clearDraft(conversationId = state.active?.conversation_id) {
    const key = draftStorageKey(conversationId);
    if (!key) return;
    try { sessionStorage.removeItem(key); } catch (_) {}
  }

  function setStatus(text, kind = "info") {
    if (status) {
      status.textContent = text || "";
      status.dataset.kind = kind;
      status.hidden = !text;
    }
    const modalStatus = document.querySelector("[data-modal]:not([hidden]) [data-modal-status]");
    if (modalStatus) {
      modalStatus.textContent = text || "";
      modalStatus.dataset.kind = kind;
    }
  }

  const COMPOSER_STATES = [
    "idle",
    "typing",
    "emoji_open",
    "reaction_open",
    "attachment_selected",
    "attachment_uploading",
    "recording_voice",
    "recording_locked",
    "recording_paused",
    "voice_preview",
    "voice_uploading",
    "send_failed",
  ];

  function currentComposerState() {
    if (state.composerSending && state.voice.state === "voice_uploading") return "voice_uploading";
    if (state.composerSending && state.attachmentQueue.some((item) => item.status === "uploading")) return "attachment_uploading";
    if (state.voice.state === "recording_voice") return "recording_voice";
    if (state.voice.state === "recording_paused") return "recording_paused";
    if (state.voice.state === "voice_preview") return "voice_preview";
    if (state.voice.state === "voice_uploading") return "voice_uploading";
    if (state.emojiOpen) return "emoji_open";
    if (state.reactionOpen) return "reaction_open";
    if (state.attachmentSheetOpen || state.attachmentQueue.length) return "attachment_selected";
    if (document.activeElement === el("[data-message-input]") || String(el("[data-message-input]")?.value || "").trim()) return "typing";
    return "idle";
  }

  function syncComposerState() {
    const next = currentComposerState();
    state.composerState = COMPOSER_STATES.includes(next) ? next : "idle";
    const shell = el("[data-composer-shell]");
    if (shell) shell.dataset.composerState = state.composerState;
    const send = el("[data-send-button]");
    if (send) {
      const voiceActive = ["recording_voice", "recording_paused", "voice_preview", "voice_uploading"].includes(state.voice.state);
      send.disabled = state.voice.state === "voice_uploading";
      send.classList.toggle("is-voice-ready", voiceActive);
      send.setAttribute("aria-label", voiceActive ? "Send voice note" : "Send message");
      send.title = voiceActive ? "Send voice note" : "Send message";
    }
    updateComposerVoiceInline();
  }

  async function api(path, options = {}, metric = "request") {
    const started = performance.now();
    const res = await fetch(API + path, {
      credentials: "same-origin",
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text || "{}"); } catch (_) { data = { ok: false, message: "The server returned an unexpected response." }; }
    const durationMs = Math.round(performance.now() - started);
    console.info("PulseSoc Messenger V3 timing", { metric, path, status: res.status, durationMs, serverTimingMs: data.timing_ms });
    if (!res.ok || data.ok === false) {
      const trace = data.trace_id ? ` Trace: ${data.trace_id}` : "";
      const mislabeledServerError = res.status >= 500 && /upload failed/i.test(String(data.message || ""));
      const message = mislabeledServerError
        ? `Messenger is temporarily unavailable. Refresh and try again.${trace}`
        : data.message || (data.status === "disabled" ? "Messenger is temporarily unavailable." : `This request could not be completed.${trace}`);
      throw Object.assign(new Error(message), { data, status: res.status, durationMs });
    }
    return data;
  }

  async function mediaApi(path, options = {}, metric = "media_foundation") {
    const started = performance.now();
    const res = await fetch(MEDIA_API + path, {
      credentials: "same-origin",
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text || "{}"); } catch (_) { data = { ok: false, message: "Media upload returned an unexpected response." }; }
    console.info("PulseSoc Messenger V3 timing", { metric, path, status: res.status, durationMs: Math.round(performance.now() - started) });
    if (!res.ok || data.ok === false) {
      const trace = data.trace_id ? ` Trace: ${data.trace_id}` : "";
      throw Object.assign(new Error((data.message || "Media upload failed.") + trace), { data, status: res.status });
    }
    return data;
  }

  async function pulseAiApi(path, options = {}, metric = "pulse_ai") {
    const started = performance.now();
    const res = await fetch(PULSE_AI_API + path, {
      credentials: "same-origin",
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text || "{}"); } catch (_) { data = { ok: false, message: "Pulse AI returned an unexpected response." }; }
    console.info("Pulse AI timing", { metric, path, status: res.status, durationMs: Math.round(performance.now() - started), serverTimingMs: data.timing_ms });
    if (!res.ok || data.ok === false) {
      const trace = data.correlation_id || data.trace_id ? ` Trace: ${data.correlation_id || data.trace_id}` : "";
      throw Object.assign(new Error((data.message || "Pulse AI is temporarily unavailable.") + trace), { data, status: res.status });
    }
    return data;
  }

  function rememberConversation(item) {
    if (!item) return null;
    const id = Number(item.conversation_id || item.id || 0);
    if (!id) return item;
    const merged = { ...(state.conversationCache.get(id) || {}), ...item, conversation_id: id };
    state.conversationCache.set(id, merged);
    return merged;
  }

  function isPulseAIId(conversationId) {
    return Number(conversationId || 0) === PULSE_AI_CONVERSATION_ID;
  }

  function isPulseAIConversation(item = state.active) {
    return Boolean(item?.ai_assistant || item?.is_ai || isPulseAIId(item?.conversation_id || item?.id));
  }

  function pulseAIConversation(overrides = {}) {
    return rememberConversation({
      conversation_id: PULSE_AI_CONVERSATION_ID,
      id: PULSE_AI_CONVERSATION_ID,
      public_id: "pulse-ai",
      conversation_type: "assistant",
      type: "ai",
      is_ai: true,
      ai_assistant: true,
      title: "Pulse AI",
      description: "Galaxy Assistant",
      member_count: 2,
      pinned: true,
      muted: false,
      verified: true,
      presence: { status: "online", active_now: true, presence_visible: true },
      last_message_preview: "Ask me anything about PulseSoc.",
      last_activity_at: new Date().toISOString(),
      ...overrides,
    });
  }

  function ensurePulseAIConversation(overrides = {}) {
    const existing = state.conversationCache.get(PULSE_AI_CONVERSATION_ID);
    const ai = pulseAIConversation({ ...(existing || {}), ...overrides });
    const withoutAI = state.conversations.filter((item) => !isPulseAIConversation(item));
    state.conversations = [ai, ...withoutAI];
    return ai;
  }

  function sortConversations() {
    state.conversations.sort((a, b) => {
      const pinDelta = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
      if (pinDelta) return pinDelta;
      const ad = new Date(a.last_activity_at || a.last_message_at || a.updated_at || a.created_at || 0).getTime() || 0;
      const bd = new Date(b.last_activity_at || b.last_message_at || b.updated_at || b.created_at || 0).getTime() || 0;
      return bd - ad || Number(b.conversation_id || 0) - Number(a.conversation_id || 0);
    });
  }

  function upsertConversation(item) {
    const remembered = rememberConversation(item);
    if (!remembered?.conversation_id) return null;
    const id = Number(remembered.conversation_id);
    const index = state.conversations.findIndex((conversation) => Number(conversation.conversation_id) === id);
    if (index >= 0) state.conversations[index] = remembered;
    else state.conversations.unshift(remembered);
    sortConversations();
    return remembered;
  }

  function initials(title) {
    return String(title || "P").trim().slice(0, 2).toUpperCase();
  }

  function presenceForUser(userId) {
    return (state.presence || []).find((item) => Number(item.user_id || 0) === Number(userId || 0)) || {};
  }

  function presenceForConversation(item) {
    if (item?.presence && item.presence.available !== false && item.presence.status) return item.presence;
    const peerId = Number(item?.peer_user_id || item?.other_user_id || item?.target_user_id || 0);
    const directPeer = peerId ? presenceForUser(peerId) : null;
    if (directPeer?.user_id) return directPeer;
    const activePeer = (state.presence || []).find((presence) => Number(presence.user_id || 0) !== currentUserId && presence.active_now);
    return activePeer || {};
  }

  function presenceLabel(presence) {
    if (!presence || presence.presence_visible === false || presence.status === "hidden") return "Presence hidden";
    if (presence.active_now || presence.status === "online") return "Online";
    if (presence.status === "away") return "Away";
    if (presence.last_seen_at) return `Last active ${relativeTime(presence.last_seen_at)}`;
    return "Offline";
  }

  function presenceClass(presence) {
    if (presence?.active_now || presence?.status === "online") return "online";
    if (presence?.status === "away") return "away";
    return "offline";
  }

  function previewMediaLabel(type, fallback = "Attachment") {
    const value = String(type || "").toLowerCase();
    if (value.includes("voice") || value.includes("audio")) return "Voice message";
    if (value.includes("video") || value.includes("reel")) return "Video";
    if (value.includes("image") || value.includes("photo") || value.includes("gif")) return "Photo";
    if (value.includes("call")) return "Missed call";
    if (value.includes("file")) return "File";
    return fallback;
  }

  function typeLabel(type) {
    const value = String(type || "").toLowerCase();
    if (value === "direct") return "Direct";
    if (value === "group") return "Group";
    if (value === "room") return "Room";
    if (value === "community_channel") return "Room";
    return value || "Chat";
  }

  function riskScan(text) {
    const value = String(text || "");
    const matches = value.match(/https?:\/\/[^\s<>"')]+/gi) || [];
    const shorteners = new Set(["bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly", "rebrand.ly", "shorturl.at"]);
    const suspiciousWords = ["airdrop", "connect wallet", "seed phrase", "verify wallet", "claim reward", "urgent login", "reset your password", "gift card"];
    const urls = [];
    let score = 0;
    matches.forEach((raw) => {
      try {
        const url = new URL(raw);
        const domain = url.hostname.toLowerCase().replace(/^www\./, "");
        const compact = `${domain}${url.pathname}`.toLowerCase();
        const flags = [];
        if (shorteners.has(domain)) flags.push("shortened link");
        if (/(login|verify|secure|wallet)[-.][a-z0-9-]+\.(ru|top|xyz|info|click|live)$/i.test(domain)) flags.push("suspicious domain");
        if (/(walletconnect|metamask|airdrop|bonus|giveaway|claim|verify|signin|password)/i.test(compact)) flags.push("phishing pattern");
        score += flags.length * 35;
        urls.push({ raw, href: url.href, domain, flags });
      } catch (_) {}
    });
    suspiciousWords.forEach((word) => {
      if (value.toLowerCase().includes(word)) score += 14;
    });
    return { risky: score >= 35, score: Math.min(100, score), urls };
  }

  function linkifiedMessageHtml(text) {
    const value = String(text || "");
    const scan = riskScan(value);
    const parts = [];
    let last = 0;
    const regex = /https?:\/\/[^\s<>"')]+/gi;
    let match;
    while ((match = regex.exec(value))) {
      parts.push(escapeHtml(value.slice(last, match.index)));
      const raw = match[0];
      let href = "";
      let domain = "";
      let risky = scan.risky;
      try {
        const url = new URL(raw);
        href = url.href;
        domain = url.hostname.replace(/^www\./, "");
        const item = scan.urls.find((entry) => entry.raw === raw);
        risky = risky || Boolean(item?.flags?.length);
      } catch (_) {
        href = "";
      }
      parts.push(href
        ? `<a href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer" data-shield-link="${risky ? "risky" : "safe"}" data-link-domain="${escapeAttr(domain)}">${escapeHtml(raw)}</a>`
        : escapeHtml(raw));
      last = regex.lastIndex;
    }
    parts.push(escapeHtml(value.slice(last)));
    return parts.join("");
  }

  function containsLocalPath(value) {
    const text = String(value || "");
    return /(?:^|[\s"'(])(?:file:\/\/)?(?:\/Users\/|\/home\/|\/var\/|\/private\/|\/tmp\/|[A-Za-z]:\\|\\\\)[^\s"'<>]+/i.test(text)
      || /(?:CoinPilotX|Desktop)[\\/][^\s"'<>]+/i.test(text);
  }

  function sanitizePreviewText(value, type = "") {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (!text) return "";
    if (containsLocalPath(text)) return previewMediaLabel(type, "Attachment");
    return text;
  }

  function conversationPreview(item) {
    const type = String(item.last_message_type || item.latest_message_type || "").toLowerCase();
    const mediaLabel = previewMediaLabel(type, type && type !== "text" ? "Attachment" : "");
    return sanitizePreviewText(item.last_message_preview || item.last_message_body || item.last_message_text || item.last_message || item.latest_message || "", type)
      || mediaLabel
      || sanitizePreviewText(item.description || "", type)
      || `${item.conversation_type || "Conversation"} / ${Number(item.member_count || 0)} members`;
  }

  function avatarHtml(item, className = "avatar") {
    if (isPulseAIConversation(item)) {
      return `<span class="${className} pulse-ai-avatar" aria-hidden="true">AI</span>`;
    }
    const url = item?.avatar_url || item?.avatar_thumbnail_url || "";
    return `<span class="${className}">${url ? `<img src="${escapeAttr(url)}" alt="">` : escapeHtml(initials(item?.title))}</span>`;
  }

  function filteredConversations() {
    const query = state.conversationSearch.trim().toLowerCase();
    return state.conversations.filter((item) => {
      const matchesQuery = !query || `${item.title || ""} ${item.conversation_type || ""} ${conversationPreview(item)}`.toLowerCase().includes(query);
      const type = String(item.conversation_type || "direct");
      const unread = Number(item.unread_count || 0) > 0;
      const shield = riskScan(conversationPreview(item)).risky || Number(item.shield_count || item.risk_score || 0) > 0;
      const matchesFilter = state.filter === "direct"
        ? type === "direct"
        : state.filter === "groups"
          ? type === "group"
          : state.filter === "rooms"
            ? type === "room" || type === "community_channel"
            : state.filter === "ai"
              ? Boolean(item.ai_assistant || item.is_ai || type === "ai" || type === "assistant")
            : state.filter === "unread"
              ? unread
              : state.filter === "shield"
                ? shield
              : true;
      return matchesQuery && matchesFilter;
    });
  }

  function renderRealtimeStatus() {
    const target = el("[data-realtime-status]");
    if (!target) return;
    const connected = Boolean(state.realtimeConnected);
    target.dataset.state = connected ? "connected" : "syncing";
    target.innerHTML = `<span aria-hidden="true"></span><b>${connected ? "Live" : "Refreshing"}</b>`;
    const threadSignal = el("[data-thread-signal-state]");
    if (threadSignal) threadSignal.textContent = connected ? "Realtime connected" : "Refreshing messages";
  }

  function messageDeliveryLabel(item) {
    const raw = String(item?.delivery_state || item?.delivery_status || (item?._pending ? "sending" : "") || "").toLowerCase();
    if (item?._failed || raw === "failed") return "Failed";
    if (item?._pending || raw === "sending") return "Sending";
    if (raw.includes("read") || raw.includes("seen")) return "Read";
    if (raw.includes("delivered")) return "Delivered";
    if (raw.includes("sent")) return "Sent";
    return "Sent";
  }

  function deliveryGlyph(label) {
    return {
      Sending: "⟲",
      Sent: "✓",
      Delivered: "✓✓",
      Read: "◉",
      Failed: "!",
    }[label] || "✓";
  }

  function threadRiskSummary() {
    const risky = (state.messages || []).filter((item) => riskScan(item.body || "").risky || item?.pulse_shield?.flagged);
    if (!risky.length) return { count: 0, label: "Shield calm", text: "No active message risk in this thread." };
    const top = Math.max(...risky.map((item) => Number(item?.pulse_shield?.score || riskScan(item.body || "").score || 0)));
    return {
      count: risky.length,
      label: top >= 75 ? "Shield critical" : "Shield attention",
      text: `${risky.length} message${risky.length === 1 ? "" : "s"} need link or scam review.`,
    };
  }

  function renderSignalIntelligence() {
    const presence = presenceForConversation(state.active || {});
    const reachability = el("[data-thread-reachability]");
    if (reachability) {
      const label = presenceLabel(presence);
      reachability.textContent = state.active ? label : "Presence unavailable.";
    }
    const risk = threadRiskSummary();
    const shieldState = el("[data-thread-shield-state]");
    if (shieldState) {
      shieldState.textContent = risk.label;
      shieldState.dataset.state = risk.count ? "attention" : "calm";
    }
    const shieldSummary = el("[data-shield-summary]");
    if (shieldSummary) shieldSummary.textContent = risk.text;
    const deliverySummary = el("[data-delivery-summary]");
    if (deliverySummary) {
      if (!state.active) deliverySummary.textContent = "Open a chat to see route confidence.";
      else {
        const mine = (state.messages || []).filter((item) => Number(item.sender_user_id || 0) === currentUserId || item.is_mine);
        const last = mine[mine.length - 1];
        deliverySummary.textContent = last ? `Last outbound signal: ${messageDeliveryLabel(last)}.` : "No outbound messages in this thread yet.";
      }
    }
    const route = el("[data-signal-route]");
    if (route) {
      route.dataset.state = state.realtimeConnected ? "live" : "syncing";
      route.querySelectorAll("span").forEach((node, index) => {
        node.dataset.active = index < (state.realtimeConnected ? 4 : 2) ? "true" : "false";
      });
    }
  }

  function renderActiveRail() {
    const rail = el("[data-active-rail]");
    if (!rail) return;
    const activeContacts = state.conversations
      .filter((item) => !isPulseAIConversation(item))
      .filter((item) => String(item.conversation_type || "") !== "group")
      .slice(0, 9)
      .map((item) => {
        const presence = presenceForConversation(item);
        return `
          <button class="active-person" type="button" data-conversation-id="${item.conversation_id}" title="${escapeAttr(item.title || "Open chat")}">
            ${avatarHtml(item, `active-avatar presence-${presenceClass(presence)}`)}
            <span>${escapeHtml(item.title || "Chat")}</span>
          </button>
        `;
      }).join("");
    rail.innerHTML = activeContacts;
  }

  function renderPinnedConversations() {
    const section = el("[data-pinned-section]");
    const rail = el("[data-pinned-conversations]");
    if (!section || !rail) return;
    const pinned = state.conversations
      .filter((item) => item.pinned && !isPulseAIConversation(item))
      .slice(0, 8);
    section.hidden = !pinned.length;
    rail.innerHTML = pinned.map((item) => `
      <button class="pinned-card" type="button" data-conversation-id="${item.conversation_id}">
        ${avatarHtml(item, "pinned-avatar")}
        <strong>${escapeHtml(item.title || "Chat")}</strong>
        <small>${escapeHtml(conversationPreview(item))}</small>
        ${Number(item.unread_count || 0) ? `<span class="badge">${Number(item.unread_count)}</span>` : `<span class="pin-mark" aria-label="Pinned">&#9679;</span>`}
      </button>`).join("");
  }

  function renderConversations() {
    if (!list) return;
    renderRealtimeStatus();
    renderActiveRail();
    const filtered = filteredConversations();
    renderPinnedConversations();
    if (!filtered.length) {
      const hasQuery = Boolean(state.conversationSearch.trim());
      const empty = hasQuery
        ? "No matching conversations. Try another signal or clear search."
        : state.filter === "groups"
        ? "No groups yet. Create a group to bring people together."
        : state.filter === "rooms"
          ? "No rooms yet. Open or create a room when you are ready."
          : state.filter === "unread"
            ? "No unread chats. You are caught up."
            : state.filter === "ai"
              ? "No AI conversations are available."
            : state.filter === "shield"
              ? "No Shield-flagged chats. Pulse Shield is calm."
            : state.filter === "direct"
              ? "No direct messages yet. Start a DM from New Chat."
              : "No conversations yet. Start a DM, create a group, or open a room.";
      list.innerHTML = `<div class="empty-state">${empty}</div>`;
      return;
    }
    list.innerHTML = filtered.map((item) => {
      const presence = presenceForConversation(item);
      const typingNames = state.active && Number(state.active.conversation_id) === Number(item.conversation_id)
        ? (state.typing || []).map((user) => user.display_name || "Someone")
        : [];
      const preview = typingNames.length ? typingSummary(typingNames) : conversationPreview(item);
      return `
      <article class="conversation ${Number(item.unread_count || 0) ? "is-unread" : ""} ${state.active && Number(state.active.conversation_id) === Number(item.conversation_id) ? "is-active" : ""}" data-conversation-row="${item.conversation_id}">
        <button class="conversation-open" type="button" data-conversation-id="${item.conversation_id}" aria-label="Open ${escapeAttr(item.title || "chat")}"></button>
        ${avatarHtml(item, `avatar presence-${presenceClass(presence)}`)}
        <span class="conversation-main">
          <strong>${escapeHtml(item.title || "Untitled chat")}${item.verified ? ` <span class="verified-mark" title="Verified">✓</span>` : ""}${item.pinned ? ` <span class="pin-mark" title="Pinned">&#9679;</span>` : ""}</strong>
          <small class="${typingNames.length ? "is-typing" : ""}">${escapeHtml(preview)}</small>
          <span class="conversation-state">${item.muted ? "Muted" : item.pinned ? "Pinned" : escapeHtml(typeLabel(item.conversation_type || "chat"))}</span>
        </span>
        <span class="conversation-meta"><time>${escapeHtml(shortTime(item.last_message_at || item.last_activity_at || item.updated_at || item.created_at))}</time>${Number(item.unread_count || 0) ? `<span class="badge">${Number(item.unread_count)}</span>` : `<span class="delivery-mark" aria-label="Read">&#10003;</span>`}</span>
      </article>
    `; }).join("");
  }

  function openConversationActions(conversationId) {
    state.actionConversationId = Number(conversationId || 0);
    const sheet = el("[data-conversation-action-sheet]");
    const item = state.conversationCache.get(state.actionConversationId);
    const pin = sheet?.querySelector('[data-conversation-action="pin"]');
    if (pin) pin.textContent = item?.pinned ? "Unpin chat" : "Pin chat";
    const mute = sheet?.querySelector('[data-conversation-action="mute"]');
    if (mute) mute.textContent = item?.muted ? "Unmute conversation" : "Mute conversation";
    if (sheet) sheet.hidden = false;
  }

  function closeConversationActions() {
    state.actionConversationId = 0;
    const sheet = el("[data-conversation-action-sheet]");
    if (sheet) sheet.hidden = true;
  }

  async function updateConversationPreference(action) {
    const id = state.actionConversationId;
    if (!id) return;
    const item = state.conversationCache.get(id);
    if (!item) return;
    if (action === "pin") {
      item.pinned = !item.pinned;
      renderConversations();
      try {
        const data = await api(`/conversations/${id}/pin`, { method: "POST", body: "{}" }, "pin_conversation");
        item.pinned = Boolean(data.pinned);
      } catch (error) {
        item.pinned = !item.pinned;
        throw error;
      } finally {
        sortConversations();
        renderConversations();
      }
    } else if (action === "unread") {
      const data = await api(`/conversations/${id}/unread`, { method: "POST", body: "{}" }, "mark_unread");
      item.unread_count = Number(data.unread_count || 1);
      renderConversations();
    } else if (action === "mute") {
      const data = await api(`/conversations/${id}/mute`, { method: "POST", body: "{}" }, "mute_conversation");
      item.muted = Boolean(data.muted);
      setStatus(data.message || (item.muted ? "Conversation muted." : "Conversation unmuted."));
      renderConversations();
    } else if (action === "archive") {
      await api(`/conversations/${id}/archive`, { method: "POST", body: "{}" }, "archive_conversation");
      state.conversations = state.conversations.filter((conversation) => Number(conversation.conversation_id) !== id);
      state.conversationCache.delete(id);
      if (state.active && Number(state.active.conversation_id) === id) {
        state.active = null;
        state.messages = [];
        state.members = [];
        setMobileMode("list");
      }
      setStatus("Conversation archived.");
      renderConversations();
      renderMessages();
      renderMembers();
    }
    closeConversationActions();
  }

  function controlStatus(text, kind = "info") {
    const node = el("[data-control-status]");
    if (!node) return;
    node.textContent = text || "";
    node.dataset.kind = kind;
    node.hidden = !text;
  }

  function activeControlConversationId() {
    if (state.controlEntry === "inbox") {
      return Number(state.controlConversationId || state.controlData?.conversation?.conversation_id || state.controlData?.conversation?.id || 0);
    }
    return Number(state.active?.conversation_id || state.controlData?.conversation?.conversation_id || state.controlData?.conversation?.id || 0);
  }

  function callOptionsForConversation(conversationId) {
    const conversation = state.conversationCache.get(Number(conversationId)) || state.active || state.controlData?.conversation || {};
    const groupLike = Boolean(conversation.is_group || ["group", "room", "community_channel"].includes(String(conversation.conversation_type || "").toLowerCase()));
    return {
      conversationId: Number(conversationId || conversation.conversation_id || conversation.id || 0),
      callScope: groupLike ? "group" : "direct",
    };
  }

  async function startConversationCall(callType = "audio", source = "thread") {
    const conversationId = source === "control" ? activeControlConversationId() : Number(state.active?.conversation_id || activeControlConversationId() || 0);
    if (!conversationId) {
      const message = "Choose a conversation before starting a call.";
      if (source === "control") controlStatus(message, "error");
      else setStatus(message);
      return;
    }
    if (!window.PulseSocCalls) {
      const message = "Pulse controls are still loading. Try again in a moment.";
      if (source === "control") controlStatus(message, "error");
      else setStatus(message);
      return;
    }
    const starter = callType === "video" ? window.PulseSocCalls.startVideoCall : window.PulseSocCalls.startAudioCall;
    const result = await starter(callOptionsForConversation(conversationId));
    const ok = result?.ok !== false;
    const message = ok ? (callType === "video" ? "Video Pulse sent." : "Voice Pulse sent.") : (result?.message || "Pulse could not start.");
    if (source === "control") controlStatus(message, ok ? "success" : "error");
    else setStatus(message);
  }

  function isControlGroup() {
    const conversation = state.controlData?.conversation || state.active || {};
    return Boolean(conversation.is_group || ["group", "room", "community_channel"].includes(String(conversation.conversation_type || "").toLowerCase()));
  }

  function controlSettingValue(sectionId, item) {
    const settings = state.controlData?.settings || {};
    return settings?.[sectionId]?.[item.setting];
  }

  function controlItemAvailable(section, item) {
    const conversation = state.controlData?.conversation || state.active || {};
    if (item.groupOnly && !isControlGroup()) return false;
    if (item.directOnly && String(conversation.conversation_type || "") !== "direct") return false;
    if (item.adminOnly && !conversation.is_admin) return false;
    if (item.requiresAi && !state.aiEnabled) return false;
    return true;
  }

  function controlItemStatus(section, item) {
    if (!controlItemAvailable(section, item)) return "hidden";
    if (item.requiresAi && !state.aiEnabled) return "hidden";
    return item.status || "ready";
  }

  function formatControlStat(item, stats = {}) {
    if (item.stat === "storage_used") return formatBytes(stats.storage_used_bytes || 0);
    if (item.stat === "media_files") return String(Number(stats.media_files || 0));
    if (["photos", "videos", "voice", "files", "links", "messages"].includes(item.stat)) return String(Number(stats[item.stat] || 0));
    return "";
  }

  function renderControlCenter() {
    const panel = el("[data-conversation-control-center]");
    const content = el("[data-control-content]");
    if (!panel || !content) return;
    if (!state.controlOpen) return;
    if (state.controlLoading) {
      content.innerHTML = `<div class="control-loading"><span></span><strong>Loading controls...</strong></div>`;
      return;
    }
    if (!state.controlData?.conversation) {
      renderControlConversationChooser(content);
      return;
    }
    const conversation = state.controlData.conversation;
    const stats = state.controlData.stats || conversation.stats || {};
    const query = state.controlSearch.trim().toLowerCase();
    const sections = CONTROL_SECTIONS.filter((section) => {
      if (section.groupOnly && !isControlGroup()) return false;
      const haystack = `${section.title} ${section.desc} ${(section.items || []).map((item) => item.label).join(" ")}`.toLowerCase();
      return !query || haystack.includes(query);
    });
    applyControlAppearanceSettings();
    const statusText = conversation.conversation_type === "direct"
      ? `${stats.activity_status || "Offline"} · Direct Conversation`
      : `${Number(stats.members || conversation.member_count || 0)} members · ${typeLabel(conversation.conversation_type)} Conversation`;
    const quickActions = [
      { label: "Search", icon: "⌕", action: "search-chat" },
      { label: "Call", icon: "☎", action: "start-audio-call" },
      { label: "Video", icon: "▣", action: "start-video-call" },
      { label: stats.muted ? "Unmute" : "Mute", icon: "🔕", action: "mute" },
      { label: stats.pinned || conversation.pinned ? "Unpin" : "Pin", icon: "📌", action: "pin" },
      { label: "Archive", icon: "▤", action: "archive", dangerConfirm: "Archive this conversation?" },
    ];
    content.innerHTML = `
      ${state.controlEntry === "inbox" ? `<button class="control-conversation-back" type="button" data-control-choose-another aria-label="Choose another conversation"><span aria-hidden="true">&larr;</span> Choose another conversation</button>` : ""}
      <section class="control-profile">
        ${avatarHtml(conversation, "control-avatar")}
        <div>
          <h3>${escapeHtml(conversation.title || "Conversation")}</h3>
          <p>${escapeHtml(statusText)}</p>
        </div>
        <div class="control-profile-actions" aria-label="Conversation shortcuts">
          <button type="button" data-control-action="search-chat" aria-label="Search chat">⌕</button>
          <button type="button" data-control-action="shared-media" aria-label="Shared media">🖼</button>
          <button type="button" data-control-action="members" aria-label="Members">👥</button>
        </div>
      </section>
      <section class="control-quick-actions" aria-label="Quick actions">
        ${quickActions.map((item) => renderControlActionButton(item)).join("")}
      </section>
      <section class="control-info-grid" aria-label="Conversation status">
        ${[
          ["🛡", "Protected", stats.security_label || "Secured session"],
          ["👥", "Members", Number(stats.members || conversation.member_count || 0)],
          ["🖼", "Media Files", Number(stats.media_files || 0)],
          ["◉", "Storage Used", formatBytes(stats.storage_used_bytes || 0)],
          ["●", "Unread", Number(stats.unread || conversation.unread_count || 0)],
          ["↔", "Connection", stats.connection || "Connected"],
        ].map(([icon, label, value]) => `<article><span>${icon}</span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(String(value))}</small></article>`).join("")}
      </section>
      <label class="control-search">
        <span aria-hidden="true">⌕</span>
        <input data-control-search type="search" autocomplete="off" placeholder="Search settings..." aria-label="Search settings" value="${escapeAttr(state.controlSearch)}">
        ${state.controlSearch ? `<button type="button" data-control-search-clear aria-label="Clear settings search">x</button>` : ""}
      </label>
      <section class="control-section-list" data-control-sections>
        ${state.controlDetail ? renderControlDetail() : ""}
        ${sections.length ? sections.map((section) => renderControlSection(section, query)).join("") : `<div class="control-empty"><strong>No settings found.</strong><small>Try a different search.</small></div>`}
      </section>
    `;
  }

  function renderControlConversationChooser(content) {
    const conversations = state.conversations
      .filter((conversation) => Number(conversation.conversation_id || conversation.id || 0))
      .slice(0, 30);
    content.innerHTML = `
      <section class="control-conversation-chooser" aria-labelledby="controlConversationChooserTitle">
        <header>
          <span class="control-chooser-orb" aria-hidden="true">&#9881;</span>
          <div>
            <h3 id="controlConversationChooserTitle">Choose a conversation</h3>
            <p>Select the chat whose controls you want to manage.</p>
          </div>
        </header>
        <div class="control-conversation-list" role="list">
          ${conversations.length ? conversations.map((conversation) => {
            const id = Number(conversation.conversation_id || conversation.id || 0);
            const presence = presenceForConversation(conversation);
            return `<button class="control-conversation-choice" type="button" data-control-select-conversation="${id}" role="listitem" aria-label="Manage ${escapeAttr(conversation.title || "conversation")}">
              ${avatarHtml(conversation, `control-choice-avatar presence-${presenceClass(presence)}`)}
              <span><strong>${escapeHtml(conversation.title || "Conversation")}</strong><small>${escapeHtml(presenceLabel(presence))} · ${escapeHtml(typeLabel(conversation.conversation_type || "conversation"))}</small></span>
              ${Number(conversation.unread_count || 0) ? `<em>${Number(conversation.unread_count)}</em>` : `<i aria-hidden="true">&rsaquo;</i>`}
            </button>`;
          }).join("") : `<div class="control-empty"><strong>No conversations available.</strong><small>Start a chat, then return here to manage its controls.</small></div>`}
        </div>
      </section>
    `;
  }

  function renderControlDetail() {
    const detail = state.controlDetail || {};
    return `<article class="control-detail" data-control-detail>
      <header>
        <div><strong>${escapeHtml(detail.title || "Conversation detail")}</strong><small>${escapeHtml(detail.subtitle || "")}</small></div>
        <button type="button" data-control-detail-close aria-label="Close detail">x</button>
      </header>
      <div class="control-detail-body">${detail.html || ""}</div>
    </article>`;
  }

  function controlEmptyHtml(title, body = "") {
    return `<div class="control-empty"><strong>${escapeHtml(title)}</strong>${body ? `<small>${escapeHtml(body)}</small>` : ""}</div>`;
  }

  function controlMemberListHtml(members = []) {
    if (!members.length) return controlEmptyHtml("No members found.");
    return `<div class="control-detail-list">${members.map((member) => `<article>
      ${avatarHtml({ title: member.display_name, avatar_url: member.avatar_url }, "control-detail-avatar")}
      <span><strong>${escapeHtml(member.display_name || "Pulse member")}</strong><small>${escapeHtml(member.role || "member")} · ${member.active_now ? "Online" : "Offline"}</small></span>
    </article>`).join("")}</div>`;
  }

  function controlMediaListHtml(items = [], kind = "media") {
    if (!items.length) return controlEmptyHtml(`No shared ${kind === "all" ? "media" : kind} found.`);
    return `<div class="control-detail-list">${items.map((item) => {
      const type = item.media_type || item.mime_type || "file";
      const size = formatBytes(item.file_size_bytes || 0);
      const preview = item.thumbnail_url || item.url || "";
      return `<article>
        <span class="control-file-icon">${type.includes("video") ? "▶" : type.includes("voice") || type.includes("audio") ? "🎤" : type.includes("image") || type.includes("photo") ? "🖼" : "▤"}</span>
        <span><strong>${escapeHtml(typeLabel(type))}</strong><small>${escapeHtml(size)} · ${escapeHtml(item.sender_display_name || "Pulse member")} · ${escapeHtml(shortTime(item.created_at || ""))}</small>${item.body_preview ? `<small>${escapeHtml(item.body_preview)}</small>` : ""}</span>
        ${preview ? `<a href="${escapeAttr(preview)}" target="_blank" rel="noopener" aria-label="Open shared media">Open</a>` : ""}
      </article>`;
    }).join("")}</div>`;
  }

  function controlLinksHtml(items = []) {
    if (!items.length) return controlEmptyHtml("No shared links found.");
    return `<div class="control-detail-list">${items.map((item) => `<article>
      <span class="control-file-icon">↗</span>
      <span><strong>${escapeHtml(item.domain || "Shared link")}</strong><small>${escapeHtml(item.sender_display_name || "Pulse member")} · ${escapeHtml(shortTime(item.created_at || ""))}</small><small>${escapeHtml(item.url || "")}</small></span>
      <a href="${escapeAttr(item.url || "#")}" target="_blank" rel="noopener" aria-label="Open shared link">Open</a>
    </article>`).join("")}</div>`;
  }

  function controlPinnedHtml(items = []) {
    if (!items.length) return controlEmptyHtml("No pinned messages yet.", "Pin a message from the message actions menu.");
    return `<div class="control-detail-list">${items.map((item) => `<article>
      <span class="control-file-icon">📌</span>
      <span><strong>${escapeHtml(item.sender_display_name || item.sender?.display_name || "Pulse member")}</strong><small>${escapeHtml(shortTime(item.created_at || ""))}</small><small>${escapeHtml(item.body || item.preview || typeLabel(item.message_type || "message"))}</small></span>
      <button type="button" data-jump-message="${escapeAttr(item.id)}" aria-label="Jump to pinned message">View</button>
    </article>`).join("")}</div>`;
  }

  function controlStatsHtml(stats = {}) {
    const rows = [
      ["Messages", stats.messages || 0],
      ["Media files", stats.media_files || 0],
      ["Photos", stats.photos || 0],
      ["Videos", stats.videos || 0],
      ["Voice messages", stats.voice || 0],
      ["Files", stats.files || 0],
      ["Links", stats.links || 0],
      ["Storage", formatBytes(stats.storage_used_bytes || 0)],
      ["Unread", stats.unread || 0],
      ["Connection", stats.connection || "Connected"],
    ];
    return `<div class="control-detail-grid">${rows.map(([label, value]) => `<article><strong>${escapeHtml(String(value))}</strong><small>${escapeHtml(label)}</small></article>`).join("")}</div>`;
  }

  function setControlDetail(title, html, subtitle = "") {
    state.controlDetail = { title, html, subtitle };
    renderControlCenter();
    window.setTimeout(() => el("[data-control-detail]")?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" }), 20);
  }

  function renderControlActionButton(item) {
    const status = item.status || "ready";
    const disabled = status !== "ready";
    return `<button class="control-quick-action ${disabled ? "is-disabled" : ""}" type="button" ${disabled ? "disabled" : `data-control-action="${escapeAttr(item.action)}"`} ${item.dangerConfirm ? `data-control-confirm="${escapeAttr(item.dangerConfirm)}"` : ""} aria-label="${escapeAttr(item.label)}" title="${disabled ? controlBadgeLabel(status) : escapeAttr(item.label)}">
      <span>${item.icon || "•"}</span><small>${escapeHtml(item.label)}</small>${disabled ? `<em>${controlBadgeLabel(status)}</em>` : ""}
    </button>`;
  }

  function renderControlSection(section, query) {
    const expanded = state.controlExpanded.has(section.id) || Boolean(query);
    const sectionMatches = !query || `${section.title} ${section.desc}`.toLowerCase().includes(query);
    const visibleItems = (section.items || []).filter((item) => {
      if (!controlItemAvailable(section, item)) return false;
      if (controlItemStatus(section, item) === "hidden") return false;
      const text = `${item.label} ${section.title}`.toLowerCase();
      return sectionMatches || text.includes(query);
    });
    if (!visibleItems.length) return "";
    return `<article class="control-section ${section.danger ? "is-danger" : ""}" data-control-section="${escapeAttr(section.id)}">
      <button class="control-section-head" type="button" data-control-section-toggle="${escapeAttr(section.id)}" aria-expanded="${expanded ? "true" : "false"}">
        <span class="control-section-icon">${section.icon || "•"}</span>
        <span><strong>${escapeHtml(section.title)}</strong><small>${escapeHtml(section.desc || "")}</small></span>
        <em>${expanded ? "⌃" : "⌄"}</em>
      </button>
      <div class="control-section-body" ${expanded ? "" : "hidden"}>
        ${visibleItems.map((item) => renderControlItem(section, item)).join("")}
      </div>
    </article>`;
  }

  function renderControlItem(section, item) {
    const status = controlItemStatus(section, item);
    if (status === "hidden") return "";
    const disabled = status !== "ready";
    const badge = disabled ? `<span class="control-badge">${controlBadgeLabel(status)}</span>` : "";
    const confirm = item.dangerConfirm ? `data-control-confirm="${escapeAttr(item.dangerConfirm)}"` : "";
    const statValue = item.stat ? formatControlStat(item, state.controlData?.stats || {}) : "";
    if (item.stat) {
      return `<div class="control-option is-stat"><span>${item.icon || "•"}</span><strong>${escapeHtml(item.label)}</strong><em>${escapeHtml(statValue)}</em></div>`;
    }
    if (item.kind === "toggle" && item.setting) {
      const value = Boolean(controlSettingValue(section.id, item));
      return `<button class="control-option" type="button" data-control-toggle="${escapeAttr(section.id)}:${escapeAttr(item.setting)}" ${disabled ? "disabled" : ""} aria-pressed="${value ? "true" : "false"}" aria-label="${escapeAttr(item.label)}">
        <span>${item.icon || "•"}</span><strong>${escapeHtml(item.label)}</strong>${badge}<i class="control-switch ${value ? "is-on" : ""}" aria-hidden="true"></i>
      </button>`;
    }
    if (item.kind === "select" && item.setting) {
      const value = String(controlSettingValue(section.id, item) || "");
      return `<label class="control-option has-select ${disabled ? "is-disabled" : ""}">
        <span>${item.icon || "•"}</span><strong>${escapeHtml(item.label)}</strong>${badge}
        <select data-control-select="${escapeAttr(section.id)}:${escapeAttr(item.setting)}" ${disabled ? "disabled" : ""} aria-label="${escapeAttr(item.label)}">
          ${(item.options || []).map(([optionValue, optionLabel]) => `<option value="${escapeAttr(optionValue)}" ${String(optionValue) === value ? "selected" : ""}>${escapeHtml(optionLabel)}</option>`).join("")}
        </select>
      </label>`;
    }
    return `<button class="control-option" type="button" ${disabled ? "disabled" : `data-control-action="${escapeAttr(item.action || "")}"`} ${confirm} aria-label="${escapeAttr(item.label)}" title="${disabled ? controlBadgeLabel(status) : escapeAttr(item.label)}">
      <span>${item.icon || "•"}</span><strong>${escapeHtml(item.label)}</strong>${badge}<em>${disabled ? "" : "›"}</em>
    </button>`;
  }

  function controlBadgeLabel(status) {
    return status === "blocked" ? "Blocked" : "Hidden";
  }

  async function openConversationControlCenter({ entry = "thread" } = {}) {
    state.controlEntry = entry === "inbox" ? "inbox" : "thread";
    state.controlConversationId = state.controlEntry === "thread" ? Number(state.active?.conversation_id || 0) : 0;
    if (state.controlEntry === "inbox") state.controlData = null;
    if (state.controlEntry === "thread" && !state.controlConversationId) {
      setStatus("Open a conversation before using the Control Center.", "error");
      return;
    }
    state.controlOpen = true;
    state.controlLoading = true;
    state.controlSearch = "";
    state.controlDetail = null;
    document.body.classList.add("conversation-control-open");
    const panel = el("[data-conversation-control-center]");
    const backdrop = el("[data-conversation-control-backdrop]");
    if (panel) {
      panel.hidden = false;
      requestAnimationFrame(() => panel.classList.add("is-open"));
    }
    if (backdrop) {
      backdrop.hidden = false;
      requestAnimationFrame(() => backdrop.classList.add("is-open"));
    }
    renderControlCenter();
    if (activeControlConversationId()) await loadConversationControlCenter(true);
    else state.controlLoading = false;
    renderControlCenter();
    window.setTimeout(() => panel?.querySelector("[data-control-search], [data-close-control-center]")?.focus(), 40);
  }

  function closeConversationControlCenter() {
    state.controlOpen = false;
    state.controlSearch = "";
    state.controlDetail = null;
    document.body.classList.remove("conversation-control-open");
    const panel = el("[data-conversation-control-center]");
    const backdrop = el("[data-conversation-control-backdrop]");
    panel?.classList.remove("is-open");
    backdrop?.classList.remove("is-open");
    window.setTimeout(() => {
      if (!state.controlOpen) {
        if (panel) panel.hidden = true;
        if (backdrop) backdrop.hidden = true;
      }
    }, 260);
  }

  async function loadConversationControlCenter(force = false) {
    const id = activeControlConversationId();
    if (!id) return;
    if (!force && state.controlData?.conversation?.conversation_id === id) {
      state.controlLoading = false;
      renderControlCenter();
      return;
    }
    state.controlLoading = true;
    controlStatus("");
    renderControlCenter();
    try {
      const data = await api(`/conversations/${id}/control-center`, {}, "conversation_control_center");
      state.controlData = data;
      if (data.conversation) rememberConversation(data.conversation);
      if (data.settings) cacheControlSettings(id, data.settings);
      applyControlAppearanceSettings();
      state.controlLoading = false;
      renderControlCenter();
    } catch (error) {
      state.controlLoading = false;
      renderControlCenter();
      controlStatus(error?.message || "Conversation controls could not load.", "error");
    }
  }

  async function saveControlSetting(section, key, value) {
    const id = activeControlConversationId();
    if (!id) return;
    const previousSettings = cloneSettings(state.controlData?.settings || readCachedControlSettings(id) || {});
    const optimisticSettings = mirrorControlSetting(cloneSettings(previousSettings), section, key, value);
    state.controlData = { ...(state.controlData || {}), settings: optimisticSettings };
    cacheControlSettings(id, optimisticSettings);
    applyControlAppearanceSettings(optimisticSettings, id);
    state.controlSaving = true;
    controlStatus("Saving...", "info");
    try {
      const data = await api(`/conversations/${id}/control-center`, {
        method: "PATCH",
        body: JSON.stringify({ section, key, value }),
      }, "conversation_control_center_update");
      state.controlData = { ...(state.controlData || {}), settings: data.settings || state.controlData?.settings };
      if (data.settings) cacheControlSettings(id, data.settings);
      applyControlAppearanceSettings();
      if (section === "notifications" && key === "mute_choice") {
        const item = state.conversationCache.get(id);
        if (item) item.muted = value !== "off";
        renderConversations();
      }
      if ((data.settings?.accessibility || {}).haptic_feedback && "vibrate" in navigator) {
        try { navigator.vibrate(12); } catch (_) {}
      }
      controlStatus(data.message || "Saved.", "success");
      renderControlCenter();
    } catch (error) {
      state.controlData = { ...(state.controlData || {}), settings: previousSettings };
      cacheControlSettings(id, previousSettings);
      applyControlAppearanceSettings(previousSettings, id);
      renderControlCenter();
      throw error;
    } finally {
      state.controlSaving = false;
    }
  }

  function controlSettingsCacheKey(conversationId) {
    const id = Number(conversationId || 0);
    return id ? `pulseMessengerControlSettings:${currentUserId}:${id}` : "";
  }

  function cloneSettings(settings) {
    try {
      return JSON.parse(JSON.stringify(settings || {}));
    } catch (_) {
      return {};
    }
  }

  function readCachedControlSettings(conversationId) {
    const id = Number(conversationId || 0);
    if (!id) return null;
    if (state.controlSettingsCache.has(id)) return cloneSettings(state.controlSettingsCache.get(id));
    const key = controlSettingsCacheKey(id);
    if (!key) return null;
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "null");
      if (parsed && typeof parsed === "object") {
        state.controlSettingsCache.set(id, parsed);
        return cloneSettings(parsed);
      }
    } catch (_) {}
    return null;
  }

  function cacheControlSettings(conversationId, settings) {
    const id = Number(conversationId || 0);
    if (!id || !settings) return;
    const snapshot = cloneSettings(settings);
    state.controlSettingsCache.set(id, snapshot);
    const key = controlSettingsCacheKey(id);
    if (!key) return;
    try {
      localStorage.setItem(key, JSON.stringify(snapshot));
    } catch (_) {}
  }

  function mirrorControlSetting(settings, section, key, value) {
    if (!settings || !section || !key) return settings || {};
    settings[section] = { ...(settings[section] || {}), [key]: value };
    if (key === "message_preview" && (section === "notifications" || section === "privacy")) {
      settings.notifications = { ...(settings.notifications || {}), message_preview: Boolean(value) };
      settings.privacy = { ...(settings.privacy || {}), message_preview: Boolean(value) };
    }
    if (key === "read_receipts" && (section === "notifications" || section === "privacy")) {
      settings.notifications = { ...(settings.notifications || {}), read_receipts: Boolean(value) };
      settings.privacy = { ...(settings.privacy || {}), read_receipts: Boolean(value) };
    }
    return settings;
  }

  function activeVisualConversationId() {
    return Number(state.active?.conversation_id || state.controlConversationId || state.controlData?.conversation?.conversation_id || state.controlData?.conversation?.id || 0);
  }

  function normalizeAppearanceValue(kind, value) {
    const text = String(value || "").trim().toLowerCase();
    if (kind === "theme") return CONTROL_THEME_OPTIONS.some(([id]) => id === text) ? text : "dark_galaxy";
    if (kind === "wallpaper") {
      if (text === "default") return "deep_space";
      return CONTROL_WALLPAPER_OPTIONS.some(([id]) => id === text) ? text : "deep_space";
    }
    if (kind === "bubble") return CONTROL_BUBBLE_OPTIONS.some(([id]) => id === text) ? text : "cyan";
    return text;
  }

  function applyControlAppearanceSettings(settingsOverride = null, conversationId = activeVisualConversationId()) {
    const id = Number(conversationId || 0);
    const controlDataId = Number(state.controlData?.conversation?.conversation_id || state.controlData?.conversation?.id || 0);
    const settings = settingsOverride || readCachedControlSettings(id) || (controlDataId === id ? state.controlData?.settings : null) || {};
    const appearance = settings.appearance || {};
    const accessibility = settings.accessibility || {};
    if (!root) return;
    root.dataset.controlTheme = normalizeAppearanceValue("theme", appearance.theme || "dark_galaxy");
    root.dataset.controlWallpaper = normalizeAppearanceValue("wallpaper", appearance.wallpaper || "deep_space");
    root.dataset.controlBubble = normalizeAppearanceValue("bubble", appearance.bubble_color || "cyan");
    root.dataset.controlDensity = appearance.density || "balanced";
    root.dataset.controlFont = accessibility.large_text ? "large" : (appearance.font_size || "medium");
    root.dataset.controlAnimation = appearance.animation_level || "balanced";
    root.dataset.controlParticles = (appearance.reduce_particles || accessibility.reduce_motion || appearance.animation_level === "reduced" || appearance.animation_level === "off") ? "reduced" : "full";
    root.classList.toggle("control-reduce-motion", Boolean(accessibility.reduce_motion || appearance.reduce_particles || appearance.animation_level === "reduced" || appearance.animation_level === "off"));
    root.classList.toggle("control-high-contrast", Boolean(accessibility.high_contrast || appearance.high_contrast));
    root.classList.toggle("control-large-text", Boolean(accessibility.large_text || ["large", "extra_large"].includes(String(appearance.font_size || ""))));
    root.classList.toggle("control-compact-density", appearance.density === "compact");
    root.classList.toggle("control-relaxed-density", appearance.density === "relaxed");
  }

  async function hydrateConversationVisualSettings(conversationId, { force = false } = {}) {
    const id = Number(conversationId || 0);
    if (!id) return;
    const cached = readCachedControlSettings(id);
    if (cached) applyControlAppearanceSettings(cached, id);
    if (!force && state.controlSettingsCache.has(id)) return;
    try {
      const data = await api(`/conversations/${id}/control-center`, {}, "conversation_control_visual_settings");
      if (data.settings) {
        cacheControlSettings(id, data.settings);
        if (Number(state.active?.conversation_id || 0) === id) applyControlAppearanceSettings(data.settings, id);
      }
    } catch (_) {
      if (cached) applyControlAppearanceSettings(cached, id);
    }
  }

  async function runControlAction(action, confirmText = "") {
    if (!action) return;
    if (confirmText && !window.confirm(confirmText)) return;
    const id = activeControlConversationId();
    if (action === "start-audio-call" || action === "start-video-call") {
      await startConversationCall(action === "start-video-call" ? "video" : "audio", "control");
      return;
    }
    if (["pin", "archive", "mark-unread", "mute"].includes(action)) {
      state.actionConversationId = id;
      const mapped = action === "mark-unread" ? "unread" : action;
      await updateConversationPreference(mapped);
      if (action === "archive") closeConversationControlCenter();
      else await loadConversationControlCenter(true);
      return;
    }
    if (action === "search-chat") {
      closeConversationControlCenter();
      toggleThreadSearch(true);
      return;
    }
    if (action === "members") {
      controlStatus("Loading members...", "info");
      const data = await api(`/conversations/${id}/members`, {}, "conversation_members");
      setControlDetail("Members", controlMemberListHtml(data.members || []), `${(data.members || []).length} participant${(data.members || []).length === 1 ? "" : "s"}`);
      controlStatus("");
      return;
    }
    if (action === "shared-media" || action === "shared-files" || action === "largest-files") {
      controlStatus("Loading shared media...", "info");
      const kind = action === "shared-files" || action === "largest-files" ? "files" : "all";
      const data = await api(`/conversations/${id}/control-center/media?kind=${encodeURIComponent(kind)}&limit=${action === "largest-files" ? 100 : 60}`, {}, "conversation_control_media");
      let items = data.items || [];
      if (action === "largest-files") items = items.slice().sort((a, b) => Number(b.file_size_bytes || 0) - Number(a.file_size_bytes || 0)).slice(0, 20);
      setControlDetail(action === "largest-files" ? "Largest Files" : action === "shared-files" ? "Shared Files" : "Shared Media", controlMediaListHtml(items, kind), `${items.length} item${items.length === 1 ? "" : "s"}`);
      controlStatus("");
      return;
    }
    if (action === "shared-links") {
      controlStatus("Loading shared links...", "info");
      const data = await api(`/conversations/${id}/control-center/links`, {}, "conversation_control_links");
      setControlDetail("Shared Links", controlLinksHtml(data.items || []), `${(data.items || []).length} link${(data.items || []).length === 1 ? "" : "s"}`);
      controlStatus("");
      return;
    }
    if (action === "pinned-messages") {
      controlStatus("Loading pinned messages...", "info");
      const data = await api(`/conversations/${id}/control-center/pins`, {}, "conversation_control_pins");
      setControlDetail("Pinned Messages", controlPinnedHtml(data.items || []), `${(data.items || []).length} pinned`);
      controlStatus("");
      return;
    }
    if (action === "storage") {
      state.controlExpanded.add("storage");
      state.controlSearch = "";
      renderControlCenter();
      el('[data-control-section="storage"]')?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (action === "message-stats" || action === "security-status") {
      if (action === "message-stats") {
        setControlDetail("Message Stats", controlStatsHtml(state.controlData?.stats || {}), "Live counts from this conversation");
      } else {
        setControlDetail("Security Status", `<div class="control-detail-copy"><p>This conversation uses authenticated PulseSoc access, participant checks, block rules, media scan state, and server-side read boundaries.</p><p>True end-to-end encryption is not claimed here; the current status is a protected channel.</p></div>`, "Protected channel");
      }
      return;
    }
    if (action === "ai-summary") return await runAIAction("summary");
    if (action === "ai-replies") return await runAIAction("smart-replies");
    if (action === "open-notification-settings") {
      window.location.href = "/pulse/settings/notifications";
      return;
    }
    if (action === "trusted-devices" || action === "active-sessions") {
      window.location.href = "/pulse/settings/devices";
      return;
    }
    if (action === "security-log") {
      window.location.href = "/pulse/settings/security";
      return;
    }
    if (action === "verify-contact") {
      const members = state.controlData?.members || state.controlData?.conversation?.participants_preview || state.members || [];
      const peer = members.find((member) => Number(member.user_id) !== currentUserId);
      setControlDetail("Verify Contact", peer ? `<div class="control-detail-copy"><p>${escapeHtml(peer.display_name || "This member")} is verified through PulseSoc authenticated participant access for this direct conversation.</p><p>Cryptographic fingerprint verification is not exposed because this chat does not claim true end-to-end encryption.</p></div>` : controlEmptyHtml("No direct peer found."), "Protected participant");
      return;
    }
    if (action === "export-chat") {
      controlStatus("Preparing export...", "info");
      const data = await api(`/conversations/${id}/control-center/export`, {}, "conversation_control_export");
      const blob = new Blob([JSON.stringify(data.export || {}, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = data.filename || `pulsesoc-conversation-${id}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      controlStatus("Export downloaded.", "success");
      return;
    }
    if (action === "clear-cache") {
      try {
        clearDraft(id);
        state.messageCache.delete(id);
        controlStatus("Local cache cleared for this device.", "success");
      } catch (_) {
        controlStatus("Local cache could not be cleared.", "error");
      }
      return;
    }
    if (action === "create-note" || action === "create-task") {
      const body = window.prompt(action === "create-note" ? "Conversation note" : "Conversation task");
      if (!body) return;
      const data = await api(`/conversations/${id}/control-center/action`, { method: "POST", body: JSON.stringify({ action, body }) }, "conversation_control_action");
      controlStatus(data.message || "Saved.", "success");
      return;
    }
    if (["report-conversation", "block-user", "clear-conversation", "delete-conversation", "leave-group", "delete-media", "reset-settings"].includes(action)) {
      const data = await api(`/conversations/${id}/control-center/action`, { method: "POST", body: JSON.stringify({ action }) }, "conversation_control_action");
      controlStatus(data.message || "Done.", "success");
      if (action === "reset-settings") {
        state.controlData = { ...(state.controlData || {}), settings: data.settings || state.controlData?.settings };
        applyControlAppearanceSettings();
      }
      if (["delete-conversation", "leave-group"].includes(action)) {
        closeConversationControlCenter();
        await loadConversations({ selectFirst: false });
      } else if (["clear-conversation", "delete-media"].includes(action)) {
        state.messageCache.delete(id);
        if (Number(state.active?.conversation_id || 0) === id) await loadMessages(id);
      } else {
        await loadConversationControlCenter(true);
      }
      return;
    }
    controlStatus("That control is hidden until its backend is ready.", "error");
  }

  function trapControlFocus(event) {
    if (!state.controlOpen || event.key !== "Tab") return;
    const panel = el("[data-conversation-control-center]");
    if (!panel) return;
    const focusable = Array.from(panel.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter((node) => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function pulseAIQuickPromptsHtml(prompts = []) {
    const items = (prompts.length ? prompts : [
      "What is PulseSoc?",
      "How do I create a Status?",
      "How do I manage crypto alerts?",
      "How do I start a video Pulse?",
      "How do notifications work?",
    ]).slice(0, 6);
    return `<div class="pulse-ai-quick-prompts" aria-label="Pulse AI quick prompts">${items.map((prompt) => `<button type="button" data-pulse-ai-prompt="${escapeAttr(prompt)}">${escapeHtml(prompt)}</button>`).join("")}</div>`;
  }

  function pulseAIPrivacyPanelHtml() {
    const settings = state.pulseAISettings || {};
    const rows = [
      ["remember_preferences", "Remember my preferences"],
      ["use_pulse_ai_chat_history", "Use my Pulse AI chat history"],
      ["assist_with_messages_when_asked", "Help with my messages when I ask"],
      ["improve_from_feedback", "Improve from my feedback"],
    ];
    return `
      <details class="pulse-ai-privacy-panel">
        <summary>Pulse AI privacy controls</summary>
        <div>
          ${rows.map(([key, label]) => {
            const enabled = Boolean(settings[key]);
            return `<button type="button" data-pulse-ai-setting="${key}" aria-pressed="${enabled ? "true" : "false"}"><span>${escapeHtml(label)}</span><b>${enabled ? "On" : "Off"}</b></button>`;
          }).join("")}
          <button type="button" data-pulse-ai-memory-clear><span>Clear Pulse AI memory</span><b>Clear</b></button>
          <button type="button" data-pulse-ai-memory-export><span>Export Pulse AI memory</span><b>Export</b></button>
        </div>
      </details>`;
  }

  function syncPulseAIComposerControls() {
    const isAI = isPulseAIConversation();
    const input = el("[data-message-input]");
    if (input) input.placeholder = isAI ? "Ask Pulse AI..." : "Type a message...";
    const attachment = el("[data-toggle-attachments]");
    const voice = el("[data-voice-start]");
    [attachment, voice].forEach((button) => {
      if (!button) return;
      button.disabled = isAI;
      button.classList.toggle("is-disabled", isAI);
      button.title = isAI ? "Pulse AI supports text conversation first." : (button === attachment ? "Add attachment" : "Record voice note");
    });
  }

  function renderMessages() {
    if (!messages) return;
    const title = el("[data-thread-title]");
    const sub = el("[data-thread-subtitle]");
    const avatar = el("[data-thread-avatar]");
    const activeIsAI = isPulseAIConversation(state.active);
    if (title) title.textContent = state.active ? state.active.title : "Choose a chat";
    const threadPresence = presenceForConversation(state.active || {});
    const presenceText = state.active ? presenceLabel(threadPresence) : "Standby";
    if (sub) {
      sub.textContent = state.active
        ? activeIsAI ? "Online · Galaxy Assistant" : `${presenceText} · ${typeLabel(state.active.conversation_type || "conversation")}`
        : "Start a secure conversation.";
      sub.setAttribute("data-presence", presenceClass(threadPresence));
      sub.setAttribute("data-presence-label", activeIsAI ? "Online" : presenceText);
    }
    if (avatar) {
      const avatarUrl = state.active?.avatar_url || state.active?.avatar_thumbnail_url || "";
      avatar.innerHTML = activeIsAI
        ? "AI"
        : state.active
        ? avatarUrl
          ? `<img src="${escapeAttr(avatarUrl)}" alt="">`
          : escapeHtml(initials(state.active.title))
        : "P";
      avatar.className = `thread-avatar ${activeIsAI ? "pulse-ai-avatar" : ""} presence-${presenceClass(threadPresence)}`;
    }
    document.querySelectorAll("[data-thread-call-audio], [data-thread-call-video], [data-thread-more]").forEach((button) => {
      const disabled = !state.active || activeIsAI;
      button.disabled = disabled;
      button.classList.toggle("is-disabled", disabled);
    });
    syncPulseAIComposerControls();
    renderTypingPill();
    renderAIHooks();
    renderTrustBadges();
    renderSignalIntelligence();
    if (!state.active) {
      messages.innerHTML = `
        <div class="thread-welcome-state">
          <span class="messenger-orb large" aria-hidden="true"></span>
          <strong>Choose a chat to begin.</strong>
          <small>Your conversations and composer open instantly here.</small>
        </div>`;
      return;
    }
    if (state.threadHydrating && !state.messages.length) {
      messages.innerHTML = `<div class="message-skeletons" aria-label="Loading recent messages"><span></span><span></span><span></span></div>`;
      return;
    }
    if (!state.messages.length) {
      messages.innerHTML = activeIsAI
        ? `<div class="thread-welcome-state pulse-ai-welcome"><span class="messenger-orb large" aria-hidden="true"></span><strong>Pulse AI is ready.</strong><small>Ask about Messenger, Reels, Status, calls, music, alerts, notifications, privacy, or how to explore PulseSoc.</small>${pulseAIQuickPromptsHtml()}${pulseAIPrivacyPanelHtml()}</div>`
        : `<div class="empty-state">No messages here yet. Send the first one.</div>`;
      return;
    }
    const older = state.hasOlder ? `<button class="load-older" type="button" data-load-older>Load older messages</button>` : "";
    const typing = activeIsAI && state.pulseAITyping ? `<article class="message is-ai-assistant is-typing-message"><strong>Pulse AI</strong><p><span class="typing-dots"><i></i><i></i><i></i></span></p></article>` : "";
    messages.innerHTML = `${activeIsAI ? pulseAIPrivacyPanelHtml() : ""}${older}<div class="message-stack">${state.messages.map((item) => messageHtml(item)).join("")}${typing}</div>`;
    if (!state.preserveScroll) smoothScrollToBottom();
    state.preserveScroll = false;
    if (state.threadSearchQuery) window.requestAnimationFrame(applyThreadSearch);
    hydrateRenderedMessages();
  }

  function hydrateRenderedMessages() {
    window.requestAnimationFrame(() => {
      document.querySelectorAll("[data-voice-message]").forEach(bindVoiceAudio);
      if (window.PulseMediaRenderer) window.PulseMediaRenderer.hydrate(messages);
    });
  }

  function renderTrustBadges() {
    const target = el("[data-thread-trust]");
    if (!target) return;
    if (!state.active) {
      target.hidden = true;
      target.innerHTML = "";
      return;
    }
    const badges = [];
    if (state.active.verified || state.active.peer_verified || state.active.verified_badge) {
      badges.push(`<span class="trust-chip verified"><b aria-hidden="true">✓</b> Verified</span>`);
    }
    if (state.active.end_to_end_encrypted === true || state.active.encryption_enabled === true) {
      badges.push(`<span class="trust-chip secure"><b aria-hidden="true">●</b> End-to-end encrypted</span>`);
    }
    if (state.active.ai_protected === true || state.active.ai_safety_enabled === true) {
      badges.push(`<span class="trust-chip"><b aria-hidden="true">AI</b> AI protected</span>`);
    }
    target.hidden = !badges.length;
    target.innerHTML = badges.join("");
  }

  function renderAIHooks() {
    const summaryButton = el("[data-ai-summary]");
    const panel = el("[data-ai-panel]");
    const output = el("[data-ai-output]");
    const showHooks = Boolean(state.aiEnabled && state.active && !isPulseAIConversation(state.active));
    if (summaryButton) summaryButton.hidden = !showHooks;
    if (panel) panel.hidden = !showHooks;
    if (output) {
      output.textContent = state.aiOutput || (state.aiEnabled ? "AI is ready when this conversation has enough context." : "AI analysis is not enabled.");
    }
  }

  function aiResultText(data, fallback) {
    if (!data?.available) return data?.message || "AI analysis is not enabled.";
    const summary = data.summary || data.chat_summary || data.result || data.output;
    if (typeof summary === "string" && summary.trim()) return summary.trim();
    const replies = data.replies || data.smart_replies || data.suggestions;
    if (Array.isArray(replies) && replies.length) return replies.map((item) => `• ${String(item)}`).join("\n");
    return fallback;
  }

  async function runAIAction(kind) {
    if (!state.aiEnabled) return setStatus("AI assistance is not enabled for Messenger.", "error");
    if (!state.active) return setStatus("Open a conversation before using AI assistance.");
    if (state.aiBusy) return;
    state.aiBusy = true;
    state.aiOutput = kind === "smart-replies" ? "Preparing smart replies..." : "Summarizing conversation...";
    renderAIHooks();
    try {
      const endpoint = kind === "smart-replies" ? "smart-replies" : "summary";
      const data = await api(`/conversations/${state.active.conversation_id}/ai/${endpoint}`, { method: "POST", body: JSON.stringify({ limit: kind === "smart-replies" ? 12 : 30 }) }, `ai_${endpoint}`);
      state.aiOutput = aiResultText(data, kind === "smart-replies" ? "No smart replies are available yet." : "No summary is available yet.");
    } catch (err) {
      state.aiOutput = err?.message || "AI analysis could not be completed.";
    } finally {
      state.aiBusy = false;
      renderAIHooks();
    }
  }

  function smoothScrollToBottom() {
    if (!messages) return;
    messages.scrollTo({ top: messages.scrollHeight, behavior: state.messages.length > 5 ? "smooth" : "auto" });
  }

  function renderTypingPill() {
    const pill = el("[data-typing-pill]");
    if (!pill) return;
    const names = (state.typing || []).map((item) => item.display_name || "Someone").filter(Boolean);
    pill.textContent = typingSummary(names);
    pill.hidden = !names.length;
    pill.classList.toggle("is-visible", names.length > 0);
  }

  function renderMembers() {
    const target = el("[data-members]");
    const summary = el("[data-details-summary]");
    const typing = el("[data-typing-state]");
    if (summary) {
      summary.textContent = state.active
        ? `${state.active.title || "Active chat"} / ${state.active.conversation_type || "conversation"} / ${Number(state.active.member_count || state.members.length || 0)} members`
        : "Choose a chat to see members, safety, and rooms.";
    }
    if (typing) {
      const names = (state.typing || []).map((item) => item.display_name || "Someone").filter(Boolean);
      typing.textContent = typingSummary(names);
      typing.classList.toggle("is-visible", names.length > 0);
    }
    renderSignalIntelligence();
    if (!target) return;
    if (!state.active) {
      target.innerHTML = `<div class="empty-state">No active conversation selected.</div>`;
      return;
    }
    if (!state.members.length) {
      target.innerHTML = `<div class="empty-state">Members load with the selected thread.</div>`;
      return;
    }
    const presenceByUser = new Map((state.presence || []).map((item) => [Number(item.user_id || 0), item]));
    target.innerHTML = state.members.map((member) => {
      const presence = presenceByUser.get(Number(member.user_id || 0)) || {};
      const label = presenceLabel(presence);
      return `
      <article class="member-row">
        <span class="avatar presence-${presenceClass(presence)}">${initials(member.display_name || member.username)}</span>
        <span><strong>${escapeHtml(member.display_name || "PulseSoc member")}</strong><small>${escapeHtml(member.role || "member")} / ${escapeHtml(label)}</small></span>
      </article>
    `; }).join("");
  }

  function renderRooms() {
    const target = el("[data-room-list]");
    if (!target) return;
    const rooms = (state.rooms || []).filter((item) => item.conversation_type === "room");
    if (!rooms.length) {
      target.innerHTML = `<div class="empty-state">No public rooms yet. Create one to start the space.</div>`;
      return;
    }
    target.innerHTML = rooms.map((room) => `
      <button class="room-row" type="button" data-room-id="${Number(room.conversation_id || 0)}">
        <strong>${escapeHtml(room.title || "PulseSoc room")}</strong>
        <small>${escapeHtml(room.privacy || "public")} / ${Number(room.member_count || 0)} members</small>
      </button>
    `).join("");
  }

  function messageHtml(item) {
    const mine = Number(item.sender_user_id || 0) === currentUserId || item.is_mine;
    const aiMessage = Boolean(item.is_ai || Number(item.sender_user_id || 0) === PULSE_AI_USER_ID);
    const attachments = (item.attachments || []).map(attachmentHtml).join("");
    const shield = riskScan(item.body || "");
    const reactionLabels = { heart: "❤️", fire: "🔥", check: "✓" };
    const reactionKeys = { "❤️": "heart", "♥️": "heart", "🔥": "fire", "✓": "check", "✅": "check", heart: "heart", fire: "fire", check: "check" };
    const normalizeReaction = value => reactionKeys[String(value || "").trim()] || String(value || "").trim();
    const activeReaction = normalizeReaction(item.my_reaction || item.viewer_reaction || "");
    const reactions = ["heart", "fire", "check"].map((reaction) => `<button type="button" class="${activeReaction === reaction ? "active" : ""}" aria-pressed="${activeReaction === reaction ? "true" : "false"}" data-react="${reaction}" data-message-id="${item.id}">${reactionLabels[reaction] || reaction}</button>`).join("");
    const summaryEntries = Array.isArray(item.reactions)
      ? item.reactions.map((reaction) => [reaction.reaction_type, reaction.count])
      : Object.entries(item.reactions || {});
    const reactionSummary = summaryEntries.filter(([reaction, count]) => reaction && Number(count || 0) > 0).map(([reaction, count]) => `<span>${escapeHtml(reactionLabels[normalizeReaction(reaction)] || reaction)} ${Number(count || 0)}</span>`).join("");
    const reply = item.reply_preview ? `<button class="reply-preview" type="button" data-jump-message="${Number(item.reply_preview.id || 0)}">Replying to ${escapeHtml(item.reply_preview.sender?.display_name || "message")}: ${escapeHtml(item.reply_preview.body || item.reply_preview.message_type || "")}</button>` : "";
    return `
      <article class="message ${mine ? "is-mine" : ""} ${aiMessage ? "is-ai-assistant" : ""} ${item.pinned ? "is-pinned" : ""}" data-message-id="${item.id}">
        ${item.pinned ? `<span class="message-pin-badge">Pinned</span>` : ""}
        ${!mine ? `<strong>${escapeHtml(item.sender?.display_name || "PulseSoc member")}</strong>` : ""}
        ${reply}
        ${shield.risky || item?.pulse_shield?.flagged ? `<div class="pulse-shield-warning" data-shield-score="${Number(item?.pulse_shield?.score || shield.score || 0)}"><strong>Pulse Shield</strong><span>Suspicious link pattern detected. Review before opening.</span></div>` : ""}
        ${item.body ? `<p>${linkifiedMessageHtml(item.body)}</p>` : ""}
        ${attachments ? `<div class="attachments">${attachments}</div>` : ""}
        ${reactionSummary ? `<div class="reaction-summary">${reactionSummary}</div>` : ""}
        <small class="message-meta"><time>${escapeHtml(shortTime(item.created_at))}</time>${item.is_edited ? " / Edited" : ""}${mine ? ` <span class="delivery-state" data-state="${escapeAttr(messageDeliveryLabel(item).toLowerCase())}">${deliveryGlyph(messageDeliveryLabel(item))} ${escapeHtml(messageDeliveryLabel(item))}</span>` : ""}</small>
        ${aiMessage ? `<div class="pulse-ai-feedback" aria-label="Pulse AI feedback"><button type="button" data-pulse-ai-feedback="helpful" data-message-id="${item.id}">Helpful</button><button type="button" data-pulse-ai-feedback="not_helpful" data-message-id="${item.id}">Not helpful</button><button type="button" data-pulse-ai-feedback="wrong" data-message-id="${item.id}">Wrong</button><button type="button" data-pulse-ai-feedback="unsafe" data-message-id="${item.id}">Unsafe</button><button type="button" data-pulse-ai-feedback="outdated" data-message-id="${item.id}">Outdated</button></div>` : ""}
        ${item._failed ? `<button class="message-retry" type="button" data-retry-message="${item.id}">Retry send</button>` : ""}
        <button class="message-menu-trigger" type="button" data-message-actions="${item.id}" aria-label="Message actions">...</button>
        <div class="reaction-row" data-reaction-menu="${item.id}" hidden>${reactions}<button type="button" data-reply-message="${item.id}">Reply</button><button type="button" data-copy-message="${item.id}">Copy</button><button type="button" data-pin-message="${item.id}">${item.pinned ? "Unpin" : "Pin"}</button>${mine ? `<button type="button" data-edit-message="${item.id}">Edit</button><button type="button" data-delete-message="${item.id}" data-delete-for="everyone">Delete</button>` : `<button type="button" data-delete-message="${item.id}" data-delete-for="self">Remove</button>`}<button type="button" data-forward-message="${item.id}">Forward</button></div>
      </article>
    `;
  }

  function attachmentHtml(item) {
    const url = item.playback_url || item.url || item.cdn_url || item.thumbnail_url || "";
    if (!url) return "";
    if (!shouldAutoLoadAttachment(item)) {
      const label = (item.media_type || item.mime_type || "attachment").replace("/", " ");
      return `<a class="attachment-manual-load" href="${escapeAttr(url)}" target="_blank" rel="noopener">Open ${escapeHtml(label)}</a>`;
    }
    if (item.voice_note || (item.media_type || "").match(/audio|voice/)) return voiceAttachmentHtml(item, url);
    if (window.PulseMediaRenderer && (item.media_type || "").match(/image|gif|video|audio/)) {
      return window.PulseMediaRenderer.renderMedia({
        ...item,
        media_url: item.url || item.cdn_url || url,
        valid_url: item.cdn_url || item.url || url,
        playback_url: item.playback_url || url,
        poster_url: item.poster_url || item.thumbnail_url || "",
        media_type: item.media_type || "file",
        mime_type: item.mime_type || (String(url).includes(".m3u8") ? "application/vnd.apple.mpegurl" : ""),
        preload_priority: "high",
        title: item.filename || "PulseSoc attachment",
      }, { surface: "messages-v2", className: "comm-v2-media-attachment" });
    }
    if ((item.media_type || "").match(/image|gif/)) return `<img src="${escapeAttr(url)}" alt="Attached media">`;
    if ((item.media_type || "").match(/video/)) return `<video src="${escapeAttr(url)}" controls playsinline webkit-playsinline preload="metadata" poster="${escapeAttr(item.thumbnail_url || "")}"></video>`;
    return `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">Open attachment</a>`;
  }

  function shouldAutoLoadAttachment(item) {
    const media = (state.controlData?.settings?.media || {});
    const type = String(item.media_type || item.mime_type || "").toLowerCase();
    if ((type.includes("image") || type.includes("photo") || type.includes("gif")) && media.auto_download_photos === false) return false;
    if (type.includes("video") && media.auto_download_videos === false) return false;
    if ((type.includes("audio") || type.includes("voice")) && media.auto_download_voice === false) return false;
    return true;
  }

  function voiceAttachmentHtml(item, url) {
    const rawWaveform = Array.isArray(item.waveform) && item.waveform.length ? item.waveform : Array.from({ length: 36 }, (_, index) => 22 + ((index * 17) % 54));
    const waveform = rawWaveform.map((level) => {
      const value = Number(level) || 0;
      return value > 0 && value <= 1 ? value * 100 : value;
    });
    const bars = waveform.slice(0, 56).map((level) => `<i style="--level:${Math.max(8, Math.min(100, Number(level) || 18))}"></i>`).join("");
    const duration = Number(item.duration_seconds || item.duration || 0);
    return `
      <div class="voice-message" data-voice-message data-playing="false">
        <button class="voice-message-play" type="button" data-voice-play aria-label="Play voice note" title="Play voice note">
          <span data-voice-play-icon aria-hidden="true">▶</span>
        </button>
        <div class="voice-message-timeline">
          <div class="voice-waveform" data-voice-playback-waveform aria-hidden="true">${bars}</div>
          <input class="voice-message-seek" type="range" data-voice-progress min="0" max="100" value="0" step="0.1" aria-label="Voice message position">
          <small class="voice-message-time"><span data-voice-current>0:00</span><span data-voice-duration>${formatDuration(duration)}</span></small>
        </div>
        <select class="voice-message-speed" data-voice-speed aria-label="Playback speed">
          <option value="1">1x</option>
          <option value="1.5">1.5x</option>
          <option value="2">2x</option>
        </select>
        <audio data-voice-audio preload="metadata" src="${escapeAttr(url)}"></audio>
      </div>
    `;
  }

  async function loadConversations({ selectFirst = !isMobile() } = {}) {
    try {
      const data = await api("/conversations", {}, "conversations_list");
      state.conversations = (data.items || data.conversations || []).map(rememberConversation);
      ensurePulseAIConversation();
      sortConversations();
      const initialTarget = initialConversationId ? state.conversations.find((item) => Number(item.conversation_id) === initialConversationId || Number(item.id) === initialConversationId) : null;
      if (!state.active && initialTarget) {
        state.active = initialTarget;
        if (isMobile()) setMobileMode("thread");
      } else if (selectFirst && !state.active && state.conversations.length) {
        state.active = state.conversations.find((item) => !isPulseAIConversation(item)) || state.conversations[0];
      } else if (state.active) {
        state.active = rememberConversation(state.conversations.find((c) => Number(c.conversation_id) === Number(state.active.conversation_id)) || state.active);
      }
      if (state.active) {
        restoreDraft(state.active.conversation_id);
        applyControlAppearanceSettings(readCachedControlSettings(state.active.conversation_id), state.active.conversation_id);
        hydrateConversationVisualSettings(state.active.conversation_id).catch(() => {});
      }
      renderConversations();
      setStatus(state.conversations.length ? "" : "No conversations yet.");
      if (state.active && !state.initialThreadLoaded && (!isMobile() || Number(state.active.conversation_id) === initialConversationId)) {
        state.initialThreadLoaded = true;
        window.requestAnimationFrame(() => loadMessages(state.active.conversation_id).catch((err) => setStatus(err.message, "error")));
      }
    } catch (err) {
      renderConversations();
      setStatus(err.message, err.data?.status === "disabled" ? "disabled" : "error");
    }
  }

  async function loadMessages(conversationId, { beforeId = 0, appendOlder = false } = {}) {
    if (isPulseAIId(conversationId)) return loadPulseAIConversation({ appendOlder });
    if (appendOlder && state.loadingThread) return;
    const targetConversationId = Number(conversationId || 0);
    if (!targetConversationId) return;
    const requestToken = ++state.activeRequestToken;
    state.loadingThread = true;
    if (!appendOlder) {
      applyControlAppearanceSettings(readCachedControlSettings(targetConversationId), targetConversationId);
      hydrateConversationVisualSettings(targetConversationId).catch(() => {});
      const cached = state.messageCache.get(targetConversationId);
      state.threadHydrating = !cached?.length;
      state.messages = cached ? cached.map((item) => ({ ...item })) : [];
      renderMessages();
      renderMembers();
    }
    try {
      const query = `limit=${INITIAL_MESSAGE_LIMIT}${beforeId ? `&before_id=${beforeId}` : ""}`;
      const data = await api(`/conversations/${targetConversationId}/messages?${query}`, {}, "selected_thread_messages");
      const nextMessages = data.messages || [];
      const nextConversation = rememberConversation(data.conversation || state.conversationCache.get(targetConversationId) || state.active);
      if (appendOlder) {
        const cached = state.messageCache.get(targetConversationId) || [];
        const seen = new Set(nextMessages.map((message) => Number(message.id)));
        state.messageCache.set(targetConversationId, [...nextMessages, ...cached.filter((message) => !seen.has(Number(message.id)))]);
      } else {
        state.messageCache.set(targetConversationId, nextMessages.map((item) => ({ ...item })));
      }
      if (!state.active || Number(state.active.conversation_id) !== targetConversationId) return;
      state.active = nextConversation;
      if (appendOlder) {
        state.preserveScroll = true;
        const seen = new Set(nextMessages.map((m) => Number(m.id)));
        state.messages = [...nextMessages, ...state.messages.filter((m) => !seen.has(Number(m.id)))];
      } else {
        state.messages = nextMessages;
      }
      state.hasOlder = Boolean(data.has_older);
      state.oldestMessageId = Number(data.oldest_message_id || state.messages[0]?.id || 0);
      state.members = data.members || state.members;
      state.typing = data.typing || [];
      state.threadHydrating = false;
      await loadPresence(state.active?.conversation_id || targetConversationId);
      if (!state.active || Number(state.active.conversation_id) !== targetConversationId) return;
      renderConversations();
      renderMessages();
      renderMembers();
      if (window.PulseMediaRenderer) window.PulseMediaRenderer.hydrate(messages);
      document.querySelectorAll("[data-voice-message]").forEach(bindVoiceAudio);
      if (!appendOlder) connectRealtimeStream();
    } finally {
      if (requestToken === state.activeRequestToken) {
        state.loadingThread = false;
        state.threadHydrating = false;
        renderMessages();
      }
    }
  }

  async function loadPulseAIConversation({ appendOlder = false } = {}) {
    if (appendOlder) return;
    const requestToken = ++state.activeRequestToken;
    state.loadingThread = true;
    state.threadHydrating = !(state.messageCache.get(PULSE_AI_CONVERSATION_ID) || []).length;
    state.messages = (state.messageCache.get(PULSE_AI_CONVERSATION_ID) || []).map((item) => ({ ...item }));
    state.members = [];
    state.typing = [];
    state.hasOlder = false;
    renderMessages();
    try {
      const data = await pulseAiApi("/conversation?limit=80", {}, "pulse_ai_conversation");
      state.pulseAISettings = data.settings || state.pulseAISettings;
      const conversation = ensurePulseAIConversation(data.conversation || {});
      state.active = conversation;
      state.messages = (data.messages || []).map((item) => ({ ...item, conversation_id: PULSE_AI_CONVERSATION_ID }));
      state.messageCache.set(PULSE_AI_CONVERSATION_ID, state.messages.map((item) => ({ ...item })));
      state.threadHydrating = false;
      renderConversations();
      renderMessages();
      renderMembers();
    } catch (err) {
      setStatus(err?.message || "Pulse AI could not load.", "error");
    } finally {
      if (requestToken === state.activeRequestToken) {
        state.loadingThread = false;
        state.threadHydrating = false;
        renderMessages();
      }
    }
  }

  async function loadOlderMessages() {
    if (!state.active || !state.oldestMessageId || !state.hasOlder) return;
    const previousHeight = messages?.scrollHeight || 0;
    await loadMessages(state.active.conversation_id, { beforeId: state.oldestMessageId, appendOlder: true });
    if (messages) messages.scrollTop = Math.max(0, messages.scrollHeight - previousHeight);
  }

  function updateChatBadges(count) {
    if (!(count || count === 0)) return;
    document.querySelectorAll("[data-chat-unread]").forEach((node) => {
      node.textContent = Number(count || 0);
      node.hidden = Number(count || 0) <= 0;
    });
    window.CoinPilotNotifications?.pollNotifications?.({ refreshList: true });
  }

  function updateNotificationBadges(payload) {
    const count = typeof payload === "number" ? payload : payload?.chat_unread_count ?? payload?.unread_count;
    updateChatBadges(count);
  }

  function normalizeRealtimeType(type) {
    const value = String(type || "").toLowerCase();
    const aliases = {
      pulse_message_created: "message_created",
      pulse_message_sent: "message_created",
      group_message_created: "message_created",
      room_message_created: "message_created",
      message_notification: "message_created",
      pulse_message_seen: "message_read",
      pulse_typing_started: "typing_started",
      typing_start: "typing_started",
      pulse_typing_stopped: "typing_stopped",
      typing_stop: "typing_stopped",
      pulse_typing: "typing",
      pulse_notification_created: "notification_created",
      conversation_updated: "conversation_updated",
      presence_updated: "presence_updated",
      unread_count_updated: "unread_count_updated",
    };
    return aliases[value] || value || "message_created";
  }

  function applyRealtimeTyping(payload, isTyping) {
    const conversationId = Number(payload?.conversation_id || 0);
    if (!conversationId || !state.active || Number(state.active.conversation_id) !== conversationId) return;
    const userId = Number(payload.user_id || payload.sender_id || 0);
    if (!userId || userId === currentUserId) return;
    const displayName = payload.display_name || payload.name || "Someone";
    state.typing = (state.typing || []).filter((item) => Number(item.user_id || 0) !== userId);
    if (isTyping) state.typing = [...state.typing, { user_id: userId, display_name: displayName, is_typing: true }];
    renderTypingPill();
    renderMembers();
    renderConversations();
  }

  function applyRealtimePresence(payload) {
    const userId = Number(payload?.user_id || 0);
    if (!userId) return;
    const nextPresence = {
      user_id: userId,
      status: payload.status || "offline",
      active_now: payload.status === "online",
      last_seen_at: payload.last_seen_at || payload.updated_at || new Date().toISOString(),
      presence_visible: payload.presence_visible !== false,
    };
    const existing = (state.presence || []).filter((item) => Number(item.user_id || 0) !== userId);
    state.presence = [...existing, nextPresence];
    renderActiveRail();
    renderConversations();
    renderMessages();
    renderMembers();
  }

  function appendRealtimeMessage(message) {
    if (!message?.id || !state.active || Number(message.conversation_id) !== Number(state.active.conversation_id)) return false;
    const clientId = message.client_message_id || message.client_temp_id || "";
    const existingIndex = state.messages.findIndex((item) => Number(item.id) === Number(message.id) || (clientId && (item.client_message_id === clientId || item.client_temp_id === clientId)));
    if (existingIndex >= 0) {
      state.messages[existingIndex] = { ...state.messages[existingIndex], ...message, _pending: false, _failed: false };
      state.messageCache.set(Number(state.active.conversation_id), state.messages.map((item) => ({ ...item })));
      renderMessages();
      document.querySelectorAll("[data-voice-message]").forEach(bindVoiceAudio);
      if (window.PulseMediaRenderer) window.PulseMediaRenderer.hydrate(messages);
      return true;
    }
    state.messages = [...state.messages, message];
    state.messageCache.set(Number(state.active.conversation_id), state.messages.map((item) => ({ ...item })));
    renderMessages();
    document.querySelectorAll("[data-voice-message]").forEach(bindVoiceAudio);
    if (window.PulseMediaRenderer) window.PulseMediaRenderer.hydrate(messages);
    api(`/conversations/${state.active.conversation_id}/read`, { method: "POST", body: JSON.stringify({}) }, "read_receipt").catch(() => {});
    return true;
  }

  function connectRealtimeStream() {
    if (document.documentElement.dataset.pulseSse !== "enabled") return;
    if (!window.PulseRealtime) return;
    const params = new URLSearchParams({
      after_id: String(state.realtimeAfterId || 0),
      limit: "80",
    });
    if (state.active?.conversation_id) params.set("conversation_id", String(state.active.conversation_id));
    const url = `${API}/realtime/stream?${params.toString()}`;
    try { window.PulseRealtime.disconnect(); } catch (_) {}
    window.PulseRealtime.connect(url);
  }

  function mergeRealtimeConversation(payload) {
    const message = payload?.message || {};
    const conversationId = Number(payload?.conversation_id || message.conversation_id || 0);
    const incomingConversation = payload?.conversation || state.conversationCache.get(conversationId) || {};
    const activeConversation = Number(state.active?.conversation_id || 0) === conversationId;
    const fallbackPreview = message.body || (message.message_type === "voice" ? "Sent a voice note." : message.message_type ? "Sent an attachment." : "");
    const next = upsertConversation({
      ...incomingConversation,
      conversation_id: conversationId,
      id: conversationId,
      last_message_id: Number(message.id || payload?.message_id || incomingConversation.last_message_id || 0),
      last_message_at: message.created_at || incomingConversation.last_message_at || new Date().toISOString(),
      last_activity_at: message.created_at || incomingConversation.last_activity_at || new Date().toISOString(),
      last_message_preview: fallbackPreview || incomingConversation.last_message_preview || incomingConversation.description || "",
      unread_count: activeConversation ? 0 : Number(incomingConversation.unread_count || 0),
    });
    if (activeConversation && next) state.active = next;
    renderConversations();
  }

  function broadcastCommEvent(payload) {
    const message = { type: "comm-v2-live-event", payload, at: Date.now() };
    try { state.tabChannel?.postMessage(message); } catch (_) {}
    try { localStorage.setItem("pulseCommV2LiveEvent", JSON.stringify(message)); } catch (_) {}
  }

  function handleRealtimeEvent(envelope, options = {}) {
    const payload = envelope?.payload || envelope || {};
    let type = normalizeRealtimeType(envelope?.event_type || envelope?.type || payload.event_type || "message_notification");
    if (type === "typing") type = payload.typing === false ? "typing_stopped" : "typing_started";
    const conversationId = Number(payload.conversation_id || payload.message?.conversation_id || 0);
    if (!conversationId && !["presence_updated", "notification_created", "unread_count_updated"].includes(type)) return;
    if (envelope?.id) state.realtimeAfterId = Math.max(state.realtimeAfterId, Number(envelope.id) || 0);
    if (type === "presence_updated") {
      applyRealtimePresence(payload);
      return;
    }
    if (type === "typing_started" || type === "typing_stopped") {
      applyRealtimeTyping(payload, type === "typing_started");
      if (!options.fromBroadcast) broadcastCommEvent(envelope);
      return;
    }
    if (type === "unread_count_updated") {
      updateNotificationBadges(payload);
      return;
    }
    mergeRealtimeConversation(payload);
    if (type === "message_created" || type === "notification_created") {
      appendRealtimeMessage(payload.message || payload.data);
      updateNotificationBadges(payload);
      if (!options.fromBroadcast) broadcastCommEvent(envelope);
    } else if (type === "message_read") {
      if (state.active && Number(state.active.conversation_id) === conversationId) {
        state.active.unread_count = 0;
        renderConversations();
      }
      if (!options.fromBroadcast) broadcastCommEvent(envelope);
    }
  }

  async function pollRealtime() {
    if (state.realtimePolling) return;
    state.realtimePolling = true;
    try {
      const params = new URLSearchParams({
        after_id: String(state.realtimeAfterId || 0),
        limit: "80",
      });
      if (state.active?.conversation_id) params.set("conversation_id", String(state.active.conversation_id));
      const data = await api(`/realtime?${params.toString()}`, {}, "realtime_delivery");
      state.realtimeAfterId = Math.max(state.realtimeAfterId, Number(data.latest_event_id || 0));
      state.realtimeConnected = /command_center|redis/i.test(String(data.transport || ""));
      (data.events || []).forEach(handleRealtimeEvent);
      updateNotificationBadges(data);
      renderRealtimeStatus();
    } catch (_) {
      state.realtimeConnected = false;
      renderRealtimeStatus();
    } finally {
      state.realtimePolling = false;
    }
  }

  function realtimePollDelay() {
    if (document.hidden) return 60000;
    return state.realtimeConnected ? 15000 : 3000;
  }

  function scheduleRealtimePoll(delay = realtimePollDelay()) {
    window.clearTimeout(state.realtimeTimer);
    state.realtimeTimer = window.setTimeout(async () => {
      if (!document.hidden) await pollRealtime();
      scheduleRealtimePoll();
    }, delay);
  }

  function bindRealtimeDelivery() {
    if (state.realtimeBound) return;
    state.realtimeBound = true;
    if (window.PulseRealtime) {
      window.PulseRealtime.on("connected", () => {
        state.realtimeConnected = true;
        renderRealtimeStatus();
        scheduleRealtimePoll();
      });
      window.PulseRealtime.on("reconnecting", () => {
        state.realtimeConnected = false;
        renderRealtimeStatus();
        pollRealtime();
        scheduleRealtimePoll(3000);
      });
      window.PulseRealtime.on("message_notification", handleRealtimeEvent);
      window.PulseRealtime.on("notification_created", handleRealtimeEvent);
      window.PulseRealtime.on("message_created", handleRealtimeEvent);
      window.PulseRealtime.on("pulse_message_created", handleRealtimeEvent);
      window.PulseRealtime.on("pulse_message_sent", handleRealtimeEvent);
      window.PulseRealtime.on("group_message_created", handleRealtimeEvent);
      window.PulseRealtime.on("room_message_created", handleRealtimeEvent);
      window.PulseRealtime.on("message_read", handleRealtimeEvent);
      window.PulseRealtime.on("pulse_message_seen", handleRealtimeEvent);
      window.PulseRealtime.on("typing_started", handleRealtimeEvent);
      window.PulseRealtime.on("typing_stopped", handleRealtimeEvent);
      window.PulseRealtime.on("pulse_typing_started", handleRealtimeEvent);
      window.PulseRealtime.on("pulse_typing_stopped", handleRealtimeEvent);
      window.PulseRealtime.on("pulse_typing", handleRealtimeEvent);
      window.PulseRealtime.on("unread_count_updated", handleRealtimeEvent);
      window.PulseRealtime.on("presence_updated", handleRealtimeEvent);
      connectRealtimeStream();
    }
    state.tabChannel?.addEventListener("message", (event) => {
      if (event.data?.type === "comm-v2-live-event") handleRealtimeEvent(event.data.payload, { fromBroadcast: true });
    });
    window.addEventListener("storage", (event) => {
      if (event.key !== "pulseCommV2LiveEvent" || !event.newValue) return;
      try {
        const data = JSON.parse(event.newValue);
        if (data?.payload) handleRealtimeEvent(data.payload, { fromBroadcast: true });
      } catch (_) {}
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && state.voice.state === "recording_voice") pauseVoiceRecording();
      if (!document.hidden) pollRealtime();
      scheduleRealtimePoll();
    });
    pollRealtime();
    scheduleRealtimePoll();
    renderRealtimeStatus();
  }

  async function loadRooms() {
    try {
      const data = await api("/rooms", {}, "rooms_list");
      state.rooms = (data.items || data.conversations || []).map(rememberConversation);
      renderRooms();
    } catch (err) {
      const target = el("[data-room-list]");
      if (target) target.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
    }
  }

  async function uploadSelectedFile(file, metadata = {}) {
    if (!file) return 0;
    if (!state.active?.conversation_id) throw new Error("Choose a conversation before uploading media.");
    const mimeType = mediaFoundationMimeType(file);
    const mediaType = mediaFoundationType(file);
    const init = await mediaApi("/init", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: Number(state.active.conversation_id || 0),
        media_type: mediaType,
        filename: file.name || `pulse-${mediaType}-${Date.now()}`,
        mime_type: mimeType,
        size_bytes: Number(file.size || 0),
      }),
    }, "media_init");
    const attachmentId = Number(init.attachment_id || 0);
    if (!attachmentId) throw new Error("Media upload did not return an attachment id.");
    const uploadFile = file.type === mimeType ? file : new File([file], file.name || `pulse-${mediaType}-${Date.now()}`, { type: mimeType });
    const fd = new FormData();
    fd.append("attachment_id", String(attachmentId));
    fd.append("file", uploadFile);
    Object.entries(metadata || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null) fd.append(key, typeof value === "string" ? value : JSON.stringify(value));
    });
    await mediaApi("/upload", { method: "POST", body: fd }, "media_upload");
    await mediaApi("/complete", {
      method: "POST",
      body: JSON.stringify({
        attachment_id: attachmentId,
        duration_ms: metadata.duration_ms || "",
        width: metadata.width || "",
        height: metadata.height || "",
        waveform_json: metadata.waveform_json || "",
      }),
    }, "media_complete");
    return attachmentId;
  }

  function attachmentKind(file) {
    const type = (file?.type || "").toLowerCase();
    const name = (file?.name || "").toLowerCase();
    if (type.startsWith("image/")) return "image";
    if (type.startsWith("video/")) return "video";
    if (type.startsWith("audio/")) return "audio";
    if (/\.(jpg|jpeg|png|gif|webp|avif)$/i.test(name)) return "image";
    if (/\.(mp4|mov|m4v|webm)$/i.test(name)) return "video";
    if (/\.(mp3|m4a|wav|ogg|webm)$/i.test(name)) return "audio";
    return "file";
  }

  function mediaFoundationMimeType(file) {
    const provided = String(file?.type || "").split(";", 1)[0].toLowerCase();
    if (MEDIA_FOUNDATION_MIMES.has(provided)) return provided;
    const ext = String(file?.name || "").split(".").pop()?.toLowerCase() || "";
    return MEDIA_FOUNDATION_MIME_BY_EXT[ext] || provided || "application/octet-stream";
  }

  function mediaFoundationType(file) {
    const kind = attachmentKind(file);
    if (kind === "image") return "photo";
    if (kind === "video") return "video";
    if (kind === "audio") return "voice";
    return "file";
  }

  function validateAttachment(file) {
    if (!file) return "Choose a file first.";
    const name = file.name || "attachment";
    const kind = attachmentKind(file);
    const blocked = /\.(exe|dll|bat|cmd|com|scr|js|jar|msi|ps1|sh)$/i;
    if (blocked.test(name)) return "That file type is blocked for safety.";
    const mimeType = mediaFoundationMimeType(file);
    if (!MEDIA_FOUNDATION_MIMES.has(mimeType)) return `${name} is not supported for Messenger media yet.`;
    const limit = state.uploadLimits[kind] || state.uploadLimits.file;
    if (file.size > limit) return `${name} is too large for Messenger. Limit: ${formatBytes(limit)}.`;
    if (state.attachmentQueue.length >= state.maxAttachments) return `You can send up to ${state.maxAttachments} attachments at once.`;
    return "";
  }

  function addAttachmentFiles(files) {
    const incoming = Array.from(files || []);
    if (!incoming.length) return;
    const next = [];
    for (const file of incoming) {
      const error = validateAttachment(file);
      if (error) {
        setStatus(error, "error");
        continue;
      }
      next.push({
        id: `att-${Date.now()}-${++state.attachmentSeq}`,
        file,
        kind: attachmentKind(file),
        status: "queued",
        progress: 0,
        attachmentId: 0,
        error: "",
        previewUrl: file.type?.startsWith("image/") || file.type?.startsWith("video/") ? URL.createObjectURL(file) : "",
      });
    }
    state.attachmentQueue = [...state.attachmentQueue, ...next].slice(0, state.maxAttachments);
    renderAttachmentPreview();
    syncComposerState();
    if (next.length) setStatus(`${next.length} attachment${next.length === 1 ? "" : "s"} ready. You can send without typing a message.`);
  }

  function removeAttachment(id) {
    const item = state.attachmentQueue.find((entry) => entry.id === id);
    if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
    state.attachmentQueue = state.attachmentQueue.filter((entry) => entry.id !== id);
    renderAttachmentPreview();
    syncComposerState();
    setStatus(state.attachmentQueue.length ? "Attachment removed." : "No attachments selected.");
  }

  function moveAttachment(id, direction) {
    const index = state.attachmentQueue.findIndex((entry) => entry.id === id);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= state.attachmentQueue.length) return;
    const copy = [...state.attachmentQueue];
    [copy[index], copy[nextIndex]] = [copy[nextIndex], copy[index]];
    state.attachmentQueue = copy;
    renderAttachmentPreview();
    syncComposerState();
  }

  function renderAttachmentPreview() {
    const rail = el("[data-attachment-preview]");
    if (!rail) return;
    rail.hidden = !state.attachmentQueue.length;
    rail.innerHTML = state.attachmentQueue.map((item, index) => {
      const media = item.kind === "image"
        ? `<img src="${escapeAttr(item.previewUrl)}" alt="">`
        : item.kind === "video"
          ? `<video src="${escapeAttr(item.previewUrl)}" playsinline preload="metadata"></video>`
          : item.kind === "audio"
            ? `<div class="attachment-file-icon">♪</div>`
            : `<div class="attachment-file-icon">FILE</div>`;
      const progress = item.status === "uploading" ? `<progress max="100" value="${Number(item.progress || 0)}"></progress>` : "";
      const stateLabel = item.status === "queued" ? "Ready to send" : item.status === "uploaded" ? "Uploaded" : item.status === "failed" ? "Needs retry" : "Uploading";
      return `
        <article class="attachment-preview-card" data-attachment-id="${escapeAttr(item.id)}" data-state="${escapeAttr(item.status)}">
          ${media}
          <div>
            <strong>${escapeHtml(item.file.name || "Attachment")}</strong>
            <small>${escapeHtml(item.error || `${stateLabel} / ${formatBytes(item.file.size || 0)}`)}</small>
            ${progress}
          </div>
          <div class="attachment-preview-actions">
            <button type="button" data-attachment-move="${escapeAttr(item.id)}" data-direction="-1" ${index === 0 ? "disabled" : ""}>↑</button>
            <button type="button" data-attachment-move="${escapeAttr(item.id)}" data-direction="1" ${index === state.attachmentQueue.length - 1 ? "disabled" : ""}>↓</button>
            ${item.status === "failed" ? `<button type="button" data-attachment-retry="${escapeAttr(item.id)}">Retry</button>` : ""}
            <button type="button" data-attachment-remove="${escapeAttr(item.id)}">${item.status === "uploading" ? "Cancel" : "Remove"}</button>
          </div>
        </article>
      `;
    }).join("");
    syncComposerState();
  }

  async function uploadAttachmentItem(item) {
    if (!item || item.status === "uploaded") return item?.attachmentId || 0;
    item.status = "uploading";
    item.progress = 10;
    item.error = "";
    renderAttachmentPreview();
    try {
      const attachmentId = await uploadSelectedFile(item.file, { attachment_kind: item.kind });
      item.status = "uploaded";
      item.progress = 100;
      item.attachmentId = attachmentId;
      renderAttachmentPreview();
      return attachmentId;
    } catch (error) {
      item.status = "failed";
      item.error = error.message || "Upload failed.";
      renderAttachmentPreview();
      throw error;
    }
  }

  async function uploadAttachmentQueue() {
    const ids = [];
    for (const item of state.attachmentQueue) {
      if (item.status === "cancelled") continue;
      ids.push(await uploadAttachmentItem(item));
    }
    return ids.filter(Boolean);
  }

  function clearAttachmentQueue() {
    state.attachmentQueue.forEach((item) => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    state.attachmentQueue = [];
    renderAttachmentPreview();
    ["[data-file]", "[data-camera-file]", "[data-photo-file]", "[data-video-file]", "[data-generic-file]"].forEach((selector) => {
      const input = el(selector);
      if (input) input.value = "";
    });
    syncComposerState();
  }

  function toggleAttachmentSheet(force) {
    const sheet = el("[data-attachment-sheet]");
    state.attachmentSheetOpen = typeof force === "boolean" ? force : !state.attachmentSheetOpen;
    if (state.attachmentSheetOpen) toggleEmojiPanel(false);
    if (sheet) {
      sheet.hidden = !state.attachmentSheetOpen;
      sheet.classList.toggle("is-open", state.attachmentSheetOpen);
    }
    syncComposerState();
  }

  function toggleEmojiPanel(force) {
    const panel = el("[data-emoji-panel]");
    state.emojiOpen = typeof force === "boolean" ? force : !state.emojiOpen;
    if (state.emojiOpen) {
      state.attachmentSheetOpen = false;
      const attachmentSheet = el("[data-attachment-sheet]");
      if (attachmentSheet) {
        attachmentSheet.hidden = true;
        attachmentSheet.classList.remove("is-open");
      }
    }
    if (panel) {
      panel.hidden = !state.emojiOpen;
      panel.classList.toggle("is-open", state.emojiOpen);
    }
    syncComposerState();
  }

  function insertEmoji(value) {
    const input = el("[data-message-input]");
    if (!input || !value) return;
    const start = Number.isFinite(input.selectionStart) ? input.selectionStart : input.value.length;
    const end = Number.isFinite(input.selectionEnd) ? input.selectionEnd : input.value.length;
    input.setRangeText(value, start, end, "end");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    toggleEmojiPanel(false);
    input.focus();
  }

  function toggleThreadSearch(force) {
    const panel = el("[data-thread-search-panel]");
    const input = el("[data-thread-search-input]");
    const open = typeof force === "boolean" ? force : Boolean(panel?.hidden);
    if (panel) panel.hidden = !open;
    if (!open) {
      state.threadSearchQuery = "";
      if (input) input.value = "";
      document.querySelectorAll("[data-message-id]").forEach((node) => {
        node.classList.remove("is-search-match", "is-search-muted");
      });
      const count = el("[data-thread-search-count]");
      if (count) count.textContent = "";
      return;
    }
    window.setTimeout(() => input?.focus(), 20);
    applyThreadSearch();
  }

  function applyThreadSearch() {
    const input = el("[data-thread-search-input]");
    const count = el("[data-thread-search-count]");
    const query = String(input?.value || "").trim().toLowerCase();
    state.threadSearchQuery = query;
    const nodes = [...document.querySelectorAll("[data-message-id]")];
    let matches = 0;
    let firstMatch = null;
    nodes.forEach((node) => {
      const matched = Boolean(query) && node.textContent.toLowerCase().includes(query);
      node.classList.toggle("is-search-match", matched);
      node.classList.toggle("is-search-muted", Boolean(query) && !matched);
      if (matched) {
        matches += 1;
        if (!firstMatch) firstMatch = node;
      }
    });
    if (count) count.textContent = query ? `${matches} result${matches === 1 ? "" : "s"}` : "";
    firstMatch?.scrollIntoView({
      block: "center",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    });
  }

  function openAttachmentOption(option) {
    toggleAttachmentSheet(false);
    if (option === "voice") return startVoiceRecording();
    if (option === "camera") return el("[data-camera-file]")?.click();
    if (option === "photo") return el("[data-photo-file]")?.click();
    if (option === "video") return el("[data-video-file]")?.click();
    return el("[data-generic-file]")?.click();
  }

  function recorderMimeType() {
    const candidates = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"];
    return candidates.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || "";
  }

  function formatDuration(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const mins = Math.floor(value / 60);
    const secs = Math.floor(value % 60);
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }

  function voiceStateLabel(voiceState = state.voice.state) {
    if (voiceState === "recording_voice") return "Recording";
    if (voiceState === "recording_locked") return "Recording locked";
    if (voiceState === "recording_paused") return "Paused";
    if (voiceState === "voice_preview") return "Voice preview";
    if (voiceState === "voice_uploading") return "Uploading voice";
    return "Ready to record";
  }

  function updateComposerVoiceInline() {
    const voiceState = state.voice.state;
    const inline = el("[data-composer-voice-inline]");
    const input = el("[data-message-input]");
    const label = el("[data-composer-voice-label]");
    const timer = el("[data-composer-voice-timer]");
    const voiceActive = ["recording_voice", "recording_locked", "recording_paused", "voice_preview", "voice_uploading"].includes(voiceState);
    if (inline) {
      inline.hidden = !voiceActive;
      inline.dataset.state = voiceState;
    }
    if (input) {
      input.hidden = voiceActive;
      input.disabled = voiceState === "voice_uploading";
    }
    if (label) label.textContent = voiceStateLabel(voiceState);
    if (timer) timer.textContent = formatDuration((state.voice.elapsedMs || 0) / 1000);
  }

  function updateVoicePanel() {
    const panel = el("[data-voice-panel]");
    const stateLabel = el("[data-voice-state]");
    const timer = el("[data-voice-timer]");
    const pause = el("[data-voice-pause]");
    const resume = el("[data-voice-resume]");
    const stop = el("[data-voice-stop]");
    const preview = el("[data-voice-preview]");
    const wave = el("[data-voice-waveform]");
    const voiceState = state.voice.state;
    if (!panel) return;
    panel.hidden = voiceState === "idle";
    panel.dataset.state = voiceState;
    el("[data-voice-start]")?.classList.toggle("is-recording", ["recording_voice", "recording_locked", "recording_paused"].includes(voiceState));
    if (stateLabel) stateLabel.textContent = voiceStateLabel(voiceState);
    if (timer) timer.textContent = formatDuration((state.voice.elapsedMs || 0) / 1000);
    if (pause) pause.hidden = voiceState !== "recording_voice";
    if (resume) resume.hidden = voiceState !== "recording_paused";
    if (stop) stop.hidden = !["recording_voice", "recording_paused"].includes(voiceState);
    if (preview) {
      preview.hidden = voiceState !== "voice_preview";
      if (state.voice.url && preview.src !== state.voice.url) preview.src = state.voice.url;
    }
    if (wave) wave.innerHTML = (state.voice.waveform.length ? state.voice.waveform : Array.from({ length: 32 }, () => 12)).slice(-56).map((level) => `<i style="--level:${Math.max(8, Math.min(100, Number(level) || 12))}"></i>`).join("");
    syncComposerState();
  }

  function startVoiceTimer() {
    window.clearTimeout(state.voice.timer);
    state.voice.startedAt = Date.now();
    const tick = () => {
      if (state.voice.state === "recording_voice") {
        state.voice.elapsedMs += Date.now() - state.voice.startedAt;
        state.voice.startedAt = Date.now();
        updateVoicePanel();
        state.voice.timer = window.setTimeout(tick, 300);
      }
    };
    state.voice.timer = window.setTimeout(tick, 300);
  }

  async function startVoiceRecording() {
    if (!state.active) return setStatus("Choose a conversation before recording.", "error");
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      return setStatus("Voice recording is not supported in this browser.", "error");
    }
    toggleAttachmentSheet(false);
    toggleEmojiPanel(false);
    discardVoiceRecording({ silent: true });
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const mimeType = recorderMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      state.voice.stream = stream;
      state.voice.recorder = recorder;
      state.voice.chunks = [];
      state.voice.waveform = [];
      state.voice.elapsedMs = 0;
      state.voice.state = "recording_voice";
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) state.voice.chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        if (recorder._discarded) return;
        const resolve = state.voice.stopResolve;
        state.voice.stopResolve = null;
        const ready = finalizeVoiceRecording(mimeType || recorder.mimeType || "audio/webm");
        if (resolve) resolve(ready);
      });
      recorder.start(500);
      startVoiceAnalyser(stream);
      startVoiceTimer();
      updateVoicePanel();
      setStatus("Recording voice note...");
    } catch (error) {
      discardVoiceRecording({ silent: true });
      setStatus(error?.name === "NotAllowedError" ? "Microphone permission was denied." : "Microphone could not start. Try again.", "error");
    }
  }

  function startVoiceAnalyser(stream) {
    window.clearTimeout(state.voice.analyserTimer);
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const context = new AudioCtx();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      state.voice.audioContext = context;
      analyser.fftSize = 128;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const sample = () => {
        if (state.voice.state !== "recording_voice") return;
        analyser.getByteTimeDomainData(data);
        const peak = data.reduce((max, value) => Math.max(max, Math.abs(value - 128)), 0);
        state.voice.waveform.push(Math.max(8, Math.min(100, Math.round((peak / 128) * 100))));
        if (state.voice.waveform.length > 80) state.voice.waveform.shift();
        updateVoicePanel();
        state.voice.analyserTimer = window.setTimeout(sample, 180);
      };
      state.voice.analyserTimer = window.setTimeout(sample, 180);
    } catch (_) {}
  }

  function pauseVoiceRecording() {
    if (state.voice.recorder?.state === "recording") {
      state.voice.elapsedMs += Date.now() - state.voice.startedAt;
      state.voice.recorder.pause();
      state.voice.state = "recording_paused";
      updateVoicePanel();
      setStatus("Voice recording paused.");
    }
  }

  function resumeVoiceRecording() {
    if (state.voice.recorder?.state === "paused") {
      state.voice.startedAt = Date.now();
      state.voice.recorder.resume();
      state.voice.state = "recording_voice";
      updateVoicePanel();
      setStatus("Recording voice note...");
    }
  }

  function stopVoiceRecording() {
    if (!state.voice.recorder || !["recording", "paused"].includes(state.voice.recorder.state)) {
      return Promise.resolve(state.voice.state === "voice_preview" && !!state.voice.blob);
    }
    if (state.voice.recorder.state === "recording") state.voice.elapsedMs += Date.now() - state.voice.startedAt;
    const stopped = new Promise((resolve) => {
      state.voice.stopResolve = resolve;
    });
    state.voice.recorder.stop();
    window.clearTimeout(state.voice.timer);
    window.clearTimeout(state.voice.analyserTimer);
    state.voice.stream?.getTracks?.().forEach((track) => track.stop());
    const closePromise = state.voice.audioContext?.close?.();
    closePromise?.catch?.(() => {});
    return stopped;
  }

  function finalizeVoiceRecording(mimeType) {
    const blob = new Blob(state.voice.chunks, { type: mimeType || "audio/webm" });
    if (blob.size < 64) {
      discardVoiceRecording({ silent: true });
      setStatus("Voice note was too short. Try again.", "error");
      return false;
    }
    state.voice.blob = blob;
    state.voice.url = URL.createObjectURL(blob);
    state.voice.state = "voice_preview";
    if (!state.voice.waveform.length) state.voice.waveform = Array.from({ length: 36 }, (_, index) => 18 + ((index * 13) % 58));
    updateVoicePanel();
    setStatus("Voice preview ready. Tap send, or discard and record again.");
    return true;
  }

  function discardVoiceRecording(options = {}) {
    window.clearTimeout(state.voice.timer);
    window.clearTimeout(state.voice.analyserTimer);
    const resolveStop = state.voice.stopResolve;
    state.voice.stopResolve = null;
    try {
      if (state.voice.recorder && ["recording", "paused"].includes(state.voice.recorder.state)) {
        state.voice.recorder._discarded = true;
        state.voice.recorder.stop();
      }
    } catch (_) {}
    state.voice.stream?.getTracks?.().forEach((track) => track.stop());
    const closePromise = state.voice.audioContext?.close?.();
    closePromise?.catch?.(() => {});
    if (state.voice.url) URL.revokeObjectURL(state.voice.url);
    state.voice = { stream: null, recorder: null, chunks: [], blob: null, url: "", startedAt: 0, elapsedMs: 0, timer: 0, analyserTimer: 0, audioContext: null, waveform: [], stopResolve: null, state: "idle" };
    updateVoicePanel();
    if (resolveStop) resolveStop(false);
    if (!options.silent) setStatus("Voice note discarded.");
  }

  function messageTypeForSend(hasVoice, attachmentIds) {
    if (hasVoice) return "voice";
    if (!attachmentIds.length) return "text";
    const kinds = state.attachmentQueue
      .filter((item) => item.attachmentId && attachmentIds.includes(item.attachmentId))
      .map((item) => item.kind);
    if (kinds.includes("video")) return "video";
    if (kinds.includes("audio")) return "audio";
    if (kinds.includes("image")) return "image";
    return "file";
  }

  async function ensureVoiceReadyForSend() {
    if (state.voice.state === "voice_preview" && state.voice.blob) return true;
    if (["recording_voice", "recording_locked", "recording_paused"].includes(state.voice.state)) {
      setStatus("Preparing voice note...");
      const ready = await stopVoiceRecording();
      if (!ready) return false;
      return state.voice.state === "voice_preview" && !!state.voice.blob;
    }
    return false;
  }

  async function uploadVoiceDraft() {
    if (!state.voice.blob || state.voice.state !== "voice_preview") return 0;
    const voiceType = String(state.voice.blob.type || "audio/webm").toLowerCase();
    const ext = voiceType.includes("webm")
      ? "webm"
      : voiceType.includes("ogg")
        ? "ogg"
        : voiceType.includes("aac")
          ? "aac"
          : voiceType.includes("mp4") || voiceType.includes("m4a")
            ? "m4a"
            : "webm";
    console.info("PulseSoc Messenger V3 voice upload", {
      mimeType: voiceType,
      extension: ext,
      size: state.voice.blob.size,
      durationSeconds: Math.max(1, Math.round((state.voice.elapsedMs || 0) / 1000)),
    });
    const file = new File([state.voice.blob], `pulse-voice-note-${Date.now()}.${ext}`, { type: voiceType });
    const durationMs = Math.max(1, Math.round(state.voice.elapsedMs || 0));
    const waveform = (state.voice.waveform || []).map((level) => Math.max(0, Math.min(1, (Number(level) || 0) / 100)));
    state.voice.state = "voice_uploading";
    updateVoicePanel();
    try {
      return await uploadSelectedFile(file, {
        attachment_kind: "voice_note",
        duration_ms: durationMs,
        duration_seconds: Math.max(1, Math.round(durationMs / 1000)),
        waveform_json: JSON.stringify(waveform),
      });
    } catch (error) {
      state.voice.state = "voice_preview";
      updateVoicePanel();
      throw error;
    }
  }

  function pendingAttachmentPreviews() {
    return state.attachmentQueue.map((item) => {
      const mediaType = item.kind === "image" ? "image" : item.kind === "audio" ? "voice" : item.kind;
      return {
        media_type: mediaType,
        mime_type: mediaFoundationMimeType(item.file),
        filename: item.file?.name || "Attachment",
        file_size: Number(item.file?.size || 0),
        file_size_bytes: Number(item.file?.size || 0),
        url: item.previewUrl || "",
        playback_url: item.kind === "video" ? item.previewUrl || "" : "",
        thumbnail_url: "",
        _local: true,
      };
    });
  }

  function toggleVoicePlayback(container) {
    const audio = container?.querySelector("[data-voice-audio]");
    const button = container?.querySelector("[data-voice-play]");
    if (!audio || !button) return;
    document.querySelectorAll("[data-voice-audio]").forEach((item) => {
      if (item !== audio) {
        item.pause();
        setVoicePlayState(item.closest("[data-voice-message]"), false);
      }
    });
    if (audio.paused) {
      if (container.dataset.loadError === "true") {
        container.dataset.loadError = "false";
        audio.load();
      }
      audio.play().then(() => {
        container.dataset.playbackError = "";
        container.dataset.loadError = "false";
        setStatus("");
        setVoicePlayState(container, true);
      }).catch((error) => {
        container.dataset.playbackError = error?.name || "PlaybackError";
        console.warn("PulseSoc voice playback failed", {
          name: error?.name || "PlaybackError",
          message: error?.message || "Voice playback was rejected.",
          readyState: audio.readyState,
          networkState: audio.networkState,
        });
        setVoicePlayState(container, false);
        const browserBlocked = error?.name === "NotAllowedError";
        container.dataset.loadError = browserBlocked ? "false" : "true";
        setStatus(browserBlocked ? "Browser sound is blocked. Allow sound, then tap play again." : "Voice message could not load. Tap play to retry.", "error");
      });
    } else {
      audio.pause();
      setVoicePlayState(container, false);
    }
    bindVoiceAudio(container);
  }

  function setVoicePlayState(container, playing) {
    if (!container) return;
    const button = container.querySelector("[data-voice-play]");
    const icon = container.querySelector("[data-voice-play-icon]");
    container.dataset.playing = playing ? "true" : "false";
    if (!button) return;
    button.dataset.playing = playing ? "true" : "false";
    button.setAttribute("aria-label", playing ? "Pause voice note" : "Play voice note");
    button.title = playing ? "Pause voice note" : "Play voice note";
    if (icon) icon.textContent = playing ? "❚❚" : "▶";
  }

  function setVoicePlaybackSpeed(container, speed) {
    const audio = container?.querySelector("[data-voice-audio]");
    if (audio) audio.playbackRate = Number(speed || 1);
  }

  function bindVoiceAudio(container) {
    const audio = container?.querySelector("[data-voice-audio]");
    if (!audio || audio.dataset.bound === "1") return;
    audio.dataset.bound = "1";
    const progress = container.querySelector("[data-voice-progress]");
    const current = container.querySelector("[data-voice-current]");
    const duration = container.querySelector("[data-voice-duration]");
    audio.addEventListener("loadedmetadata", () => {
      if (duration) duration.textContent = formatDuration(audio.duration || 0);
    });
    audio.addEventListener("timeupdate", () => {
      if (current) current.textContent = formatDuration(audio.currentTime || 0);
      const percent = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      if (progress) progress.value = String(percent);
      container.style.setProperty("--voice-progress", `${percent}%`);
    });
    audio.addEventListener("ended", () => {
      setVoicePlayState(container, false);
      container.style.setProperty("--voice-progress", "0%");
    });
    audio.addEventListener("error", () => {
      setVoicePlayState(container, false);
      container.dataset.loadError = "true";
    });
    progress?.addEventListener("input", () => {
      if (!audio.duration) return;
      audio.currentTime = (Number(progress.value || 0) / 100) * audio.duration;
    });
  }

  function bind() {
    document.addEventListener("click", async (event) => {
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (!target) return;
      try {
        if (target.closest("[data-pulse-ai]")) return;
        const shieldLink = target.closest("[data-shield-link]");
        if (shieldLink && shieldLink.dataset.shieldLink === "risky") {
          const domain = shieldLink.dataset.linkDomain || "this link";
          if (!window.confirm(`Pulse Shield warning: ${domain} may be risky. Open it anyway?`)) {
            event.preventDefault();
            return;
          }
        }
        const actionTrigger = target.closest("[data-open-conversation-actions]");
        if (actionTrigger) return openConversationActions(actionTrigger.dataset.openConversationActions);
        if (target.closest("[data-close-conversation-actions]")) return closeConversationActions();
        const conversationAction = target.closest("[data-conversation-action]");
        if (conversationAction && !conversationAction.disabled) return await updateConversationPreference(conversationAction.dataset.conversationAction);
        if (target.closest("[data-manage-pins]")) {
          const firstPinned = state.conversations.find((item) => item.pinned);
          if (firstPinned) openConversationActions(firstPinned.conversation_id);
          return;
        }
        const conversation = target.closest("[data-conversation-id]");
        if (conversation) {
          const id = Number(conversation.dataset.conversationId || 0);
          if (state.composerSending) return setStatus("Finish sending the current message before switching chats.");
          if (state.active && Number(state.active.conversation_id) === id) {
            setMobileMode("thread");
            restoreDraft(id);
            el("[data-message-input]")?.focus();
            return;
          }
          saveActiveDraft();
          toggleThreadSearch(false);
          toggleAttachmentSheet(false);
          toggleEmojiPanel(false);
          discardVoiceRecording({ silent: true });
          clearAttachmentQueue();
          closeConversationControlCenter();
          if (isPulseAIId(id)) {
            state.active = ensurePulseAIConversation();
            const cached = state.messageCache.get(PULSE_AI_CONVERSATION_ID) || [];
            state.messages = cached.map((item) => ({ ...item }));
            state.threadHydrating = !cached.length;
            state.members = [];
            state.hasOlder = false;
            restoreDraft(PULSE_AI_CONVERSATION_ID);
            renderMessages();
            renderMembers();
            setMobileMode("thread");
            await loadPulseAIConversation();
            el("[data-message-input]")?.focus();
            return;
          }
          state.active = rememberConversation(state.conversationCache.get(id) || state.conversations.find((item) => Number(item.conversation_id) === id));
          if (state.active) state.active.unread_count = 0;
          applyControlAppearanceSettings(readCachedControlSettings(id), id);
          hydrateConversationVisualSettings(id).catch(() => {});
          const cached = state.messageCache.get(id) || [];
          state.messages = cached.map((item) => ({ ...item }));
          state.threadHydrating = !cached.length;
          state.members = [];
          state.hasOlder = false;
          restoreDraft(id);
          renderMessages();
          renderMembers();
          setMobileMode("thread");
          await loadMessages(id);
          el("[data-message-input]")?.focus();
          return;
        }
        const filter = target.closest("[data-filter]");
        if (filter) {
          saveActiveDraft();
          toggleThreadSearch(false);
          toggleAttachmentSheet(false);
          toggleEmojiPanel(false);
          discardVoiceRecording({ silent: true });
          clearAttachmentQueue();
          const messageInput = el("[data-message-input]");
          if (messageInput) messageInput.value = "";
          state.filter = filter.dataset.filter;
          state.active = null;
          state.messages = [];
          state.members = [];
          state.initialThreadLoaded = false;
          document.querySelectorAll("[data-filter]").forEach((btn) => {
            btn.classList.toggle("is-active", btn === filter);
            btn.setAttribute("aria-selected", btn === filter ? "true" : "false");
          });
          renderConversations();
          renderMessages();
          renderMembers();
          return;
        }
        if (target.closest("[data-open-new-chat]")) return openModal("new-chat");
        if (target.closest("[data-open-new-group]")) return openModal("new-group");
        if (target.closest("[data-open-new-room]")) return openModal("new-room");
        if (target.closest("[data-close-modal]")) return closeModals();
        const controlCenterTrigger = target.closest("[data-open-control-center]");
        if (controlCenterTrigger) return await openConversationControlCenter({ entry: controlCenterTrigger.dataset.controlCenterEntry || "thread" });
        if (target.closest("[data-close-control-center]") || target.closest("[data-conversation-control-backdrop]")) return closeConversationControlCenter();
        const controlConversation = target.closest("[data-control-select-conversation]");
        if (controlConversation) {
          const id = Number(controlConversation.dataset.controlSelectConversation || 0);
          const conversation = state.conversationCache.get(id) || state.conversations.find((item) => Number(item.conversation_id || item.id || 0) === id);
          if (!id || !conversation) {
            controlStatus("That conversation is no longer available.", "error");
            return;
          }
          state.controlConversationId = id;
          state.controlData = null;
          state.active = rememberConversation(conversation);
          state.controlSearch = "";
          await loadConversationControlCenter(true);
          return;
        }
        if (target.closest("[data-control-choose-another]")) {
          state.controlConversationId = 0;
          state.controlData = null;
          state.controlSearch = "";
          controlStatus("");
          renderControlCenter();
          return;
        }
        const controlSectionToggle = target.closest("[data-control-section-toggle]");
        if (controlSectionToggle) {
          const id = controlSectionToggle.dataset.controlSectionToggle;
          if (state.controlExpanded.has(id)) state.controlExpanded.delete(id);
          else state.controlExpanded.add(id);
          renderControlCenter();
          return;
        }
        const controlSearchClear = target.closest("[data-control-search-clear]");
        if (controlSearchClear) {
          state.controlSearch = "";
          renderControlCenter();
          window.setTimeout(() => el("[data-control-search]")?.focus(), 20);
          return;
        }
        if (target.closest("[data-control-detail-close]")) {
          state.controlDetail = null;
          renderControlCenter();
          return;
        }
        const jumpMessage = target.closest("[data-jump-message]");
        if (jumpMessage) {
          closeConversationControlCenter();
          const node = document.querySelector(`[data-message-id="${CSS.escape(String(jumpMessage.dataset.jumpMessage || ""))}"]`);
          node?.scrollIntoView({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
          node?.classList.add("is-search-match");
          window.setTimeout(() => node?.classList.remove("is-search-match"), 1400);
          return;
        }
        const controlToggle = target.closest("[data-control-toggle]");
        if (controlToggle) {
          const [section, key] = String(controlToggle.dataset.controlToggle || "").split(":");
          const current = controlToggle.getAttribute("aria-pressed") === "true";
          await saveControlSetting(section, key, !current);
          return;
        }
        const controlAction = target.closest("[data-control-action]");
        if (controlAction && controlAction.closest("[data-conversation-control-center]")) {
          await runControlAction(controlAction.dataset.controlAction || "", controlAction.dataset.controlConfirm || "");
          return;
        }
        if (target.closest("[data-toggle-details]")) return toggleDetails();
        if (target.closest("[data-thread-call-audio]")) return await startConversationCall("audio", "thread");
        if (target.closest("[data-thread-call-video]")) return await startConversationCall("video", "thread");
        if (target.closest("[data-thread-search]")) return toggleThreadSearch(true);
        if (target.closest("[data-close-thread-search]")) return toggleThreadSearch(false);
        if (target.closest("[data-thread-mute]")) {
          if (state.active?.conversation_id) {
            state.actionConversationId = Number(state.active.conversation_id);
            return await updateConversationPreference("mute");
          }
        }
        if (target.closest("[data-thread-more]")) {
          if (state.active?.conversation_id) return openConversationActions(state.active.conversation_id);
        }
        if (target.closest("[data-ai-summary]")) return await runAIAction("summary");
        if (target.closest("[data-ai-replies]")) return await runAIAction("smart-replies");
        const aiPrompt = target.closest("[data-pulse-ai-prompt]");
        if (aiPrompt) {
          const input = el("[data-message-input]");
          if (input) {
            input.value = aiPrompt.dataset.pulseAiPrompt || "";
            input.focus();
            syncComposerState();
            el("[data-composer]")?.requestSubmit();
          }
          return;
        }
        const aiFeedback = target.closest("[data-pulse-ai-feedback]");
        if (aiFeedback) return await sendPulseAIFeedback(aiFeedback.dataset.messageId, aiFeedback.dataset.pulseAiFeedback);
        const aiSetting = target.closest("[data-pulse-ai-setting]");
        if (aiSetting) return await togglePulseAISetting(aiSetting.dataset.pulseAiSetting);
        if (target.closest("[data-pulse-ai-memory-clear]")) return await clearPulseAIMemory();
        if (target.closest("[data-pulse-ai-memory-export]")) return await exportPulseAIMemory();
        if (target.closest("[data-mobile-list]")) {
          setMobileMode("list");
          return;
        }
        const messageActions = target.closest("[data-message-actions]");
        if (messageActions) {
          const menu = el(`[data-reaction-menu="${messageActions.dataset.messageActions}"]`);
          document.querySelectorAll("[data-reaction-menu]").forEach((item) => {
            if (item !== menu) item.hidden = true;
          });
          if (menu) {
            menu.hidden = !menu.hidden;
            state.reactionOpen = !menu.hidden;
            syncComposerState();
          }
          return;
        }
        const person = target.closest("[data-person-id]");
        if (person?.closest("[data-person-results]")) return await runAction(person, "Opening chat...", () => openDm(Number(person.dataset.personId || 0)));
        if (person?.closest("[data-group-person-results]")) return addGroupMember(Number(person.dataset.personId || 0));
        const removeMember = target.closest("[data-remove-group-member]");
        if (removeMember) return removeGroupMember(Number(removeMember.dataset.removeGroupMember || 0));
        const createGroupButton = target.closest("[data-create-group]");
        if (createGroupButton) return await runAction(createGroupButton, "Creating group...", createGroup);
        const createRoomButton = target.closest("[data-create-room]");
        if (createRoomButton) return await runAction(createRoomButton, "Creating room...", createRoom);
        if (target.closest("[data-toggle-attachments]")) return toggleAttachmentSheet();
        if (target.closest("[data-toggle-emoji]")) return toggleEmojiPanel();
        const emoji = target.closest("[data-emoji-value]");
        if (emoji) return insertEmoji(emoji.dataset.emojiValue || "");
        const attachmentOption = target.closest("[data-attachment-option]");
        if (attachmentOption) return openAttachmentOption(attachmentOption.dataset.attachmentOption || "file");
        const removeAttachmentButton = target.closest("[data-attachment-remove]");
        if (removeAttachmentButton) return removeAttachment(removeAttachmentButton.dataset.attachmentRemove);
        const retryAttachmentButton = target.closest("[data-attachment-retry]");
        if (retryAttachmentButton) {
          const item = state.attachmentQueue.find((entry) => entry.id === retryAttachmentButton.dataset.attachmentRetry);
          if (item) await uploadAttachmentItem(item);
          return;
        }
        const retryMessageButton = target.closest("[data-retry-message]");
        if (retryMessageButton) return retryFailedMessage(Number(retryMessageButton.dataset.retryMessage || 0));
        const moveAttachmentButton = target.closest("[data-attachment-move]");
        if (moveAttachmentButton) return moveAttachment(moveAttachmentButton.dataset.attachmentMove, Number(moveAttachmentButton.dataset.direction || 0));
        if (target.closest("[data-voice-start]")) return await startVoiceRecording();
        if (target.closest("[data-voice-pause]")) return pauseVoiceRecording();
        if (target.closest("[data-voice-resume]")) return resumeVoiceRecording();
        if (target.closest("[data-voice-stop]")) return stopVoiceRecording();
        if (target.closest("[data-voice-discard]")) return discardVoiceRecording();
        const voicePlay = target.closest("[data-voice-play]");
        if (voicePlay) return toggleVoicePlayback(voicePlay.closest("[data-voice-message]"));
        const speed = target.closest("[data-voice-speed]");
        if (speed) return setVoicePlaybackSpeed(speed.closest("[data-voice-message]"), speed.value);
        if (target.closest("[data-load-older]")) return await loadOlderMessages();
        const room = target.closest("[data-room-id]");
        if (room) return await runAction(room, "Opening room...", () => openRoom(Number(room.dataset.roomId || 0)));
        const react = target.closest("[data-react]");
        if (react) {
          state.reactionOpen = false;
          syncComposerState();
          return await reactToMessage(react.dataset.messageId, react.dataset.react);
        }
        const reply = target.closest("[data-reply-message]");
        if (reply) return startReply(Number(reply.dataset.replyMessage || 0));
        const copy = target.closest("[data-copy-message]");
        if (copy) return await copyMessage(Number(copy.dataset.copyMessage || 0));
        const pin = target.closest("[data-pin-message]");
        if (pin) return await pinMessage(Number(pin.dataset.pinMessage || 0));
        const jump = target.closest("[data-jump-message]");
        if (jump) return jumpToMessage(Number(jump.dataset.jumpMessage || 0));
        const edit = target.closest("[data-edit-message]");
        if (edit) return await editMessage(Number(edit.dataset.editMessage || 0));
        const del = target.closest("[data-delete-message]");
        if (del) return await deleteMessage(Number(del.dataset.deleteMessage || 0), del.dataset.deleteFor || "self");
        const forward = target.closest("[data-forward-message]");
        if (forward) return await forwardMessage(Number(forward.dataset.forwardMessage || 0));
        if (target.closest("[data-report-last]")) return await reportLast();
        if (target.closest("[data-block-peer]")) return await blockPeer();
      } catch (err) {
        console.error("PulseSoc Messenger V3 action failed", err);
        setStatus(err?.message || "That action could not be completed. Please try again.", "error");
      }
    });
    el("[data-composer]")?.addEventListener("submit", sendMessage);
    el("[data-message-input]")?.addEventListener("input", () => {
      saveActiveDraft();
      debounceTyping();
      syncComposerState();
    });
    el("[data-message-input]")?.addEventListener("focus", syncComposerState);
    el("[data-message-input]")?.addEventListener("blur", () => {
      sendTypingStopped();
      syncComposerState();
    });
    el("[data-thread-search-input]")?.addEventListener("input", applyThreadSearch);
    el("[data-person-search]")?.addEventListener("input", () => debouncePeopleSearch("direct"));
    el("[data-group-person-search]")?.addEventListener("input", () => debouncePeopleSearch("group"));
    el("[data-conversation-search]")?.addEventListener("input", (event) => {
      state.conversationSearch = event.target.value || "";
      renderConversations();
    });
    document.addEventListener("input", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target?.matches("[data-control-search]")) return;
      state.controlSearch = target.value || "";
      renderControlCenter();
      window.setTimeout(() => el("[data-control-search]")?.focus(), 0);
    });
    let pressTimer = 0;
    let swipeMessage = null;
    document.addEventListener("pointerdown", (event) => {
      const message = event.target instanceof Element ? event.target.closest("[data-message-id]") : null;
      if (message && isMobile() && !event.target.closest("button,a,select,input")) {
        swipeMessage = { id: Number(message.dataset.messageId || 0), startX: event.clientX, startY: event.clientY, node: message };
      }
      const row = event.target instanceof Element ? event.target.closest("[data-conversation-row]") : null;
      if (!row || event.target.closest("button,a")) return;
      pressTimer = window.setTimeout(() => openConversationActions(row.dataset.conversationRow), 520);
    });
    document.addEventListener("pointermove", (event) => {
      if (!swipeMessage?.node) return;
      const dx = event.clientX - swipeMessage.startX;
      const dy = Math.abs(event.clientY - swipeMessage.startY);
      if (dy > 28) return;
      const offset = Math.max(-56, Math.min(56, dx));
      swipeMessage.node.style.transform = `translateX(${offset}px)`;
    }, { passive: true });
    document.addEventListener("pointerup", (event) => {
      if (swipeMessage?.node) {
        const dx = event.clientX - swipeMessage.startX;
        const dy = Math.abs(event.clientY - swipeMessage.startY);
        swipeMessage.node.style.transform = "";
        if (Math.abs(dx) > 54 && dy < 36) startReply(swipeMessage.id);
      }
      swipeMessage = null;
    }, { passive: true });
    document.addEventListener("pointercancel", () => {
      if (swipeMessage?.node) swipeMessage.node.style.transform = "";
      swipeMessage = null;
    }, { passive: true });
    ["pointerup", "pointercancel", "pointermove"].forEach((type) => document.addEventListener(type, () => {
      window.clearTimeout(pressTimer);
      pressTimer = 0;
    }, { passive: true }));
    document.addEventListener("change", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const controlSelect = target?.closest("[data-control-select]");
      if (controlSelect) {
        const [section, key] = String(controlSelect.dataset.controlSelect || "").split(":");
        saveControlSetting(section, key, controlSelect.value).catch((err) => {
          console.error("PulseSoc Messenger V3 control setting failed", err);
          controlStatus(err?.message || "That setting could not be saved.", "error");
        });
        return;
      }
      const speed = target?.closest("[data-voice-speed]");
      if (speed) setVoicePlaybackSpeed(speed.closest("[data-voice-message]"), speed.value);
      if (target?.matches("[data-file], [data-camera-file], [data-photo-file], [data-video-file], [data-generic-file]")) {
        addAttachmentFiles(target.files);
      }
    });
    document.addEventListener("paste", (event) => {
      if (!state.active) return;
      const files = Array.from(event.clipboardData?.files || []).filter(Boolean);
      if (!files.length) return;
      event.preventDefault();
      addAttachmentFiles(files);
    });
    const thread = el(".comm-thread");
    thread?.addEventListener("dragover", (event) => {
      if (!state.active || !event.dataTransfer?.types?.includes("Files")) return;
      event.preventDefault();
      thread.classList.add("is-dragging-file");
    });
    thread?.addEventListener("dragleave", (event) => {
      if (event.relatedTarget instanceof Node && thread.contains(event.relatedTarget)) return;
      thread.classList.remove("is-dragging-file");
    });
    thread?.addEventListener("drop", (event) => {
      thread.classList.remove("is-dragging-file");
      if (!state.active || !event.dataTransfer?.files?.length) return;
      event.preventDefault();
      addAttachmentFiles(event.dataTransfer.files);
    });
    document.addEventListener("keydown", async (event) => {
      trapControlFocus(event);
      if (event.key === "Escape") {
        if (state.controlOpen) {
          closeConversationControlCenter();
          return;
        }
        toggleEmojiPanel(false);
        toggleAttachmentSheet(false);
        toggleThreadSearch(false);
        return closeModals();
      }
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
      const roomModal = event.target.closest?.('[data-modal="new-room"]');
      const groupModal = event.target.closest?.('[data-modal="new-group"]');
      if (roomModal) {
        event.preventDefault();
        await runAction(el("[data-create-room]"), "Creating room...", createRoom);
      } else if (groupModal && !event.target.closest?.("[data-group-person-search]")) {
        event.preventDefault();
        await runAction(el("[data-create-group]"), "Creating group...", createGroup);
      }
    });
    document.querySelectorAll("[data-modal]").forEach((modal) => {
      modal.addEventListener("click", (event) => {
        if (event.target === modal) closeModals();
      });
    });
    const syncViewport = () => {
      const viewport = window.visualViewport;
      const offset = viewport ? Math.max(0, Math.round(window.innerHeight - viewport.height - viewport.offsetTop)) : 0;
      document.documentElement.style.setProperty("--messenger-keyboard-offset", `${offset}px`);
    };
    window.visualViewport?.addEventListener("resize", syncViewport, { passive: true });
    window.visualViewport?.addEventListener("scroll", syncViewport, { passive: true });
    syncViewport();
    window.addEventListener("pagehide", () => {
      saveActiveDraft();
      discardVoiceRecording({ silent: true });
      clearAttachmentQueue();
    });
    syncComposerState();
  }

  function debounceTyping() {
    if (!state.active || isPulseAIConversation(state.active)) return;
    window.clearTimeout(state.typingTimer);
    window.clearTimeout(state.typingStopTimer);
    state.typingTimer = window.setTimeout(sendTypingIndicator, 450);
    state.typingStopTimer = window.setTimeout(sendTypingStopped, 5000);
  }

  async function sendTypingIndicator() {
    if (!state.active || isPulseAIConversation(state.active)) return;
    const now = Date.now();
    if (now - state.typingSentAt < 2500) return;
    state.typingSentAt = now;
    try {
      await api(`/conversations/${state.active.conversation_id}/typing`, {
        method: "POST",
        body: JSON.stringify({ is_typing: true }),
      }, "typing_indicator");
    } catch (_) {}
  }

  async function sendTypingStopped() {
    window.clearTimeout(state.typingTimer);
    window.clearTimeout(state.typingStopTimer);
    if (!state.active || isPulseAIConversation(state.active)) return;
    try {
      await api(`/conversations/${state.active.conversation_id}/typing`, {
        method: "POST",
        body: JSON.stringify({ is_typing: false }),
      }, "typing_stopped");
    } catch (_) {}
  }

  async function sendPresenceHeartbeat() {
    try {
      await api("/presence/heartbeat", { method: "POST", body: JSON.stringify({ status: "online" }) }, "presence_heartbeat");
    } catch (_) {}
  }

  function schedulePresenceHeartbeat() {
    window.setTimeout(async () => {
      await sendPresenceHeartbeat();
      schedulePresenceHeartbeat();
    }, 30000);
  }

  async function loadPresence(conversationId) {
    if (!conversationId) return;
    try {
      const data = await api(`/conversations/${conversationId}/presence`, {}, "conversation_presence");
      state.presence = data.presence || [];
    } catch (_) {}
  }

  function openModal(name) {
    closeModals();
    const modal = el(`[data-modal="${name}"]`);
    if (!modal) {
      setStatus("That creation panel is unavailable. Refresh and try again.", "error");
      return;
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    if (isMobile()) setMobileMode("create");
    window.setTimeout(() => modal.querySelector("input")?.focus(), 30);
  }

  function closeModals() {
    document.querySelectorAll("[data-modal]").forEach((modal) => {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
    });
    if (isMobile()) setMobileMode(state.active ? "thread" : "list");
  }

  function resetCreationModal(name) {
    const modal = el(`[data-modal="${name}"]`);
    if (!modal) return;
    modal.querySelectorAll("input:not([type=file])").forEach((input) => { input.value = ""; });
    modal.querySelectorAll("[data-person-results], [data-group-person-results]").forEach((target) => { target.innerHTML = ""; });
    modal.querySelectorAll("[data-modal-status]").forEach((target) => {
      target.textContent = "";
      target.dataset.kind = "info";
    });
  }

  async function runAction(button, pendingText, action) {
    if (state.actionPending) return;
    state.actionPending = true;
    const previousText = button?.textContent || "";
    if (button) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    setStatus(pendingText);
    try {
      const result = await action();
      if (result !== false) setStatus("");
      return result;
    } catch (err) {
      console.error("PulseSoc Messenger V3 action failed", err);
      setStatus(err?.message || "Messenger action failed. Please try again.", "error");
      return false;
    } finally {
      state.actionPending = false;
      if (button?.isConnected) {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        if (previousText && !button.textContent.trim()) button.textContent = previousText;
      }
    }
  }

  function debouncePeopleSearch(kind) {
    const key = kind === "group" ? "groupSearchTimer" : "searchTimer";
    window.clearTimeout(state[key]);
    state[key] = window.setTimeout(() => searchPeople(kind), 260);
  }

  async function searchPeople(kind) {
    const input = kind === "group" ? el("[data-group-person-search]") : el("[data-person-search]");
    const target = kind === "group" ? el("[data-group-person-results]") : el("[data-person-results]");
    const query = String(input?.value || "").trim();
    if (!target) return;
    if (query.length < 2) {
      target.innerHTML = `<div class="empty-state">Type at least two characters.</div>`;
      return;
    }
    try {
      target.innerHTML = `<div class="empty-state">Searching...</div>`;
      const data = await api(`/people/search?q=${encodeURIComponent(query)}`, {}, "people_search");
      const people = data.people || data.items || [];
      people.forEach(rememberPerson);
      target.innerHTML = people.length ? people.map((person) => personResultHtml(person)).join("") : `<div class="empty-state">No people found.</div>`;
    } catch (err) {
      target.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
    }
  }

  function personResultHtml(person) {
    const remembered = rememberPerson({
      user_id: Number(person.user_id || 0),
      display_name: person.display_name || "PulseSoc member",
      username: person.username || "",
      avatar_url: person.avatar_url || "",
    });
    return `
      <button class="person-result" type="button" data-person-id="${Number(remembered.user_id || 0)}">
        ${avatarHtml({ ...remembered, title: remembered.display_name || remembered.username }, "avatar")}
        <span><strong>${escapeHtml(remembered.display_name || "PulseSoc member")}</strong><small>${escapeHtml(remembered.username ? `@${remembered.username}` : person.matched_email ? "Email match" : "PulseSoc member")}</small></span>
        <span aria-hidden="true">+</span>
      </button>
    `;
  }

  function rememberPerson(person) {
    const userId = Number(person?.user_id || 0);
    if (!userId) return person || {};
    const remembered = { ...(state.peopleCache.get(userId) || {}), ...person, user_id: userId };
    state.peopleCache.set(userId, remembered);
    return remembered;
  }

  async function openDm(target) {
    if (!target) {
      setStatus("Choose someone to message.", "error");
      return false;
    }
    saveActiveDraft();
    const data = await api("/direct/open", { method: "POST", body: JSON.stringify({ target_user_id: target }) }, "create_direct");
    state.active = rememberConversation(data.conversation);
    if (!state.active?.conversation_id) throw new Error("The chat opened without a conversation ID. Please retry.");
    state.initialThreadLoaded = false;
    closeModals();
    resetCreationModal("new-chat");
    await loadConversations({ selectFirst: false });
    restoreDraft(state.active.conversation_id);
    await loadMessages(state.active.conversation_id);
    setMobileMode("thread");
    el("[data-message-input]")?.focus();
  }

  function addGroupMember(selectedUserId) {
    const person = state.peopleCache.get(Number(selectedUserId)) || {};
    const userId = Number(person.user_id || 0);
    if (!userId) return setStatus("That member could not be selected. Search again and retry.", "error");
    if (state.groupMembers.some((item) => Number(item.user_id) === userId)) return setStatus("That person is already selected.");
    state.groupMembers = [...state.groupMembers, person];
    renderSelectedPeople();
    setStatus(`${person.display_name || "Member"} added to the group.`);
  }

  function removeGroupMember(userId) {
    state.groupMembers = state.groupMembers.filter((item) => Number(item.user_id) !== Number(userId));
    renderSelectedPeople();
  }

  function renderSelectedPeople() {
    const target = el("[data-selected-people]");
    if (!target) return;
    target.innerHTML = state.groupMembers.length ? state.groupMembers.map((person) => `
      <div class="selected-person">
        <span class="avatar">${initials(person.display_name || person.username)}</span>
        <span><strong>${escapeHtml(person.display_name || "PulseSoc member")}</strong><small>${escapeHtml(person.username ? `@${person.username}` : "Selected")}</small></span>
        <button type="button" data-remove-group-member="${Number(person.user_id || 0)}">Remove</button>
      </div>
    `).join("") : `<div class="empty-state">Select at least one person.</div>`;
  }

  async function createGroup() {
    const title = String(el("[data-group-title]")?.value || "").trim();
    const memberIds = state.groupMembers.map((item) => Number(item.user_id || 0)).filter(Boolean);
    if (!title) {
      setStatus("Name the group before creating it.", "error");
      el("[data-group-title]")?.focus();
      return false;
    }
    if (!memberIds.length) {
      setStatus("Add at least one person to create a group.", "error");
      el("[data-group-person-search]")?.focus();
      return false;
    }
    saveActiveDraft();
    const data = await api("/groups", { method: "POST", body: JSON.stringify({ title, member_ids: memberIds }) }, "create_group");
    state.active = rememberConversation(data.conversation);
    if (!state.active?.conversation_id) throw new Error("The group was created without a conversation ID. Please retry.");
    state.initialThreadLoaded = false;
    state.groupMembers = [];
    renderSelectedPeople();
    closeModals();
    resetCreationModal("new-group");
    await loadConversations({ selectFirst: false });
    restoreDraft(state.active.conversation_id);
    await loadMessages(state.active.conversation_id);
    setMobileMode("thread");
    el("[data-message-input]")?.focus();
  }

  async function createRoom() {
    const title = String(el("[data-room-title]")?.value || "").trim();
    const privacy = String(el("[data-room-privacy]")?.value || "public");
    const description = String(el("[data-room-description]")?.value || "").trim();
    if (!title) {
      setStatus("Name the room before creating it.", "error");
      el("[data-room-title]")?.focus();
      return false;
    }
    saveActiveDraft();
    const data = await api("/rooms", { method: "POST", body: JSON.stringify({ title, privacy, description }) }, "create_room");
    state.active = rememberConversation(data.conversation);
    if (!state.active?.conversation_id) throw new Error("The room was created without a conversation ID. Please retry.");
    state.initialThreadLoaded = false;
    closeModals();
    resetCreationModal("new-room");
    await loadConversations({ selectFirst: false });
    await loadRooms();
    restoreDraft(state.active.conversation_id);
    await loadMessages(state.active.conversation_id);
    setMobileMode("thread");
    el("[data-message-input]")?.focus();
  }

  async function openRoom(roomId) {
    if (!roomId) return;
    if (state.composerSending) return setStatus("Finish sending the current message before switching chats.");
    saveActiveDraft();
    toggleAttachmentSheet(false);
    toggleEmojiPanel(false);
    discardVoiceRecording({ silent: true });
    clearAttachmentQueue();
    state.active = rememberConversation(state.conversationCache.get(roomId) || state.rooms.find((item) => Number(item.conversation_id) === roomId));
    const cached = state.messageCache.get(roomId) || [];
    state.messages = cached.map((item) => ({ ...item }));
    state.threadHydrating = !cached.length;
    state.members = [];
    state.typing = [];
    restoreDraft(roomId);
    renderConversations();
    renderMessages();
    renderMembers();
    setMobileMode("thread");
    await loadMessages(roomId);
    connectRealtimeStream();
  }

  async function sendPulseAIMessage(event) {
    event.preventDefault();
    if (state.composerSending) return;
    const input = el("[data-message-input]");
    const body = (input?.value || "").trim();
    if (!body) return setStatus("Ask Pulse AI something first.", "error");
    if (state.attachmentQueue.length || state.voice.state !== "idle") {
      return setStatus("Pulse AI supports text conversation first. Remove attachments or voice notes before sending.", "error");
    }
    const clientMessageId = `pulse-ai-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const pendingUser = {
      id: -Date.now(),
      message_id: -Date.now(),
      conversation_id: PULSE_AI_CONVERSATION_ID,
      client_message_id: clientMessageId,
      sender_user_id: currentUserId,
      sender_id: currentUserId,
      sender: { user_id: currentUserId, display_name: "You", avatar_url: "" },
      is_mine: true,
      message_type: "text",
      body,
      delivery_status: "sending",
      delivery_state: "sending",
      created_at: new Date().toISOString(),
      attachments: [],
      reactions: [],
      _pending: true,
    };
    state.composerSending = true;
    state.pulseAITyping = true;
    state.messages = [...state.messages, pendingUser];
    state.messageCache.set(PULSE_AI_CONVERSATION_ID, state.messages.map((item) => ({ ...item })));
    if (input) input.value = "";
    clearDraft(PULSE_AI_CONVERSATION_ID);
    renderMessages();
    setStatus("Pulse AI is thinking...");
    try {
      const data = await pulseAiApi("/message", {
        method: "POST",
        body: JSON.stringify({ message: body, client_message_id: clientMessageId }),
      }, "pulse_ai_message");
      state.pulseAITyping = false;
      state.messages = (data.messages || []).map((item) => ({ ...item, conversation_id: PULSE_AI_CONVERSATION_ID }));
      state.messageCache.set(PULSE_AI_CONVERSATION_ID, state.messages.map((item) => ({ ...item })));
      ensurePulseAIConversation(data.conversation || {});
      renderConversations();
      renderMessages();
      setStatus("");
    } catch (err) {
      state.pulseAITyping = false;
      const serverMessages = err?.data?.messages || [];
      if (serverMessages.length) {
        state.messages = serverMessages.map((item) => ({ ...item, conversation_id: PULSE_AI_CONVERSATION_ID }));
      } else {
        const failed = {
          id: -Date.now() - 1,
          message_id: -Date.now() - 1,
          conversation_id: PULSE_AI_CONVERSATION_ID,
          sender_user_id: PULSE_AI_USER_ID,
          sender: { user_id: PULSE_AI_USER_ID, display_name: "Pulse AI", avatar_url: "" },
          is_mine: false,
          is_ai: true,
          message_type: "text",
          body: err?.data?.message || "Pulse AI is temporarily unavailable. Please try again soon.",
          delivery_status: "failed",
          delivery_state: "failed",
          error_code: err?.data?.error || "ai_unavailable",
          correlation_id: err?.data?.correlation_id || err?.data?.trace_id || "",
          created_at: new Date().toISOString(),
          attachments: [],
          reactions: [],
        };
        state.messages = state.messages.map((item) => item.client_message_id === clientMessageId ? { ...item, _pending: false, delivery_status: "sent", delivery_state: "sent" } : item);
        state.messages = [...state.messages, failed];
      }
      state.messageCache.set(PULSE_AI_CONVERSATION_ID, state.messages.map((item) => ({ ...item })));
      renderMessages();
      setStatus(err?.message || "Pulse AI is temporarily unavailable.", "error");
    } finally {
      state.composerSending = false;
      state.pulseAITyping = false;
      renderMessages();
    }
  }

  async function sendPulseAIFeedback(messageId, rating) {
    try {
      const data = await pulseAiApi("/feedback", {
        method: "POST",
        body: JSON.stringify({ message_id: Number(messageId || 0), rating }),
      }, "pulse_ai_feedback");
      setStatus(data.message || "Feedback saved.");
    } catch (err) {
      setStatus(err?.message || "Pulse AI feedback could not be saved.", "error");
    }
  }

  async function togglePulseAISetting(key) {
    const current = Boolean((state.pulseAISettings || {})[key]);
    const next = { ...(state.pulseAISettings || {}), [key]: !current };
    state.pulseAISettings = next;
    renderMessages();
    try {
      const data = await pulseAiApi("/settings", {
        method: "PATCH",
        body: JSON.stringify({ [key]: !current }),
      }, "pulse_ai_settings_update");
      state.pulseAISettings = data.settings || next;
      setStatus("Pulse AI privacy setting saved.");
    } catch (err) {
      state.pulseAISettings = { ...(state.pulseAISettings || {}), [key]: current };
      setStatus(err?.message || "Pulse AI setting could not be saved.", "error");
    } finally {
      renderMessages();
    }
  }

  async function clearPulseAIMemory() {
    if (!window.confirm("Clear Pulse AI personalization memory? This does not delete your messages.")) return;
    try {
      const data = await pulseAiApi("/memory/clear", { method: "POST", body: "{}" }, "pulse_ai_memory_clear");
      setStatus(data.message || "Pulse AI memory cleared.");
    } catch (err) {
      setStatus(err?.message || "Pulse AI memory could not be cleared.", "error");
    }
  }

  async function exportPulseAIMemory() {
    try {
      const data = await pulseAiApi("/memory/export", {}, "pulse_ai_memory_export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "pulse-ai-memory.json";
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus("Pulse AI memory export prepared.");
    } catch (err) {
      setStatus(err?.message || "Pulse AI memory could not be exported.", "error");
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    if (state.composerSending) return;
    if (!state.active) return setStatus("Choose a conversation first.", "error");
    if (isPulseAIConversation(state.active)) return sendPulseAIMessage(event);
    const sendingConversationId = Number(state.active.conversation_id || 0);
    const input = el("[data-message-input]");
    const body = input?.value || "";
    let hasVoice = state.voice.state === "voice_preview" && !!state.voice.blob;
    if (!hasVoice && ["recording_voice", "recording_locked", "recording_paused"].includes(state.voice.state)) {
      hasVoice = await ensureVoiceReadyForSend();
    }
    if (!body.trim() && !state.attachmentQueue.length && !hasVoice) {
      return setStatus("Type a message, attach something, or record a voice note.", "error");
    }
    const clientMessageId = `comm-v2-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const pendingId = -Date.now();
    const pendingVoiceAttachment = hasVoice ? [{
      voice_note: true,
      media_type: "audio/voice",
      playback_url: state.voice.url,
      url: state.voice.url,
      duration_seconds: Math.max(1, Math.round((state.voice.elapsedMs || 0) / 1000)),
      waveform: state.voice.waveform || [],
      _local: true,
    }] : [];
    const pendingAttachments = [...pendingAttachmentPreviews(), ...pendingVoiceAttachment];
    const pendingMessage = {
      id: pendingId,
      message_id: pendingId,
      conversation_id: sendingConversationId,
      client_message_id: clientMessageId,
      client_temp_id: clientMessageId,
      sender_user_id: currentUserId,
      sender_id: currentUserId,
      sender: { user_id: currentUserId, display_name: "You", avatar_url: "" },
      sender_display_name: "You",
      sender_avatar: "",
      is_mine: true,
      message_type: messageTypeForSend(hasVoice, state.attachmentQueue.length ? [1] : []),
      body: body.trim() || (hasVoice ? "Voice message" : "Attachment"),
      reply_to_message_id: state.replyTo?.id || 0,
      delivery_status: "sending",
      delivery_state: "sending",
      moderation_status: "approved",
      attachments: pendingAttachments,
      reactions: [],
      created_at: new Date().toISOString(),
      _pending: true,
    };
    state.composerSending = true;
    try {
      state.messages = [...state.messages, pendingMessage];
      state.messageCache.set(sendingConversationId, state.messages.map((item) => ({ ...item })));
      renderMessages();
      toggleAttachmentSheet(false);
      toggleEmojiPanel(false);
      setStatus(hasVoice ? "Uploading voice note..." : state.attachmentQueue.length ? "Uploading attachments..." : "Sending...");
      const attachmentIds = state.attachmentQueue.length ? await uploadAttachmentQueue() : [];
      const voiceId = hasVoice ? await uploadVoiceDraft() : 0;
      const allAttachmentIds = [...attachmentIds, ...(voiceId ? [voiceId] : [])];
      const data = await api(`/conversations/${sendingConversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({
          body,
          message_type: messageTypeForSend(hasVoice, allAttachmentIds),
          attachment_ids: allAttachmentIds,
          reply_to_message_id: state.replyTo?.id || 0,
          client_message_id: clientMessageId,
        }),
      }, "send_message");
      if (input) input.value = "";
      clearDraft(sendingConversationId);
      clearAttachmentQueue();
      if (hasVoice) discardVoiceRecording({ silent: true });
      state.replyTo = null;
      if (data.message) {
        const cached = state.messageCache.get(sendingConversationId) || [];
        const cachedIndex = cached.findIndex((item) => item.client_message_id === clientMessageId || item.client_temp_id === clientMessageId || Number(item.id) === Number(data.message.id));
        const nextCached = [...cached];
        if (cachedIndex >= 0) nextCached[cachedIndex] = { ...data.message, _pending: false, _failed: false };
        else nextCached.push(data.message);
        state.messageCache.set(sendingConversationId, nextCached);
        if (Number(state.active?.conversation_id || 0) === sendingConversationId) {
          state.messages = nextCached.map((item) => ({ ...item }));
          renderMessages();
          document.querySelectorAll("[data-voice-message]").forEach(bindVoiceAudio);
        }
      } else {
        await loadMessages(sendingConversationId);
      }
      setStatus("");
    } catch (err) {
      const cached = state.messageCache.get(sendingConversationId) || [];
      const index = cached.findIndex((item) => item.client_message_id === clientMessageId || item.client_temp_id === clientMessageId);
      if (index >= 0) {
        cached[index] = { ...cached[index], delivery_status: "failed", delivery_state: "failed", _pending: false, _failed: true };
        state.messageCache.set(sendingConversationId, cached);
      }
      if (Number(state.active?.conversation_id || 0) === sendingConversationId) {
        state.messages = cached.map((item) => ({ ...item }));
        renderMessages();
      }
      setStatus(err.message, "error");
    } finally {
      state.composerSending = false;
    }
  }

  function retryFailedMessage(messageId) {
    const failed = state.messages.find((item) => Number(item.id) === Number(messageId) && item._failed);
    if (!failed || state.composerSending) return;
    const input = el("[data-message-input]");
    if (input && !input.value.trim() && failed.body && !["Attachment", "Voice message"].includes(failed.body)) {
      input.value = failed.body;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    state.messages = state.messages.filter((item) => Number(item.id) !== Number(messageId));
    if (state.active?.conversation_id) {
      state.messageCache.set(Number(state.active.conversation_id), state.messages.map((item) => ({ ...item })));
    }
    renderMessages();
    el("[data-composer]")?.requestSubmit();
  }

  async function reactToMessage(messageId, reaction) {
    const id = Number(messageId || 0);
    const index = state.messages.findIndex((item) => Number(item.id) === id);
    if (index < 0 || state.messages[index]._reactionPending) return;
    const previous = { ...state.messages[index], reactions: { ...(state.messages[index].reactions || {}) } };
    const current = state.messages[index];
    const normalizeReaction = value => ({ "❤️": "heart", "♥️": "heart", "🔥": "fire", "✓": "check", "✅": "check", heart: "heart", fire: "fire", check: "check" }[String(value || "").trim()] || String(value || "").trim());
    const currentReaction = normalizeReaction(current.my_reaction || current.viewer_reaction || "");
    const wasActive = currentReaction === reaction;
    const nextReactions = { ...(current.reactions || {}) };
    if (currentReaction) {
      const currentLabel = { heart: "❤️", fire: "🔥", check: "✓" }[currentReaction] || currentReaction;
      if (nextReactions[currentReaction]) nextReactions[currentReaction] = Math.max(0, Number(nextReactions[currentReaction] || 0) - 1);
      if (nextReactions[currentLabel]) nextReactions[currentLabel] = Math.max(0, Number(nextReactions[currentLabel] || 0) - 1);
    }
    if (!wasActive) nextReactions[reaction] = Number(nextReactions[reaction] || 0) + 1;
    state.messages[index] = { ...current, reactions: nextReactions, my_reaction: wasActive ? "" : reaction, viewer_reaction: wasActive ? "" : reaction, _reactionPending: true };
    renderMessages();
    const button = document.querySelector(`[data-message-id="${id}"][data-react="${reaction}"]`);
    animateReactionButton(button, reaction === "fire" ? "🔥" : reaction === "check" ? "✓" : "❤️");
    try {
      const data = await api(`/messages/${id}/reactions`, {
        method: "POST",
        body: JSON.stringify({ reaction_type: reaction })
      }, "message_reaction");
      state.messages[index] = { ...state.messages[index], reactions: data.reactions || {}, my_reaction: normalizeReaction(data.my_reaction || ""), viewer_reaction: normalizeReaction(data.my_reaction || ""), _reactionPending: false };
      renderMessages();
    } catch (error) {
      state.messages[index] = previous;
      renderMessages();
      setStatus(error.message || "Reaction failed.", "error");
    }
  }

  function startReply(messageId) {
    const item = state.messages.find((message) => Number(message.id) === Number(messageId));
    if (!item) return setStatus("That message is no longer available.", "error");
    state.replyTo = { id: Number(item.id), body: item.body || item.message_type || "attachment" };
    setStatus(`Replying to: ${state.replyTo.body}`);
    el("[data-message-input]")?.focus();
  }

  async function copyMessage(messageId) {
    const item = state.messages.find((message) => Number(message.id) === Number(messageId));
    const text = item?.body || "";
    if (!text) return setStatus("This message has no text to copy.", "error");
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.style.position = "fixed";
      scratch.style.opacity = "0";
      document.body.appendChild(scratch);
      scratch.select();
      document.execCommand("copy");
      scratch.remove();
    }
    setStatus("Message copied.");
  }

  async function pinMessage(messageId) {
    const id = Number(messageId || 0);
    if (!id) return;
    const data = await api(`/messages/${id}/pin`, { method: "POST", body: "{}" }, "pin_message");
    if (data.message) {
      state.messages = state.messages.map((message) => Number(message.id) === id ? data.message : message);
      renderMessages();
      setStatus(data.pinned ? "Message pinned." : "Message unpinned.");
    }
  }

  function jumpToMessage(messageId) {
    const target = document.querySelector(`[data-message-id="${Number(messageId)}"]`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("is-highlighted");
      window.setTimeout(() => target.classList.remove("is-highlighted"), 1400);
    }
  }

  async function editMessage(messageId) {
    const item = state.messages.find((message) => Number(message.id) === Number(messageId));
    if (!item) return;
    const nextBody = window.prompt("Edit message", item.body || "");
    if (nextBody === null) return;
    const data = await api(`/messages/${messageId}`, { method: "PATCH", body: JSON.stringify({ body: nextBody }) }, "edit_message");
    if (data.message) {
      state.messages = state.messages.map((message) => Number(message.id) === Number(messageId) ? data.message : message);
      renderMessages();
    }
  }

  async function deleteMessage(messageId, deleteFor) {
    if (!window.confirm(deleteFor === "everyone" ? "Delete this message for everyone?" : "Remove this message from your view?")) return;
    await api(`/messages/${messageId}`, { method: "DELETE", body: JSON.stringify({ delete_for: deleteFor }) }, "delete_message");
    state.messages = state.messages.filter((message) => Number(message.id) !== Number(messageId));
    renderMessages();
    setStatus("Message deleted.");
  }

  async function forwardMessage(messageId) {
    if (!state.conversations.length) await loadConversations({ selectFirst: false });
    const choices = state.conversations.filter((item) => Number(item.conversation_id) !== Number(state.active?.conversation_id || 0));
    if (!choices.length) return setStatus("Create another conversation before forwarding.", "error");
    const names = choices.map((item, index) => `${index + 1}. ${item.title}`).join("\\n");
    const selected = Number(window.prompt(`Forward to which conversation?\\n${names}`, "1") || 0);
    const target = choices[selected - 1];
    if (!target) return;
    const data = await api(`/messages/${messageId}/forward`, { method: "POST", body: JSON.stringify({ conversation_ids: [target.conversation_id] }) }, "forward_message");
    setStatus(data.message || "Message forwarded.");
  }

  function typingSummary(names) {
    if (!names.length) return "No one is typing right now.";
    if (names.length === 1) return `${names[0]} is typing...`;
    if (names.length === 2) return `${names[0]} and ${names[1]} are typing...`;
    return `${names[0]} and ${names.length - 1} others are typing...`;
  }

  async function reportLast() {
    const last = state.messages[state.messages.length - 1];
    if (!last) return setStatus("No message is available to report.", "error");
    await api(`/messages/${last.id}/report`, { method: "POST", body: JSON.stringify({ reason: "Reported from Messenger V3" }) }, "report");
    setStatus("Report sent to moderation.");
  }

  async function blockPeer() {
    const peer = state.members.find((m) => Number(m.user_id) !== currentUserId);
    if (!peer) return setStatus("No peer is available to block.", "error");
    await api("/blocks", { method: "POST", body: JSON.stringify({ blocked_user_id: peer.user_id, reason: "Blocked from Messenger V3" }) }, "block");
    setStatus("Member blocked.");
  }

  function toggleDetails() {
    state.detailsOpen = !state.detailsOpen;
    root?.classList.toggle("details-open", state.detailsOpen);
  }

  function shortTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function relativeTime(value) {
    if (!value) return "unknown";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return "just now";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    return `${days}d ago`;
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(value > 20 * 1024 * 1024 ? 0 : 1)} MB`;
    if (value >= 1024) return `${Math.round(value / 1024)} KB`;
    return `${value || 0} B`;
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#96;");
  }

  function animateReactionButton(button, emoji = "❤️") {
    if (!button) return;
    button.classList.remove("is-popping");
    void button.offsetWidth;
    button.classList.add("is-popping");
    window.setTimeout(() => button.classList.remove("is-popping"), 360);
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
    const rect = button.getBoundingClientRect();
    const floater = document.createElement("span");
    floater.className = "pulse-reaction-float";
    floater.textContent = emoji;
    floater.style.left = `${rect.left + rect.width / 2}px`;
    floater.style.top = `${rect.top + rect.height / 2}px`;
    document.body.appendChild(floater);
    window.setTimeout(() => floater.remove(), 900);
  }

  bind();
  setMobileMode(isMobile() ? "list" : "desktop");
  sendPresenceHeartbeat();
  schedulePresenceHeartbeat();
  mobileQuery.addEventListener?.("change", () => setMobileMode(isMobile() ? (state.active ? "thread" : "list") : "desktop"));
  renderMessages();
  renderMembers();
  renderRooms();
  loadConversations();
  loadRooms();
  bindRealtimeDelivery();
})();
