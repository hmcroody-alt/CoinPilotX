/**
 * What the device can actually play right now, with the network gone.
 *
 * WHY THIS IS A SEPARATE QUESTION
 *
 * "Is this media cached?" has no useful answer, because a media asset is not one
 * file. A reel has a poster, a thumbnail, a manifest and the video itself, and
 * every one of them can be present or absent independently. A boolean over that
 * set collapses into the app's most expensive lie: a reel that shows its poster,
 * announces itself as available offline, and then spins forever on a video that
 * was never fetched. §67 exists because that lie is indistinguishable from
 * success until the user taps it.
 *
 * So the answer is a state, and each state is a claim we can defend:
 *
 *   NONE             nothing usable on disk
 *   PREVIEW_ONLY     a still frame, and only a still frame
 *   PARTIAL_PLAYABLE playback can start and will run out partway
 *   FULL_OFFLINE     the whole thing is here and verified
 *
 * A THUMBNAIL IS NOT A CACHED VIDEO. PREVIEW_ONLY exists precisely so that a
 * poster can be shown — it is genuinely useful, the cell renders instead of
 * being blank — without that being upgraded into a promise about playback.
 *
 * WHY A MANIFEST DOES NOT COUNT
 *
 * An HLS manifest is a few kilobytes of text listing segment URLs. Caching it
 * feels like caching the video and is worth almost nothing offline: the segments
 * it points at are not on disk, so playback fails at the first fetch. It is
 * counted as neither preview nor playable, which is the only honest reading.
 *
 * ON PARTIAL_PLAYABLE, AND WHY NOTHING RETURNS IT YET
 *
 * The obvious implementation — look for the downloader's `.part` file and call
 * it partial — is wrong, and wrong in the direction that matters. A partial MP4
 * is playable only if its moov atom sits at the front of the file; when the
 * atom is at the end, which is the default for most encoders, a 90%-complete
 * file plays for exactly zero seconds. Reporting PARTIAL_PLAYABLE off the mere
 * existence of a partial file would therefore reintroduce the same lie one layer
 * down, and it would be harder to see because the number attached to it looks
 * like evidence.
 *
 * The state is defined here because callers must handle it — the vocabulary is
 * the point — but it is only ever returned for an entry that carries a verified
 * `playableBytes`, set by a writer that has actually confirmed a playable
 * prefix. No writer does that yet, so today the honest answer is one of the
 * other three. Wiring it up is a follow-up: verify faststart on the first
 * kilobyte, record the prefix length, and this function starts returning it
 * without any caller changing.
 */

import { peekCachedMedia, mediaCacheKey as diskCacheKey } from "../../media/mediaCache";
import { isVideoMedia, mediaIdentityOf, renditionUrl } from "./mediaIdentity";
import type { MediaDescriptor, MediaIdentity, MediaRendition } from "./mediaIdentity";

export type OfflinePlaybackState = "none" | "preview_only" | "partial_playable" | "full_offline";

export type OfflinePlaybackReport = {
  state: OfflinePlaybackState;
  /** Null when the media has no durable identity, which is itself a NONE. */
  identity: MediaIdentity | null;
  /** The rendition that can actually be played, when one can. */
  playableRendition: MediaRendition | null;
  /** The rendition backing a preview, when there is one. */
  previewRendition: MediaRendition | null;
  /** Bytes on disk for the playable rendition. 0 when none is playable. */
  playableBytes: number;
  /** Local file to hand a player, or null. Never a remote URL. */
  fileUri: string | null;
};

/**
 * Renditions that constitute the content itself, most preferred first.
 *
 * `manifest` is deliberately absent — see the module note.
 */
const PLAYABLE_RENDITIONS: readonly MediaRendition[] = ["full", "feed"];

/** Renditions that can back a still preview, most preferred first. */
const PREVIEW_RENDITIONS: readonly MediaRendition[] = ["poster", "thumb"];

const EMPTY: OfflinePlaybackReport = {
  state: "none",
  identity: null,
  playableRendition: null,
  previewRendition: null,
  playableBytes: 0,
  fileUri: null
};

/**
 * Report what is genuinely available on disk for this media.
 *
 * Every claim is backed by a verified file: `peekCachedMedia` confirms the file
 * exists and matches its recorded size before this reports anything as present,
 * so a FULL_OFFLINE here cannot be a dangling index entry.
 */
export async function offlinePlaybackStateFor(
  media: MediaDescriptor | null | undefined
): Promise<OfflinePlaybackReport> {
  const identity = mediaIdentityOf(media);
  if (!media || !identity) return EMPTY;

  const playable = await firstPresentRendition(media, PLAYABLE_RENDITIONS);
  if (playable) {
    // A verified whole file. `playableBytes` on the entry means a writer proved a
    // usable prefix; absent that, a complete entry is complete.
    const partialBytes = Number((playable.entry as { playableBytes?: number }).playableBytes || 0);
    const isPartial = partialBytes > 0 && partialBytes < playable.entry.bytes;
    return {
      state: isPartial ? "partial_playable" : "full_offline",
      identity,
      playableRendition: playable.rendition,
      previewRendition: (await firstPresentRendition(media, PREVIEW_RENDITIONS))?.rendition ?? null,
      playableBytes: isPartial ? partialBytes : playable.entry.bytes,
      fileUri: playable.entry.fileUri
    };
  }

  const preview = await firstPresentRendition(media, PREVIEW_RENDITIONS);
  if (preview) {
    return {
      state: "preview_only",
      identity,
      playableRendition: null,
      previewRendition: preview.rendition,
      playableBytes: 0,
      fileUri: preview.entry.fileUri
    };
  }

  return { ...EMPTY, identity };
}

/**
 * The single question a surface should ask before offering offline playback.
 *
 * Exists so no screen writes `state === "full_offline" || state === ...` and
 * gets the boundary wrong — PREVIEW_ONLY is the case that reads like a yes and
 * is not one.
 */
export function canPlayOffline(report: OfflinePlaybackReport): boolean {
  return report.state === "full_offline" || report.state === "partial_playable";
}

/**
 * Whether there is anything at all worth rendering without the network.
 *
 * A preview is worth rendering: a feed of posters beats a feed of grey boxes.
 * This is the weaker claim, and keeping it separate from `canPlayOffline` is
 * what stops the two from being conflated at a call site.
 */
export function hasOfflineVisual(report: OfflinePlaybackReport): boolean {
  return report.state !== "none";
}

/**
 * Which rendition a surface should fetch to move this media to FULL_OFFLINE.
 *
 * Returns null when the media is already playable offline, or when it has no
 * playable rendition URL at all — a video still transcoding has a row and a
 * poster but nothing to download, and asking for it is a guaranteed 404.
 */
export function renditionToMakeOffline(
  media: MediaDescriptor | null | undefined,
  report: OfflinePlaybackReport
): { rendition: MediaRendition; url: string } | null {
  if (!media || report.state === "full_offline") return null;
  for (const rendition of PLAYABLE_RENDITIONS) {
    const url = renditionUrl(media, rendition);
    if (url) return { rendition, url };
  }
  return null;
}

/**
 * Video is the case where the distinction bites; images are their own preview.
 *
 * Kept as a named helper because "should I warn the user this needs a download?"
 * is asked by several surfaces and the answer is not simply "is it cached".
 */
export function needsDownloadForPlayback(
  media: MediaDescriptor | null | undefined,
  report: OfflinePlaybackReport
): boolean {
  return isVideoMedia(media) && !canPlayOffline(report);
}

async function firstPresentRendition(
  media: MediaDescriptor,
  renditions: readonly MediaRendition[]
): Promise<{ rendition: MediaRendition; entry: { bytes: number; fileUri: string } } | null> {
  for (const rendition of renditions) {
    // The URL is needed because the disk key falls back to it when the media has
    // no numeric id. A rendition the backend never emitted has no URL and is
    // therefore not something we could be holding.
    const url = renditionUrl(media, rendition);
    const key = diskCacheKey({ mediaId: media.id ?? media.media_id ?? null, url, rendition });
    if (!key) continue;
    const entry = await peekCachedMedia(key);
    if (entry) return { rendition, entry };
  }
  return null;
}
