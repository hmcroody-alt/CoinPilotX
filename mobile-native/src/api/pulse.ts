import { pulseApi } from "./pulseApi";

export type Conversation = {
  id: number;
  title?: string;
  name?: string;
  latest_message?: string;
  updated_at?: string;
};

export type Message = {
  id: number;
  sender_id?: number;
  body?: string;
  text?: string;
  created_at?: string;
};

export function getMissionControl() {
  return pulseApi<Record<string, unknown>>("/api/dashboard/mission-control");
}

export function getConversations() {
  return pulseApi<{ conversations?: Conversation[]; items?: Conversation[] }>("/api/pulse/messages/conversations");
}

export function getConversation(conversationId: number) {
  return pulseApi<{ messages?: Message[]; conversation?: Conversation }>(`/api/pulse/messages/${conversationId}`);
}

export function sendMessage(conversationId: number, body: string) {
  return pulseApi<{ ok: boolean; message?: Message }>(`/api/pulse/messages/${conversationId}/send`, {
    method: "POST",
    body: JSON.stringify({ body, message: body, text: body })
  });
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
