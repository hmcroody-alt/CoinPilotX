import * as SecureStore from "expo-secure-store";
import { pulseApi } from "../api/client";

const OFFLINE_QUEUE_KEY = "pulse.mobile.communications.offlineQueue";

export type ConversationKind = "direct" | "group" | "room" | "community" | "channel";

export type ConversationSummary = {
  id: number | string;
  conversation_id?: number | string;
  room_id?: number | string;
  group_id?: number | string;
  community_id?: number | string;
  channel_id?: number | string;
  kind?: ConversationKind | string;
  type?: string;
  title?: string;
  name?: string;
  display_name?: string;
  preview_text?: string;
  last_message_preview?: string;
  last_message_at?: string;
  unread_count?: number;
  read_at?: string;
  delivered_at?: string;
  typing_users?: string[];
  online_count?: number;
  presence?: "online" | "away" | "offline" | string;
  conversation_type?: string;
  participants_preview?: Array<{ user_id?: number | string; display_name?: string; avatar_url?: string }>;
};

export type CommunicationAttachment = {
  id?: number | string;
  media_upload_id?: number | string;
  media_type?: string;
  media_url?: string;
  playback_url?: string;
  thumbnail_url?: string;
  filename?: string;
  file_size?: number;
  voice_note?: boolean;
  duration_seconds?: number;
};

export type CommunicationMessage = {
  id: number | string;
  message_id?: number | string;
  conversation_id?: number | string;
  sender_user_id?: number | string;
  sender_name?: string;
  body?: string;
  content?: string;
  message_type?: string;
  created_at?: string;
  delivered_at?: string;
  read_at?: string;
  attachments?: CommunicationAttachment[];
  reactions?: Array<{ reaction?: string; reaction_type?: string; count?: number }>;
};

export type MessageDraft = {
  conversationId: string;
  body: string;
  kind?: ConversationKind | string;
  clientId: string;
  createdAt: string;
};

export type CommunicationsBootstrap = {
  all: ConversationSummary[];
  direct: ConversationSummary[];
  group: ConversationSummary[];
  room: ConversationSummary[];
  community_channel: ConversationSummary[];
  queued: MessageDraft[];
};

export type SendMessageResult = {
  ok?: boolean;
  queued?: boolean;
  message_id?: number | string;
  client_id?: string;
};

export async function loadCommunicationsBootstrap(): Promise<CommunicationsBootstrap> {
  const [all, queued] = await Promise.all([
    loadCollection("/api/pulse/communications/v2/conversations", ["conversations", "items"]),
    loadQueuedMessages()
  ]);
  return {
    all,
    direct: all.filter(item => conversationKind(item) === "direct"),
    group: all.filter(item => conversationKind(item) === "group"),
    room: all.filter(item => conversationKind(item) === "room"),
    community_channel: all.filter(item => conversationKind(item) === "community_channel"),
    queued
  };
}

export async function loadConversationMessages(conversationId: string | number, beforeId = 0): Promise<{ messages: CommunicationMessage[]; hasOlder?: boolean; oldestMessageId?: number }> {
  const params = new URLSearchParams({ limit: "50" });
  if (beforeId) params.set("before_id", String(beforeId));
  const data = await pulseApi<Record<string, unknown>>(`/api/pulse/communications/v2/conversations/${conversationId}/messages?${params.toString()}`);
  const messages = Array.isArray(data.messages) ? data.messages.filter(isCommunicationMessage) : [];
  return {
    messages,
    hasOlder: Boolean(data.has_older),
    oldestMessageId: Number(data.oldest_message_id || 0)
  };
}

export async function sendCommunicationMessage(draft: Omit<MessageDraft, "clientId" | "createdAt">): Promise<SendMessageResult> {
  const payload: MessageDraft = {
    ...draft,
    clientId: createClientId(),
    createdAt: new Date().toISOString()
  };
  try {
    const result = await pulseApi<SendMessageResult>(`/api/pulse/communications/v2/conversations/${payload.conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        body: payload.body,
        message_type: "text",
        client_message_id: payload.clientId
      })
    });
    return result;
  } catch (error) {
    await enqueueMessage(payload);
    return { ok: false, queued: true, client_id: payload.clientId };
  }
}

export async function flushQueuedMessages(): Promise<{ attempted: number; sent: number; remaining: number }> {
  const queued = await loadQueuedMessages();
  const remaining: MessageDraft[] = [];
  let sent = 0;
  for (const draft of queued) {
    try {
      await pulseApi(`/api/pulse/communications/v2/conversations/${draft.conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({
          body: draft.body,
          message_type: "text",
          client_message_id: draft.clientId
        })
      });
      sent += 1;
    } catch {
      remaining.push(draft);
    }
  }
  await saveQueuedMessages(remaining);
  return { attempted: queued.length, sent, remaining: remaining.length };
}

export async function markConversationRead(conversationId: string | number) {
  return pulseApi(`/api/pulse/communications/v2/conversations/${conversationId}/read`, { method: "POST" });
}

export async function sendTypingHeartbeat(conversationId: string | number) {
  return pulseApi(`/api/pulse/communications/v2/conversations/${conversationId}/typing`, {
    method: "POST",
    body: JSON.stringify({ is_typing: true })
  });
}

export async function loadQueuedMessages(): Promise<MessageDraft[]> {
  const raw = await SecureStore.getItemAsync(OFFLINE_QUEUE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isMessageDraft) : [];
  } catch {
    return [];
  }
}

async function enqueueMessage(draft: MessageDraft) {
  const queued = await loadQueuedMessages();
  queued.push(draft);
  await saveQueuedMessages(queued.slice(-50));
}

async function saveQueuedMessages(messages: MessageDraft[]) {
  await SecureStore.setItemAsync(OFFLINE_QUEUE_KEY, JSON.stringify(messages));
}

async function loadCollection(path: string, keys: string[]): Promise<ConversationSummary[]> {
  try {
    const data = await pulseApi<Record<string, unknown>>(path);
    for (const key of keys) {
      const value = data[key];
      if (Array.isArray(value)) return value.filter(isConversationSummary);
    }
    if (Array.isArray(data.items)) return data.items.filter(isConversationSummary);
    return [];
  } catch {
    return [];
  }
}

function isConversationSummary(value: unknown): value is ConversationSummary {
  return typeof value === "object" && value !== null;
}

function isCommunicationMessage(value: unknown): value is CommunicationMessage {
  return typeof value === "object" && value !== null && Boolean((value as CommunicationMessage).id || (value as CommunicationMessage).message_id);
}

function isMessageDraft(value: unknown): value is MessageDraft {
  if (typeof value !== "object" || value === null) return false;
  const draft = value as MessageDraft;
  return Boolean(draft.conversationId && draft.body && draft.clientId && draft.createdAt);
}

function createClientId() {
  return `mobile-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function conversationKind(conversation: ConversationSummary) {
  const raw = String(conversation.conversation_type || conversation.kind || conversation.type || "direct");
  return raw === "community" || raw === "channel" ? "community_channel" : raw;
}
