import AsyncStorage from "@react-native-async-storage/async-storage";
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
  const conversations = normalizeConversations(data.conversations || data.items || []);
  await cacheConversations(conversations);
  return conversations;
}

export async function loadCachedConversations() {
  try {
    const cached = await AsyncStorage.getItem(CONVERSATION_CACHE_KEY);
    if (!cached) return [];
    return normalizeConversations(JSON.parse(cached) as MessengerConversation[]);
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
  const messages = normalizeMessages(data.messages || data.items || [], conversationId);
  if (!params.beforeId) await cacheMessages(conversationId, messages);
  return { ...data, messages };
}

export async function syncConversation(conversationId: number, afterId = 0) {
  const data = await pulseApi<ConversationResponse>(`/api/pulse/messages/${conversationId}/sync?after_id=${afterId}&limit=80`);
  return {
    ...data,
    messages: normalizeMessages(data.messages || [], conversationId)
  };
}

export async function loadCachedMessages(conversationId: number) {
  const key = messageCacheKey(conversationId);
  try {
    const cached = await AsyncStorage.getItem(key);
    if (!cached) return [];
    return normalizeMessages(JSON.parse(cached) as MessengerMessage[], conversationId);
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
      local_created_at: payload.local_created_at || ""
    })
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
        unread_count: Number(item.unread_count || 0)
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
        duration_seconds: Number(item.duration_seconds || item.duration || 0)
      };
    })
    .filter((item) => item.id > 0);
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
