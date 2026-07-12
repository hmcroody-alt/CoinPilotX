import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSESOC_QA_MESSENGER_FIXTURES } from "./config";
import { pulseApi } from "./pulseApi";

const CONVERSATION_CACHE_KEY = "pulsesoc.native.messenger.conversations";
const messageCacheKey = (conversationId: number) => `pulsesoc.native.messenger.messages.${conversationId}`;

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
  local_status?: "sending" | "failed" | "sent";
  local_error?: string;
  client_message_id?: string;
};

export type MessengerPresence = {
  users?: Array<{ user_id?: number; status?: string; display_name?: string }>;
  typing?: Array<{ user_id?: number; is_typing?: boolean; display_name?: string }>;
  [key: string]: unknown;
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
  last_message_id?: number;
  poll_interval_ms?: number;
  sync_interval_ms?: number;
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
};

export type MediaUploadResult = {
  ok: boolean;
  media_url?: string;
  thumbnail_url?: string;
  message_type?: string;
  type?: string;
  file_size?: number;
  media?: Record<string, unknown>;
};

export async function listConversations() {
  const data = await pulseApi<ConversationListResponse>("/api/pulse/messages/conversations");
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
  const data = await pulseApi<ConversationResponse>(`/api/pulse/messages/${conversationId}/messages${suffix}`);
  const messages = withQaMessages(conversationId, normalizeMessages(data.messages || data.items || [], conversationId));
  if (!params.beforeId) await cacheMessages(conversationId, messages);
  return { ...data, messages };
}

export async function syncConversation(conversationId: number, afterId = 0) {
  const data = await pulseApi<ConversationResponse>(`/api/pulse/messages/${conversationId}/sync?after_id=${afterId}&limit=80`);
  return {
    ...data,
    messages: withQaMessages(conversationId, normalizeMessages(data.messages || [], conversationId))
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
  return pulseApi<{ ok: boolean; message?: string; data?: MessengerMessage; message_id?: number }>(`/api/pulse/messages/${conversationId}/send`, {
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
      reply_preview: payload.reply_preview || ""
    })
  });
}

export async function reactToMessage(messageId: number, reactionType = "pulse") {
  return pulseApi<{ ok?: boolean; removed?: boolean; reaction_type?: string; reactions?: Record<string, number> }>(
    `/api/pulse/messages/${messageId}/react`,
    {
      method: "POST",
      body: JSON.stringify({ reaction_type: reactionType })
    }
  );
}

export async function deleteMessage(messageId: number, scope: "self" | "everyone" = "self") {
  return pulseApi<{ ok?: boolean; deleted?: boolean; message?: string }>(`/api/pulse/messages/${messageId}/delete`, {
    method: "POST",
    body: JSON.stringify({ scope })
  });
}

export async function reportMessage(messageId: number, reason = "Needs review") {
  return pulseApi<{ ok?: boolean; report_id?: number; message?: string }>(`/api/pulse/messages/${messageId}/report`, {
    method: "POST",
    body: JSON.stringify({ reason })
  });
}

export async function pinConversation(conversationId: number, pinned = true) {
  return pulseApi<{ ok?: boolean; pinned?: boolean; message?: string }>(`/api/pulse/messages/${conversationId}/pin`, {
    method: "POST",
    body: JSON.stringify({ pinned })
  });
}

export async function markConversationSeen(conversationId: number) {
  return pulseApi<{ ok: boolean; last_read_message_id?: number }>(`/api/pulse/messages/${conversationId}/seen`, { method: "POST" });
}

export async function sendTyping(conversationId: number, typing: boolean) {
  return pulseApi<{ ok: boolean; typing: boolean }>(`/api/pulse/messages/${conversationId}/typing`, {
    method: "POST",
    body: JSON.stringify({ typing, is_typing: typing })
  });
}

export async function searchMessenger(query: string) {
  const data = await pulseApi<{ ok: boolean; conversations?: MessengerConversation[]; messages?: MessengerMessage[]; users?: Array<Record<string, unknown>> }>(
    `/api/pulse/messages/search?q=${encodeURIComponent(query)}`
  );
  return {
    conversations: normalizeConversations(data.conversations || []),
    messages: normalizeMessages(data.messages || [], 0),
    users: data.users || []
  };
}

export async function uploadMessengerMedia(input: {
  conversationId: number;
  uri: string;
  name: string;
  mimeType: string;
  voice?: boolean;
  durationSeconds?: number;
}) {
  const form = new FormData();
  form.append("conversation_id", String(input.conversationId));
  if (input.voice) form.append("voice", "true");
  if (input.durationSeconds) form.append("duration_seconds", String(input.durationSeconds));
  form.append("file", {
    uri: input.uri,
    name: input.name,
    type: input.mimeType
  } as unknown as Blob);
  return pulseApi<MediaUploadResult>("/api/pulse/messages/media/upload", {
    method: "POST",
    body: form
  });
}

export function normalizeConversations(items: MessengerConversation[]) {
  return items
    .map((item) => {
      const id = Number(item.conversation_id || item.id || 0);
      return {
        ...item,
        id,
        conversation_id: id,
        title: item.title || item.name || `Conversation ${id}`,
        unread_count: Number(item.unread_count || 0),
        pinned: Boolean(item.pinned),
        muted: Boolean(item.muted),
        typing: Boolean(item.typing),
        failed: Boolean(item.failed),
        verified: Boolean(item.verified)
      };
    })
    .filter((item) => item.id > 0);
}

export function normalizeMessages(items: MessengerMessage[], fallbackConversationId: number) {
  return items
    .map((item) => {
      const id = Number(item.message_id || item.id || 0);
      return {
        ...item,
        id,
        message_id: id,
        conversation_id: Number(item.conversation_id || fallbackConversationId || 0),
        body: item.body || item.content || item.text || "",
        message_type: item.message_type || item.type || "text",
        delivery_status: item.delivery_status || item.status || item.local_status || "sent",
        file_size: Number(item.file_size || 0),
        duration_seconds: Number(item.duration_seconds || item.duration || 0),
        reactions: normalizeReactionCounts(item.reactions || {}),
        reply_to_message_id: Number(item.reply_to_message_id || 0) || undefined
      };
    })
    .filter((item) => item.id > 0);
}

function normalizeReactionCounts(input: Record<string, number>) {
  return Object.entries(input || {}).reduce<Record<string, number>>((next, [key, value]) => {
    const count = Number(value || 0);
    if (count > 0) next[key] = count;
    return next;
  }, {});
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
    qaMessage(conversationId, 6, "incoming", "Voice note placeholder", base + 300_000, { message_type: "voice", duration_seconds: 12, sender_display_name: "Voice QA" }),
    qaMessage(conversationId, 7, "incoming", "This moderated sample keeps the UI safe when content is unavailable.", base + 360_000, { moderation_state: "moderated", moderated_at: new Date(base + 360_000).toISOString() }),
    qaMessage(conversationId, 9, "incoming", "Short.", base + 380_000, { sender_display_name: "Maria Cherie" }),
    qaMessage(conversationId, 10, "outgoing", "This is a long multiline PulseSoc message used to verify bubble width, wrapping, bottom anchoring, timestamps, and readable spacing across compact and Pro Max layouts.\nThe second line must remain inside the same production-shaped bubble.", base + 400_000, { delivery_status: "read", seen_at: new Date(base + 410_000).toISOString() }),
    qaMessage(conversationId, 11, "incoming", "https://pulsesoc.com/pulse/messages", base + 420_000, { sender_display_name: "Link QA" }),
    qaMessage(conversationId, 12, "incoming", "✨", base + 440_000, { sender_display_name: "Emoji QA" }),
    qaMessage(conversationId, 13, "incoming", "PulseSoc system notice", base + 460_000, { message_type: "system", sender_display_name: "PulseSoc" }),
    qaMessage(conversationId, 14, "incoming", "Video attachment preview", base + 480_000, { message_type: "video", media_url: "/static/uploads/pulse_media/qa-video.mp4", sender_display_name: "Media QA" }),
    qaMessage(conversationId, 15, "incoming", "Pulse Command QA document", base + 500_000, { message_type: "file", media_url: "/static/llms.txt", file_size: 2048, sender_display_name: "Document QA" })
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
