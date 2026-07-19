import { mediaDisplayUrl, PulseMedia } from "../api/feed";
import { pulseApi } from "../api/pulseApi";
import { canonicalMediaId, isCanonicalMediaReady } from "./mediaContract";

export type MediaAccessResult = {
  media: PulseMedia;
  url: string;
  ready: boolean;
};

/**
 * Refreshes the existing canonical media record. It never creates a replacement
 * record and never treats a CDN/signed URL as media identity.
 */
export async function refreshCanonicalMediaAccess(media: PulseMedia): Promise<MediaAccessResult> {
  const mediaId = canonicalMediaId(media);
  if (!mediaId) return { media, url: mediaDisplayUrl(media), ready: isCanonicalMediaReady(media) };
  const response = await pulseApi<{ ok?: boolean; media?: PulseMedia; status?: string; processing_status?: string }>(`/api/pulse/media/${mediaId}/status`);
  const refreshed: PulseMedia = {
    ...media,
    ...(response.media || {}),
    id: Number(response.media?.id || media.id || mediaId),
    media_id: Number(response.media?.media_id || media.media_id || mediaId),
    status: response.media?.status || response.status || media.status,
    processing_status: response.media?.processing_status || response.processing_status || media.processing_status
  };
  return { media: refreshed, url: mediaDisplayUrl(refreshed), ready: isCanonicalMediaReady(refreshed) };
}

export function canonicalMediaPlaybackUrl(media: PulseMedia) {
  return mediaDisplayUrl({
    ...media,
    media_url: media.playback_url || media.hls_url || media.mux_hls_url || media.valid_url || media.cdn_url || media.media_url || media.url || ""
  });
}
