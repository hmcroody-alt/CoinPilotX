import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { PulseAuthor } from "./feed";
import { pulseApi } from "./pulseApi";
import {
  buildLiveStartPayload,
  normalizeGuestRequests,
  normalizeLiveGuests,
  normalizeLiveKitCredentials,
  normalizeLiveStartResult,
  type LiveGuest,
  type LiveGuestRequest,
  type LiveKitCredentials,
  type LiveStartResult
} from "../live/liveSession";
import type { LiveStudioDraft } from "../live/liveStudioReadiness";

export type LiveKitRole = "host" | "guest" | "viewer";

const LIVE_CACHE_KEY = "pulsesoc.native.live.discovery";
const liveStateCacheKey = (liveId: number) => `pulsesoc.native.live.state.${liveId}`;

export type LivePlayback = {
  ok?: boolean;
  live_id?: number;
  status?: string;
  hls_url?: string;
  playback_url?: string;
  mux_playback_id?: string;
  mux_live_status?: string;
  webrtc_room_id?: string;
  poster_url?: string;
  supports_hls?: boolean;
  supports_webrtc?: boolean;
  preferred_transport?: string;
  direct_mode?: boolean;
  state_machine?: string;
};

export type PulseLiveItem = {
  id: number;
  live_id: number;
  type?: string;
  title?: string;
  creator_name?: string;
  category?: string;
  status?: string;
  publish_state?: string;
  stream_health?: string;
  viewer_count?: number;
  reaction_count?: number;
  chat_count?: number;
  thumbnail_url?: string;
  preview_url?: string;
  live_url?: string;
  studio_url?: string;
  playback?: LivePlayback;
  author?: PulseAuthor;
  creator?: PulseAuthor;
  started_at?: string;
  scheduled_at?: string;
  ai_rating?: string;
  momentum?: string;
};

export type PulseLiveChatMessage = {
  id: number;
  live_id?: number;
  user_id?: number;
  body: string;
  display_name?: string;
  message_type?: string;
  moderation_status?: string;
  pinned?: boolean | number;
  created_at?: string;
};

export type PulseLiveState = {
  ok?: boolean;
  live_id: number;
  status?: string;
  publish_state?: string;
  direct_mode?: boolean;
  viewer_count?: number;
  viewer_role?: string;
  accepting_guests?: boolean;
  cohost_enabled?: boolean;
  messages?: PulseLiveChatMessage[];
  playback?: LivePlayback;
  discovery?: PulseLiveItem;
  reaction_cloud?: Record<string, unknown> | unknown[];
  health?: Record<string, unknown>;
  presence?: Record<string, unknown>;
  mux?: {
    live_status?: string;
    playback_id?: string;
    playback_url?: string;
    quota_exhausted?: boolean;
  };
  livekit?: {
    room?: string;
    egress_status?: string;
    egress_error?: string;
  };
  message?: string;
};

export type LiveNowResponse = {
  ok?: boolean;
  items?: PulseLiveItem[];
  scheduled?: PulseLiveItem[];
  events?: PulseLiveItem[];
  trace_id?: string;
  ranking?: string;
  message?: string;
};

export async function listLiveNow(params: { limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 24) });
  const data = await pulseApi<LiveNowResponse>(`/api/pulse/live-now?${query.toString()}`);
  const items = normalizeLiveItems(data.items || []);
  const scheduled = normalizeLiveItems(data.scheduled || data.events || []).filter((item) => isScheduledLive(item));
  await cacheLiveDiscovery({ ...data, items, scheduled }).catch(() => undefined);
  return { ...data, items, scheduled };
}

export async function getLiveState(liveId: number) {
  const data = await pulseApi<PulseLiveState>(`/api/pulse/live/${liveId}/state`);
  const state = normalizeLiveState(data, liveId);
  await writeJsonCache(liveStateCacheKey(liveId), state).catch(() => undefined);
  return state;
}

export async function joinLive(liveId: number) {
  return pulseApi<{ ok?: boolean; status?: string; role?: string; viewer_count?: number; next_url?: string; message?: string }>(
    `/api/pulse/live/${liveId}/join`,
    { method: "POST", body: JSON.stringify({ source: "native" }) }
  );
}

export async function listLiveChat(liveId: number) {
  const data = await pulseApi<{ ok?: boolean; messages?: PulseLiveChatMessage[] }>(`/api/pulse/live/${liveId}/chat`);
  return normalizeLiveChat(data.messages || []);
}

export async function sendLiveChat(liveId: number, body: string) {
  const data = await pulseApi<{ ok?: boolean; message?: string; message_id?: number; moderation_status?: string }>(
    `/api/pulse/live/${liveId}/chat`,
    { method: "POST", body: JSON.stringify({ body }) }
  );
  return {
    ...data,
    chat: normalizeLiveChat([
      {
        id: Number(data.message_id || Date.now()),
        live_id: liveId,
        body,
        display_name: "You",
        moderation_status: data.moderation_status || "approved",
        created_at: new Date().toISOString()
      }
    ])[0]
  };
}

export async function reactToLive(liveId: number, reactionType = "fire") {
  return pulseApi<{ ok?: boolean; reaction_id?: number; reaction_type?: string }>(`/api/pulse/live/${liveId}/react`, {
    method: "POST",
    body: JSON.stringify({ reaction_type: reactionType })
  });
}

export async function loadCachedLiveDiscovery() {
  const cached = await readJsonCache<LiveNowResponse>(LIVE_CACHE_KEY, (data) => ({
    ...data,
    items: normalizeLiveItems(data.items || []),
    scheduled: normalizeLiveItems(data.scheduled || data.events || []).filter((item) => isScheduledLive(item))
  }));
  return cached || { items: [], scheduled: [] };
}

export async function loadCachedLiveState(liveId: number) {
  return readJsonCache<PulseLiveState>(liveStateCacheKey(liveId), (data) => normalizeLiveState(data, liveId));
}

export async function cacheLiveDiscovery(data: LiveNowResponse) {
  await writeJsonCache(LIVE_CACHE_KEY, {
    ...data,
    items: (data.items || []).slice(0, 60),
    scheduled: (data.scheduled || []).slice(0, 40)
  });
}

/**
 * Start a native broadcast. Maps the Live Studio draft into the backend
 * `/api/pulse/live/start` contract and returns the normalized go-live result
 * (live id, LiveKit room, token url, feed post). Throws a real PulseApiError on
 * failure — callers must surface the honest message, never a fake success.
 */
export async function startLive(draft: LiveStudioDraft): Promise<LiveStartResult> {
  const payload = buildLiveStartPayload(draft);
  const data = await pulseApi<Record<string, unknown>>("/api/pulse/live/start", {
    method: "POST",
    body: JSON.stringify({ ...payload, destinations: ["pulse"] })
  });
  const result = normalizeLiveStartResult(data);
  if (!result) {
    throw new Error(typeof data.message === "string" ? data.message : "PulseSoc could not start the broadcast.");
  }
  return result;
}

/**
 * Mint a LiveKit access token for a live room. `role: "host"` requests publish
 * permission (host only), `"guest"` requests a co-host publish token, `"viewer"`
 * a subscribe-only token. Returns null when the backend returns no usable
 * token/url so the caller can surface an honest error instead of a blank preview.
 */
export async function getLiveKitToken(liveId: number, role: LiveKitRole = "viewer"): Promise<LiveKitCredentials | null> {
  const data = await pulseApi<Record<string, unknown>>(`/api/pulse/live/${liveId}/livekit/token`, {
    method: "POST",
    body: JSON.stringify({ role })
  });
  return normalizeLiveKitCredentials(data);
}

export type EndLiveResult = {
  recordingStatus: string;
  replayUrl: string;
  replayAvailable: boolean;
};

/** End a broadcast (host only). Optionally attach a replay url. */
export async function endLive(liveId: number, opts: { replayUrl?: string } = {}): Promise<EndLiveResult> {
  const body: Record<string, unknown> = { source: "native" };
  if (opts.replayUrl) body.replay_url = opts.replayUrl;
  const data = await pulseApi<Record<string, unknown>>(`/api/pulse/live/${liveId}/end`, {
    method: "POST",
    body: JSON.stringify(body)
  });
  return {
    recordingStatus: String(data.recording_status || ""),
    replayUrl: String(data.replay_url || ""),
    replayAvailable: Boolean(data.replay_available)
  };
}

/** List pending guest/co-host join requests for a live (host only). */
export async function listJoinRequests(liveId: number): Promise<LiveGuestRequest[]> {
  const data = await pulseApi<{ requests?: unknown }>(`/api/pulse/live/${liveId}/join-requests`);
  return normalizeGuestRequests(data.requests);
}

/**
 * Host guest-management snapshot: pending join requests plus the active guests
 * already on stage. One call to the real `GET /join-requests` endpoint, which
 * returns both arrays. Host-gated by the backend.
 */
export async function listGuestManagement(liveId: number): Promise<{ requests: LiveGuestRequest[]; guests: LiveGuest[] }> {
  const data = await pulseApi<{ requests?: unknown; guests?: unknown }>(`/api/pulse/live/${liveId}/join-requests`);
  return { requests: normalizeGuestRequests(data.requests), guests: normalizeLiveGuests(data.guests) };
}

/** Mute, unmute, or remove an active guest (host only). Wired to the real guest-action endpoint. */
export async function guestAction(liveId: number, guestId: number, action: "mute" | "unmute" | "remove") {
  return pulseApi<{ ok?: boolean; status?: string; guest_id?: number; message?: string }>(
    `/api/pulse/live/${liveId}/guests/${guestId}/${action}`,
    { method: "POST", body: JSON.stringify({ source: "native" }) }
  );
}

export const muteGuest = (liveId: number, guestId: number) => guestAction(liveId, guestId, "mute");
export const unmuteGuest = (liveId: number, guestId: number) => guestAction(liveId, guestId, "unmute");
export const removeGuest = (liveId: number, guestId: number) => guestAction(liveId, guestId, "remove");

/** Accept or deny a pending guest/co-host join request (host only). */
export async function respondToJoinRequest(liveId: number, requestId: number, action: "accept" | "deny") {
  return pulseApi<{ ok?: boolean; status?: string; message?: string }>(
    `/api/pulse/live/${liveId}/join-requests/${requestId}/${action}`,
    { method: "POST", body: JSON.stringify({ source: "native" }) }
  );
}

/** Ask the host to join a live as a co-host publisher. */
export async function requestToJoinLive(
  liveId: number,
  readiness: { cameraReady?: boolean; micReady?: boolean; networkQuality?: string } = {}
) {
  return pulseApi<{ ok?: boolean; request_id?: number; status?: string; message?: string }>(
    `/api/pulse/live/${liveId}/join-request`,
    {
      method: "POST",
      body: JSON.stringify({
        requested_role: "cohost",
        camera_ready: Boolean(readiness.cameraReady),
        mic_ready: Boolean(readiness.micReady),
        network_quality: String(readiness.networkQuality || "good")
      })
    }
  );
}

/** Cancel your own pending co-host join request. */
export async function cancelJoinRequest(liveId: number, requestId: number) {
  return pulseApi<{ ok?: boolean; status?: string; message?: string }>(
    `/api/pulse/live/${liveId}/join-requests/${requestId}/cancel`,
    { method: "POST", body: JSON.stringify({ source: "native" }) }
  );
}

/** Open the native web viewer for a live. Studio/host broadcasting is fully native — no web handoff. */
export async function openLiveWebFallback(liveId?: number) {
  if (!liveId) return;
  return {
    ok: false,
    liveId,
    target: liveWebUrl(liveId),
    status: "native_provider_boundary",
    message: "Live playback remains inside the native Live viewer until the provider room is available."
  };
}

export function liveWebUrl(liveId?: number) {
  return liveId ? `${PULSE_API_BASE_URL}/pulse/reels?live=${encodeURIComponent(String(liveId))}` : `${PULSE_API_BASE_URL}/pulse/live`;
}

export function normalizeLiveItems(items: PulseLiveItem[]) {
  return (items || []).map(normalizeLiveItem).filter((item) => item.id > 0);
}

export function normalizeLiveItem(item: PulseLiveItem): PulseLiveItem {
  const id = Number(item.live_id || item.id || item.playback?.live_id || 0);
  const playback = normalizePlayback(item.playback || {}, id);
  return {
    ...item,
    id,
    live_id: id,
    title: String(item.title || "PulseSoc Live"),
    creator_name: String(item.creator_name || item.author?.display_name || item.creator?.display_name || "PulseSoc Creator"),
    category: String(item.category || "Live"),
    status: String(item.status || item.publish_state || playback.status || "live"),
    publish_state: String(item.publish_state || item.status || playback.state_machine || "live"),
    stream_health: String(item.stream_health || playback.state_machine || ""),
    viewer_count: Number(item.viewer_count || 0),
    reaction_count: Number(item.reaction_count || 0),
    chat_count: Number(item.chat_count || 0),
    thumbnail_url: String(item.thumbnail_url || item.preview_url || playback.poster_url || ""),
    preview_url: String(item.preview_url || item.thumbnail_url || playback.poster_url || ""),
    live_url: item.live_url || `/pulse/live/${id}`,
    playback
  };
}

export function normalizeLiveState(data: PulseLiveState, liveId: number): PulseLiveState {
  const id = Number(data.live_id || liveId || data.discovery?.id || 0);
  const playback = normalizePlayback(data.playback || { playback_url: data.mux?.playback_url }, id);
  const discovery = data.discovery ? normalizeLiveItem({ ...data.discovery, playback }) : undefined;
  return {
    ...data,
    live_id: id,
    status: String(data.status || discovery?.status || playback.status || "live"),
    publish_state: String(data.publish_state || discovery?.publish_state || playback.state_machine || "live"),
    viewer_count: Number(data.viewer_count || discovery?.viewer_count || 0),
    viewer_role: String(data.viewer_role || "viewer"),
    messages: normalizeLiveChat(data.messages || []),
    playback,
    discovery
  };
}

export function normalizeLiveChat(messages: PulseLiveChatMessage[]) {
  return (messages || [])
    .map((message) => ({
      ...message,
      id: Number(message.id || 0),
      live_id: Number(message.live_id || 0),
      user_id: Number(message.user_id || 0),
      body: String(message.body || ""),
      display_name: String(message.display_name || "Viewer"),
      message_type: String(message.message_type || "text"),
      moderation_status: String(message.moderation_status || "approved"),
      pinned: Boolean(message.pinned),
      created_at: String(message.created_at || "")
    }))
    .filter((message) => message.id > 0 && message.body);
}

export function livePlaybackUrl(item: PulseLiveItem | PulseLiveState | null | undefined) {
  const playback = item?.playback || {};
  const muxId = String(playback.mux_playback_id || "");
  return String(playback.playback_url || playback.hls_url || (muxId ? `https://stream.mux.com/${muxId}.m3u8` : ""));
}

export function livePosterUrl(item: PulseLiveItem | PulseLiveState | null | undefined) {
  return String(item?.playback?.poster_url || ("thumbnail_url" in (item || {}) ? (item as PulseLiveItem).thumbnail_url : "") || "");
}

export function liveSupportsNativePlayback(item: PulseLiveItem | PulseLiveState | null | undefined) {
  const playback = item?.playback || {};
  return Boolean(livePlaybackUrl(item) && (playback.supports_hls || playback.preferred_transport === "hls" || playback.playback_url || playback.hls_url));
}

export function liveSupportsNativeWebRtc(item: PulseLiveItem | PulseLiveState | null | undefined) {
  const playback = item?.playback || {};
  return Boolean(
    playback.supports_webrtc ||
      playback.webrtc_room_id ||
      playback.preferred_transport === "webrtc" ||
      ("livekit" in (item || {}) && Boolean((item as PulseLiveState).livekit?.room))
  );
}

export function isScheduledLive(item: PulseLiveItem) {
  const status = String(item.status || item.publish_state || "").toLowerCase();
  return status === "scheduled" || Boolean(item.scheduled_at);
}

function normalizePlayback(playback: LivePlayback, liveId: number): LivePlayback {
  const muxId = String(playback.mux_playback_id || "");
  const playbackUrl = String(playback.playback_url || playback.hls_url || (muxId ? `https://stream.mux.com/${muxId}.m3u8` : ""));
  return {
    ...playback,
    live_id: Number(playback.live_id || liveId || 0),
    status: String(playback.status || "live"),
    hls_url: String(playback.hls_url || playbackUrl),
    playback_url: playbackUrl,
    mux_playback_id: muxId,
    mux_live_status: String(playback.mux_live_status || ""),
    webrtc_room_id: String(playback.webrtc_room_id || ""),
    poster_url: String(playback.poster_url || ""),
    supports_hls: Boolean(playback.supports_hls || playbackUrl),
    supports_webrtc: Boolean(playback.supports_webrtc || playback.webrtc_room_id),
    preferred_transport: String(playback.preferred_transport || (playbackUrl ? "hls" : playback.webrtc_room_id ? "webrtc" : "waiting")),
    direct_mode: Boolean(playback.direct_mode),
    state_machine: String(playback.state_machine || playback.status || "")
  };
}
