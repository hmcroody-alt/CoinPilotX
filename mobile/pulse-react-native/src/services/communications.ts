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
};

export type MessageDraft = {
  conversationId: string;
  body: string;
  kind?: ConversationKind | string;
  clientId: string;
  createdAt: string;
};

export type CommunicationsBootstrap = {
  directs: ConversationSummary[];
  groups: ConversationSummary[];
  rooms: ConversationSummary[];
  communities: ConversationSummary[];
  channels: ConversationSummary[];
  queued: MessageDraft[];
};

export type SendMessageResult = {
  ok?: boolean;
  queued?: boolean;
  message_id?: number | string;
  client_id?: string;
};

export async function loadCommunicationsBootstrap(): Promise<CommunicationsBootstrap> {
  const [directs, groups, rooms, communities, channels, queued] = await Promise.all([
    loadCollection("/api/pulse/messages/conversations", ["conversations", "items"]),
    loadCollection("/api/pulse/groups", ["groups", "items"]),
    loadCollection("/api/pulse/rooms", ["rooms", "items"]),
    loadCollection("/api/pulse/communities", ["communities", "items"]),
    loadCollection("/api/pulse/channels", ["channels", "items"]),
    loadQueuedMessages()
  ]);
  return { directs, groups, rooms, communities, channels, queued };
}

export async function sendCommunicationMessage(draft: Omit<MessageDraft, "clientId" | "createdAt">): Promise<SendMessageResult> {
  const payload: MessageDraft = {
    ...draft,
    clientId: createClientId(),
    createdAt: new Date().toISOString()
  };
  try {
    const result = await pulseApi<SendMessageResult>("/api/pulse/messages/send", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: payload.conversationId,
        body: payload.body,
        message_type: "text",
        client_id: payload.clientId
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
      await pulseApi("/api/pulse/messages/send", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: draft.conversationId,
          body: draft.body,
          message_type: "text",
          client_id: draft.clientId
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
  return pulseApi(`/api/pulse/messages/conversations/${conversationId}/read`, { method: "POST" });
}

export async function sendTypingHeartbeat(conversationId: string | number) {
  return pulseApi(`/api/pulse/messages/conversations/${conversationId}/typing`, { method: "POST" });
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

function isMessageDraft(value: unknown): value is MessageDraft {
  if (typeof value !== "object" || value === null) return false;
  const draft = value as MessageDraft;
  return Boolean(draft.conversationId && draft.body && draft.clientId && draft.createdAt);
}

function createClientId() {
  return `mobile-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
