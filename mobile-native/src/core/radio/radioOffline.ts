/**
 * Pulse Radio's side of the shared offline platform.
 *
 * Radio had no offline story at all. The queue existed only in module memory, so
 * a cold start with no network reached `listPulseRadioTracks()`, failed, and
 * reported "Pulse Radio has no playable tracks right now." — which is not what
 * had happened. Tracks streamed straight from their delivery URL, so nothing was
 * ever on disk to fall back to even after an hour of listening.
 *
 * Nothing here is a new authority. The queue goes through `core/cache` (the same
 * envelope every other surface reads, so the queue has a knowable age), the
 * audio bytes go through `media/mediaCache` and `media/mediaDownloader` (the same
 * budget, eviction and verification every other cached file is subject to), and
 * connectivity comes from `core/connectivity`. §22's "NO DUPLICATE AUTHORITIES"
 * is the reason this file is thin.
 *
 * ON THE CACHE KEY (§38)
 *
 * `mediaCacheKey` is handed the track id AND the url, and resolves identity in
 * that order, dropping the query string when it has to fall back to the url. A
 * re-signed delivery URL therefore lands on the key the previous signature
 * produced. Keying on the URL as given would mean every signature rotation
 * silently orphaned the file it had just downloaded — the cache would grow and
 * never once be hit, which reads as "offline playback doesn't work" long after
 * the bug that caused it.
 */

import type { PulseRadioTrack } from "../../api/radio";
import { readJsonCacheEntry, writeJsonCache } from "../cache";
import { connectivityState } from "../connectivity";
import { dropCachedMedia, mediaCacheKey, peekCachedMedia } from "../../media/mediaCache";
import { downloadMedia } from "../../media/mediaDownloader";

export const RADIO_QUEUE_CACHE_KEY = "pulsesoc.native.radio.queue.v1";

/**
 * Enough to open the app in a tunnel and still have somewhere to go, not enough
 * to matter against the storage budget. The queue is metadata; the bytes are
 * accounted for separately by the media cache.
 */
const MAX_CACHED_QUEUE = 40;

export type RadioQueueSnapshot = {
  tracks: PulseRadioTrack[];
  /** When the queue was written, or null when unknown. Never defaulted to now. */
  storedAt: number | null;
  ageMs: number | null;
};

/** A playable source plus whether it needs the network to stay up. */
export type RadioSource = {
  uri: string;
  /** True when `uri` is a verified local file and playback cannot be cut off. */
  offline: boolean;
};

const EMPTY_SNAPSHOT: RadioQueueSnapshot = { tracks: [], storedAt: null, ageMs: null };

/**
 * The disk key for a track's audio.
 *
 * Empty string means "not cacheable" — the media cache's own convention, kept
 * rather than translated so a caller cannot mistake a synthesised key for a real
 * one.
 */
export function radioTrackCacheKey(track: PulseRadioTrack | null | undefined): string {
  if (!track) return "";
  return mediaCacheKey({ mediaId: track.id, url: track.audioUrl, rendition: "full" });
}

export async function cacheRadioQueue(tracks: PulseRadioTrack[]): Promise<void> {
  const usable = normalizeQueue(tracks).slice(0, MAX_CACHED_QUEUE);
  // An empty write would replace a usable queue with nothing, turning one failed
  // fetch into a permanently empty radio.
  if (!usable.length) return;
  await writeJsonCache(RADIO_QUEUE_CACHE_KEY, usable);
}

export async function loadCachedRadioQueue(): Promise<RadioQueueSnapshot> {
  const entry = await readJsonCacheEntry<PulseRadioTrack[]>(RADIO_QUEUE_CACHE_KEY, normalizeQueue);
  if (!entry || !entry.value.length) return EMPTY_SNAPSHOT;
  return { tracks: entry.value, storedAt: entry.storedAt, ageMs: entry.ageMs };
}

/**
 * The local file for a track, or null.
 *
 * `peekCachedMedia` verifies the file exists and matches its recorded size, so a
 * non-null answer here is a file that will actually open — not an index row that
 * outlived the bytes it describes.
 */
export async function offlineRadioFileUri(track: PulseRadioTrack | null | undefined): Promise<string | null> {
  const key = radioTrackCacheKey(track);
  if (!key) return null;
  const entry = await peekCachedMedia(key).catch(() => null);
  return entry?.fileUri || null;
}

/**
 * What to hand the player for this track.
 *
 * Disk first, always — not only when offline. A cached file starts instantly,
 * costs no data and cannot stall, and there is no scenario where streaming bytes
 * the device already holds is the better choice.
 */
export async function resolveRadioSource(track: PulseRadioTrack | null | undefined): Promise<RadioSource | null> {
  if (!track) return null;
  const fileUri = await offlineRadioFileUri(track);
  if (fileUri) return { uri: fileUri, offline: true };
  if (!track.audioUrl) return null;
  return { uri: track.audioUrl, offline: false };
}

/**
 * Forget the cached copy of a track the player could not open.
 *
 * Called only when playback failed on a source that came off disk. Without it a
 * file that is the right size and the wrong bytes is re-served on every attempt,
 * and the track is permanently broken for that account — a cache that keeps
 * handing back something it has already been told does not work.
 */
export async function discardCachedRadioTrack(track: PulseRadioTrack | null | undefined): Promise<boolean> {
  const key = radioTrackCacheKey(track);
  if (!key) return false;
  return dropCachedMedia(key).catch(() => false);
}

/**
 * Pull a track onto disk so the next tunnel does not interrupt it.
 *
 * Deliberately narrow. §13's rule — do not auto-download the large thing — is
 * about respecting a data plan, and a radio track is a song, but the restraint
 * still applies at the edges: nothing is warmed while the connectivity authority
 * reports anything short of ONLINE, because DEGRADED is precisely the state
 * where a speculative download competes with the audio that is currently
 * playing.
 *
 * Resolves false rather than throwing. Warming is an optimisation; a failure to
 * warm must never become a playback error.
 */
export async function warmRadioTrack(track: PulseRadioTrack | null | undefined): Promise<boolean> {
  if (!track?.audioUrl) return false;
  if (connectivityState() !== "online") return false;
  const key = radioTrackCacheKey(track);
  if (!key) return false;
  // Already held. `downloadMedia` would short-circuit too, but asking first
  // keeps the common case off the download queue entirely.
  if (await offlineRadioFileUri(track)) return true;
  try {
    await downloadMedia({
      url: track.audioUrl,
      mediaId: track.id,
      rendition: "full",
      kind: "audio",
      surface: "pulse_radio"
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Which track to warm next given where the listener is.
 *
 * One ahead, never the whole queue. Downloading forty songs because someone
 * pressed play is the §13 mistake with a smaller file size, and the only track
 * whose availability changes the listening experience in the next few minutes is
 * the one after this one.
 */
export function nextTrackToWarm(
  queue: PulseRadioTrack[],
  order: number[],
  orderPosition: number
): PulseRadioTrack | null {
  if (!queue.length || !order.length) return null;
  const nextPosition = orderPosition + 1;
  if (nextPosition >= order.length) return null;
  return queue[order[nextPosition]] || null;
}

function normalizeQueue(value: PulseRadioTrack[] | null | undefined): PulseRadioTrack[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((track) => normalizeTrack(track))
    .filter((track): track is PulseRadioTrack => Boolean(track));
}

function normalizeTrack(track: PulseRadioTrack | null | undefined): PulseRadioTrack | null {
  if (!track) return null;
  const id = String(track.id || "").trim();
  const audioUrl = String(track.audioUrl || "").trim();
  // A track with no id and no url is not a track we could ever play; keeping it
  // would only let it reach the player and fail there.
  if (!id || !audioUrl) return null;
  return {
    id,
    title: String(track.title || "PulseSoc Radio").trim(),
    artist: String(track.artist || "PulseSoc Music").trim(),
    audioUrl,
    coverArtUrl: track.coverArtUrl ? String(track.coverArtUrl) : undefined
  };
}
