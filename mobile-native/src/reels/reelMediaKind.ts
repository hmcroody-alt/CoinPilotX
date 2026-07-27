import type { PulseReel } from "../api/reels";
import type { PulseMedia } from "../api/feed";
import { mediaDisplayUrl, mediaKind } from "../api/feed";

/**
 * Pure classification for the unified immersive Reels feed. Every feed item is
 * classified into exactly one render kind so `ReelPlayerCard` can dispatch to a
 * purpose-built native renderer:
 *   - video      : a single autoplaying clip (the classic Reel)
 *   - photo      : a single still image that stays put until the user scrolls
 *   - carousel   : multiple slides swiped horizontally
 *   - livestream : a broadcast that is live right now (played inline in-feed)
 *   - replay     : a finished broadcast's recording (played like a video)
 *
 * This module has no React or native dependencies so it is fully unit-testable.
 */

export type ReelMediaKind = "video" | "photo" | "carousel" | "livestream" | "replay";

export type ReelMediaSlide = {
  key: string;
  kind: "image" | "video";
  url: string;
  poster: string;
};

const ACTIVE_LIVE_STATUSES = new Set(["live", "active", "streaming", "online", "publishing", "started", "connected"]);
const ENDED_LIVE_STATUSES = new Set(["ended", "completed", "complete", "offline", "finished", "stopped", "vod", "replay", "archived"]);

export function reelLiveSessionId(reel: PulseReel): number {
  return Number(reel.live_session_id || reel.live?.live_session_id || 0);
}

function contentTypeToken(reel: PulseReel): string {
  return String(reel.content_type || reel.post_type || "").toLowerCase();
}

export function reelIsLiveContent(reel: PulseReel): boolean {
  const token = contentTypeToken(reel);
  return token === "live" || token === "livestream" || token === "replay" || reelLiveSessionId(reel) > 0;
}

function reelLiveStatus(reel: PulseReel): string {
  return String(reel.live?.status || reel.live?.publish_state || "").toLowerCase();
}

/** True when the broadcast is happening right now (as opposed to a finished replay). */
export function reelIsActiveLive(reel: PulseReel): boolean {
  if (!reelIsLiveContent(reel)) return false;
  if (contentTypeToken(reel) === "replay") return false;
  const status = reelLiveStatus(reel);
  if (ENDED_LIVE_STATUSES.has(status)) return false;
  // Unknown/empty status on live content defaults to active — content_type "live"
  // is only emitted while a session is streaming; ended sessions carry a status.
  return true;
}

function slideKindForMedia(media: PulseMedia): "image" | "video" {
  return mediaKind(media) === "video" ? "video" : "image";
}

function slideVideoUrl(media: PulseMedia): string {
  const muxPlaybackId = String(media.mux_playback_id || "").trim();
  if (muxPlaybackId) return `https://stream.mux.com/${muxPlaybackId}.m3u8`;
  return mediaDisplayUrl({ ...media, media_url: media.playback_url || media.hls_url || media.media_url || "" });
}

function slidePosterUrl(media: PulseMedia): string {
  return mediaDisplayUrl({ ...media, media_url: media.poster_url || media.thumbnail_url || "" });
}

/**
 * Ordered slides for photo/carousel rendering. Each slide is resolved to a
 * playable/displayable URL and a poster. Slides without a usable URL are dropped.
 */
export function reelMediaSlides(reel: PulseReel): ReelMediaSlide[] {
  const media = reel.media || [];
  const slides: ReelMediaSlide[] = [];
  media.forEach((item, index) => {
    const kind = slideKindForMedia(item);
    const url = kind === "video" ? slideVideoUrl(item) : mediaDisplayUrl(item);
    if (!url) return;
    slides.push({
      key: String(item.media_id || item.id || item.public_id || `${reel.id}:${index}`),
      kind,
      url,
      poster: slidePosterUrl(item) || (kind === "image" ? url : "")
    });
  });
  if (!slides.length && reel.video_url) {
    slides.push({ key: `${reel.id}:legacy`, kind: "video", url: reel.video_url, poster: reel.poster_url || "" });
  }
  return slides;
}

export function classifyReelMedia(reel: PulseReel): ReelMediaKind {
  if (reelIsLiveContent(reel)) {
    return reelIsActiveLive(reel) ? "livestream" : "replay";
  }
  const token = contentTypeToken(reel);
  const slides = reelMediaSlides(reel);
  if (token.includes("carousel") || token.includes("gallery") || token.includes("album")) {
    return "carousel";
  }
  if (slides.length > 1) return "carousel";
  if (slides.length === 1) return slides[0].kind === "image" ? "photo" : "video";
  if (token.includes("photo") || token.includes("image")) return "photo";
  return "video";
}
