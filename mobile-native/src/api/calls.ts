import AsyncStorage from "@react-native-async-storage/async-storage";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { getPushInstallationId } from "./installationId";
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
  app_id?: string;
  channel_name?: string;
  uid?: number;
  url?: string;
  room_name?: string;
  room_type?: "audio_call" | "video_call" | string;
  participant_role?: "caller" | "callee" | string;
  can_publish?: boolean;
  can_subscribe?: boolean;
  can_publish_sources?: string[];
  realtime_audio_shared_path_enabled?: boolean;
  realtime_audio_v2_enabled?: boolean;
  realtime_audio_v2_fallback_enabled?: boolean;
  /**
   * Media quality V2 rollout flags, decided entirely server-side and delivered
   * on this response. Absent means the verified stable configuration, which is
   * what every client runs today.
   */
  media_quality?: Record<string, unknown>;
  expires_at?: string;
  message?: string;
};

export type PulseCall = {
  ok?: boolean;
  call_id: string;
  public_id?: string;
  /**
   * The call's CallKit identity, issued by the server (a UUIDv5 of `public_id`).
   *
   * CallKit requires a UUID and `call_id` is not one, so something has to bridge them.
   * Deriving it on the device would mean two independent derivations that have to agree
   * forever; instead the server derives it once and every layer — the PushKit payload,
   * CallKit, and the call record itself — quotes the same string. Optional only because
   * a response from a backend older than the VoIP work will not carry it.
   */
  call_uuid?: string;
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

export type PulseCallCapabilities = {
  provider?: string;
  group_calls_enabled?: boolean;
  audio_calls_enabled?: boolean;
  video_calls_enabled?: boolean;
  max_audio_participants?: number;
  max_video_participants?: number;
};

export type InviteToCallResponse = PulseCallEnvelope & {
  invited_user_ids?: number[];
  already_in_call?: number[];
  max_participants?: number;
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

/**
 * Answer a call, and tell the server *which device* answered.
 *
 * The device id is not bookkeeping. Accepting moves the call out of `ringing`, and that
 * edge fans an `answered_elsewhere` VoIP cancel out to every device belonging to the
 * answering user, so the ones still ringing stop — an iPad left showing a full-screen
 * CallKit UI for a call that is already being taken elsewhere has no way out but to
 * answer a dead call. That cancel carries the *same* call UUID as the call just
 * answered, so `_voip_stop_ringing` spares the answering device — but only when the
 * accept body names it, which is what `_answering_device_ids` reads.
 *
 * With no id in the body the exclusion set is empty and the phone cancels its own call:
 * `AppDelegate`'s `cancel_call` branch calls `endCall(withUUID:reason:)` on the UUID it
 * is currently connected on, CallKit tears the UI down as `answeredElsewhere`, and
 * because the media session no longer waits on CallKit to start, Agora keeps running
 * underneath it. The call is live and the system says it ended.
 *
 * The keys are omitted rather than sent empty when the id cannot be read — see
 * `getPushInstallationId`, which returns empty on a locked keychain rather than minting
 * an id that matches no registration. A body that admits it does not know which device
 * it is beats one that names a device that was never registered.
 */
export async function acceptCall(callId: string) {
  const deviceId = await getPushInstallationId().catch(() => "");
  const data = await pulseApi<PulseCall | PulseCallEnvelope>(`/api/calls/${encodeURIComponent(callId)}/accept`, {
    method: "POST",
    body: JSON.stringify({
      source: "native",
      ...(deviceId ? { device_id: deviceId, installation_id: deviceId } : {})
    })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return call;
}

export async function inviteToCall(callId: string, userIds: number[]) {
  const data = await pulseApi<InviteToCallResponse>(`/api/calls/${encodeURIComponent(callId)}/invite`, {
    method: "POST",
    body: JSON.stringify({ user_ids: userIds, source: "native" })
  });
  const call = normalizeCallPayload(data);
  await cacheCallStatus(call).catch(() => undefined);
  return {
    ...call,
    invited_user_ids: data.invited_user_ids || [],
    already_in_call: data.already_in_call || [],
    max_participants: Number(data.max_participants || 0) || undefined
  };
}

export async function getCallCapabilities() {
  const data = await pulseApi<{ ok?: boolean; capabilities?: PulseCallCapabilities; message?: string }>(
    "/api/calls/capabilities"
  );
  return normalizeCapabilities(data.capabilities || {});
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

/**
 * File this device's PushKit token so the backend can ring it through CallKit.
 *
 * `device_id` is not optional decoration — `register_voip_token` rejects the request with
 * `missing_device_id` without it, so a call that omits it never registers at all and the
 * phone silently keeps using the alert-push fallback forever. It is resolved here rather
 * than asked of the caller precisely so that no caller can forget it, and so that the id
 * is guaranteed to be the same one `/api/push/subscribe` used: the alert-push suppression
 * joins the two registrations on that string alone.
 *
 * No `environment` is reported. The client cannot tell a sandbox APNs entitlement from a
 * production one at runtime — `__DEV__` is false in a Release build that still carries
 * `aps-environment: development` — so a guess here would be wrong exactly when it matters
 * and would point the server at the wrong APNs host. Omitting it lets the server fall back
 * to `default_environment()`, the same `APNS_USE_SANDBOX` switch the alert sender already
 * follows, which keeps VoIP and alert pushes from disagreeing about the host.
 */
export async function registerVoipPushToken(token: string, payload: Record<string, unknown> = {}) {
  const deviceId = await getPushInstallationId().catch(() => "");
  return pulseApi<{ ok?: boolean; message?: string; voip_ready?: boolean; status?: string }>("/api/calls/voip-token", {
    method: "POST",
    body: JSON.stringify({
      token,
      platform: "ios",
      provider: "apns_voip",
      device_id: deviceId,
      installation_id: deviceId,
      ...payload,
      source: "native"
    })
  });
}

/**
 * Stop this device ringing for the signed-in account.
 *
 * Revokes by device id as well as by token because at logout the token is frequently not
 * in memory: PushKit only hands it to JS through the `register` event, which fires once
 * per launch, and a user who signs out without having received a call in that session has
 * nothing to send. The backend's `revoke_token` accepts either identifier and is scoped to
 * the caller's own `user_id`, so the device-id path is both sufficient and safe.
 *
 * Failing to call this leaves an active token behind, and an active token means two things
 * at once: the server keeps suppressing the alert push for a device that is no longer
 * listening, and it can still ring a signed-out phone with the caller's name on the
 * CallKit screen.
 */
export async function unregisterVoipPushToken(options: { token?: string; reason?: string } = {}) {
  const deviceId = await getPushInstallationId().catch(() => "");
  return pulseApi<{ ok?: boolean; message?: string; revoked?: number }>("/api/calls/voip-token/revoke", {
    method: "POST",
    body: JSON.stringify({
      token: options.token || undefined,
      device_id: deviceId,
      installation_id: deviceId,
      platform: "ios",
      reason: options.reason || "logout",
      source: "native"
    })
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
    room_name: String(call.room_name || join.room_name || ""),
    participants: call.participants || [],
    join
  };
}

function normalizeJoin(join: PulseCallJoin): PulseCallJoin {
  return {
    ...join,
    token: String(join.token || ""),
    room_name: String(join.room_name || ""),
    provider: "agora"
  };
}

export function normalizeCapabilities(capabilities: PulseCallCapabilities): PulseCallCapabilities {
  return {
    provider: String(capabilities.provider || "agora"),
    group_calls_enabled: Boolean(capabilities.group_calls_enabled),
    audio_calls_enabled: capabilities.audio_calls_enabled !== false,
    video_calls_enabled: capabilities.video_calls_enabled !== false,
    max_audio_participants: Math.max(2, Number(capabilities.max_audio_participants || 0) || 12),
    max_video_participants: Math.max(2, Number(capabilities.max_video_participants || 0) || 6)
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
