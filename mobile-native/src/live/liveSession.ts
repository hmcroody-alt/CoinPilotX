/**
 * Pure, testable helpers for native live broadcasting.
 *
 * Everything that maps the Live Studio draft into the backend `/api/pulse/live/start`
 * contract, normalizes the responses (start result, LiveKit credentials, guest requests),
 * and formats live UI values lives here so it can be unit-tested without a device, a
 * network, or the LiveKit native module.
 */

import { normalizeLiveAudioV2Flag } from "./liveAudioFlags";
import type { LiveAudience, LiveStudioDraft, LiveTypeKey } from "./liveStudioReadiness";

export type LiveStartPayload = {
  title: string;
  category: string;
  audience: LiveAudience;
  premium_only: boolean;
  allow_comments: boolean;
  record_replay: boolean;
  multi_guest: boolean;
  live_type: LiveTypeKey;
  description?: string;
  context_type: "native";
};

export type LiveStartResult = {
  liveId: number;
  room: string;
  webrtcRoomId: string;
  hlsUrl: string;
  feedPostId: number;
  tokenUrl: string;
};

export type LiveKitCredentials = {
  provider?: "livekit" | "agora";
  broadcastId: number;
  hostUserId: number;
  authorizationVersion: string;
  token: string;
  url: string;
  appId?: string;
  channelName?: string;
  uid?: number;
  room: string;
  identity: string;
  canPublish: boolean;
  canSubscribe: boolean;
  canPublishSources: string[];
  canPublishData: boolean;
  canUpdateOwnMetadata: boolean;
  roomJoin: boolean;
  role: string;
  guestId: number;
  requestId: number;
  participantName: string;
  traceId: string;
  expiresAt: string;
  /**
   * Server-authoritative livestream audio V2 rollout decision, delivered on the
   * token response the client already fetches for every broadcast. Default OFF:
   * an older backend that omits the field runs the legacy path.
   */
  audioV2Enabled: boolean;
  publisherAudioV2Enabled: boolean;
  /** Whether the client may drop back to the legacy path if V2 fails at runtime. */
  audioV2FallbackEnabled: boolean;
  /** Server-authoritative, QA-account-only privacy-safe diagnostic timeline. */
  audioTraceEnabled: boolean;
  /**
   * Media quality V2 rollout flags, decided entirely server-side. Kept as the
   * raw payload and normalised by parseMediaQualityFlags at the point of use,
   * so this module does not become a second place where flag semantics live.
   * Absent means the verified stable configuration.
   */
  mediaQuality?: Record<string, unknown> | null;
};

/**
 * Guard the co-host publish path (Issue 5: "guest joining fails"). A viewer, or a
 * requester the host has not accepted yet, is handed a token with
 * `can_publish:false` and `guest_id:0`. Connecting the LiveKit room as a
 * *publisher* with such a token silently produces no outgoing audio/video — the
 * guest appears to join but nobody can hear or see them. Callers must confirm the
 * minted credentials actually grant publish AND are bound to a real guest slot
 * (a usable token + url) before entering publisher mode; otherwise they must
 * surface an honest "not verified yet" error instead of a dead on-stage bubble.
 */
export function canConnectAsCohostPublisher(
  credentials: LiveKitCredentials | null | undefined
): credentials is LiveKitCredentials {
  const publishSources = credentials?.canPublishSources?.length
    ? credentials.canPublishSources
    : credentials?.canPublish
      ? ["microphone", "camera"]
      : [];
  return Boolean(
    credentials &&
      credentials.token &&
      (credentials.provider === "agora"
        ? credentials.appId && credentials.channelName && credentials.uid
        : credentials.url) &&
      credentials.canPublish &&
      credentials.canPublishSources.includes("camera") &&
      publishSources.includes("microphone") &&
      credentials.guestId > 0
  );
}

export type LiveGuestRequest = {
  requestId: number;
  userId: number;
  displayName: string;
  username: string;
  avatarUrl: string;
  status: string;
  cameraReady: boolean;
  micReady: boolean;
  requestedAt: string;
};

export type LiveGuest = {
  guestId: number;
  userId: number;
  requestId: number;
  displayName: string;
  avatarUrl: string;
  role: string;
  roleLabel: string;
  status: string;
  audioMuted: boolean;
  videoEnabled: boolean;
  joinedAt: string;
};

const CATEGORY_BY_LIVE_TYPE: Record<LiveTypeKey, string> = {
  solo: "Just Chatting",
  guest: "Collab",
  interview: "Interview",
  podcast: "Podcast",
  panel: "Panel",
  music: "Music",
  gaming: "Gaming",
  shopping: "Shopping",
  class: "Education",
  community: "Community"
};

const MULTI_GUEST_TYPES = new Set<LiveTypeKey>(["guest", "interview", "panel", "community"]);

function toStr(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function toNum(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function toBool(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") return value === "1" || value.toLowerCase() === "true";
  return false;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => toStr(entry).trim().toLowerCase())
    .filter(Boolean);
}

/** Map a validated Live Studio draft into the backend go-live request body. */
export function buildLiveStartPayload(draft: LiveStudioDraft): LiveStartPayload {
  const title = draft.title.trim().slice(0, 120) || "PulseSoc Live";
  const description = draft.description.trim().slice(0, 500);
  const payload: LiveStartPayload = {
    title,
    category: CATEGORY_BY_LIVE_TYPE[draft.liveType] || "Live",
    audience: draft.audience,
    premium_only: draft.audience === "subscribers",
    allow_comments: draft.allowComments,
    record_replay: draft.recordReplay,
    multi_guest: MULTI_GUEST_TYPES.has(draft.liveType),
    live_type: draft.liveType,
    context_type: "native"
  };
  if (description) payload.description = description;
  return payload;
}

/** Normalize the `/api/pulse/live/start` response into a stable native shape. */
export function normalizeLiveStartResult(raw: Record<string, unknown> | null | undefined): LiveStartResult | null {
  const data = raw || {};
  const livekit = (data.livekit as Record<string, unknown> | undefined) || {};
  const liveId = toNum(data.live_id ?? data.id);
  if (liveId <= 0) return null;
  return {
    liveId,
    room: toStr(livekit.room ?? data.webrtc_room_id),
    webrtcRoomId: toStr(data.webrtc_room_id ?? livekit.room),
    hlsUrl: toStr(data.hls_url ?? data.playback_url),
    feedPostId: toNum(data.feed_post_id),
    tokenUrl: toStr(livekit.token_url)
  };
}

/**
 * Normalize the `/api/pulse/live/<id>/livekit/token` response. Accepts either
 * `livekit_url` or `url` for the wss endpoint. Returns null when there is no usable
 * token or url — callers must surface an honest error, never a fake preview.
 */
export function normalizeLiveKitCredentials(raw: Record<string, unknown> | null | undefined): LiveKitCredentials | null {
  const data = raw || {};
  const token = toStr(data.token);
  const provider = toStr(data.provider, "livekit").toLowerCase() === "agora" ? "agora" : "livekit";
  const url = toStr(data.livekit_url ?? data.url);
  const appId = toStr(data.app_id);
  const channelName = toStr(data.channel_name ?? data.room);
  const uid = toNum(data.uid);
  if (!token || (provider === "livekit" ? !url : !appId || !channelName || !uid)) return null;
  return {
    broadcastId: toNum(data.live_id ?? data.broadcast_id),
    hostUserId: toNum(data.host_user_id),
    authorizationVersion: toStr(data.authorization_version ?? data.trace_id, "v1"),
    token,
    url,
    ...(provider === "agora" ? { provider, appId, channelName, uid } : {}),
    room: toStr(data.room),
    identity: toStr(data.identity),
    canPublish: toBool(data.can_publish),
    canSubscribe: toBool(data.can_subscribe ?? true),
    canPublishSources: toStringArray(data.can_publish_sources),
    canPublishData: toBool(data.can_publish_data ?? true),
    canUpdateOwnMetadata: toBool(data.can_update_own_metadata),
    roomJoin: toBool(data.room_join ?? true),
    role: toStr(data.role, "viewer"),
    guestId: toNum(data.guest_id),
    requestId: toNum(data.request_id),
    participantName: toStr(data.participant_name),
    traceId: toStr(data.trace_id),
    expiresAt: toStr(data.expires_at),
    // Strict boolean normalisation at the API boundary: anything other than an
    // explicit server `true` (missing field, "false", 0, "0") runs the legacy path.
    audioV2Enabled: normalizeLiveAudioV2Flag(data.audio_v2_enabled),
    publisherAudioV2Enabled: normalizeLiveAudioV2Flag(data.publisher_audio_v2_enabled),
    audioV2FallbackEnabled: data.audio_v2_fallback_enabled !== false,
    audioTraceEnabled: data.audio_trace_enabled === true,
    mediaQuality:
      data.media_quality && typeof data.media_quality === "object" && !Array.isArray(data.media_quality)
        ? (data.media_quality as Record<string, unknown>)
        : null
  };
}

export function normalizeGuestRequest(raw: Record<string, unknown> | null | undefined): LiveGuestRequest | null {
  const data = raw || {};
  const user = (data.user as Record<string, unknown> | undefined) || (data.guest as Record<string, unknown> | undefined) || {};
  const requestId = toNum(data.request_id ?? data.id);
  const userId = toNum(data.user_id ?? user.user_id ?? user.id);
  if (requestId <= 0 || userId <= 0) return null;
  return {
    requestId,
    userId,
    displayName: toStr(data.display_name ?? user.display_name ?? user.username, "Viewer"),
    username: toStr(data.username ?? user.username),
    avatarUrl: toStr(data.avatar_url ?? user.avatar_url),
    status: toStr(data.status, "pending"),
    cameraReady: toBool(data.camera_ready),
    micReady: toBool(data.mic_ready),
    requestedAt: toStr(data.requested_at ?? data.created_at)
  };
}

/** Dedupe + keep only actionable pending requests, most recent first. */
export function normalizeGuestRequests(raw: unknown): LiveGuestRequest[] {
  const list = Array.isArray(raw) ? raw : [];
  const seen = new Set<number>();
  const out: LiveGuestRequest[] = [];
  for (const entry of list) {
    const req = normalizeGuestRequest(entry as Record<string, unknown>);
    if (!req) continue;
    if (seen.has(req.requestId)) continue;
    seen.add(req.requestId);
    out.push(req);
  }
  return out.sort((a, b) => b.requestedAt.localeCompare(a.requestedAt));
}

export function normalizeLiveGuest(raw: Record<string, unknown> | null | undefined): LiveGuest | null {
  const data = raw || {};
  const guestId = toNum(data.id ?? data.guest_id);
  const userId = toNum(data.user_id);
  if (guestId <= 0) return null;
  const role = toStr(data.role, "cohost");
  return {
    guestId,
    userId,
    requestId: toNum(data.request_id),
    displayName: toStr(data.display_name ?? data.username, "Guest"),
    avatarUrl: toStr(data.avatar_url),
    role,
    roleLabel: toStr(data.role_label, role === "cohost" ? "Co-host" : "Guest"),
    status: toStr(data.status, "active"),
    audioMuted: toBool(data.audio_muted),
    videoEnabled: toBool(data.video_enabled),
    joinedAt: toStr(data.joined_at ?? data.live_at)
  };
}

/** Dedupe active guests by guest id, preserving backend layout order. */
export function normalizeLiveGuests(raw: unknown): LiveGuest[] {
  const list = Array.isArray(raw) ? raw : [];
  const seen = new Set<number>();
  const out: LiveGuest[] = [];
  for (const entry of list) {
    const guest = normalizeLiveGuest(entry as Record<string, unknown>);
    if (!guest) continue;
    if (seen.has(guest.guestId)) continue;
    seen.add(guest.guestId);
    out.push(guest);
  }
  return out;
}

export function pendingGuestRequests(requests: LiveGuestRequest[]): LiveGuestRequest[] {
  return requests.filter((request) => request.status === "pending" || request.status === "requested");
}

export function elapsedLabel(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatViewerCount(count: number): string {
  const value = Math.max(0, Math.floor(Number(count) || 0));
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value % 1000 >= 100 ? 1 : 0)}K`;
  return `${(value / 1_000_000).toFixed(value % 1_000_000 >= 100_000 ? 1 : 0)}M`;
}
