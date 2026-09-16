/**
 * The runtime that binds planner, queue, cache and warm primitives together.
 *
 * Surfaces talk to this and nothing else. They say "I am Reels, here is my
 * list, the user is on item 7, they are scrolling forward slowly" and this
 * decides what to fetch, what to stop fetching, and what to protect from
 * eviction. A surface never touches the queue or the cache directly, because
 * every time a surface has been allowed to manage its own media lifecycle it
 * has grown a second, subtly different copy of the policy.
 *
 * STARTUP COST (§34)
 *
 * Nothing here runs until a surface calls apply(). No module-scope timers, no
 * AppState subscription, no eager instantiation -- the shared instance is
 * created on first use. Importing this file costs a few closures.
 */

import { recordDuration } from "../perfTrace";
import { planMediaPrefetch } from "./mediaPrefetchPlanner";
import type { MediaSurface, PrefetchPlan, PrefetchPlanInput } from "./mediaPrefetchPlanner";
import { MediaPrefetchQueue } from "./mediaPrefetchQueue";
import type { PrefetchQueueEvent } from "./mediaPrefetchQueue";
import { MediaPrefetchCache, sharedMediaPrefetchCache } from "./mediaPrefetchCache";
import type { MediaCacheEvent } from "./mediaPrefetchCache";
import { createNetworkEstimator, observeWarmDuration } from "./mediaViewportSignals";
import type { NetworkEstimator } from "./mediaViewportSignals";
import { audioWarmPlan, imageWarmTargets, videoWarmPlan, warmImageUrl, warmVideoPlan } from "./mediaWarm";
import { identityFromCacheKey } from "./mediaIdentity";

export type MediaPrefetcherOptions = {
  cache?: MediaPrefetchCache;
  queue?: MediaPrefetchQueue;
  /** Injected in tests so no real bytes move. */
  warmImage?: typeof warmImageUrl;
  warmVideo?: typeof warmVideoPlan;
  telemetry?: (name: string, durationMs: number, attributes: Record<string, string | number | boolean>) => void;
};

export class MediaPrefetcher {
  private readonly cache: MediaPrefetchCache;
  private readonly queue: MediaPrefetchQueue;
  private readonly warmImage: typeof warmImageUrl;
  private readonly warmVideo: typeof warmVideoPlan;
  private readonly telemetry: NonNullable<MediaPrefetcherOptions["telemetry"]>;

  /** Keys this surface currently has outstanding, so stale ones can be dropped. */
  private outstanding = new Map<MediaSurface, Set<string>>();
  private pinned = new Map<MediaSurface, string[]>();
  private network: NetworkEstimator = createNetworkEstimator();

  constructor(options: MediaPrefetcherOptions = {}) {
    this.cache = options.cache ?? sharedMediaPrefetchCache();
    this.warmImage = options.warmImage ?? warmImageUrl;
    this.warmVideo = options.warmVideo ?? warmVideoPlan;
    this.telemetry = options.telemetry ?? ((name, durationMs, attributes) => recordDuration(name, durationMs, attributes));
    this.queue =
      options.queue ??
      new MediaPrefetchQueue({
        onEvent: (event) => this.onQueueEvent(event)
      });
  }

  get networkTier() {
    return this.network.tier;
  }

  /**
   * Reconcile a surface's prefetch state with where the user now is.
   *
   * Idempotent: calling it twice with the same input enqueues nothing the
   * second time, because keys already warm or already in flight are skipped.
   * Surfaces call it on every viewability change, so it has to be cheap when
   * nothing has moved.
   */
  apply(surface: MediaSurface, input: Omit<PrefetchPlanInput, "surface" | "networkTier"> & { networkTier?: never }): PrefetchPlan {
    const plan = planMediaPrefetch({ ...input, surface, networkTier: this.network.tier });

    // Stale predictions first. Freeing a slot before asking for a new one is
    // what keeps a fast scroll from queueing four windows' worth of guesses.
    const previous = this.outstanding.get(surface);
    if (previous) {
      for (const key of previous) {
        if (!plan.keep.has(key)) this.queue.cancelKey(key, "superseded");
      }
    }
    this.outstanding.set(surface, new Set(plan.keep));

    this.repin(surface, plan.pin);

    for (const target of plan.warm) {
      if (this.cache.isWarm(target.key)) continue;
      if (this.queue.has(target.key)) {
        // Already in flight or waiting; enqueue again only so a promotion to a
        // more urgent band is recorded.
        this.queue.enqueue({
          key: target.key,
          priority: target.priority,
          tag: surface,
          run: () => undefined
        });
        continue;
      }

      this.queue.enqueue({
        key: target.key,
        priority: target.priority,
        tag: surface,
        run: async (signal) => {
          if (target.video) {
            const videoPlan = videoWarmPlan(target.media);
            if (videoPlan.kind === "none") return;
            const result = await this.warmVideo(videoPlan, signal);
            if (signal.cancelled) return;
            this.cache.remember({
              key: target.key,
              rendition: "manifest",
              sourceUrl: videoPlan.url,
              state: result.ok ? "warm" : "failed",
              bytes: result.bytes
            });
            return;
          }

          // §29. Attached music rides the same queue, the same cancellation
          // signal and the same cache as everything else, so a blur that drops
          // a reel's video warm drops its track with it. A separate audio
          // prewarmer would have needed its own copy of all three (§20).
          if (target.rendition === "audio") {
            const audioPlan = audioWarmPlan(target.media);
            if (audioPlan.kind === "none") return;
            const result = await this.warmVideo(audioPlan, signal);
            if (signal.cancelled) return;
            this.cache.remember({
              key: target.key,
              rendition: "audio",
              sourceUrl: audioPlan.url,
              state: result.ok ? "warm" : "failed",
              bytes: result.bytes
            });
            return;
          }

          const url = imageWarmTargets(target.media, target.rendition);
          if (!url) return;
          const result = await this.warmImage(url, signal);
          if (signal.cancelled) return;
          this.cache.remember({
            key: target.key,
            rendition: target.rendition,
            sourceUrl: url,
            state: result.ok ? "warm" : "failed"
          });
        }
      });
    }

    return plan;
  }

  /**
   * The surface is gone. Drop its predictions and release its pins.
   *
   * Called on blur and unmount. Without it a user who opens Reels, scrolls, and
   * leaves has left four video warms running against a screen that no longer
   * exists, competing with whatever they opened instead.
   */
  release(surface: MediaSurface) {
    this.queue.cancelTag(surface);
    this.outstanding.delete(surface);
    this.repin(surface, []);
  }

  /** Every surface at once, e.g. on background. */
  releaseAll() {
    for (const surface of [...this.outstanding.keys()]) this.release(surface);
  }

  isWarm(key: string) {
    return this.cache.isWarm(key);
  }

  /** Non-mutating readiness check for a render path. */
  peek(key: string) {
    return this.cache.peek(key);
  }

  /**
   * A signed URL came back 403. Every rendition of that asset is keyed on the
   * same identity, so they are all suspect and all dropped -- re-warming from a
   * refreshed URL is cheap, serving a stale one is a broken image.
   */
  invalidateIdentity(identity: string) {
    this.queue.cancelMatching((key) => identityFromCacheKey(key) === identity);
    return this.cache.forgetIdentity(identity);
  }

  onMemoryWarning() {
    this.cache.trimToPinned();
    this.telemetry("media_memory_warning", 0, this.cache.stats());
  }

  stats() {
    return { cache: this.cache.stats(), queue: this.queue.snapshot(), tier: this.network.tier };
  }

  private repin(surface: MediaSurface, keys: string[]) {
    for (const key of this.pinned.get(surface) ?? []) this.cache.unpin(key);
    for (const key of keys) this.cache.pin(key);
    this.pinned.set(surface, keys);
  }

  private onQueueEvent(event: PrefetchQueueEvent) {
    if (event.type === "media_prefetch_completed") {
      // Only real network work informs the network estimate. A cache hit
      // completing in 0ms would otherwise convince the estimator that a dying
      // connection is excellent.
      if (event.durationMs > 0) this.network = observeWarmDuration(this.network, event.durationMs);
      this.queue.setNetworkTier(this.network.tier);
      this.telemetry("media_prefetch_completed", event.durationMs, {
        surface: event.tag,
        priority: event.priority,
        identity_kind: identityKindOf(event.key)
      });
      return;
    }

    if (event.type === "media_prefetch_started") {
      this.telemetry("media_prefetch_started", 0, { surface: event.tag, priority: event.priority });
      return;
    }

    if (event.type === "prefetch_cancelled") {
      this.telemetry("prefetch_cancelled", 0, { surface: event.tag, priority: event.priority, reason: event.reason });
      return;
    }

    this.telemetry("media_prefetch_failed", 0, {
      surface: event.tag,
      priority: event.priority,
      reason: event.reason
    });
  }
}

/**
 * The cache key carries a URL path when nothing more durable was available, and
 * a URL path can carry a username. Telemetry gets the identity *kind* only.
 */
function identityKindOf(key: string): string {
  const identity = identityFromCacheKey(key);
  const cut = identity.indexOf(":");
  return cut === -1 ? "unknown" : identity.slice(0, cut);
}

let shared: MediaPrefetcher | null = null;

export function sharedMediaPrefetcher() {
  if (!shared) shared = new MediaPrefetcher();
  return shared;
}

export function __resetSharedMediaPrefetcher(next?: MediaPrefetcher) {
  shared = next ?? null;
}
