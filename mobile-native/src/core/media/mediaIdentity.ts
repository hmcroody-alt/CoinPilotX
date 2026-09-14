/**
 * Stable identity for a piece of media, independent of the URL it is served from.
 *
 * WHY THIS EXISTS
 *
 * Delivery URLs are not stable. R2 objects can be handed out with a query
 * signature, Mux thumbnails carry `?token=`/`?width=` parameters, and the
 * backend is free to move a rendition between hosts. If the prefetch cache were
 * keyed by URL then every refreshed signature would look like a brand-new asset:
 * the warm entry for the reel the user is about to swipe to would be invisible,
 * the fetch would be repeated, and the cache would fill with duplicates of the
 * same bytes until the LRU evicted something that was still needed.
 *
 * So identity is derived from whatever the backend already treats as permanent,
 * in descending order of trust:
 *
 *   1. the Mux playback id  -- survives re-signing and host changes
 *   2. the numeric media row id
 *   3. the URL with its query string removed
 *
 * (3) is a fallback, not a design: it is correct for the `/uploads/...` and
 * bare-CDN paths that carry no signature, and for anything else it is still
 * strictly better than keying on the signature itself.
 */

/**
 * Renditions are part of the key, never a separate cache.
 *
 * A poster and a full-resolution image share a media identity but are different
 * bytes, so they must not collide. Keeping the rendition in the key (rather than
 * in a parallel map) means one bounded LRU governs total memory across every
 * size of every asset -- see mediaPrefetchCache.
 */
export type MediaRendition = "thumb" | "feed" | "full" | "poster" | "manifest";

export type MediaIdentity = string;

/** The shape the feed/status/reels serializers actually emit. */
export type MediaDescriptor = {
  id?: number | string | null;
  media_id?: number | string | null;
  mux_playback_id?: string | null;
  playback_url?: string | null;
  media_url?: string | null;
  valid_url?: string | null;
  cdn_url?: string | null;
  thumbnail_url?: string | null;
  poster_url?: string | null;
  mux_thumbnail_url?: string | null;
  type?: string | null;
  media_type?: string | null;
  width?: number | null;
  height?: number | null;
  aspect_ratio?: number | null;
  duration?: number | null;
  mux_processing?: boolean | null;
  processing_status?: string | null;
  mux_status?: string | null;
  hydration_state?: string | null;
};

/**
 * Strip the part of a URL that is allowed to change without the bytes changing.
 *
 * Only the query and fragment are dropped. The path is kept in full because for
 * unsigned CDN objects the path IS the identity, and two different assets under
 * the same host would otherwise collapse onto one key.
 */
export function stripVolatileUrlParts(url: string): string {
  const withoutFragment = url.split("#")[0] ?? url;
  const withoutQuery = withoutFragment.split("?")[0] ?? withoutFragment;
  return withoutQuery;
}

/**
 * Derive the stable identity for a media descriptor.
 *
 * Returns null when there is nothing durable to key on, which callers must treat
 * as "not prefetchable" rather than inventing a key. A synthesised key would be
 * unique per render and would defeat the cache while still consuming its budget.
 */
export function mediaIdentityOf(media: MediaDescriptor | null | undefined): MediaIdentity | null {
  if (!media) return null;

  const playbackId = typeof media.mux_playback_id === "string" ? media.mux_playback_id.trim() : "";
  if (playbackId) return `mux:${playbackId}`;

  const rowId = media.id ?? media.media_id;
  if (typeof rowId === "number" && Number.isFinite(rowId)) return `media:${rowId}`;
  if (typeof rowId === "string" && rowId.trim()) return `media:${rowId.trim()}`;

  const url = firstNonEmpty(media.playback_url, media.valid_url, media.cdn_url, media.media_url);
  if (url) return `url:${stripVolatileUrlParts(url)}`;

  return null;
}

/** The cache key. Identity plus rendition, never the delivery URL. */
export function mediaCacheKey(identity: MediaIdentity, rendition: MediaRendition): string {
  return `${identity}#${rendition}`;
}

/**
 * Pull the identity back out of a cache key.
 *
 * Renditions never contain "#", and identities may (a URL path could), so the
 * split has to come from the right. Splitting from the left would truncate any
 * identity containing a fragment-like character.
 */
export function identityFromCacheKey(key: string): MediaIdentity {
  const cut = key.lastIndexOf("#");
  return cut === -1 ? key : key.slice(0, cut);
}

export function isVideoMedia(media: MediaDescriptor | null | undefined): boolean {
  if (!media) return false;
  const kind = (media.type ?? media.media_type ?? "").toLowerCase();
  return kind === "video";
}

export function isImageMedia(media: MediaDescriptor | null | undefined): boolean {
  if (!media) return false;
  const kind = (media.type ?? media.media_type ?? "").toLowerCase();
  return kind === "image" || kind === "photo";
}

/**
 * Whether the media is playable right now.
 *
 * §56/§57: a post whose Mux asset is still transcoding has a row and a poster
 * but no segments, so prefetching its manifest is a guaranteed 404 and
 * autoplaying it is a guaranteed spinner. Both are refused at the source.
 */
export function isMediaReady(media: MediaDescriptor | null | undefined): boolean {
  if (!media) return false;
  if (media.mux_processing === true) return false;
  if (media.hydration_state === "missing") return false;
  const status = (media.processing_status ?? media.mux_status ?? "").toLowerCase();
  if (status && ["preparing", "processing", "uploading", "errored", "failed"].includes(status)) return false;
  return Boolean(firstNonEmpty(media.playback_url, media.valid_url, media.cdn_url, media.media_url));
}

/**
 * The URL to warm for a given rendition, or null when that rendition does not
 * exist for this media.
 *
 * Deliberately does NOT fall back from a poster to the full asset. A missing
 * poster should show a placeholder (§35); silently substituting the original
 * would download a multi-megabyte image to fill a thumbnail slot, which is the
 * exact failure §11 and §45 exist to prevent.
 */
export function renditionUrl(media: MediaDescriptor, rendition: MediaRendition): string | null {
  switch (rendition) {
    case "poster":
      return firstNonEmpty(media.poster_url, media.mux_thumbnail_url, media.thumbnail_url);
    case "thumb":
      return firstNonEmpty(media.thumbnail_url, media.mux_thumbnail_url, media.poster_url);
    case "feed":
      return firstNonEmpty(media.cdn_url, media.valid_url, media.media_url);
    case "full":
      return firstNonEmpty(media.media_url, media.valid_url, media.cdn_url);
    case "manifest":
      return firstNonEmpty(media.playback_url, media.valid_url);
    default:
      return null;
  }
}

/**
 * Aspect ratio known before a byte of the image is fetched, so the cell can
 * reserve its geometry and the list does not jump when the image lands (§13).
 *
 * Falls back to width/height when the backend did not compute the ratio, and to
 * null when neither is available -- callers must then reserve a default box
 * rather than collapse to zero height.
 */
export function knownAspectRatio(media: MediaDescriptor | null | undefined): number | null {
  if (!media) return null;
  const ratio = media.aspect_ratio;
  if (typeof ratio === "number" && Number.isFinite(ratio) && ratio > 0) return ratio;
  const w = media.width;
  const h = media.height;
  if (typeof w === "number" && typeof h === "number" && w > 0 && h > 0) return w / h;
  return null;
}

function firstNonEmpty(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}
