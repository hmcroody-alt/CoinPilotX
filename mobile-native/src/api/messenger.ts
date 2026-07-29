import AsyncStorage from "@react-native-async-storage/async-storage";
import { File } from "expo-file-system";
import { PULSESOC_QA_MESSENGER_FIXTURES } from "./config";
import { PulseApiError, pulseApi } from "./pulseApi";

const CONVERSATION_CACHE_KEY = "pulsesoc.native.messenger.v2.conversations";
const messageCacheKey = (conversationId: number) => `pulsesoc.native.messenger.v2.messages.${conversationId}`;
const OUTBOUND_QUEUE_KEY = "pulsesoc.native.messenger.v2.outbound_queue";
const MESSENGER_API = "/api/pulse/communications/v2";
const conversationListeners = new Set<(conversation: MessengerConversation) => void>();

export const PULSE_AI_CONVERSATION_ID = -9001001;
export const PULSE_AI_USER_ID = -9001001;
export const PULSE_AI_DISPLAY_NAME = "UNDX";
export const PULSE_AI_AGENT_ID = "undx";
export const PULSE_AI_ASSISTANT_ID = "undx";
export const PULSE_AI_CONVERSATION_TYPE = "undx_intelligence";
/**
 * Presence marker for automated conversations (UNDX and friends).
 *
 * Deliberately outside the human presence vocabulary ("online" / "away" /
 * "offline"). A bot is reachable whenever the service is up, which is not the
 * same claim as "this person is at their device right now", and conflating the
 * two is precisely the fake-online problem this system exists to remove.
 */
export const ASSISTANT_PRESENCE = "assistant";

export type MessengerConversation = {
  id: number;
  conversation_id: number;
  title: string;
  name?: string;
  conversation_type?: string;
  latest_message?: string;
  last_message_preview?: string;
  last_activity_at?: string;
  updated_at?: string;
  unread_count?: number;
  avatar_url?: string;
  presence?: string;
  other_public_player_id?: string;
  public_player_id?: string;
  sender_prefix?: string;
  pinned?: boolean;
  muted?: boolean;
  typing?: boolean;
  failed?: boolean;
  delivery_status?: string;
  trust_state?: string;
  verified?: boolean;
};

export type MessengerMessage = {
  id: number;
  message_id: number;
  conversation_id: number;
  sender_id?: number;
  sender_user_id?: number;
  body?: string;
  content?: string;
  text?: string;
  type?: string;
  message_type?: string;
  media_url?: string;
  thumbnail_url?: string;
  file_size?: number;
  duration?: number;
  duration_seconds?: number;
  waveform?: number[];
  attachment_id?: number;
  attachment_ids?: number[];
  attachments?: Array<Record<string, unknown>>;
  delivery_status?: string;
  status?: string;
  created_at?: string;
  delivered_at?: string;
  seen_at?: string;
  is_mine?: boolean;
  reactions?: Record<string, number>;
  viewer_reaction?: string;
  reply_to_message_id?: number;
  reply_preview?: string;
  sender_display_name?: string;
  sender_trust_state?: string;
  forwarded?: boolean;
  edited_at?: string;
  deleted_at?: string;
  moderated_at?: string;
  moderation_state?: string;
  local_status?: "sending" | "queued" | "failed" | "sent";
  local_error?: string;
  client_message_id?: string;
};

export type MessengerPresence = {
  users?: Array<{ user_id?: number; status?: string; display_name?: string }>;
  typing?: Array<{ user_id?: number; is_typing?: boolean; display_name?: string }>;
  [key: string]: unknown;
};

export type MessengerUserSearchResult = {
  id: number;
  user_id: number;
  display_name: string;
  public_pulse_id?: string;
  public_player_id?: string;
  avatar_url?: string;
  premium?: boolean;
  premium_mark?: string;
  label?: string;
  is_self?: boolean;
};

export type DirectConversationResult = {
  ok: boolean;
  conversation_id: number;
  thread_id?: number;
  target_user_id: number;
  redirect_url?: string;
  trace_id?: string;
};

export type ConversationListResponse = {
  ok: boolean;
  conversations?: MessengerConversation[];
  items?: MessengerConversation[];
};

export type ConversationResponse = {
  ok?: boolean;
  conversation?: MessengerConversation;
  messages?: MessengerMessage[];
  items?: MessengerMessage[];
  presence?: MessengerPresence;
  typing?: Array<{ user_id?: number; is_typing?: boolean; display_name?: string }>;
  last_message_id?: number;
  poll_interval_ms?: number;
  sync_interval_ms?: number;
  quick_prompts?: string[];
  settings?: Record<string, unknown>;
  response_components?: UndxResponseComponent[];
};

/**
 * A card the server asks the client to draw.
 *
 * Two producers emit these. The V4/V5 conversational path emits the `*_card`
 * names; the UNDX agent runtime emits the second group, which carry a receipt
 * (`status`, `verification_state`, `verified`) alongside the same confirmation
 * fields. Both are accepted here and normalised into one shape by
 * `src/undx/actionCards.ts` — the client must not grow a second confirmation
 * contract, and `component` must not be compared to a literal outside that module.
 */
export type UndxResponseComponent = {
  component:
    | "confirmation_card"
    | "progress_card"
    | "draft_preview"
    | "settings_summary"
    | "conflict_resolution_card"
    | "verified_success_card"
    | "honest_failure_card"
    | "search_result_card"
    // Emitted by the agent runtime (services/undx_agent_contracts.py :: CardType).
    | "action_confirmation"
    | "action_progress"
    | "action_success_receipt"
    | "action_failure"
    | "setting_change_receipt"
    | "crypto_alert_card"
    | "relationship_change_receipt"
    | "message_draft_confirmation"
    | "search_results"
    | "unsupported_capability"
    | "permission_denied"
    | "retry_action"
    | "profile_result"
    | "content_result"
    | "conversation_result";
  // Agent receipt fields. `verified` is the only field that may be read as a claim
  // that the change actually happened; `status` alone does not imply a read-back.
  capability_id?: string;
  // Exactly the four values of `VerificationState` in
  // services/undx_agent_contracts.py, and no others. The previous union listed
  // "unverified", "pending" and "mismatch" — none of which the server has ever sent —
  // while omitting the two it does send for a change it could not confirm. Any client
  // code that narrowed on those names was therefore dead, and a card the server had
  // marked unverifiable would have fallen into whatever branch was left. The parity
  // test in src/undx/__tests__ compares this list against the Python enum.
  verification_state?:
    | "verified"
    | "verification_pending"
    | "verification_failed"
    | "impossible_to_verify";
  verified?: boolean;
  verification_detail?: string;
  title?: string;
  message?: string;
  risk?: string;
  task_id?: string;
  undo_capability_id?: string;
  /**
   * The arguments the undo capability must be invoked with.
   *
   * Never reconstruct these from the card. For a notification preference the
   * reversing call is the *same* capability with the value flipped, so replaying
   * what was just sent would re-apply the change rather than undo it. The server
   * sends this only when the undo can actually be performed, and clears it together
   * with `undo_capability_id` when it cannot.
   */
  undo_arguments?: Record<string, unknown>;
  can_undo?: boolean;
  timestamp?: string;
  canonical_resource_ids?: string[];
  idempotent_replay?: boolean;
  record_count?: number;
  records?: Array<Record<string, unknown>>;
  data?: Record<string, unknown>;
  action_name?: string;
  target?: string;
  // A preference is a boolean on the wire and a threshold is a number. These stay
  // unnarrowed and are formatted for display in one place, because `false` is a
  // meaningful before-state and a string-typed field invites a falsy check that
  // would drop it.
  current_value?: string | boolean | number | null;
  proposed_value?: string | boolean | number | null;
  risk_summary?: string;
  confirmation_id?: string;
  confirmation_token?: string;
  expires_at?: string;
  status?: string;
  value?: string | boolean | number | null;
  search_session_id?: string;
  canonical_content_id?: number;
  content_type?: "post" | "reel" | "video";
  creator_id?: number;
  preview_text?: string;
  thumbnail_or_media_reference?: string;
  created_at?: string;
  deep_link?: string;
  relevance_reason?: string;
};

export type ConversationControlSettings = Record<string, Record<string, boolean | string | number>>;

export type ConversationControlCapabilities = {
  search?: boolean;
  members?: boolean;
  shared_media?: boolean;
  message_stats?: boolean;
  pin?: boolean;
  archive?: boolean;
  mark_unread?: boolean;
  mute?: boolean;
  report?: boolean;
  block?: boolean;
  voice_call?: boolean;
  video_call?: boolean;
  effects?: boolean;
  export_chat?: boolean;
  [key: string]: boolean | string | number | undefined;
};

export type ConversationControlMember = {
  user_id?: number;
  role?: string;
  joined_at?: string;
  last_seen_at?: string;
  display_name?: string;
  avatar_url?: string;
  presence?: string;
  active_now?: boolean;
};

export type ConversationControlStats = {
  messages?: number;
  media_files?: number;
  photos?: number;
  videos?: number;
  voice?: number;
  files?: number;
  links?: number;
  storage_used_bytes?: number;
  unread?: number;
  members?: number;
  connection?: string;
  security_label?: string;
  activity_status?: string;
  muted?: boolean;
  pinned?: boolean;
  role?: string;
  [key: string]: boolean | string | number | undefined;
};

export type ConversationControlData = {
  ok?: boolean;
  conversation?: MessengerConversation & {
    member_count?: number;
    is_group?: boolean;
    is_admin?: boolean;
    viewer_role?: string;
    members?: ConversationControlMember[];
    stats?: ConversationControlStats;
    settings?: ConversationControlSettings;
    capabilities?: ConversationControlCapabilities;
    participants_preview?: ConversationControlMember[];
  };
  members?: ConversationControlMember[];
  stats?: ConversationControlStats;
  settings?: ConversationControlSettings;
  capabilities?: ConversationControlCapabilities;
  message?: string;
};

export type ConversationControlMediaItem = {
  id?: number;
  message_id?: number;
  media_type?: string;
  mime_type?: string;
  file_size_bytes?: number;
  duration_seconds?: number;
  url?: string;
  thumbnail_url?: string;
  created_at?: string;
  sender_user_id?: number;
  sender_display_name?: string;
  body_preview?: string;
};

export type ConversationControlExport = {
  conversation_id?: number;
  generated_at?: string;
  message_count?: number;
  messages?: Array<{
    id?: number;
    sender_user_id?: number;
    sender_display_name?: string;
    message_type?: string;
    body?: string;
    created_at?: string;
    edited_at?: string;
  }>;
};

export type SendMessagePayload = {
  body?: string;
  message_type?: string;
  media_url?: string;
  thumbnail_url?: string;
  file_size?: number;
  duration_seconds?: number;
  client_message_id?: string;
  local_created_at?: string;
  reply_to_message_id?: number;
  reply_preview?: string;
  media_ids?: number[];
  attachment_ids?: number[];
};

export type MediaUploadResult = {
  ok: boolean;
  media_id?: number;
  attachment_id?: number;
  media_url?: string;
  playback_url?: string;
  thumbnail_url?: string;
  download_url?: string;
  signed_url?: string;
  message_type?: string;
  type?: string;
  media_type?: string;
  file_size?: number;
  size_bytes?: number;
  media?: Record<string, unknown>;
};

export async function listConversations() {
  const data = await pulseApi<ConversationListResponse>(`${MESSENGER_API}/conversations`);
  const conversations = withQaConversations(normalizeConversations(data.conversations || data.items || []));
  await cacheConversations(conversations);
  return conversations;
}

export async function loadCachedConversations() {
  try {
    const cached = await AsyncStorage.getItem(CONVERSATION_CACHE_KEY);
    if (!cached) return withQaConversations([]);
    return withQaConversations(normalizeConversations(JSON.parse(cached) as MessengerConversation[]));
  } catch {
    await AsyncStorage.removeItem(CONVERSATION_CACHE_KEY).catch(() => undefined);
    return [];
  }
}

export async function cacheConversations(conversations: MessengerConversation[]) {
  await AsyncStorage.setItem(CONVERSATION_CACHE_KEY, JSON.stringify(conversations.slice(0, 100)));
}

export async function getConversation(conversationId: number, params: { limit?: number; beforeId?: number } = {}) {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.beforeId) query.set("before_id", String(params.beforeId));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const data = await pulseApi<ConversationResponse>(`${MESSENGER_API}/conversations/${conversationId}/messages${suffix}`);
  const messages = withQaMessages(conversationId, normalizeMessages(data.messages || data.items || [], conversationId));
  if (!params.beforeId) await cacheMessages(conversationId, messages);
  return { ...data, messages };
}

export async function getPulseAiConversation(params: { limit?: number } = {}) {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit || 80));
  const data = await pulseApi<ConversationResponse>(`/api/pulse-ai/conversation?${query.toString()}`);
  const conversation = normalizePulseAiConversation(data.conversation);
  const messages = normalizePulseAiMessages(data.messages || data.items || []);
  await cacheMessages(PULSE_AI_CONVERSATION_ID, messages);
  await upsertCachedConversation(conversation).catch(() => undefined);
  return {
    ...data,
    conversation,
    messages,
    presence: { typing: [] }
  };
}

export async function sendPulseAiMessage(payload: { body: string; client_message_id?: string; ui_context?: Record<string, unknown> }) {
  const data = await pulseApi<ConversationResponse & { reply?: string; latency_ms?: number; correlation_id?: string }>("/api/pulse-ai/message", {
    method: "POST",
    body: JSON.stringify({
      message: payload.body,
      body: payload.body,
      client_message_id: payload.client_message_id || "",
      conversation_id: PULSE_AI_CONVERSATION_ID,
      participant_id: PULSE_AI_USER_ID,
      agent_id: PULSE_AI_AGENT_ID,
      assistant_id: PULSE_AI_ASSISTANT_ID,
      conversation_type: PULSE_AI_CONVERSATION_TYPE,
      identity: PULSE_AI_DISPLAY_NAME,
      ui_context: payload.ui_context || {}
    })
  });
  const conversation = normalizePulseAiConversation(data.conversation);
  const messages = normalizePulseAiMessages(data.messages || data.items || []);
  await cacheMessages(PULSE_AI_CONVERSATION_ID, messages);
  await upsertCachedConversation(conversation).catch(() => undefined);
  return {
    ...data,
    conversation,
    messages
  };
}

export async function confirmPulseAiAction(confirmationToken: string) {
  return pulseApi<{ ok: boolean; message?: string; response_components?: UndxResponseComponent[] }>("/api/pulse-ai/actions/confirm", {
    method: "POST",
    body: JSON.stringify({ confirmation_token: confirmationToken })
  });
}

export async function cancelPulseAiAction(confirmationToken: string) {
  return pulseApi<{ ok: boolean; revoked?: boolean; message?: string }>("/api/pulse-ai/actions/cancel", {
    method: "POST",
    body: JSON.stringify({ confirmation_token: confirmationToken })
  });
}

export async function syncConversation(conversationId: number, afterId = 0) {
  if (conversationId === PULSE_AI_CONVERSATION_ID) {
    const data = await getPulseAiConversation({ limit: 80 });
    return {
      ...data,
      messages: (data.messages || []).filter((message) => message.id > afterId),
      presence: { typing: [] }
    };
  }
  const data = await pulseApi<ConversationResponse>(`${MESSENGER_API}/conversations/${conversationId}/messages?limit=80`);
  const messages = normalizeMessages(data.messages || data.items || [], conversationId)
    .filter((message) => message.id > afterId);
  return {
    ...data,
    presence: data.presence || { typing: data.typing || [] },
    messages: withQaMessages(conversationId, messages)
  };
}

export async function loadCachedMessages(conversationId: number) {
  const key = messageCacheKey(conversationId);
  try {
    const cached = await AsyncStorage.getItem(key);
    if (!cached) return withQaMessages(conversationId, []);
    return withQaMessages(conversationId, normalizeMessages(JSON.parse(cached) as MessengerMessage[], conversationId));
  } catch {
    await AsyncStorage.removeItem(key).catch(() => undefined);
    return [];
  }
}

export async function cacheMessages(conversationId: number, messages: MessengerMessage[]) {
  await AsyncStorage.setItem(messageCacheKey(conversationId), JSON.stringify(messages.slice(-200)));
}

export async function sendConversationMessage(conversationId: number, payload: SendMessagePayload) {
  if (conversationId === PULSE_AI_CONVERSATION_ID) {
    const data = await sendPulseAiMessage({ body: payload.body || "", client_message_id: payload.client_message_id });
    const serverMessage = (data.messages || []).find((message) => message.client_message_id === payload.client_message_id)
      || (data.messages || []).filter((message) => message.is_mine).slice(-1)[0];
    return { ok: true, data: serverMessage, message_id: serverMessage?.id };
  }
  const result = await pulseApi<{ ok: boolean; message?: MessengerMessage | string; data?: MessengerMessage; message_id?: number }>(`${MESSENGER_API}/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      body: payload.body || "",
      message: payload.body || "",
      text: payload.body || "",
      message_type: payload.message_type || "text",
      type: payload.message_type || "text",
      media_url: payload.media_url || "",
      thumbnail_url: payload.thumbnail_url || "",
      file_size: payload.file_size || 0,
      duration_seconds: payload.duration_seconds || 0,
      client_message_id: payload.client_message_id || "",
      local_created_at: payload.local_created_at || "",
      reply_to_message_id: payload.reply_to_message_id || 0,
      reply_preview: payload.reply_preview || "",
      media_ids: payload.media_ids || [],
      attachment_ids: payload.attachment_ids || [],
      message_attachment_ids: payload.attachment_ids || []
    })
  });
  const serverMessage = typeof result.message === "object"
    ? normalizeMessages([result.message], conversationId)[0]
    : result.data
      ? normalizeMessages([result.data], conversationId)[0]
      : undefined;
  return { ...result, data: serverMessage };
}

export async function enqueueMessengerMessage(conversationId: number, payload: SendMessagePayload) {
  const queue = await readOutboundQueue();
  const clientId = payload.client_message_id || `native-queued-${Date.now()}`;
  if (!queue.some((item) => item.payload.client_message_id === clientId)) {
    queue.push({ conversationId, payload: { ...payload, client_message_id: clientId } });
    await AsyncStorage.setItem(OUTBOUND_QUEUE_KEY, JSON.stringify(queue.slice(-100)));
  }
}

export async function drainMessengerQueue(conversationId: number) {
  const queue = await readOutboundQueue();
  const remaining: typeof queue = [];
  const sent: MessengerMessage[] = [];
  for (const item of queue) {
    if (item.conversationId !== conversationId) { remaining.push(item); continue; }
    try {
      const result = await sendConversationMessage(item.conversationId, item.payload);
      if (result.data) sent.push(result.data);
    } catch {
      remaining.push(item);
    }
  }
  await AsyncStorage.setItem(OUTBOUND_QUEUE_KEY, JSON.stringify(remaining));
  return sent;
}

async function readOutboundQueue(): Promise<Array<{ conversationId: number; payload: SendMessagePayload }>> {
  try { return JSON.parse((await AsyncStorage.getItem(OUTBOUND_QUEUE_KEY)) || "[]"); } catch { return []; }
}

export async function reactToMessage(messageId: number, reactionType = "pulse") {
  const result = await pulseApi<{ ok?: boolean; message?: MessengerMessage }>(
    `${MESSENGER_API}/messages/${messageId}/reactions`,
    {
      method: "POST",
      body: JSON.stringify({ reaction: reactionType, reaction_type: reactionType })
    }
  );
  const message = result.message ? normalizeMessages([result.message], 0)[0] : undefined;
  return {
    ...result,
    removed: !message?.viewer_reaction,
    reaction_type: message?.viewer_reaction || "",
    reactions: message?.reactions
  };
}

export async function deleteMessage(messageId: number, scope: "self" | "everyone" = "self") {
  return pulseApi<{ ok?: boolean; deleted?: boolean; message?: string }>(`${MESSENGER_API}/messages/${messageId}`, {
    method: "DELETE",
    body: JSON.stringify({ delete_for: scope })
  });
}

export async function reportMessage(messageId: number, reason = "Needs review") {
  return pulseApi<{ ok?: boolean; report_id?: number; message?: string }>(`${MESSENGER_API}/messages/${messageId}/report`, {
    method: "POST",
    body: JSON.stringify({ reason })
  });
}

export async function pinConversation(conversationId: number, pinned = true) {
  return pulseApi<{ ok?: boolean; pinned?: boolean; message?: string }>(`${MESSENGER_API}/conversations/${conversationId}/pin`, {
    method: "POST",
    body: JSON.stringify({ pinned })
  });
}

export async function markConversationSeen(conversationId: number) {
  return pulseApi<{ ok: boolean; last_read_message_id?: number }>(`${MESSENGER_API}/conversations/${conversationId}/read`, { method: "POST" });
}

export async function sendTyping(conversationId: number, typing: boolean) {
  return pulseApi<{ ok: boolean; typing?: boolean; is_typing?: boolean }>(`${MESSENGER_API}/conversations/${conversationId}/typing`, {
    method: "POST",
    body: JSON.stringify({ typing, is_typing: typing })
  });
}

export async function getConversationControlCenter(conversationId: number) {
  const data = await pulseApi<ConversationControlData>(`${MESSENGER_API}/conversations/${conversationId}/control-center`);
  return normalizeConversationControlData(data);
}

export async function updateConversationControlSetting(conversationId: number, section: string, key: string, value: boolean | string | number) {
  const data = await pulseApi<ConversationControlData>(`${MESSENGER_API}/conversations/${conversationId}/control-center`, {
    method: "PATCH",
    body: JSON.stringify({ section, key, value })
  });
  return normalizeConversationControlData(data);
}

export async function listConversationMembers(conversationId: number) {
  const data = await pulseApi<{ ok?: boolean; members?: ConversationControlMember[] }>(`${MESSENGER_API}/conversations/${conversationId}/members`);
  return data.members || [];
}

export async function listConversationControlMedia(conversationId: number, kind = "all", limit = 60) {
  const query = new URLSearchParams({ kind, limit: String(limit) });
  return pulseApi<{ ok?: boolean; items?: ConversationControlMediaItem[]; count?: number; kind?: string }>(
    `${MESSENGER_API}/conversations/${conversationId}/control-center/media?${query.toString()}`
  );
}

export async function listConversationControlLinks(conversationId: number, limit = 80) {
  return pulseApi<{ ok?: boolean; items?: Array<Record<string, unknown>>; count?: number }>(
    `${MESSENGER_API}/conversations/${conversationId}/control-center/links?limit=${encodeURIComponent(String(limit))}`
  );
}

export async function listConversationPinnedMessages(conversationId: number, limit = 50) {
  return pulseApi<{ ok?: boolean; items?: MessengerMessage[]; count?: number }>(
    `${MESSENGER_API}/conversations/${conversationId}/control-center/pins?limit=${encodeURIComponent(String(limit))}`
  );
}

export async function exportConversationControlData(conversationId: number) {
  return pulseApi<{ ok?: boolean; export?: ConversationControlExport; filename?: string; conversation?: MessengerConversation }>(
    `${MESSENGER_API}/conversations/${conversationId}/control-center/export`
  );
}

export async function runConversationControlAction(conversationId: number, action: string, body?: string) {
  return pulseApi<{ ok?: boolean; message?: string; settings?: ConversationControlSettings; conversation_id?: number }>(
    `${MESSENGER_API}/conversations/${conversationId}/control-center/action`,
    {
      method: "POST",
      body: JSON.stringify(body ? { action, body } : { action })
    }
  );
}

export async function muteConversation(conversationId: number) {
  return pulseApi<{ ok?: boolean; muted?: boolean; muted_until?: string; message?: string }>(`${MESSENGER_API}/conversations/${conversationId}/mute`, {
    method: "POST",
    body: "{}"
  });
}

export async function archiveConversation(conversationId: number) {
  return pulseApi<{ ok?: boolean; archived?: boolean; message?: string }>(`${MESSENGER_API}/conversations/${conversationId}/archive`, {
    method: "POST",
    body: "{}"
  });
}

export async function markConversationUnread(conversationId: number) {
  return pulseApi<{ ok?: boolean; unread_count?: number; message?: string }>(`${MESSENGER_API}/conversations/${conversationId}/unread`, {
    method: "POST",
    body: "{}"
  });
}

export async function searchMessenger(query: string) {
  const encoded = encodeURIComponent(query);
  const [messageData, peopleData] = await Promise.all([
    pulseApi<{ ok: boolean; messages?: MessengerMessage[]; items?: MessengerMessage[] }>(`${MESSENGER_API}/search?q=${encoded}`),
    pulseApi<{ ok: boolean; people?: MessengerUserSearchResult[]; items?: MessengerUserSearchResult[] }>(`${MESSENGER_API}/people/search?q=${encoded}`)
  ]);
  return {
    conversations: [],
    messages: normalizeMessages(messageData.messages || messageData.items || [], 0),
    users: (peopleData.people || peopleData.items || []).map(normalizeMessengerUser).filter((item) => item.user_id > 0 && !item.is_self)
  };
}

export async function searchConversationMessages(conversationId: number, query: string) {
  const clean = query.trim();
  if (!clean) return [];
  const encoded = encodeURIComponent(clean);
  const data = await pulseApi<{ ok: boolean; messages?: MessengerMessage[]; items?: MessengerMessage[] }>(
    `${MESSENGER_API}/search?q=${encoded}&conversation_id=${encodeURIComponent(String(conversationId))}&limit=50`
  );
  return normalizeMessages(data.messages || data.items || [], conversationId);
}

export async function searchMessengerUsers(query: string) {
  const clean = query.trim();
  if (!clean) return [];
  const data = await pulseApi<{ ok: boolean; people?: MessengerUserSearchResult[]; items?: MessengerUserSearchResult[] }>(
    `${MESSENGER_API}/people/search?q=${encodeURIComponent(clean)}`
  );
  return (data.people || data.items || []).map(normalizeMessengerUser).filter((item) => item.user_id > 0 && !item.is_self);
}

const directConversationRequests = new Map<number, Promise<DirectConversationResult>>();

export async function openDirectConversation(target: MessengerUserSearchResult) {
  const targetUserId = Number(target.user_id || target.id || 0);
  if (targetUserId <= 0) throw new Error("Choose a valid PulseSoc recipient.");
  const active = directConversationRequests.get(targetUserId);
  if (active) return active;

  const request = pulseApi<DirectConversationResult>(`${MESSENGER_API}/direct/open`, {
    method: "POST",
    body: JSON.stringify({ target_user_id: targetUserId })
  }).then(async (result) => {
    const conversationId = Number(result.conversation_id || 0);
    if (conversationId <= 0) throw new Error("PulseSoc did not return a conversation.");
    await upsertCachedConversation({
      id: conversationId,
      conversation_id: conversationId,
      title: target.display_name,
      conversation_type: "direct",
      avatar_url: target.avatar_url || "",
      other_public_player_id: target.public_player_id || target.public_pulse_id?.replace(/^@/, "") || "",
      verified: Boolean(target.premium_mark),
      trust_state: target.premium ? "premium" : ""
    });
    return { ...result, conversation_id: conversationId, target_user_id: targetUserId };
  }).finally(() => {
    directConversationRequests.delete(targetUserId);
  });

  directConversationRequests.set(targetUserId, request);
  return request;
}

export async function upsertCachedConversation(conversation: MessengerConversation) {
  const normalized = normalizeConversations([conversation])[0];
  if (!normalized) return;
  const cached = await loadCachedConversations();
  await cacheConversations([normalized, ...cached.filter((item) => item.id !== normalized.id)]);
  conversationListeners.forEach((listener) => listener(normalized));
}

export function subscribeConversationUpdates(listener: (conversation: MessengerConversation) => void) {
  conversationListeners.add(listener);
  return () => {
    conversationListeners.delete(listener);
  };
}

function normalizeConversationControlData(data: ConversationControlData): ConversationControlData {
  const rawConversation = data.conversation ? normalizeConversations([data.conversation])[0] : undefined;
  const conversation = rawConversation ? { ...data.conversation, ...rawConversation } : data.conversation;
  const stats = data.stats || conversation?.stats || {};
  const settings = data.settings || conversation?.settings || {};
  const capabilities = data.capabilities || conversation?.capabilities || {};
  const members = data.members || conversation?.members || conversation?.participants_preview || [];
  return { ...data, conversation, stats, settings, capabilities, members };
}

export async function updateCachedConversationPreview(conversationId: number, preview: string, timestamp = new Date().toISOString()) {
  const cached = await loadCachedConversations();
  const existing = cached.find((item) => item.id === conversationId);
  if (!existing) return;
  await upsertCachedConversation({
    ...existing,
    latest_message: preview,
    last_message_preview: preview,
    last_activity_at: timestamp,
    updated_at: timestamp,
    failed: false,
    delivery_status: "sent"
  });
}

export async function uploadMessengerMedia(input: {
  conversationId: number;
  uri: string;
  name: string;
  mimeType: string;
  sizeBytes?: number;
  voice?: boolean;
  durationSeconds?: number;
}) {
  if (input.conversationId === PULSE_AI_CONVERSATION_ID) {
    throw new PulseApiError("UNDX supports text conversation in native chat right now. Remove the attachment and send a message.", 400, "pulse_ai_text_only");
  }
  const mimeType = messengerFoundationMimeType(input.mimeType, input.name, input.voice);
  const mediaType = messengerFoundationMediaType(input.name, mimeType, input.voice);
  const sizeBytes = resolveLocalMessengerFileSize(input.uri, input.sizeBytes);
  if (sizeBytes <= 0) {
    throw new PulseApiError(
      input.voice
        ? "PulseSoc could not read this recording. Record the voice message again."
        : "PulseSoc could not read this attachment. Choose the file again.",
      400,
      "local_file_size_unavailable"
    );
  }
  const init = await pulseApi<{ ok?: boolean; attachment_id?: number }>("/api/messages/media/init", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: input.conversationId,
      media_type: mediaType,
      filename: input.name || `pulse-${mediaType}-${Date.now()}`,
      mime_type: mimeType,
      size_bytes: sizeBytes
    })
  });
  const attachmentId = Number(init.attachment_id || 0);
  if (!attachmentId) throw new PulseApiError("Media upload did not return an attachment id.", 502, "attachment_init_failed");

  const form = new FormData();
  form.append("attachment_id", String(attachmentId));
  if (input.durationSeconds) {
    const durationMs = Math.max(1, Math.round(input.durationSeconds * 1000));
    form.append("duration_ms", String(durationMs));
    form.append("duration_seconds", String(input.durationSeconds));
  }
  form.append("file", {
    uri: input.uri,
    name: input.name,
    type: mimeType
  } as unknown as Blob);
  await pulseApi<MediaUploadResult>("/api/messages/media/upload", {
    method: "POST",
    body: form
  });
  const completed = await pulseApi<MediaUploadResult>("/api/messages/media/complete", {
    method: "POST",
    body: JSON.stringify({
      attachment_id: attachmentId,
      duration_ms: input.durationSeconds ? Math.max(1, Math.round(input.durationSeconds * 1000)) : "",
      width: "",
      height: "",
      waveform_json: ""
    })
  });
  const downloadUrl = String(completed.download_url || `/api/messages/media/${attachmentId}/download`);
  return {
    ...completed,
    attachment_id: attachmentId,
    media_id: Number(completed.media_id || 0),
    media_url: String(completed.signed_url || completed.media_url || downloadUrl),
    playback_url: String(completed.playback_url || completed.signed_url || downloadUrl),
    thumbnail_url: String(completed.thumbnail_url || ""),
    download_url: downloadUrl,
    message_type: input.voice ? "voice" : mediaType === "photo" ? "image" : mediaType,
    type: input.voice ? "voice" : mediaType === "photo" ? "image" : mediaType,
    file_size: Number(completed.file_size || completed.size_bytes || sizeBytes)
  };
}

export function resolveLocalMessengerFileSize(uri: string, declaredSize?: number) {
  const provided = Math.max(0, Number(declaredSize || 0));
  if (provided > 0) return provided;
  try {
    const file = new File(uri);
    return file.exists ? Math.max(0, Number(file.size || 0)) : 0;
  } catch {
    return 0;
  }
}

export function isRetryableMessengerSendError(error: unknown) {
  if (!(error instanceof PulseApiError)) return false;
  if (error.status === 0 || error.status === 408 || error.status >= 500) return true;
  return ["request_unreachable", "timeout", "network_error", "service_unavailable"].includes(String(error.code || ""));
}

function messengerFoundationMediaType(name: string, mimeType: string, voice?: boolean) {
  if (voice) return "voice";
  const normalized = String(mimeType || "").toLowerCase();
  const lowerName = String(name || "").toLowerCase();
  if (normalized.startsWith("image/") || /\.(jpg|jpeg|png|gif|webp|avif)$/i.test(lowerName)) return "photo";
  if (normalized.startsWith("video/") || /\.(mp4|mov|m4v|webm)$/i.test(lowerName)) return "video";
  if (normalized.startsWith("audio/") || /\.(mp3|m4a|wav|ogg|webm|aac)$/i.test(lowerName)) return "voice";
  return "file";
}

function messengerFoundationMimeType(mimeType: string, name: string, voice?: boolean) {
  const provided = String(mimeType || "").split(";", 1)[0].toLowerCase();
  if (voice) {
    if (provided === "audio/x-m4a" || provided === "audio/m4a" || provided === "audio/mp4a-latm" || provided === "application/x-m4a") return "audio/mp4";
    if (provided === "application/octet-stream") return "audio/mp4";
    if (provided) return provided;
  }
  if (provided) return provided;
  const ext = String(name || "").split(".").pop()?.toLowerCase() || "";
  if (ext === "jpg" || ext === "jpeg") return "image/jpeg";
  if (ext === "png") return "image/png";
  if (ext === "gif") return "image/gif";
  if (ext === "webp") return "image/webp";
  if (ext === "mp4") return "video/mp4";
  if (ext === "mov") return "video/quicktime";
  if (ext === "m4a" || voice) return "audio/mp4";
  if (ext === "mp3") return "audio/mpeg";
  if (ext === "wav") return "audio/wav";
  if (ext === "ogg") return "audio/ogg";
  if (ext === "aac") return "audio/aac";
  return "application/octet-stream";
}

export function normalizeConversations(items: MessengerConversation[]) {
  const normalized = items
    .map((item) => {
      const id = Number(item.conversation_id || item.id || 0);
      const raw = item as MessengerConversation & {
        display_name?: unknown;
        last_message?: unknown;
        presence?: unknown;
        type?: unknown;
      };
      return {
        ...item,
        id,
        conversation_id: id,
        title: safeText(item.title) || safeText(item.name) || safeText(raw.display_name) || `Conversation ${id}`,
        name: safeText(item.name),
        conversation_type: safeText(item.conversation_type) || safeText(raw.type) || "direct",
        latest_message: normalizeMessengerPreview(messageText(item.latest_message || raw.last_message)),
        last_message_preview: normalizeMessengerPreview(messageText(item.last_message_preview)),
        last_activity_at: safeText(item.last_activity_at),
        updated_at: safeText(item.updated_at),
        avatar_url: safeText(item.avatar_url),
        presence: normalizePresence(raw.presence),
        trust_state: safeText(item.trust_state),
        delivery_status: safeText(item.delivery_status),
        unread_count: Number(item.unread_count || 0),
        pinned: Boolean(item.pinned),
        muted: Boolean(item.muted),
        typing: Boolean(item.typing),
        failed: Boolean(item.failed),
        verified: Boolean(item.verified)
      };
    })
    .filter((item) => item.id > 0 || item.id === PULSE_AI_CONVERSATION_ID);
  const byId = new Map<number, MessengerConversation>();
  normalized.forEach((item) => {
    const current = byId.get(item.id);
    if (!current || conversationSortTime(item) >= conversationSortTime(current)) byId.set(item.id, item);
  });
  return Array.from(byId.values()).sort((a, b) => conversationSortTime(b) - conversationSortTime(a));
}

function normalizePulseAiConversation(item?: MessengerConversation): MessengerConversation {
  const now = new Date().toISOString();
  const normalized = normalizeConversations([
    {
      ...(item || {}),
      id: PULSE_AI_CONVERSATION_ID,
      conversation_id: PULSE_AI_CONVERSATION_ID,
      title: PULSE_AI_DISPLAY_NAME,
      name: PULSE_AI_DISPLAY_NAME,
      conversation_type: item?.conversation_type || PULSE_AI_CONVERSATION_TYPE,
      latest_message: item?.latest_message || item?.last_message_preview || "Message UNDX",
      last_message_preview: item?.last_message_preview || item?.latest_message || "Message UNDX",
      last_activity_at: item?.last_activity_at || item?.updated_at || now,
      updated_at: item?.updated_at || now,
      // UNDX is a service, not a person. It carries the dedicated "assistant"
      // marker rather than a human presence value so it never flows through
      // the human online/last-seen renderer. Presence for real people is only
      // ever supplied by the server's unified presence service.
      presence: ASSISTANT_PRESENCE,
      pinned: true,
      trust_state: "intelligence",
      verified: true
    }
  ])[0];
  return normalized || {
    id: PULSE_AI_CONVERSATION_ID,
    conversation_id: PULSE_AI_CONVERSATION_ID,
    title: PULSE_AI_DISPLAY_NAME,
    name: PULSE_AI_DISPLAY_NAME,
    conversation_type: PULSE_AI_CONVERSATION_TYPE,
    latest_message: "Message UNDX",
    last_message_preview: "Message UNDX",
    last_activity_at: now,
    updated_at: now,
    presence: ASSISTANT_PRESENCE,
    pinned: true,
    trust_state: "intelligence",
    verified: true
  };
}

function normalizePulseAiMessages(items: MessengerMessage[]) {
  return normalizeMessages(items, PULSE_AI_CONVERSATION_ID).map((message) => {
    const mine = Boolean(message.is_mine);
    return {
      ...message,
      conversation_id: PULSE_AI_CONVERSATION_ID,
      sender_user_id: mine ? message.sender_user_id : PULSE_AI_USER_ID,
      sender_id: mine ? message.sender_id : PULSE_AI_USER_ID,
      sender_display_name: mine ? message.sender_display_name || "You" : PULSE_AI_DISPLAY_NAME,
      sender_trust_state: mine ? message.sender_trust_state : "intelligence"
    };
  });
}

function normalizeMessengerUser(item: MessengerUserSearchResult): MessengerUserSearchResult {
  const id = Number(item.user_id || item.id || 0);
  return {
    ...item,
    id,
    user_id: id,
    display_name: String(item.display_name || item.public_pulse_id || item.public_player_id || "PulseSoc member"),
    public_pulse_id: String(item.public_pulse_id || (item.public_player_id ? `@${item.public_player_id}` : "")),
    public_player_id: String(item.public_player_id || item.public_pulse_id || "").replace(/^@/, ""),
    avatar_url: String(item.avatar_url || ""),
    premium: Boolean(item.premium),
    is_self: Boolean(item.is_self)
  };
}

export function normalizeMessages(items: MessengerMessage[], fallbackConversationId: number) {
  return items
    .map((item) => {
      const id = Number(item.message_id || item.id || 0);
      const messageType = safeText(item.message_type) || safeText(item.type) || "text";
      const body = normalizeMessengerBody(messageText(item.body || item.content || item.text), messageType);
      const attachment = firstAttachment(item);
      return {
        ...item,
        id,
        message_id: id,
        conversation_id: Number(item.conversation_id || fallbackConversationId || 0),
        body,
        message_type: messageType,
        delivery_status: safeText(item.delivery_status) || safeText(item.status) || safeText(item.local_status) || "sent",
        file_size: Number(item.file_size || 0),
        duration_seconds: Number(item.duration_seconds || item.duration || attachment?.duration_seconds || attachment?.duration || 0),
        waveform: normalizeVoiceWaveform(item.waveform || attachment?.waveform || attachment?.waveform_json),
        attachment_id: Number(item.attachment_id || attachment?.attachment_id || attachment?.id || 0) || undefined,
        reactions: normalizeReactionCounts(item.reactions),
        viewer_reaction: safeText(item.viewer_reaction) || safeText((item as MessengerMessage & { my_reaction?: string }).my_reaction),
        media_url: safeText(item.media_url) || attachmentValue(item, "url") || attachmentValue(item, "cdn_url") || attachmentValue(item, "playback_url"),
        thumbnail_url: safeText(item.thumbnail_url) || attachmentValue(item, "thumbnail_url"),
        reply_preview: messageText(item.reply_preview),
        sender_display_name: safeText(item.sender_display_name),
        sender_trust_state: safeText(item.sender_trust_state),
        created_at: safeText(item.created_at),
        edited_at: safeText(item.edited_at),
        deleted_at: safeText(item.deleted_at),
        moderated_at: safeText(item.moderated_at),
        moderation_state: safeText(item.moderation_state),
        reply_to_message_id: Number(item.reply_to_message_id || 0) || undefined
      };
    })
    .filter((item) => item.id > 0);
}

function safeText(value: unknown) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function normalizeMessengerBody(value: string, messageType?: string) {
  if (isVoiceMessageType(messageType) && isTechnicalVoiceValue(value)) return "";
  return value;
}

function normalizeMessengerPreview(value: string) {
  return isTechnicalVoiceValue(value) ? "Voice message" : value;
}

function isVoiceMessageType(value?: string) {
  return ["voice", "audio", "voice_message", "audio_message"].includes(String(value || "").toLowerCase());
}

function isGeneratedVoiceFilename(value?: string) {
  return /^pulsesoc[-_ ]voice[-_]\d+\.(m4a|mp4|aac|mp3|wav|webm|ogg)$/i.test(String(value || "").trim());
}

function isTechnicalVoiceValue(value?: string) {
  const normalized = String(value || "").trim();
  if (!normalized) return false;
  return isGeneratedVoiceFilename(normalized)
    || /^(?:file:\/\/|https?:\/\/|\/).+/i.test(normalized)
    || /^[^\s/\\]+\.(m4a|mp4|aac|mp3|wav|webm|ogg)$/i.test(normalized)
    || /^(?:storage|object|media)[-_ ]?(?:key|id)\s*[:=]/i.test(normalized);
}

function normalizeVoiceWaveform(value: unknown) {
  let candidate = value;
  if (typeof candidate === "string") {
    try { candidate = JSON.parse(candidate); } catch { candidate = []; }
  }
  if (!Array.isArray(candidate)) return [];
  return candidate.slice(0, 80).map((level) => {
    const numeric = Number(level || 0);
    return Math.max(0, Math.min(1, numeric > 1 ? numeric / 100 : numeric));
  });
}

function firstAttachment(item: MessengerMessage) {
  return Array.isArray(item.attachments) && item.attachments[0] ? item.attachments[0] : undefined;
}

function messageText(value: unknown) {
  const direct = safeText(value);
  if (direct) return direct;
  if (!value || typeof value !== "object") return "";
  const record = value as Record<string, unknown>;
  return safeText(record.body) || safeText(record.content) || safeText(record.text) || safeText(record.preview) || "";
}

function normalizePresence(value: unknown) {
  const direct = safeText(value).toLowerCase();
  if (direct) return direct === ASSISTANT_PRESENCE ? ASSISTANT_PRESENCE : normalizePresenceToken(direct);
  if (!value || typeof value !== "object") return "";
  const record = value as Record<string, unknown>;
  // A privacy-restricted or invisible user is reported by the server as
  // available:false. Treat that as "no presence at all" rather than falling
  // through to a status field, so hidden users render exactly like users we
  // simply have no information about.
  if (record.available === false) return "";
  const token = (safeText(record.status) || safeText(record.presence) || safeText(record.state)).toLowerCase();
  return normalizePresenceToken(token);
}

/**
 * Collapse whatever the server said into the vocabulary this client renders.
 *
 * Anything unrecognised becomes "" (unknown), never an online-ish value. That
 * asymmetry is deliberate: the failure mode of guessing wrong must be showing
 * a live user as offline, not showing an offline user as live.
 */
function normalizePresenceToken(token: string) {
  if (!token) return "";
  if (token === "online" || token === "away" || token === "offline") return token;
  return "";
}

function conversationSortTime(item: Pick<MessengerConversation, "last_activity_at" | "updated_at">) {
  const value = Date.parse(item.last_activity_at || item.updated_at || "");
  return Number.isFinite(value) ? value : 0;
}

function normalizeReactionCounts(input: unknown) {
  if (Array.isArray(input)) {
    return input.reduce<Record<string, number>>((next, item) => {
      if (!item || typeof item !== "object") return next;
      const reaction = item as { reaction_type?: string; count?: number };
      const key = String(reaction.reaction_type || "");
      const count = Number(reaction.count || 0);
      if (key && count > 0) next[key] = count;
      return next;
    }, {});
  }
  return Object.entries((input || {}) as Record<string, number>).reduce<Record<string, number>>((next, [key, value]) => {
    const count = Number(value || 0);
    if (count > 0) next[key] = count;
    return next;
  }, {});
}

function attachmentValue(item: MessengerMessage, key: string) {
  const attachment = firstAttachment(item);
  return String(attachment?.[key] || "");
}

function withQaConversations(conversations: MessengerConversation[]) {
  if (!PULSESOC_QA_MESSENGER_FIXTURES) return conversations;
  const existing = new Set(conversations.map((item) => item.id));
  const fixtures = qaConversations().filter((item) => !existing.has(item.id));
  return [...fixtures, ...conversations].slice(0, 80);
}

function withQaMessages(conversationId: number, messages: MessengerMessage[]) {
  if (!PULSESOC_QA_MESSENGER_FIXTURES) return messages;
  const fixtures = qaMessages(conversationId);
  if (!fixtures.length) return messages;
  const existing = new Set(messages.map((item) => item.id));
  return [...fixtures.filter((item) => !existing.has(item.id)), ...messages].sort((a, b) => a.id - b.id);
}

function qaConversations(): MessengerConversation[] {
  const now = Date.now();
  return [
    qaConversation(9001, "Roody Cherie", "Reviewing the new Pulse Command shell.", now - 40_000, { unread_count: 4, pinned: true, presence: "online", verified: true, trust_state: "founder" }),
    qaConversation(9002, "UNDX", "I found three high-signal updates for your network.", now - 130_000, { unread_count: 2, presence: "active", verified: true, conversation_type: "intelligence" }),
    qaConversation(9003, "Creator Operations", "Maria: The scheduled room is live in 15 minutes.", now - 420_000, { conversation_type: "group", sender_prefix: "Maria", typing: true }),
    qaConversation(9004, "Safety Review", "Report queued and awaiting moderation decision.", now - 1_800_000, { muted: true, conversation_type: "safety" }),
    qaConversation(9005, "Media Upload QA", "Video attachment failed. Tap to retry.", now - 2_400_000, { failed: true, delivery_status: "failed" }),
    qaConversation(9006, "No Avatar Long Name QA Contact With Wrapping", "Long names stay readable without covering badges.", now - 3_600_000)
  ];
}

function qaConversation(id: number, title: string, preview: string, timestamp: number, extra: Partial<MessengerConversation> = {}): MessengerConversation {
  return normalizeConversations([
    {
      id,
      conversation_id: id,
      title,
      latest_message: preview,
      last_activity_at: new Date(timestamp).toISOString(),
      conversation_type: extra.conversation_type || "direct",
      ...extra
    }
  ])[0];
}

function qaMessages(conversationId: number): MessengerMessage[] {
  if (![9001, 9002, 9003, 9004, 9005, 9006].includes(conversationId)) return [];
  const base = Date.now() - 1000 * 60 * 45;
  const common = [
    qaMessage(conversationId, 1, "incoming", "Pulse Command now has populated local QA states for simulator review.", base, { reactions: { pulse: 2 }, sender_display_name: "Maria Cherie" }),
    qaMessage(conversationId, 2, "outgoing", "Good. Keep the backend authoritative and make the interface feel alive.", base + 60_000, { delivery_status: "read", seen_at: new Date(base + 80_000).toISOString() }),
    qaMessage(conversationId, 3, "incoming", "Reply, reaction, report, delete, and media states should all be visible.", base + 130_000, { reply_to_message_id: 2, reply_preview: "Keep the backend authoritative..." }),
    qaMessage(conversationId, 4, "outgoing", "Testing a failed retry state.", base + 180_000, { delivery_status: "failed", local_status: "failed", local_error: "QA simulated network failure." }),
    qaMessage(conversationId, 5, "incoming", "Image attachment preview", base + 240_000, { message_type: "image", media_url: "/static/img/pulsesoc_logo.png", thumbnail_url: "/static/img/pulsesoc_logo.png", sender_display_name: "Media QA" }),
    qaMessage(conversationId, 6, "incoming", "pulsesoc-voice-1784432743856.m4a", base + 300_000, { message_type: "voice", media_url: "/static/sounds/notification-soft.wav", duration_seconds: 12, waveform: [0.2, 0.48, 0.72, 0.35, 0.9, 0.56, 0.28, 0.66, 0.42, 0.78], sender_display_name: "Voice QA" }),
    qaMessage(conversationId, 7, "incoming", "This moderated sample keeps the UI safe when content is unavailable.", base + 360_000, { moderation_state: "moderated", moderated_at: new Date(base + 360_000).toISOString() }),
    qaMessage(conversationId, 9, "incoming", "Short.", base + 380_000, { sender_display_name: "Maria Cherie" }),
    qaMessage(conversationId, 10, "outgoing", "This is a long multiline PulseSoc message used to verify bubble width, wrapping, bottom anchoring, timestamps, and readable spacing across compact and Pro Max layouts.\nThe second line must remain inside the same production-shaped bubble.", base + 400_000, { delivery_status: "read", seen_at: new Date(base + 410_000).toISOString() }),
    qaMessage(conversationId, 11, "incoming", "https://pulsesoc.com/pulse/messages", base + 420_000, { sender_display_name: "Link QA" }),
    qaMessage(conversationId, 12, "incoming", "✨", base + 440_000, { sender_display_name: "Emoji QA" }),
    qaMessage(conversationId, 13, "incoming", "PulseSoc system notice", base + 460_000, { message_type: "system", sender_display_name: "PulseSoc" }),
    qaMessage(conversationId, 14, "incoming", "Video attachment preview", base + 480_000, { message_type: "video", media_url: "/static/uploads/pulse_media/qa-video.mp4", sender_display_name: "Media QA" }),
    qaMessage(conversationId, 15, "incoming", "Pulse Command QA document", base + 500_000, { message_type: "file", media_url: "/static/llms.txt", file_size: 2048, sender_display_name: "Document QA" }),
    qaMessage(conversationId, 16, "outgoing", "", base + 520_000, { message_type: "audio_message", media_url: "/static/sounds/notification-soft.wav", duration_seconds: 1, delivery_status: "read", seen_at: new Date(base + 530_000).toISOString() })
  ];
  if (conversationId === 9002) {
    common.push(qaMessage(conversationId, 8, "incoming", "UNDX is ready to summarize your next creator, safety, or commerce signal.", base + 420_000, { sender_display_name: "UNDX", sender_trust_state: "intelligence", reactions: { spark: 1 } }));
  }
  return common;
}

function qaMessage(
  conversationId: number,
  index: number,
  direction: "incoming" | "outgoing",
  body: string,
  timestamp: number,
  extra: Partial<MessengerMessage> = {}
): MessengerMessage {
  return normalizeMessages([
    {
      id: conversationId * 100 + index,
      message_id: conversationId * 100 + index,
      conversation_id: conversationId,
      body,
      is_mine: direction === "outgoing",
      created_at: new Date(timestamp).toISOString(),
      delivery_status: "sent",
      ...extra
    }
  ], conversationId)[0];
}

export function createLocalMessage(conversationId: number, body: string, messageType = "text"): MessengerMessage {
  const now = new Date().toISOString();
  const localId = -Date.now();
  return {
    id: localId,
    message_id: localId,
    conversation_id: conversationId,
    body,
    message_type: messageType,
    delivery_status: "sending",
    local_status: "sending",
    created_at: now,
    is_mine: true,
    client_message_id: `native-${Math.abs(localId)}`
  };
}
