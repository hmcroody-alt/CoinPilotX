import { pulseApi } from "./pulseApi";
import {
  getConversation as getMessengerConversation,
  listConversations,
  sendConversationMessage,
  MessengerConversation,
  MessengerMessage
} from "./messenger";

export type Conversation = MessengerConversation;

export type Message = MessengerMessage;

export function getMissionControl() {
  return pulseApi<Record<string, unknown>>("/api/dashboard/mission-control");
}

export function getConversations() {
  return listConversations().then((conversations) => ({ ok: true, conversations, items: conversations }));
}

export function getConversation(conversationId: number) {
  return getMessengerConversation(conversationId);
}

export function sendMessage(conversationId: number, body: string) {
  return sendConversationMessage(conversationId, { body });
}

/**
 * One question to the PulseSoc assistant, persisted to the caller's own AI
 * conversation by the server. Owner comes from the session; there is no user
 * parameter to get wrong.
 *
 * `ok` is part of the contract, not an afterthought: the route answers HTTP 200
 * with `ok: false` and an explanatory `message` when the router failed, so a
 * caller that only checked for a thrown error would render the failure text as
 * though it were the assistant's reply.
 */
export function askPulseAi(message: string) {
  return pulseApi<{ ok?: boolean; response?: string; reply?: string; message?: string }>(
    "/api/pulse/assistant/chat",
    {
      method: "POST",
      body: JSON.stringify({ message })
    }
  );
}

export function getProfile() {
  return pulseApi<Record<string, unknown>>("/api/pulse/profile/me");
}
