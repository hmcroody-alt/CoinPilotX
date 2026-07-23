import AsyncStorage from "@react-native-async-storage/async-storage";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const ACTIVE_CALLS_CACHE_KEY = "pulsesoc.native.calls.active";
const callStatusCacheKey = (callId: string) => `pulsesoc.native.calls.status.${callId}`;

export type PulseCallType = "audio" | "video";
export type PulseCallStatus =
  | "ringing"
  | "accepted"
  | "connecting"
  | "connected"
  | "active"
  | "ended"
  | "declined"
  | "missed"
  | "failed"
  | string;

export type PulseCallParticipant = {
  user_id?: number;
  participant_id?: number;
  display_name?: string;
  username?: string;
  avatar_url?: string;
  role?: string;
  status?: string;
  muted_audio?: boolean;
  muted_video?: boolean;
  joined_at?: string;
  left_at?: string;
};

export type PulseCallJoin = {
  ok?: boolean;
  provider?: string;
  token?: string;
  livekit_url?: string;
  url?: string;
  room_name?: string;
  expires_at?: string;
  message?: string;
};

export type PulseCall = {
  ok?: boolean;
  call_id: string;
  public_id?: string;
  conversation_id?: number;
  room_name?: string;
  provider?: string;
  call_type?: PulseCallType;
  call_scope?: string;
  status?: PulseCallStatus;
  started_at?: string;
  created_at?: string;
  accepted_at?: string;
  connected_at?: string;
  ended_at?: string;
  duration_seconds?: number;
  end_reason?: string;
  participants?: PulseCallParticipant[];
  participant?: PulseCallParticipant;
  livekit?: {
    ok?: boolean;
    configured?: boolean;
    provider?: string;
    room_name?: string;
    livekit_url?: string;
    message?: string;
  };
  join?: PulseCallJoin;
  message?: string;
};

export type PulseCallEvent = {
  id?: number;
  call_id?: string;
  event_type?: string;
  type?: string;
  actor_user_id?: number;
  metadata?: Record<string, unknown>;
  created_at?: string;
};

export type ActiveCallsResponse = {
  ok?: boolean;
  calls?: PulseCall[];
  items?: PulseCall[];
  message?: string;
};

export type PulseCallEnvelope = {
  ok?: boolean;
  call?: PulseCall;
  join?: PulseCallJoin;
  calls?: PulseCall[];
  items?: PulseCall[];
  events?: PulseCallEvent[];
  message?: string;
};

export type CallStatusResponse = PulseCallEnvelope & PulseCall & {
  events?: PulseCallEvent[];
};

export type CallControlAction =
  | "mute-audio"
  | "unmute-audio"
  | "enable-video"
  | "disable-video"
  | "switch-camera"
  | "speaker"
  | "minimize"
  | "restore"
  | "visibility";

export async function startConversationCall(conversationId: number, callType: PulseCallType) {
  const endpoint = `/api/pulse/communications/v2/conversations/${encodeURIComponent(String(conversationId))}/${callType === "video" ? "video" : "voice"}/start`;
  const data = await pulseApi<PulseCall | PulseCallEnvelope>(endpoint, {
    method: "POST",
    body: JSON.stringify({ source: "native", call_type: callType })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return call;
}

export async function startCall(payload: {
  conversation_id?: number;
  participant_user_ids?: number[];
  recipient_user_ids?: number[];
  call_type?: PulseCallType;
  call_scope?: string;
}) {
  const recipientUserIds = payload.recipient_user_ids || payload.participant_user_ids || [];
  const data = await pulseApi<PulseCall | PulseCallEnvelope>("/api/calls/start", {
    method: "POST",
    body: JSON.stringify({ ...payload, recipient_user_ids: recipientUserIds, source: "native" })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return call;
}

export async function acceptCall(callId: string) {
  const data = await pulseApi<PulseCall | PulseCallEnvelope>(`/api/calls/${encodeURIComponent(callId)}/accept`, {
    method: "POST",
    body: JSON.stringify({ source: "native" })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return call;
}

export async function markRingSeen(callId: string) {
  return pulseApi<{ ok?: boolean; status?: string }>(`/api/calls/${encodeURIComponent(callId)}/ring-seen`, {
    method: "POST",
    body: JSON.stringify({ source: "native" })
  });
}

export async function declineCall(callId: string, reason = "native_decline") {
  const data = await pulseApi<PulseCall | PulseCallEnvelope>(`/api/calls/${encodeURIComponent(callId)}/decline`, {
    method: "POST",
    body: JSON.stringify({ reason, source: "native" })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return call;
}

export async function endCall(callId: string, reason = "native_hangup") {
  const data = await pulseApi<PulseCall | PulseCallEnvelope>(`/api/calls/${encodeURIComponent(callId)}/end`, {
    method: "POST",
    body: JSON.stringify({ reason, source: "native" })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return call;
}

export async function requestCallJoinToken(callId: string) {
  const data = await pulseApi<PulseCall | PulseCallJoin | PulseCallEnvelope>(`/api/calls/${encodeURIComponent(callId)}/join-token`, {
    method: "POST",
    body: JSON.stringify({ source: "native" })
  });
  if (isRecord(data)) {
    const record = data as Record<string, unknown>;
    if (isRecord(record.join)) return normalizeJoin(record.join as PulseCallJoin);
    if (isRecord(record.call) || "call_id" in record || "public_id" in record) return normalizeCallPayload(data as PulseCall | PulseCallEnvelope).join || {};
  }
  return normalizeJoin(data as PulseCallJoin);
}

export async function getCallStatus(callId: string) {
  const data = await pulseApi<CallStatusResponse>(`/api/calls/${encodeURIComponent(callId)}/status`);
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return { ...data, ...call, events: data.events || [] };
}

export async function getActiveCalls() {
  const data = await pulseApi<ActiveCallsResponse>("/api/calls/active");
  const calls = normalizeCalls(data.calls || data.items || []);
  return { ...data, calls };
}

export async function getConversationCalls(conversationId: number) {
  const data = await pulseApi<{ ok?: boolean; calls?: PulseCall[] }>(
    `/api/conversations/${encodeURIComponent(String(conversationId))}/calls`
  );
  return { ...data, calls: normalizeCalls(data.calls || []) };
}

export async function markCallConnected(callId: string, payload: Record<string, unknown> = {}) {
  const data = await pulseApi<PulseCall | PulseCallEnvelope>(`/api/calls/${encodeURIComponent(callId)}/connected`, {
    method: "POST",
    body: JSON.stringify({ ...payload, source: "native" })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return call;
}

export async function registerVoipPushToken(token: string, payload: Record<string, unknown> = {}) {
  return pulseApi<{ ok?: boolean; message?: string }>("/api/calls/voip-token", {
    method: "POST",
    body: JSON.stringify({ token, platform: "ios", provider: "apns_voip", ...payload, source: "native" })
  });
}

export async function unregisterVoipPushToken(token: string) {
  return pulseApi<{ ok?: boolean; message?: string }>("/api/calls/voip-token/revoke", {
    method: "POST",
    body: JSON.stringify({ token, platform: "ios", source: "native" })
  });
}

export async function submitCallQuality(callId: string, payload: Record<string, unknown>) {
  return pulseApi<{ ok?: boolean; report_id?: number; message?: string }>(`/api/calls/${encodeURIComponent(callId)}/quality`, {
    method: "POST",
    body: JSON.stringify({ ...payload, source: "native" })
  });
}

export async function getCallEvents(callId: string) {
  const data = await pulseApi<{ ok?: boolean; events?: PulseCallEvent[] }>(`/api/calls/${encodeURIComponent(callId)}/events`);
  return data.events || [];
}

export async function sendCallControl(callId: string, action: CallControlAction, payload: Record<string, unknown> = {}) {
  return pulseApi<{ ok?: boolean; status?: string; message?: string }>(`/api/calls/${encodeURIComponent(callId)}/${action}`, {
    method: "POST",
    body: JSON.stringify({ ...payload, source: "native" })
  });
}

export async function loadCachedActiveCalls() {
  // Cached call metadata is never authoritative evidence that media is active.
  await AsyncStorage.removeItem(ACTIVE_CALLS_CACHE_KEY).catch(() => undefined);
  return { calls: [] };
}

export async function loadCachedCallStatus(callId: string) {
  return readJsonCache<PulseCall>(callStatusCacheKey(callId), normalizeCall);
}

export async function cacheCallStatus(call: PulseCall) {
  if (!call.call_id) return;
  await writeJsonCache(callStatusCacheKey(call.call_id), call);
}

export async function openCallWebFallback(callId?: string, conversationId?: number) {
  const target = callId
    ? `${PULSE_API_BASE_URL}/pulse/messages${conversationId ? `/${encodeURIComponent(String(conversationId))}` : ""}?call_id=${encodeURIComponent(callId)}`
    : `${PULSE_API_BASE_URL}/pulse/messages`;
  return {
    ok: false,
    target,
    status: "native_provider_boundary",
    message: "Call options remain inside the native call experience."
  };
}

export function normalizeCalls(calls: PulseCall[]) {
  return (calls || []).map(normalizeCallPayload).filter((call) => call.call_id);
}

export function normalizeCall(call: PulseCall): PulseCall {
  return normalizeCallPayload(call);
}

export function normalizeCallPayload(data: PulseCall | PulseCallEnvelope): PulseCall {
  const envelope = isRecord(data) ? data as PulseCallEnvelope : {};
  const call = isRecord(envelope.call) ? envelope.call as PulseCall : data as PulseCall;
  const envelopeJoin = isRecord(envelope.join) ? envelope.join as PulseCallJoin : {};
  const callJoin = isRecord(call.join) ? call.join as PulseCallJoin : {};
  return normalizeCallRecord({ ...call, join: Object.keys(envelopeJoin).length ? envelopeJoin : callJoin });
}

function normalizeCallRecord(call: PulseCall): PulseCall {
  const id = String(call.public_id || call.call_id || "");
  const join = normalizeJoin(call.join || {});
  return {
    ...call,
    call_id: id,
    public_id: String(call.public_id || id || ""),
    conversation_id: Number(call.conversation_id || 0) || undefined,
    call_type: call.call_type === "video" ? "video" : "audio",
    status: String(call.status || "ringing"),
    room_name: String(call.room_name || call.livekit?.room_name || join.room_name || ""),
    participants: call.participants || [],
    join
  };
}

function normalizeJoin(join: PulseCallJoin): PulseCallJoin {
  return {
    ...join,
    token: String(join.token || ""),
    livekit_url: String(join.livekit_url || join.url || ""),
    room_name: String(join.room_name || ""),
    provider: String(join.provider || "livekit")
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
