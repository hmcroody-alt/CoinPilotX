import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL, PULSESOC_QA_STATUS_FIXTURES } from "./config";
import { mediaDisplayUrl, mediaKind, PulseAuthor, PulseMedia } from "./feed";
import { pulseApi, PulseApiError } from "./pulseApi";

const STATUS_CACHE_PREFIX = "pulsesoc.native.status.";
const statusCacheKey = (lane: string) => `${STATUS_CACHE_PREFIX}${lane || "for_you"}`;

export type PulseStatusMusic = {
  id?: string | number;
  track_id?: string | number;
  title?: string;
  artist?: string;
  audio_title?: string;
  audio_artist?: string;
  audio_url?: string;
  attached_audio_url?: string;
  preview_url?: string;
  music_id?: string;
  audio_id?: string;
  mood?: string;
  genre?: string;
  duration_seconds?: number;
};

export type StatusVisibility = "public" | "followers" | "private";
export type StatusType = "text" | "photo" | "video" | "music" | "live" | "ai";

/**
 * Status reaction vocabulary for the native icon action rail.
 *
 * The production route `/api/pulse/status/<id>/react` accepts any freeform
 * string up to 40 chars (no server-side enum) and always REPLACES the
 * caller's prior reaction row rather than supporting removal. This fixed
 * list is the client's own curated, presentable vocabulary — every value
 * in it is a legal, acceptable `reaction_type` for that route, so nothing
 * here is "unsupported" by the backend. Do not add a value the backend
 * would reject, and do not add a "none"/removal value: removal is not a
 * verified backend contract (see reports/pulsesoc_native_status_futuristic_icon_actions_2026-07-19.md).
 */
export type StatusReactionType = "love" | "fire" | "clap" | "laugh" | "wow" | "hundred" | "pulse";

export const DEFAULT_STATUS_REACTION: StatusReactionType = "love";

export const STATUS_REACTIONS: Array<{ type: StatusReactionType; label: string; icon: string; iconFilled: string; color: string }> = [
  { type: "love", label: "Love", icon: "heart-outline", iconFilled: "heart", color: "#ff5fa8" },
  { type: "fire", label: "Fire", icon: "flame-outline", iconFilled: "flame", color: "#ff8a3d" },
  { type: "clap", label: "Applause", icon: "thumbs-up-outline", iconFilled: "thumbs-up", color: "#61eaf6" },
  { type: "laugh", label: "Laugh", icon: "happy-outline", iconFilled: "happy", color: "#ffd166" },
  { type: "wow", label: "Mind blown", icon: "flash-outline", iconFilled: "flash", color: "#b98bff" },
  { type: "hundred", label: "Hundred", icon: "ribbon-outline", iconFilled: "ribbon", color: "#36e58f" },
  { type: "pulse", label: "Pulse", icon: "pulse-outline", iconFilled: "pulse", color: "#61eaf6" }
];

export type PulseStatus = {
  id: number;
  status_id: number;
  user_id?: number;
  author?: PulseAuthor;
  author_name?: string;
  author_avatar_url?: string;
  status_type?: string;
  body?: string;
  visibility?: string;
  media_ids?: number[];
  media?: PulseMedia[];
  music?: PulseStatusMusic;
  ai_context?: Record<string, unknown>;
  created_at?: string;
  expires_at?: string;
  viewed?: boolean;
  view_count?: number;
  completion_rate?: number;
  owner_analytics?: {
    views?: number;
    completion_rate?: number;
    replies?: number;
    reactions?: number;
    shares?: number;
  };
  reaction_count?: number;
  reply_count?: number;
  share_count?: number;
  /**
   * Client-local only. The production rail/list payload never returns a
   * per-viewer reaction (only an aggregate `reaction_count` — confirmed by
   * backend audit, see report "Known limitations"), so this is set purely
   * from local optimistic/confirmed state after a reaction is sent in the
   * current session, and is not persisted or trustworthy across app restarts.
   */
  viewer_reaction?: StatusReactionType | string;
  author_live?: boolean;
  story_count?: number;
  unseen_count?: number;
  creator_status_ids?: number[];
  can_manage?: boolean;
  muted?: boolean;
  fixture_state?: "ready" | "uploading" | "failed" | "expired" | "deleted" | "private" | "blocked" | "reported" | "offline_queued";
};

export type StatusRailResponse = {
  ok?: boolean;
  items?: PulseStatus[];
  rail_items?: PulseStatus[];
  lane?: string;
  lanes?: string[];
  discovery_signal?: Record<string, unknown>;
  trace_id?: string;
};

export type CreateStatusPayload = {
  status_type: StatusType;
  body?: string;
  visibility?: StatusVisibility;
  duration_hours?: number;
  media_ids?: number[];
  music_media_id?: number;
  music_track_id?: string | number;
  effect_name?: string;
  sticker?: string;
  link_url?: string;
  ai_context?: Record<string, unknown>;
};

export type StatusCreateResponse = {
  ok?: boolean;
  success?: boolean;
  status?: PulseStatus;
  status_id?: number;
  media_url?: string;
  message?: string;
  trace_id?: string;
};

export type StatusAiStory = {
  caption?: string;
  style?: string;
  tags?: string[];
  visual?: Record<string, unknown>;
  [key: string]: unknown;
};

export type StatusAiStoryResponse = {
  ok?: boolean;
  story?: StatusAiStory;
  trace_id?: string;
};

export type StatusMusicResponse = {
  ok?: boolean;
  items?: PulseStatusMusic[];
  provider?: string;
  trace_id?: string;
};

export async function listStatuses(params: { lane?: string } = {}) {
  const lane = params.lane || "for_you";
  const query = new URLSearchParams({ lane });
  const data = await pulseApi<StatusRailResponse>(`/api/pulse/status/rail?${query.toString()}`);
  const items = PULSESOC_QA_STATUS_FIXTURES ? statusQaFixtures() : normalizeStatuses(data.items || []);
  const railItems = PULSESOC_QA_STATUS_FIXTURES ? items.filter((item) => !["expired", "deleted", "blocked"].includes(item.fixture_state || "")) : normalizeStatuses(data.rail_items || []);
  await cacheStatuses(lane, items, railItems).catch(() => undefined);
  return { ...data, items, rail_items: railItems, lane };
}

export async function createStatus(payload: CreateStatusPayload) {
  const data = await pulseApi<StatusCreateResponse>("/api/pulse/status", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return {
    ...data,
    status_id: Number(data.status_id || data.status?.id || data.status?.status_id || 0),
    status: data.status ? normalizeStatus(data.status) : undefined
  };
}

export async function searchStatusMusic(params: { query?: string; limit?: number } = {}) {
  const query = new URLSearchParams({
    q: params.query || "",
    limit: String(params.limit || 8)
  });
  return pulseApi<StatusMusicResponse>(`/api/pulse/status/music/search?${query.toString()}`);
}

export async function listTrendingStatusMusic(params: { limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 8) });
  return pulseApi<StatusMusicResponse>(`/api/pulse/status/music/trending?${query.toString()}`);
}

export async function generateStatusAiStory(prompt: string, style = "cinematic") {
  return pulseApi<StatusAiStoryResponse>("/api/pulse/status/ai-story", {
    method: "POST",
    body: JSON.stringify({ prompt, style })
  });
}

export async function loadCachedStatuses(lane = "for_you") {
  try {
    const cached = await AsyncStorage.getItem(statusCacheKey(lane));
    if (!cached) return { items: [], rail_items: [] };
    const parsed = JSON.parse(cached) as { items?: PulseStatus[]; rail_items?: PulseStatus[] };
    return {
      items: normalizeStatuses(parsed.items || []),
      rail_items: normalizeStatuses(parsed.rail_items || [])
    };
  } catch {
    await AsyncStorage.removeItem(statusCacheKey(lane)).catch(() => undefined);
    return { items: [], rail_items: [] };
  }
}

export async function cacheStatuses(lane: string, items: PulseStatus[], railItems: PulseStatus[]) {
  await AsyncStorage.setItem(statusCacheKey(lane), JSON.stringify({ items: items.slice(0, 80), rail_items: railItems.slice(0, 24) }));
}

export async function trackStatusView(statusId: number, params: { completed?: boolean; completionRatio?: number; watchMs?: number } = {}) {
  return pulseApi<{ ok?: boolean; status_id?: number; view_count?: number; completion_rate?: number }>(`/api/pulse/status/${statusId}/view`, {
    method: "POST",
    body: JSON.stringify({
      completed: Boolean(params.completed),
      completion_ratio: Number(params.completionRatio || 0),
      watch_ms: Number(params.watchMs || 0),
      source: "native"
    })
  });
}

export async function reactToStatus(statusId: number, reactionType: string = DEFAULT_STATUS_REACTION) {
  return pulseApi<{ ok?: boolean; status_id?: number; reaction_type?: string; reaction_count?: number }>(`/api/pulse/status/${statusId}/react`, {
    method: "POST",
    body: JSON.stringify({ reaction_type: reactionType })
  });
}

/** Concise, user-facing copy for a failed Status reaction, mirroring api/deleteErrors.ts's status-code mapping. */
export function describeStatusReactionError(err: unknown): string {
  if (err instanceof PulseApiError) {
    if (err.status === 401) return "Your session expired. Sign in again to react.";
    if (err.status === 404) return "This Status is no longer available.";
    if (err.status === 429) return "Too many attempts. Wait a moment and try again.";
    if (err.status >= 500) return "Reaction could not be sent right now. Try again.";
    return err.message || "Reaction could not be sent.";
  }
  if (err instanceof TypeError || (err instanceof Error && /network/i.test(err.message))) {
    return "You're offline. Reconnect to react.";
  }
  return "Reaction could not be sent.";
}

export async function replyToStatus(statusId: number, body: string) {
  return pulseApi<{ ok?: boolean; reply?: { id?: number; status_id?: number; body?: string; created_at?: string }; trace_id?: string }>(
    `/api/pulse/status/${statusId}/reply`,
    { method: "POST", body: JSON.stringify({ body }) }
  );
}

export async function shareStatus(statusId: number) {
  return pulseApi<{ ok?: boolean; status_id?: number; share_count?: number; trace_id?: string }>(`/api/pulse/status/${statusId}/share`, {
    method: "POST",
    body: JSON.stringify({ surface: "native" })
  });
}

export async function updateStatus(statusId: number, payload: { body?: string; visibility?: StatusVisibility }) {
  const data = await pulseApi<StatusCreateResponse>(`/api/pulse/status/${statusId}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
  return { ...data, status: data.status ? normalizeStatus(data.status) : undefined };
}

export async function deleteStatus(statusId: number) {
  const result = await pulseApi<{ ok?: boolean; status_id?: number; message?: string }>(`/api/pulse/status/${statusId}`, { method: "DELETE" });
  await removeStatusFromCache(statusId).catch(() => undefined);
  return result;
}

export async function removeStatusFromCache(statusId: number, lane = "for_you") {
  const cached = await loadCachedStatuses(lane);
  await cacheStatuses(lane, cached.items.filter((item) => item.id !== statusId), cached.rail_items.filter((item) => item.id !== statusId));
}

export function reconcileStatusItems(current: PulseStatus[], incoming: PulseStatus[], now = Date.now()) {
  const byId = new Map<number, PulseStatus>();
  [...incoming, ...current].forEach((item) => {
    const status = normalizeStatus(item);
    const expired = status.expires_at ? Date.parse(status.expires_at) <= now : false;
    if (status.id > 0 && !expired && !["deleted", "blocked", "expired"].includes(status.fixture_state || "") && !byId.has(status.id)) byId.set(status.id, status);
  });
  return Array.from(byId.values()).sort((a, b) => Date.parse(b.created_at || "") - Date.parse(a.created_at || ""));
}

export function pulseStatusUrl(statusId: number) {
  return `${PULSE_API_BASE_URL}/pulse/status?status_id=${encodeURIComponent(String(statusId))}`;
}

export function normalizeStatuses(items: PulseStatus[]) {
  return items.map(normalizeStatus).filter((status) => status.id > 0);
}

export function statusQaFixtures(): PulseStatus[] {
  if (!PULSESOC_QA_STATUS_FIXTURES) return [];
  const now = Date.now();
  const image = `${PULSE_API_BASE_URL}/static/uploads/pulse_media/2026/07/04/status-audit-ed8ead33bcab09b0.png`;
  const video = `${PULSE_API_BASE_URL}/static/uploads/pulse_media/2026/07/04/status-audit-1f07a372cd135689.webm`;
  const fixture = (id: number, status_type: StatusType, body: string, options: Partial<PulseStatus> = {}): PulseStatus => normalizeStatus({
    id, status_id: id, user_id: 7000 + id, status_type, body, visibility: "public",
    created_at: new Date(now - id * 42_000).toISOString(), expires_at: new Date(now + 86_400_000).toISOString(),
    author: { id: 7000 + id, user_id: 7000 + id, display_name: `Status Pilot ${id - 9100}`, username: `status_pilot_${id}`, avatar_url: "" },
    story_count: 1, unseen_count: 1, view_count: 18 + id % 9, reaction_count: id % 5, reply_count: id % 3, share_count: id % 4,
    ...options
  });
  return [
    fixture(9101, "text", "The native Status constellation is live. Fast, calm, and unmistakably PulseSoc.", { can_manage: true, author: { id: 1, user_id: 1, display_name: "Your Status", username: "native_owner", avatar_url: "" }, view_count: 48, reaction_count: 12, reply_count: 5, share_count: 3 }),
    fixture(9102, "photo", "A luminous image Status with a long caption that remains readable across every iPhone width without hiding the viewer controls.", { media: [{ id: 8102, media_url: image, valid_url: image, media_type: "image" }], story_count: 3 }),
    fixture(9103, "video", "Video Status · original sound", { media: [{ id: 8103, media_url: video, valid_url: video, media_type: "video", thumbnail_url: image }], viewed: true, unseen_count: 0 }),
    fixture(9104, "music", "Creator-safe music Status", { music: { track_id: "qa-orbit", title: "Orbit Signal", artist: "PulseSoc Audio", mood: "galactic" } }),
    fixture(9105, "photo", "Image with subtle music attribution", { media: [{ id: 8105, media_url: image, valid_url: image, media_type: "image" }], music: { track_id: "qa-drift", title: "Soft Drift", artist: "PulseSoc Audio" } }),
    fixture(9106, "ai", "AI-assisted Status caption, with internal tooling and model details kept private.", { ai_context: { fixture: true } }),
    fixture(9107, "live", "Live creator preview", { author_live: true, story_count: 2 }),
    fixture(9108, "text", "Muted creator Status", { muted: true, viewed: true, unseen_count: 0 }),
    fixture(9109, "text", "Uploading Status", { fixture_state: "uploading" }),
    fixture(9110, "photo", "Upload failed. Retry is available.", { fixture_state: "failed", media: [{ id: 8110, media_url: image, valid_url: image, media_type: "image" }] }),
    fixture(9111, "text", "Private audience fixture", { fixture_state: "private", visibility: "private" }),
    fixture(9112, "text", "Offline queued Status", { fixture_state: "offline_queued" }),
    fixture(9113, "text", "Expired fixture", { fixture_state: "expired", expires_at: new Date(now - 1_000).toISOString() }),
    fixture(9114, "text", "Deleted fixture", { fixture_state: "deleted" }),
    fixture(9115, "text", "Reported fixture", { fixture_state: "reported" }),
    fixture(9116, "text", "Blocked fixture", { fixture_state: "blocked" })
  ];
}

export function normalizeStatus(item: PulseStatus): PulseStatus {
  const id = Number(item.status_id || item.id || 0);
  const media = normalizeStatusMedia(item);
  return {
    ...item,
    id,
    status_id: id,
    author: normalizeStatusAuthor(item),
    body: String(item.body || ""),
    media,
    view_count: Number(item.view_count || 0),
    reaction_count: Number(item.reaction_count || 0),
    reply_count: Number(item.reply_count || 0),
    share_count: Number(item.share_count || 0),
    story_count: Number(item.story_count || 1),
    unseen_count: Number(item.unseen_count || 0),
    creator_status_ids: (item.creator_status_ids || []).map(Number).filter(Boolean),
    viewed: Boolean(item.viewed)
  };
}

export function statusMediaUrl(status: PulseStatus) {
  const media = (status.media || [])[0] || {};
  return mediaDisplayUrl({
    ...media,
    media_url: media.valid_url || media.playback_url || media.hls_url || media.media_url || media.url || ""
  });
}

export function statusPosterUrl(status: PulseStatus) {
  const media = (status.media || [])[0] || {};
  return mediaDisplayUrl({
    ...media,
    media_url: media.poster_url || media.thumbnail_url || media.valid_url || media.media_url || ""
  });
}

export function statusMediaKind(status: PulseStatus) {
  const media = (status.media || [])[0];
  if (!media) return status.status_type === "text" ? "text" : "file";
  return mediaKind(media);
}

export function statusMusicLabel(status: PulseStatus) {
  const music = status.music || {};
  const title = music.audio_title || music.title || "";
  const artist = music.audio_artist || music.artist || "";
  if (title && artist) return `${title} · ${artist}`;
  return title || artist;
}

function normalizeStatusAuthor(item: PulseStatus): PulseAuthor {
  return {
    ...(item.author || {}),
    id: Number(item.user_id || item.author?.id || 0),
    user_id: Number(item.user_id || item.author?.user_id || 0),
    display_name: item.author?.display_name || item.author?.name || item.author_name || "PulseSoc member",
    avatar_url: item.author?.avatar_url || item.author_avatar_url || ""
  };
}

function normalizeStatusMedia(item: PulseStatus) {
  return (item.media || []).filter((media) => Boolean(mediaDisplayUrl(media)));
}
