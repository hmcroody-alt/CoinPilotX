export type PulseVideoItem = {
  id: number | string;
  title?: string;
  description?: string;
  caption?: string;
  body?: string;
  created_at?: string;
  thumbnail_url?: string;
  poster_url?: string;
  media_url?: string;
  playback_url?: string;
  mux_hls_url?: string;
  mux_playback_id?: string;
  duration?: number | string;
  duration_seconds?: number | string;
  status?: string;
  processing_status?: string;
  visibility?: string;
  creator?: PulseCreator;
  owner?: PulseCreator;
  author?: PulseCreator;
  user?: PulseCreator;
  likes_count?: number;
  reactions_count?: number;
  comments_count?: number;
  saves_count?: number;
  shares_count?: number;
  viewer_reaction?: string;
};

export type PulseCreator = {
  display_name?: string;
  full_name?: string;
  username?: string;
  public_player_id?: string;
  avatar_url?: string;
  avatar_thumbnail_url?: string;
};

export function readCreator(item: PulseVideoItem) {
  return item.creator || item.owner || item.author || item.user || {};
}

export function readTitle(item: PulseVideoItem, fallback: string) {
  return item.title || item.caption || item.description?.split("\n")[0] || item.body?.split("\n")[0] || fallback;
}

export function readDescription(item: PulseVideoItem) {
  return item.description || item.caption || item.body || "";
}

export function readPoster(item: PulseVideoItem) {
  return item.thumbnail_url || item.poster_url || "";
}

export function readPlaybackUrl(item: PulseVideoItem) {
  if (item.mux_hls_url) return item.mux_hls_url;
  if (item.mux_playback_id) return `https://stream.mux.com/${item.mux_playback_id}.m3u8`;
  return item.playback_url || item.media_url || "";
}

export function isProcessing(item: PulseVideoItem) {
  const status = String(item.processing_status || item.status || "").toLowerCase();
  return status.includes("processing") || status.includes("preparing") || status.includes("queued");
}

export function isFailed(item: PulseVideoItem) {
  const status = String(item.processing_status || item.status || "").toLowerCase();
  return status.includes("failed") || status.includes("error");
}
