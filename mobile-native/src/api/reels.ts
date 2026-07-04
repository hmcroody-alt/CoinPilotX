import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL } from "./config";
import { mediaDisplayUrl, PulseAuthor, PulseComment, PulseMedia, normalizeComments } from "./feed";
import { pulseApi } from "./pulseApi";

const REELS_CACHE_KEY = "pulsesoc.native.reels.feed";
const reelDetailCacheKey = (reelId: number) => `pulsesoc.native.reels.detail.${reelId}`;

export type PulseReelAudio = {
  id?: number;
  track_id?: number;
  title?: string;
  artist?: string;
  audio_url?: string;
  attached_audio_url?: string;
  preview_url?: string;
  audio_start_time?: number;
  audio_volume?: number;
  original_audio_muted?: boolean;
};

export type PulseReel = {
  id: number;
  reel_id: number;
  post_id?: number;
  user_id?: number;
  title?: string;
  caption?: string;
  body?: string;
  category?: string;
  created_at?: string;
  human_time?: string;
  author?: PulseAuthor;
  media?: PulseMedia[];
  audio?: PulseReelAudio;
  video_url?: string;
  poster_url?: string;
  processing_status?: string;
  transcoding_status?: string;
  comments_disabled?: boolean;
  reactions_disabled?: boolean;
  can_manage?: boolean;
  viewer_reaction?: string;
  reaction_counts?: Record<string, number>;
  reactions_count?: number;
  comments_count?: number;
  comment_count?: number;
  share_count?: number;
  replay_count?: number;
  view_count?: number;
  saved?: boolean;
  reposted?: boolean;
  ai_tags?: string[];
  tags?: string[];
  preview_comments?: PulseComment[];
};

export type ReelsFeedResponse = {
  ok?: boolean;
  data?: { reels?: PulseReel[] };
  reels?: PulseReel[];
  categories?: string[];
  lane?: string;
  lane_label?: string;
  has_more?: boolean;
  next_offset?: number;
};

export async function listReels(params: { lane?: string; category?: string; limit?: number; offset?: number; includeComments?: boolean } = {}) {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit || 8));
  query.set("offset", String(params.offset || 0));
  query.set("tab", params.lane || "for_you");
  if (params.category) query.set("category", params.category);
  if (params.includeComments) query.set("include_comments", "1");
  const data = await pulseApi<ReelsFeedResponse>(`/api/pulse/reels/feed?${query.toString()}`);
  const reels = normalizeReels(data.reels || data.data?.reels || []);
  if (!params.offset) await cacheReels(reels);
  return {
    ...data,
    reels,
    has_more: Boolean(data.has_more),
    next_offset: Number(data.next_offset ?? (params.offset || 0) + reels.length)
  };
}

export async function loadCachedReels() {
  try {
    const cached = await AsyncStorage.getItem(REELS_CACHE_KEY);
    if (!cached) return [];
    return normalizeReels(JSON.parse(cached) as PulseReel[]);
  } catch {
    await AsyncStorage.removeItem(REELS_CACHE_KEY).catch(() => undefined);
    return [];
  }
}

export async function cacheReels(reels: PulseReel[]) {
  await AsyncStorage.setItem(REELS_CACHE_KEY, JSON.stringify(reels.slice(0, 50)));
}

export async function loadCachedReelDetail(reelId: number) {
  try {
    const cached = await AsyncStorage.getItem(reelDetailCacheKey(reelId));
    if (!cached) return null;
    const parsed = JSON.parse(cached) as { reel?: PulseReel; comments?: PulseComment[] };
    return {
      reel: parsed.reel ? normalizeReel(parsed.reel) : undefined,
      comments: normalizeComments(parsed.comments || [])
    };
  } catch {
    await AsyncStorage.removeItem(reelDetailCacheKey(reelId)).catch(() => undefined);
    return null;
  }
}

export async function getReelDetail(reelId: number) {
  const comments = await listReelComments(reelId);
  const cached = await loadCachedReelDetail(reelId);
  const reel = cached?.reel;
  const detail = { reel, comments };
  await AsyncStorage.setItem(reelDetailCacheKey(reelId), JSON.stringify(detail)).catch(() => undefined);
  return detail;
}

export async function listReelComments(reelId: number) {
  const data = await pulseApi<{ ok?: boolean; comments?: PulseComment[]; flat_comments?: PulseComment[]; items?: PulseComment[] }>(
    `/api/pulse/reels/${reelId}/comments`
  );
  return normalizeComments(data.flat_comments || data.comments || data.items || []);
}

export async function addReelComment(reelId: number, body: string) {
  const data = await pulseApi<{ ok?: boolean; comment?: PulseComment; comment_id?: number; message?: string }>(
    `/api/pulse/reels/${reelId}/comments`,
    { method: "POST", body: JSON.stringify({ body, parent_comment_id: 0 }) }
  );
  return data.comment || { id: Number(data.comment_id || Date.now()), comment_id: Number(data.comment_id || Date.now()), body };
}

export async function reactToReel(reelId: number, reactionType = "fire") {
  return pulseApi<{ ok?: boolean; removed?: boolean; reaction_type?: string; reaction_counts?: Record<string, number>; reel_id?: number }>(
    `/api/pulse/reels/${reelId}/react`,
    { method: "POST", body: JSON.stringify({ reaction_type: reactionType }) }
  );
}

export async function saveReel(reelId: number) {
  return pulseApi<{ ok?: boolean; saved?: boolean; message?: string }>(`/api/pulse/reels/${reelId}/save`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function repostReel(reelId: number) {
  return pulseApi<{ ok?: boolean; message?: string; post_id?: number }>(`/api/pulse/reels/${reelId}/repost`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function shareReel(reelId: number) {
  return pulseApi<{ ok?: boolean; share_url?: string; message?: string }>(`/api/pulse/reels/${reelId}/share`, {
    method: "POST",
    body: JSON.stringify({ channel: "native" })
  });
}

export async function markReelNotInterested(reelId: number) {
  return pulseApi<{ ok?: boolean; hidden?: boolean; message?: string }>(`/api/pulse/reels/${reelId}/not-interested`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function followReelCreator(reelId: number) {
  return pulseApi<{ ok?: boolean; following?: boolean; message?: string; creator_user_id?: number }>(`/api/pulse/reels/${reelId}/follow-creator`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function reportReel(reelId: number, reason = "reported from native Reels") {
  return pulseApi<{ ok?: boolean; report_id?: number; message?: string }>("/api/pulse/report", {
    method: "POST",
    body: JSON.stringify({ target_type: "reel", target_id: reelId, reason })
  });
}

export async function trackReelView(reelId: number, watchMs = 0) {
  return pulseApi<{ ok?: boolean; view_count?: number; reel_id?: number }>(`/api/pulse/reels/${reelId}/view`, {
    method: "POST",
    body: JSON.stringify({ watch_ms: watchMs, source: "native" })
  });
}

export function reelWebUrl(reelId: number) {
  return `${PULSE_API_BASE_URL}/pulse/reels/${reelId}`;
}

export function normalizeReels(items: PulseReel[]) {
  return items.map(normalizeReel).filter((reel) => reel.id > 0);
}

export function normalizeReel(item: PulseReel): PulseReel {
  const id = Number(item.reel_id || item.id || 0);
  const media = normalizeReelMedia(item);
  return {
    ...item,
    id,
    reel_id: id,
    caption: item.caption || item.body || "",
    media,
    author: item.author || {},
    reaction_counts: normalizeReactionCounts(item.reaction_counts || {}),
    comments_count: Number(item.comments_count ?? item.comment_count ?? item.preview_comments?.length ?? 0),
    reactions_count: Number(item.reactions_count || 0),
    replay_count: Number(item.replay_count || item.view_count || 0),
    saved: Boolean(item.saved),
    reposted: Boolean(item.reposted),
    preview_comments: normalizeComments(item.preview_comments || [])
  };
}

export function reelVideoUrl(reel: PulseReel) {
  const media = (reel.media || [])[0] || {};
  const muxPlaybackId = String(media.mux_playback_id || "").trim();
  if (muxPlaybackId) return `https://stream.mux.com/${muxPlaybackId}.m3u8`;
  return mediaDisplayUrl({
    ...media,
    media_url: media.playback_url || media.hls_url || media.media_url || reel.video_url || ""
  });
}

export function reelPosterUrl(reel: PulseReel) {
  const media = (reel.media || [])[0] || {};
  return mediaDisplayUrl({
    ...media,
    media_url: media.poster_url || media.thumbnail_url || reel.poster_url || ""
  });
}

export function reelIsPlayable(reel: PulseReel) {
  const status = String(reel.processing_status || reel.transcoding_status || "").toLowerCase();
  if (status && !["ready", "asset_ready", "available", "completed"].includes(status)) return false;
  return Boolean(reelVideoUrl(reel));
}

function normalizeReelMedia(item: PulseReel) {
  const media = [...(item.media || [])];
  if (!media.length && item.video_url) {
    media.push({ media_type: "video", media_url: item.video_url, poster_url: item.poster_url });
  }
  return media;
}

function normalizeReactionCounts(counts: Record<string, number>) {
  return Object.fromEntries(Object.entries(counts || {}).map(([key, value]) => [key, Number(value || 0)]));
}
