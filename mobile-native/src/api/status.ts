import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL } from "./config";
import { mediaDisplayUrl, mediaKind, PulseAuthor, PulseMedia } from "./feed";
import { pulseApi } from "./pulseApi";

const STATUS_CACHE_PREFIX = "pulsesoc.native.status.";
const statusCacheKey = (lane: string) => `${STATUS_CACHE_PREFIX}${lane || "for_you"}`;

export type PulseStatusMusic = {
  title?: string;
  artist?: string;
  audio_title?: string;
  audio_artist?: string;
  audio_url?: string;
  attached_audio_url?: string;
  preview_url?: string;
  music_id?: string;
  audio_id?: string;
};

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
  reaction_count?: number;
  reply_count?: number;
  share_count?: number;
  author_live?: boolean;
  story_count?: number;
  unseen_count?: number;
  creator_status_ids?: number[];
  can_manage?: boolean;
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

export async function listStatuses(params: { lane?: string } = {}) {
  const lane = params.lane || "for_you";
  const query = new URLSearchParams({ lane });
  const data = await pulseApi<StatusRailResponse>(`/api/pulse/status/rail?${query.toString()}`);
  const items = normalizeStatuses(data.items || []);
  const railItems = normalizeStatuses(data.rail_items || []);
  await cacheStatuses(lane, items, railItems).catch(() => undefined);
  return { ...data, items, rail_items: railItems, lane };
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

export async function reactToStatus(statusId: number, reactionType = "fire") {
  return pulseApi<{ ok?: boolean; status_id?: number; reaction_type?: string; reaction_count?: number }>(`/api/pulse/status/${statusId}/react`, {
    method: "POST",
    body: JSON.stringify({ reaction_type: reactionType })
  });
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

export function pulseStatusUrl(statusId: number) {
  return `${PULSE_API_BASE_URL}/pulse/status?status_id=${encodeURIComponent(String(statusId))}`;
}

export function normalizeStatuses(items: PulseStatus[]) {
  return items.map(normalizeStatus).filter((status) => status.id > 0);
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
