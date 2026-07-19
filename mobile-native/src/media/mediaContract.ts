export type CanonicalMediaKind = "image" | "video" | "audio" | "voice" | "file" | "live";

export type CanonicalMediaRecord = {
  id?: number;
  media_id?: number;
  attachment_id?: number;
  public_id?: string;
  owner_user_id?: number;
  post_id?: number;
  reel_id?: number;
  message_id?: number;
  status_id?: number;
  type?: string;
  media_type?: string;
  mime_type?: string;
  container?: string;
  video_codec?: string;
  audio_codec?: string;
  duration_ms?: number;
  duration_seconds?: number;
  file_size?: number;
  width?: number;
  height?: number;
  aspect_ratio?: number;
  url?: string;
  media_url?: string;
  playback_url?: string;
  thumbnail_url?: string;
  poster_url?: string;
  download_url?: string;
  cdn_url?: string;
  valid_url?: string;
  hls_url?: string;
  mux_hls_url?: string;
  mux_playback_id?: string;
  waveform?: number[];
  playback_mime_type?: string;
  status?: string;
  processing_status?: string;
  transcoding_status?: string;
  mux_status?: string;
  moderation_status?: string;
  visibility?: string;
  privacy?: string;
  expires_at?: string;
  url_expires_at?: string;
  music_track_id?: string | number;
  original_audio_id?: string | number;
  attached_audio_url?: string;
  has_audio?: boolean;
  is_available?: boolean;
  alt?: string;
  caption?: string;
  created_at?: string;
  updated_at?: string;
  deleted_at?: string;
};

const READY_STATES = new Set(["ready", "asset_ready", "available", "completed", "complete", "published"]);
const FAILED_STATES = new Set(["failed", "error", "rejected", "deleted", "cancelled", "expired"]);
const SIGNED_QUERY_KEYS = ["x-amz-signature", "x-amz-credential", "signature", "policy", "key-pair-id", "token"];

export function canonicalMediaId(media: CanonicalMediaRecord) {
  return Number(media.media_id || media.id || media.attachment_id || 0);
}

export function canonicalMediaState(media: CanonicalMediaRecord) {
  return String(media.processing_status || media.transcoding_status || media.mux_status || media.status || "").trim().toLowerCase();
}

export function isCanonicalMediaReady(media: CanonicalMediaRecord) {
  const state = canonicalMediaState(media);
  return !state || READY_STATES.has(state);
}

export function isCanonicalMediaTerminal(media: CanonicalMediaRecord) {
  return FAILED_STATES.has(canonicalMediaState(media));
}

export function isLikelyExpiringMediaUrl(value?: string) {
  if (!value) return false;
  try {
    const url = new URL(value);
    const keys = Array.from(url.searchParams.keys()).map((key) => key.toLowerCase());
    return SIGNED_QUERY_KEYS.some((candidate) => keys.includes(candidate));
  } catch {
    return false;
  }
}

/** Cached metadata may keep stable public URLs, but never persists signed credentials. */
export function mediaRecordForCache<T extends CanonicalMediaRecord>(media: T): T {
  const next = { ...media } as T;
  (["url", "media_url", "playback_url", "download_url", "cdn_url", "valid_url", "hls_url", "mux_hls_url", "attached_audio_url"] as const).forEach((key) => {
    if (isLikelyExpiringMediaUrl(next[key] as string | undefined)) delete next[key];
  });
  return next;
}
