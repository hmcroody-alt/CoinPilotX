import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL, PULSESOC_QA_REELS_FIXTURES } from "./config";
import { mediaDisplayUrl, PulseAuthor, PulseComment, PulseMedia, normalizeComments } from "./feed";
import { pulseApi } from "./pulseApi";
import { isLikelyExpiringMediaUrl, mediaRecordForCache } from "../media/mediaContract";

const REELS_CACHE_KEY = "pulsesoc.native.reels.feed";
const REELS_CACHE_META_KEY = "pulsesoc.native.reels.feed.meta";
const REEL_COMMENT_DRAFT_KEY = "pulsesoc.native.reels.comment_draft";
const reelsCacheKey = (lane = "for_you") => `${REELS_CACHE_KEY}.${lane}`;
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
  /** Music is already digitally mixed into the uploaded MP4; do not attach a second player. */
  audio_baked_in?: boolean;
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
  moderation_status?: string;
  availability?: string;
  visibility_state?: string;
  restriction_reason?: string;
  deleted_at?: string;
  is_removed?: boolean;
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
  content_type?: string;
  post_type?: string;
  live_session_id?: number;
  viewer_follows_author?: boolean;
  live?: {
    live_session_id?: number;
    status?: string;
    publish_state?: string;
    playback_url?: string;
    preview_url?: string;
    viewer_count?: number;
    live_url?: string;
    stream_health?: string;
  };
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

export type CreateReelPayload = {
  title?: string;
  caption?: string;
  category?: string;
  visibility?: "public" | "followers" | "private";
  media_ids: number[];
  music_track_id?: string;
  // Defense-in-depth music metadata (additive; backend stays the source of truth).
  attached_audio_url?: string;
  original_audio_muted?: boolean;
  audio_start_time?: number;
  sound_start_seconds?: number;
  audio_volume?: number;
  audio_baked_in?: boolean;
  share_to_feed?: boolean;
};

export type CreateReelResponse = {
  ok?: boolean;
  success?: boolean;
  reel_id?: number;
  post_id?: number;
  media_id?: number;
  processing_status?: string;
  mux_status?: string;
  next_url?: string;
  reel?: PulseReel;
  data?: { reel?: PulseReel };
  message?: string;
};

export async function createReel(payload: CreateReelPayload) {
  const data = await pulseApi<CreateReelResponse>("/api/pulse/reels/create", {
    method: "POST",
    body: JSON.stringify({
      title: payload.title || "PulseSoc Reel",
      caption: payload.caption || "",
      category: payload.category || "Community",
      visibility: payload.visibility || "public",
      privacy: payload.visibility || "public",
      post_type: "video",
      media_ids: payload.media_ids,
      music_track_id: payload.music_track_id || "",
      audio_track_id: payload.music_track_id || "",
      attached_audio_url: payload.attached_audio_url || "",
      original_audio_muted: payload.original_audio_muted ?? Boolean(payload.music_track_id),
      audio_start_time: payload.audio_start_time ?? 0,
      sound_start_seconds: payload.sound_start_seconds ?? payload.audio_start_time ?? 0,
      audio_volume: payload.audio_volume ?? 1,
      audio_baked_in: Boolean(payload.audio_baked_in),
      share_to_feed: Boolean(payload.share_to_feed)
    })
  });
  return {
    ...data,
    reel_id: Number(data.reel_id || data.reel?.id || data.data?.reel?.id || 0),
    post_id: Number(data.post_id || data.reel?.post_id || data.data?.reel?.post_id || 0),
    reel: data.reel || data.data?.reel
  };
}

export async function listReels(params: { lane?: string; category?: string; limit?: number; offset?: number; includeComments?: boolean } = {}) {
  if (PULSESOC_QA_REELS_FIXTURES) {
    const all = reelsQaFixtures();
    const lane = params.lane || "for_you";
    const filtered = lane === "live" ? all.filter((item) => item.content_type === "live") : lane === "music" ? all.filter((item) => item.audio?.title) : all;
    const offset = Number(params.offset || 0);
    const reels = filtered.slice(offset, offset + Number(params.limit || 8));
    return { ok: true, lane, lane_label: lane.replace(/_/g, " "), reels, has_more: offset + reels.length < filtered.length, next_offset: offset + reels.length };
  }
  const query = new URLSearchParams();
  query.set("limit", String(params.limit || 8));
  query.set("offset", String(params.offset || 0));
  query.set("tab", params.lane || "for_you");
  if (params.category) query.set("category", params.category);
  if (params.includeComments) query.set("include_comments", "1");
  const data = await pulseApi<ReelsFeedResponse>(`/api/pulse/reels/feed?${query.toString()}`);
  const reels = normalizeReels(data.reels || data.data?.reels || []);
  if (!params.offset) await cacheReels(reels, params.lane || "for_you");
  return {
    ...data,
    reels,
    has_more: Boolean(data.has_more),
    next_offset: Number(data.next_offset ?? (params.offset || 0) + reels.length)
  };
}

export async function loadCachedReels(lane = "for_you") {
  try {
    const cached = await AsyncStorage.getItem(reelsCacheKey(lane)) || (lane === "for_you" ? await AsyncStorage.getItem(REELS_CACHE_KEY) : null);
    if (!cached) return [];
    return normalizeReels(JSON.parse(cached) as PulseReel[]);
  } catch {
    await AsyncStorage.removeItem(REELS_CACHE_KEY).catch(() => undefined);
    return [];
  }
}

export async function loadCachedReelsSnapshot(lane = "for_you") {
  const reels = await loadCachedReels(lane);
  let cachedAt = 0;
  try {
    const raw = await AsyncStorage.getItem(`${REELS_CACHE_META_KEY}.${lane}`);
    cachedAt = Number(raw || 0);
  } catch {
    cachedAt = 0;
  }
  return { reels, cachedAt };
}

export async function cacheReels(reels: PulseReel[], lane = "for_you") {
  await Promise.all([
    AsyncStorage.setItem(reelsCacheKey(lane), JSON.stringify(reels.slice(0, 50).map(reelForCache))),
    AsyncStorage.setItem(`${REELS_CACHE_META_KEY}.${lane}`, String(Date.now()))
  ]);
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
  return (await getReelComments(reelId)).comments;
}

export async function getReelComments(reelId: number) {
  const data = await pulseApi<{ ok?: boolean; comments?: PulseComment[]; flat_comments?: PulseComment[]; items?: PulseComment[]; comments_count?: number }>(`/api/pulse/reels/${reelId}/comments`);
  const comments = normalizeComments(data.comments || data.items || data.flat_comments || []);
  return {
    comments,
    flatComments: normalizeComments(data.flat_comments || data.comments || data.items || []),
    commentsCount: Number(data.comments_count ?? data.flat_comments?.length ?? countCommentTree(comments))
  };
}

export async function addReelComment(reelId: number, body: string, parentCommentId = 0) {
  const data = await pulseApi<{ ok?: boolean; comment?: PulseComment; comment_id?: number; message?: string }>(
    `/api/pulse/reels/${reelId}/comments`,
    { method: "POST", body: JSON.stringify({ body, parent_comment_id: parentCommentId }) }
  );
  return data.comment || { id: Number(data.comment_id || Date.now()), comment_id: Number(data.comment_id || Date.now()), body };
}

export async function reactToReelComment(commentId: number, reactionType = "like") {
  return pulseApi<{ ok?: boolean; removed?: boolean; reaction_type?: string; reaction_counts?: Record<string, number> }>(`/api/pulse/reels/comments/${commentId}/react`, {
    method: "POST",
    body: JSON.stringify({ reaction_type: reactionType })
  });
}

export async function editReelComment(commentId: number, body: string) {
  return pulseApi<{ ok?: boolean; comment?: PulseComment; message?: string }>(`/api/pulse/reels/comments/${commentId}`, {
    method: "PATCH",
    body: JSON.stringify({ body })
  });
}

export async function deleteReelComment(commentId: number) {
  return pulseApi<{ ok?: boolean; deleted?: boolean; message?: string }>(`/api/pulse/reels/comments/${commentId}`, {
    method: "DELETE"
  });
}

export async function deleteReel(reelId: number) {
  return pulseApi<{ ok?: boolean; message?: string; reel_id?: number; trace_id?: string }>(`/api/pulse/reels/${reelId}`, {
    method: "DELETE"
  });
}

export async function reactToReel(reelId: number, reactionType = "fire") {
  return pulseApi<{ ok?: boolean; removed?: boolean; reaction_type?: string; reaction_counts?: Record<string, number>; reel_id?: number }>(
    `/api/pulse/reels/${reelId}/react`,
    { method: "POST", body: JSON.stringify({ reaction_type: reactionType }) }
  );
}

// `saveReel(reelId)` used to live here: an empty-bodied toggle. It is gone
// rather than aliased, because a toggle cannot be retried safely — a dropped
// response followed by a retry undoes the save — and because a second way to
// save a Reel is how a Reel came to look saved on one screen and unsaved on the
// next. Reels save through `social/saveContract.setSavedOnServer` like
// everything else, which states the wanted state instead of asking for a flip.

export type ReelRepostResponse = {
  ok?: boolean;
  message?: string;
  post_id?: number;
  reel_id?: number;
  original_post_id?: number;
  reposted?: boolean;
  is_reposted?: boolean;
  repost_count?: number;
  removed?: boolean;
};

/**
 * Repost or un-repost a reel. `undo` maps to DELETE.
 *
 * Shares its backend with repostPost in api/feed.ts, because a reel repost is a
 * pulse_posts row pointing at the reel's post exactly as a post's repost is. The
 * response therefore carries the same `reposted` flag and `repost_count` a feed
 * caller gets, which is what lets this be a toggle rather than the one-way button
 * it used to be.
 */
export async function repostReel(reelId: number, options: { undo?: boolean } = {}) {
  const undo = Boolean(options.undo);
  return pulseApi<ReelRepostResponse>(`/api/pulse/reels/${reelId}/repost`, {
    method: undo ? "DELETE" : "POST",
    body: JSON.stringify({ undo })
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

export async function reportReelComment(commentId: number, reason = "reported from native Reels") {
  return pulseApi<{ ok?: boolean; report_id?: number; message?: string }>("/api/pulse/report", {
    method: "POST",
    body: JSON.stringify({ target_type: "reel_comment", target_id: commentId, reason })
  });
}

export type ReelCommentDraft = {
  body: string;
  replyToCommentId: number;
  updatedAt: number;
};

export async function loadReelCommentDraft(reelId: number): Promise<ReelCommentDraft | null> {
  try {
    const raw = await AsyncStorage.getItem(`${REEL_COMMENT_DRAFT_KEY}.${reelId}`);
    if (!raw) return null;
    const draft = JSON.parse(raw) as ReelCommentDraft;
    return String(draft.body || "").trim() ? { body: String(draft.body), replyToCommentId: Number(draft.replyToCommentId || 0), updatedAt: Number(draft.updatedAt || 0) } : null;
  } catch {
    await clearReelCommentDraft(reelId);
    return null;
  }
}

export async function saveReelCommentDraft(reelId: number, body: string, replyToCommentId = 0) {
  const cleanBody = String(body || "");
  if (!cleanBody.trim()) return clearReelCommentDraft(reelId);
  const draft: ReelCommentDraft = { body: cleanBody, replyToCommentId: Number(replyToCommentId || 0), updatedAt: Date.now() };
  await AsyncStorage.setItem(`${REEL_COMMENT_DRAFT_KEY}.${reelId}`, JSON.stringify(draft));
}

export async function clearReelCommentDraft(reelId: number) {
  await AsyncStorage.removeItem(`${REEL_COMMENT_DRAFT_KEY}.${reelId}`).catch(() => undefined);
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
  const seen = new Set<number>();
  return items.map(normalizeReel).filter((reel) => {
    if (reel.id === 0 || !Number.isFinite(reel.id) || seen.has(reel.id)) return false;
    seen.add(reel.id);
    return true;
  });
}

function countCommentTree(comments: PulseComment[]): number {
  return comments.reduce((total, comment) => total + 1 + countCommentTree(comment.replies || []), 0);
}

export function normalizeReel(item: PulseReel): PulseReel {
  const liveId = Number(item.live_session_id || item.live?.live_session_id || 0);
  const isLive = String(item.content_type || item.post_type || "").toLowerCase() === "live" || liveId > 0;
  const id = isLive && liveId ? -Math.abs(liveId) : Number(item.reel_id || item.id || 0);
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

function reelForCache(reel: PulseReel): PulseReel {
  const audio = reel.audio ? { ...reel.audio } : undefined;
  if (audio?.audio_url && isLikelyExpiringMediaUrl(audio.audio_url)) delete audio.audio_url;
  if (audio?.attached_audio_url && isLikelyExpiringMediaUrl(audio.attached_audio_url)) delete audio.attached_audio_url;
  if (audio?.preview_url && isLikelyExpiringMediaUrl(audio.preview_url)) delete audio.preview_url;
  return { ...reel, media: (reel.media || []).map(mediaRecordForCache), audio };
}

function reelsQaFixtures(): PulseReel[] {
  const video = "https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_ts/master.m3u8";
  const poster = `${PULSE_API_BASE_URL}/static/brand/pulsesoc-logo-20260606.png`;
  const author = { user_id: 9001, display_name: "Nova Signal", username: "novasignal", public_player_id: "qa-nova" };
  return normalizeReels([
    { id: 81001, reel_id: 81001, title: "A living signal", caption: "Video stays dominant. Comments remain hidden until you ask for them. #PulseSoc #Future", author, video_url: video, poster_url: poster, processing_status: "ready", reactions_count: 1284, reaction_counts: { fire: 740, smart: 544 }, comments_count: 86, share_count: 31, view_count: 18420, preview_comments: qaComments(81001) },
    { id: 81002, reel_id: 81002, title: "Invisible soundtrack", caption: "Attached music is functional without becoming the interface.", author: { ...author, display_name: "Luna Current", username: "lunacurrent" }, video_url: video, poster_url: poster, processing_status: "ready", reactions_count: 402, comments_count: 12, audio: { id: 72, track_id: 72, title: "Quiet Orbit", artist: "PulseSoc Music", attached_audio_url: video, audio_start_time: 0, audio_volume: 0.18, original_audio_muted: true } },
    { id: 81003, reel_id: 81003, title: "Long caption stress test", caption: "This intentionally long caption verifies truncation, contrast, action-rail clearance, compact width behavior, and the fact that metadata never takes over the moving image. The full story remains available without burying the Reel.", author, video_url: video, poster_url: poster, processing_status: "ready", reactions_count: 0, comments_count: 0 },
    { id: 81004, reel_id: 81004, title: "Processing signal", caption: "The upload remains recoverable while media processing completes.", author, video_url: video, poster_url: poster, processing_status: "processing", comments_count: 0, reactions_count: 0 },
    { id: "live-81005" as unknown as number, reel_id: "live-81005" as unknown as number, content_type: "live", post_type: "live", live_session_id: 81005, title: "PulseSoc Live inside Reels", caption: "Realtime broadcast", author, live: { live_session_id: 81005, status: "live", playback_url: video, viewer_count: 317, stream_health: "active" }, media: [{ media_type: "video", media_url: video, playback_url: video, poster_url: poster, has_audio: true }], reactions_count: 91, comments_count: 24, view_count: 317 }
  ] as PulseReel[]);
}

function qaComments(postId: number): PulseComment[] {
  return [{
    id: 91001,
    comment_id: 91001,
    post_id: postId,
    user_id: 9011,
    body: "The video still owns the screen. This feels fast.",
    created_at: new Date().toISOString(),
    can_edit: false,
    can_delete: false,
    reaction_counts: { like: 8 },
    author: { user_id: 9011, display_name: "Mira Flux", username: "miraflux" },
    reply_count: 1,
    replies: [{
      id: 91002,
      comment_id: 91002,
      parent_comment_id: 91001,
      post_id: postId,
      user_id: 9001,
      body: "Replies stay attached to the canonical thread.",
      created_at: new Date().toISOString(),
      can_edit: true,
      can_delete: true,
      reaction_counts: { like: 3 },
      author: { user_id: 9001, display_name: "Nova Signal", username: "novasignal" },
      replies: [],
    }],
  }];
}
