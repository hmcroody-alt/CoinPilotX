/**
 * The bounded record of what has been warmed.
 *
 * WHAT THIS DOES AND DOES NOT HOLD
 *
 * It does not hold pixels. Decoded images live in RN's own image cache and
 * video segments live in the player's buffer; neither is addressable from JS.
 * What this holds is the *claim* that a given identity+rendition has been
 * fetched, plus the size we believe it cost.
 *
 * That distinction is the whole reason the bound matters. Because we cannot
 * free the underlying bytes directly, the only lever on memory is how much we
 * ever ask to be resident at once. An unbounded warm list is an unbounded image
 * cache by proxy: scroll a feed for ten minutes and every image that ever
 * passed the viewport is still being kept alive by a prefetch that nobody
 * retired. So entries are capped by count and by estimated bytes, the oldest
 * unreferenced ones are dropped first, and dropping one is what allows the
 * platform cache underneath to reclaim it.
 *
 * Keys are mediaCacheKey(identity, rendition) -- stable identity plus
 * rendition, never a signed delivery URL. A URL-keyed cache re-downloads the
 * same bytes every time the signature is refreshed and fills up with duplicates
 * of one asset; see mediaIdentity.
 */

import { identityFromCacheKey } from "./mediaIdentity";
import type { MediaRendition } from "./mediaIdentity";

export type MediaWarmState = "warm" | "failed";

export type MediaCacheEntry = {
  key: string;
  rendition: MediaRendition;
  /** The URL this was warmed from. Kept for debugging, never for keying. */
  sourceUrl: string;
  state: MediaWarmState;
  /** Best estimate of the bytes this entry is keeping resident. */
  bytes: number;
  warmedAt: number;
  /** Entries pinned by a visible surface are never evicted. */
  pinned: boolean;
};

export type MediaCacheEvictReason = "count" | "bytes" | "memory_warning" | "manual";

export type MediaCacheEvent =
  | { type: "cache_memory_hit"; key: string }
  | { type: "media_prefetch_miss"; key: string }
  | { type: "media_cache_evicted"; key: string; reason: MediaCacheEvictReason };

/**
 * Defaults sized for a phone, not for a benchmark.
 *
 * 96 entries is roughly three screens of feed thumbnails plus the reel window,
 * and 48MB is the estimate ceiling rather than a measured RSS -- the point is
 * that the number exists and is small, not that it is exact. A cap that is
 * wrong by 20% still prevents the failure mode; having no cap does not.
 */
export const MEDIA_CACHE_LIMITS = Object.freeze({
  maxEntries: 96,
  maxBytes: 48 * 1024 * 1024
});

/**
 * Used when the backend did not tell us how big something is, which is the
 * common case for images. Picked per rendition so a poster is not accounted for
 * as though it were a full-resolution original.
 */
const ESTIMATED_BYTES: Record<MediaRendition, number> = {
  thumb: 40 * 1024,
  poster: 120 * 1024,
  feed: 320 * 1024,
  full: 1200 * 1024,
  // A manifest is a few KB of text; the segments it points at are the player's
  // budget, not ours, and counting them here would double-book them.
  manifest: 8 * 1024,
  // Unlike the image renditions this is not a guess: the attached-music warm
  // fetches a capped byte range, so the estimate IS the cap
  // (AUDIO_PREFETCH_BYTE_CAP). Charging it against the same ceiling is what
  // keeps §29 from spending outside the 48MB budget.
  audio: 256 * 1024
};

export type MediaPrefetchCacheOptions = {
  maxEntries?: number;
  maxBytes?: number;
  onEvent?: (event: MediaCacheEvent) => void;
  now?: () => number;
};

export class MediaPrefetchCache {
  /** Insertion order is the LRU order; recall() re-inserts to refresh it. */
  private entries = new Map<string, MediaCacheEntry>();
  private bytesUsed = 0;
  private readonly maxEntries: number;
  private readonly maxBytes: number;
  private readonly onEvent: (event: MediaCacheEvent) => void;
  private readonly now: () => number;

  constructor(options: MediaPrefetchCacheOptions = {}) {
    this.maxEntries = options.maxEntries ?? MEDIA_CACHE_LIMITS.maxEntries;
    this.maxBytes = options.maxBytes ?? MEDIA_CACHE_LIMITS.maxBytes;
    this.onEvent = options.onEvent ?? (() => undefined);
    this.now = options.now ?? (() => Date.now());
  }

  get size() {
    return this.entries.size;
  }

  get bytes() {
    return this.bytesUsed;
  }

  /** Non-mutating. Use when asking "is this ready" should not count as a use. */
  peek(key: string): MediaCacheEntry | null {
    return this.entries.get(key) ?? null;
  }

  isWarm(key: string) {
    return this.entries.get(key)?.state === "warm";
  }

  /**
   * Look up and mark as recently used, emitting the hit/miss telemetry §39 asks
   * for. Failed entries count as a miss for the caller -- there is nothing to
   * display -- but are kept so a known-bad URL is not retried on every frame.
   */
  recall(key: string): MediaCacheEntry | null {
    const entry = this.entries.get(key);
    if (!entry) {
      this.onEvent({ type: "media_prefetch_miss", key });
      return null;
    }
    this.entries.delete(key);
    this.entries.set(key, entry);
    if (entry.state === "warm") {
      this.onEvent({ type: "cache_memory_hit", key });
      return entry;
    }
    this.onEvent({ type: "media_prefetch_miss", key });
    return entry;
  }

  remember(input: {
    key: string;
    rendition: MediaRendition;
    sourceUrl: string;
    state?: MediaWarmState;
    bytes?: number;
    pinned?: boolean;
  }) {
    const bytes =
      typeof input.bytes === "number" && Number.isFinite(input.bytes) && input.bytes > 0
        ? input.bytes
        : ESTIMATED_BYTES[input.rendition] ?? ESTIMATED_BYTES.feed;

    const previous = this.entries.get(input.key);
    if (previous) {
      this.entries.delete(input.key);
      this.bytesUsed -= previous.bytes;
    }

    const entry: MediaCacheEntry = {
      key: input.key,
      rendition: input.rendition,
      sourceUrl: input.sourceUrl,
      state: input.state ?? "warm",
      bytes,
      warmedAt: this.now(),
      pinned: input.pinned ?? previous?.pinned ?? false
    };
    this.entries.set(entry.key, entry);
    this.bytesUsed += entry.bytes;
    this.enforceLimits();
    return entry;
  }

  /**
   * Protect an entry from eviction while a surface is showing it.
   *
   * Without this, a long scroll can evict the image currently on screen to make
   * room for a guess about the next one, and the user watches a loaded photo
   * turn back into a placeholder.
   */
  pin(key: string) {
    const entry = this.entries.get(key);
    if (entry) entry.pinned = true;
  }

  unpin(key: string) {
    const entry = this.entries.get(key);
    if (entry) entry.pinned = false;
  }

  unpinAll() {
    for (const entry of this.entries.values()) entry.pinned = false;
  }

  forget(key: string, reason: MediaCacheEvictReason = "manual") {
    const entry = this.entries.get(key);
    if (!entry) return false;
    this.entries.delete(key);
    this.bytesUsed -= entry.bytes;
    this.onEvent({ type: "media_cache_evicted", key, reason });
    return true;
  }

  /** Drop every rendition of one asset, e.g. after a signed-URL 403. */
  forgetIdentity(identity: string) {
    let dropped = 0;
    for (const key of [...this.entries.keys()]) {
      if (identityFromCacheKey(key) === identity) {
        this.forget(key);
        dropped += 1;
      }
    }
    return dropped;
  }

  /**
   * iOS memory warning handler. Pinned entries survive: they are on screen, and
   * releasing them would guarantee a visible regression in exchange for memory
   * the OS was only asking us to consider.
   */
  trimToPinned() {
    for (const entry of [...this.entries.values()]) {
      if (!entry.pinned) this.forget(entry.key, "memory_warning");
    }
  }

  clear() {
    this.entries.clear();
    this.bytesUsed = 0;
  }

  stats() {
    let pinned = 0;
    let failed = 0;
    for (const entry of this.entries.values()) {
      if (entry.pinned) pinned += 1;
      if (entry.state === "failed") failed += 1;
    }
    return {
      entries: this.entries.size,
      bytes: this.bytesUsed,
      pinned,
      failed,
      maxEntries: this.maxEntries,
      maxBytes: this.maxBytes
    };
  }

  keysInLruOrder() {
    return [...this.entries.keys()];
  }

  /**
   * Evict from the least-recently-used end until both bounds hold.
   *
   * Pinned entries are skipped rather than counted out of the budget, so a
   * pathological caller that pins everything gets a cache over its limit rather
   * than an infinite loop. That is the right failure: over-budget and visible
   * beats a hang.
   */
  private enforceLimits() {
    const overCount = () => this.entries.size > this.maxEntries;
    const overBytes = () => this.bytesUsed > this.maxBytes;

    if (!overCount() && !overBytes()) return;

    for (const entry of this.entries.values()) {
      if (!overCount() && !overBytes()) break;
      if (entry.pinned) continue;
      const reason = overBytes() && !overCount() ? "bytes" : "count";
      this.entries.delete(entry.key);
      this.bytesUsed -= entry.bytes;
      this.onEvent({ type: "media_cache_evicted", key: entry.key, reason });
    }
  }
}

/**
 * One cache for the whole app.
 *
 * A per-screen cache would mean the reel you warmed in the feed is cold when
 * you open it in Reels, and the total memory would be the sum of every screen's
 * budget rather than one budget -- which is the unbounded case wearing a
 * different hat.
 */
let shared: MediaPrefetchCache | null = null;

export function sharedMediaPrefetchCache() {
  if (!shared) shared = new MediaPrefetchCache();
  return shared;
}

export function __resetSharedMediaPrefetchCache(next?: MediaPrefetchCache) {
  shared = next ?? null;
}
