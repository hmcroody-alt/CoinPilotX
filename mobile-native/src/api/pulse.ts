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

export function askPulseAi(message: string) {
  return pulseApi<{ response?: string; reply?: string; message?: string }>("/api/pulse/assistant/chat", {
    method: "POST",
    body: JSON.stringify({ message })
  });
}

export function getProfile() {
  return pulseApi<Record<string, unknown>>("/api/pulse/profile/me");
}
