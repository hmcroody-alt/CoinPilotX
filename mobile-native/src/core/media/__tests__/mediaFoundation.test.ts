import {
  identityFromCacheKey,
  isMediaReady,
  knownAspectRatio,
  mediaCacheKey,
  mediaIdentityOf,
  renditionUrl,
  stripVolatileUrlParts
} from "../mediaIdentity";
import type { MediaDescriptor } from "../mediaIdentity";
import { MediaPrefetchQueue, MEDIA_PRIORITY } from "../mediaPrefetchQueue";
import type { PrefetchQueueEvent } from "../mediaPrefetchQueue";
import { MediaPrefetchCache } from "../mediaPrefetchCache";
import { planMediaPrefetch, priorityForDistance, SURFACE_POLICIES } from "../mediaPrefetchPlanner";
import { shouldAutoplay, AUTOPLAY_STARTS_UNMUTED, mayPrefetchWithoutPlaying } from "../mediaAutoplayPolicy";
import {
  createNetworkEstimator,
  createScrollTracker,
  isSufficientlyVisible,
  observeWarmDuration,
  trackScroll,
  VISIBLE_PERCENT_THRESHOLD
} from "../mediaViewportSignals";
import { MANIFEST_BYTE_CAP, VIDEO_PREFETCH_BYTE_CAP, videoWarmPlan } from "../mediaWarm";

const video = (over: Partial<MediaDescriptor> = {}): MediaDescriptor => ({
  id: 1,
  type: "video",
  mux_playback_id: "abc123",
  playback_url: "https://stream.mux.com/abc123.m3u8",
  poster_url: "https://img.mux.com/abc123/thumbnail.jpg",
  ...over
});

const photo = (over: Partial<MediaDescriptor> = {}): MediaDescriptor => ({
  id: 2,
  type: "image",
  media_url: "https://cdn.pulsesoc.com/uploads/a.jpg",
  cdn_url: "https://cdn.pulsesoc.com/feed/a.jpg",
  thumbnail_url: "https://cdn.pulsesoc.com/thumb/a.jpg",
  ...over
});

const settle = () => new Promise((resolve) => setImmediate(resolve));

describe("media identity", () => {
  it("keys on the playback id, not the signed URL", () => {
    const signed = video({ playback_url: "https://stream.mux.com/abc123.m3u8?token=eyJhb" });
    const refreshed = video({ playback_url: "https://stream.mux.com/abc123.m3u8?token=DIFFERENT" });
    expect(mediaIdentityOf(signed)).toBe("mux:abc123");
    expect(mediaIdentityOf(signed)).toBe(mediaIdentityOf(refreshed));
  });

  it("falls back to the row id, then to the query-stripped URL", () => {
    expect(mediaIdentityOf({ id: 44, type: "image", media_url: "/u/a.jpg" })).toBe("media:44");
    expect(mediaIdentityOf({ type: "image", media_url: "https://x/a.jpg?sig=1#f" })).toBe("url:https://x/a.jpg");
  });

  it("returns null rather than inventing a key", () => {
    expect(mediaIdentityOf({ type: "image" })).toBeNull();
    expect(mediaIdentityOf(null)).toBeNull();
    expect(mediaIdentityOf(undefined)).toBeNull();
  });

  it("keeps the path, because for unsigned CDN objects the path is the identity", () => {
    expect(stripVolatileUrlParts("https://x/a/b/c.jpg?s=1")).toBe("https://x/a/b/c.jpg");
    expect(mediaIdentityOf({ media_url: "https://x/a.jpg" })).not.toBe(mediaIdentityOf({ media_url: "https://x/b.jpg" }));
  });

  it("round-trips identity through a cache key containing a fragment-like path", () => {
    const identity = "url:https://x/a#b";
    expect(identityFromCacheKey(mediaCacheKey(identity, "feed"))).toBe(identity);
  });

  it("separates renditions of the same asset", () => {
    expect(mediaCacheKey("mux:abc123", "poster")).not.toBe(mediaCacheKey("mux:abc123", "manifest"));
  });

  it("refuses media that is still transcoding", () => {
    expect(isMediaReady(video())).toBe(true);
    expect(isMediaReady(video({ mux_processing: true }))).toBe(false);
    expect(isMediaReady(video({ processing_status: "preparing" }))).toBe(false);
    expect(isMediaReady(video({ hydration_state: "missing" }))).toBe(false);
  });

  it("never substitutes the full asset for a missing poster", () => {
    const noPoster = photo({ poster_url: null, mux_thumbnail_url: null, thumbnail_url: null });
    expect(renditionUrl(noPoster, "poster")).toBeNull();
    expect(renditionUrl(noPoster, "full")).toBe("https://cdn.pulsesoc.com/uploads/a.jpg");
  });

  /**
   * The chains have to end where the app's own display resolver ends.
   *
   * `mediaAccess.mediaDisplayUrl` falls through to `url` / `hls_url` /
   * `mux_hls_url`, and the messenger serializer emits a bare `url`. When those
   * spellings were missing here the media was not broken -- it was invisible:
   * `renditionUrl` returned null, the planner produced no target, and the asset
   * silently never warmed while every other test stayed green. A benchmark
   * caught it; no assertion did.
   */
  it("resolves the spellings the display resolver accepts", () => {
    const bare = { type: "image", url: "https://cdn.pulsesoc.com/uploads/bare.jpg" };
    expect(renditionUrl(bare, "full")).toBe("https://cdn.pulsesoc.com/uploads/bare.jpg");
    expect(renditionUrl(bare, "feed")).toBe("https://cdn.pulsesoc.com/uploads/bare.jpg");
    expect(mediaIdentityOf(bare)).toBe("url:https://cdn.pulsesoc.com/uploads/bare.jpg");
    // Still no substitution into a thumbnail slot.
    expect(renditionUrl(bare, "thumb")).toBeNull();

    const hls = { type: "video", hls_url: "https://stream.pulsesoc.com/v/9.m3u8?token=x" };
    expect(renditionUrl(hls, "manifest")).toBe("https://stream.pulsesoc.com/v/9.m3u8?token=x");
    expect(mediaIdentityOf(hls)).toBe("url:https://stream.pulsesoc.com/v/9.m3u8");
  });

  it("knows the aspect ratio before a byte is fetched", () => {
    expect(knownAspectRatio(photo({ aspect_ratio: 1.5 }))).toBe(1.5);
    expect(knownAspectRatio(photo({ width: 200, height: 100 }))).toBe(2);
    expect(knownAspectRatio(photo())).toBeNull();
  });
});

describe("priority queue", () => {
  const makeQueue = (events: PrefetchQueueEvent[] = []) =>
    new MediaPrefetchQueue({ onEvent: (e) => events.push(e), now: () => 0 });

  it("bounds concurrency", async () => {
    const queue = makeQueue();
    let peak = 0;
    let live = 0;
    const hold: Array<() => void> = [];
    for (let i = 0; i < 20; i += 1) {
      queue.enqueue({
        key: `k${i}`,
        priority: 2,
        tag: "feed",
        run: () =>
          new Promise<void>((resolve) => {
            live += 1;
            peak = Math.max(peak, live);
            hold.push(() => {
              live -= 1;
              resolve();
            });
          })
      });
    }
    await settle();
    expect(peak).toBe(queue.concurrencyLimit);
    expect(peak).toBeLessThan(20);
    hold.forEach((release) => release());
  });

  it("serves a visible request before queued speculation", async () => {
    const queue = makeQueue();
    const order: string[] = [];
    const hold: Array<() => void> = [];
    const block = (key: string) => () =>
      new Promise<void>((resolve) => {
        order.push(key);
        hold.push(resolve);
      });

    for (let i = 0; i < 8; i += 1) {
      queue.enqueue({ key: `far${i}`, priority: MEDIA_PRIORITY.DISTANT as 4, tag: "feed", run: block(`far${i}`) });
    }
    queue.enqueue({ key: "visible", priority: MEDIA_PRIORITY.VISIBLE as 0, tag: "feed", run: block("visible") });
    await settle();

    // Every slot was already full of P4 work, so the only way "visible" starts
    // is preemption.
    expect(order).toContain("visible");
    hold.forEach((release) => release());
  });

  it("requeues a preempted task rather than losing it", async () => {
    const events: PrefetchQueueEvent[] = [];
    const queue = new MediaPrefetchQueue({ onEvent: (e) => events.push(e), networkTier: "weak", now: () => 0 });
    const hold: Array<() => void> = [];
    queue.enqueue({
      key: "guess",
      priority: 4,
      tag: "feed",
      run: () => new Promise<void>((resolve) => hold.push(resolve))
    });
    await settle();
    queue.enqueue({ key: "onscreen", priority: 0, tag: "feed", run: () => undefined });
    await settle();

    expect(events.some((e) => e.type === "prefetch_cancelled" && e.key === "guess" && e.reason === "preempted")).toBe(true);
    expect(queue.has("guess")).toBe(true);
    hold.forEach((release) => release());
  });

  it("never preempts a visible task for another visible task", async () => {
    const events: PrefetchQueueEvent[] = [];
    const queue = new MediaPrefetchQueue({ onEvent: (e) => events.push(e), networkTier: "weak", now: () => 0 });
    const hold: Array<() => void> = [];
    queue.enqueue({ key: "a", priority: 0, tag: "feed", run: () => new Promise<void>((r) => hold.push(r)) });
    await settle();
    queue.enqueue({ key: "b", priority: 0, tag: "feed", run: () => undefined });
    await settle();
    expect(events.some((e) => e.type === "prefetch_cancelled")).toBe(false);
    hold.forEach((r) => r());
  });

  it("collapses duplicate keys into one task", async () => {
    const queue = makeQueue();
    const run = jest.fn(() => Promise.resolve());
    queue.enqueue({ key: "same", priority: 3, tag: "feed", run });
    queue.enqueue({ key: "same", priority: 3, tag: "reels", run });
    await settle();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("promotes a waiting key without replacing its work", async () => {
    const queue = new MediaPrefetchQueue({ networkTier: "weak", now: () => 0 });
    const hold: Array<() => void> = [];
    const real = jest.fn(() => Promise.resolve());
    queue.enqueue({ key: "busy", priority: 0, tag: "feed", run: () => new Promise<void>((r) => hold.push(r)) });
    await settle();
    queue.enqueue({ key: "later", priority: 4, tag: "feed", run: real });
    queue.enqueue({ key: "later", priority: 1, tag: "feed", run: () => undefined });
    expect(queue.snapshot().pending[0]).toMatchObject({ key: "later", priority: 1 });
    hold.forEach((r) => r());
    await settle();
    expect(real).toHaveBeenCalledTimes(1);
  });

  it("drops a surface's predictions when it is left", async () => {
    const events: PrefetchQueueEvent[] = [];
    const queue = new MediaPrefetchQueue({ onEvent: (e) => events.push(e), networkTier: "weak", now: () => 0 });
    queue.enqueue({ key: "r1", priority: 1, tag: "reels", run: () => new Promise(() => undefined) });
    queue.enqueue({ key: "r2", priority: 2, tag: "reels", run: () => undefined });
    queue.enqueue({ key: "f1", priority: 2, tag: "feed", run: () => undefined });
    await settle();

    queue.cancelTag("reels");
    expect(events.filter((e) => e.type === "prefetch_cancelled").map((e) => e.key).sort()).toEqual(["r1", "r2"]);
    // The other surface is untouched: leaving Reels must not cancel the feed's
    // work, which is the whole reason cancellation is scoped by tag.
    expect(queue.has("f1")).toBe(true);
  });

  it("shrinks concurrency on a weak network", () => {
    const queue = new MediaPrefetchQueue({ networkTier: "good" });
    expect(queue.concurrencyLimit).toBe(4);
    queue.setNetworkTier("weak");
    expect(queue.concurrencyLimit).toBe(1);
  });
});

describe("bounded cache", () => {
  it("evicts least-recently-used past the entry cap", () => {
    const cache = new MediaPrefetchCache({ maxEntries: 3, maxBytes: 1e9 });
    for (const key of ["a", "b", "c"]) cache.remember({ key, rendition: "thumb", sourceUrl: key });
    cache.recall("a");
    cache.remember({ key: "d", rendition: "thumb", sourceUrl: "d" });
    expect(cache.size).toBe(3);
    expect(cache.isWarm("b")).toBe(false);
    expect(cache.isWarm("a")).toBe(true);
  });

  it("stays bounded under sustained scrolling", () => {
    const cache = new MediaPrefetchCache({ maxEntries: 10, maxBytes: 1e9 });
    for (let i = 0; i < 5000; i += 1) cache.remember({ key: `k${i}`, rendition: "feed", sourceUrl: `u${i}` });
    expect(cache.size).toBeLessThanOrEqual(10);
  });

  it("evicts on the byte budget even when the entry count is fine", () => {
    const cache = new MediaPrefetchCache({ maxEntries: 1000, maxBytes: 300 });
    cache.remember({ key: "a", rendition: "full", sourceUrl: "a", bytes: 200 });
    cache.remember({ key: "b", rendition: "full", sourceUrl: "b", bytes: 200 });
    expect(cache.bytes).toBeLessThanOrEqual(300);
    expect(cache.isWarm("a")).toBe(false);
  });

  it("does not evict what is on screen", () => {
    const cache = new MediaPrefetchCache({ maxEntries: 2, maxBytes: 1e9 });
    cache.remember({ key: "onscreen", rendition: "feed", sourceUrl: "x", pinned: true });
    for (let i = 0; i < 50; i += 1) cache.remember({ key: `g${i}`, rendition: "feed", sourceUrl: "y" });
    expect(cache.isWarm("onscreen")).toBe(true);
  });

  it("drops every rendition of an asset when its identity is invalidated", () => {
    const cache = new MediaPrefetchCache();
    cache.remember({ key: mediaCacheKey("mux:a", "poster"), rendition: "poster", sourceUrl: "p" });
    cache.remember({ key: mediaCacheKey("mux:a", "manifest"), rendition: "manifest", sourceUrl: "m" });
    cache.remember({ key: mediaCacheKey("mux:b", "poster"), rendition: "poster", sourceUrl: "p2" });
    expect(cache.forgetIdentity("mux:a")).toBe(2);
    expect(cache.isWarm(mediaCacheKey("mux:b", "poster"))).toBe(true);
  });

  it("keeps a failed entry so a bad URL is not retried every frame, but reports a miss", () => {
    const seen: string[] = [];
    const cache = new MediaPrefetchCache({ onEvent: (e) => seen.push(e.type) });
    cache.remember({ key: "bad", rendition: "feed", sourceUrl: "x", state: "failed" });
    expect(cache.recall("bad")).not.toBeNull();
    expect(cache.isWarm("bad")).toBe(false);
    expect(seen).toContain("media_prefetch_miss");
  });

  it("releases everything unpinned on a memory warning", () => {
    const cache = new MediaPrefetchCache();
    cache.remember({ key: "keep", rendition: "feed", sourceUrl: "x", pinned: true });
    cache.remember({ key: "drop", rendition: "feed", sourceUrl: "y" });
    cache.trimToPinned();
    expect(cache.isWarm("keep")).toBe(true);
    expect(cache.isWarm("drop")).toBe(false);
  });
});

describe("prefetch planner", () => {
  const reels = Array.from({ length: 20 }, (_, i) => video({ id: i, mux_playback_id: `m${i}` }));

  it("puts the visible item at P0 and grades the rest by distance", () => {
    expect(priorityForDistance(0, 1)).toBe(MEDIA_PRIORITY.VISIBLE);
    expect(priorityForDistance(1, 1)).toBe(MEDIA_PRIORITY.NEXT);
    expect(priorityForDistance(2, 1)).toBe(MEDIA_PRIORITY.NEAR);
    expect(priorityForDistance(-1, 1)).toBe(MEDIA_PRIORITY.NEAR);
    expect(priorityForDistance(9, 1)).toBe(MEDIA_PRIORITY.DISTANT);
  });

  it("warms the next reel's manifest and the one after that's poster only", () => {
    const plan = planMediaPrefetch({ surface: "reels", items: reels, activeIndex: 5, direction: "forward" });
    const at = (index: number) => plan.warm.filter((w) => w.index === index).map((w) => w.rendition).sort();
    expect(at(5)).toEqual(["manifest", "poster"]);
    expect(at(6)).toEqual(["manifest", "poster"]);
    expect(at(7)).toEqual(["poster"]);
    expect(at(8)).toEqual(["poster"]);
  });

  it("retains the previous reel for a reverse swipe", () => {
    const plan = planMediaPrefetch({ surface: "reels", items: reels, activeIndex: 5, direction: "forward" });
    expect(plan.warm.some((w) => w.index === 4)).toBe(true);
    expect(plan.warm.find((w) => w.index === 4)?.priority).toBe(MEDIA_PRIORITY.NEAR);
  });

  it("does not reach past its window", () => {
    const plan = planMediaPrefetch({ surface: "reels", items: reels, activeIndex: 5, direction: "forward" });
    const indices = plan.warm.map((w) => w.index);
    expect(Math.max(...indices)).toBeLessThanOrEqual(5 + SURFACE_POLICIES.reels.ahead);
    expect(Math.min(...indices)).toBeGreaterThanOrEqual(5 - SURFACE_POLICIES.reels.behind);
  });

  it("follows the thumb backwards", () => {
    const plan = planMediaPrefetch({ surface: "feed", items: reels, activeIndex: 10, direction: "backward" });
    const indices = plan.warm.map((w) => w.index);
    expect(Math.min(...indices)).toBeLessThan(10);
    expect(Math.max(...indices)).toBeLessThanOrEqual(10 + SURFACE_POLICIES.feed.behind);
  });

  it("collapses to posters during a fling", () => {
    const plan = planMediaPrefetch({ surface: "feed", items: reels, activeIndex: 5, velocity: "fast" });
    expect(plan.warm.every((w) => w.rendition === SURFACE_POLICIES.feed.cheap)).toBe(true);
    expect(plan.warm.some((w) => w.rendition === "manifest")).toBe(false);
  });

  it("stops guessing entirely under Data Saver", () => {
    const plan = planMediaPrefetch({ surface: "reels", items: reels, activeIndex: 5, dataSaver: true });
    expect(plan.warm.every((w) => w.index === 5)).toBe(true);
    expect(plan.warm.every((w) => w.rendition === "poster")).toBe(true);
  });

  it("shrinks the window on a weak network", () => {
    const good = planMediaPrefetch({ surface: "feed", items: reels, activeIndex: 5, networkTier: "good" });
    const weak = planMediaPrefetch({ surface: "feed", items: reels, activeIndex: 5, networkTier: "weak" });
    expect(weak.warm.length).toBeLessThan(good.warm.length);
  });

  it("never warms video for Messenger or a profile grid", () => {
    for (const surface of ["messenger", "grid"] as const) {
      const plan = planMediaPrefetch({ surface, items: reels, activeIndex: 5 });
      expect(plan.warm.some((w) => w.rendition === "manifest")).toBe(false);
      expect(plan.warm.every((w) => w.rendition === "thumb")).toBe(true);
    }
  });

  it("warms only the poster of an asset that is still transcoding", () => {
    const items = [video({ id: 1, mux_playback_id: "p1", mux_processing: true })];
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 0 });
    expect(plan.warm.map((w) => w.rendition)).toEqual(["poster"]);
  });

  it("plans nothing for a blurred surface", () => {
    expect(planMediaPrefetch({ surface: "reels", items: reels, activeIndex: 5, active: false }).warm).toHaveLength(0);
  });

  it("skips items with no durable identity instead of inventing one", () => {
    const items = [photo(), { type: "image" } as MediaDescriptor, photo({ id: 9 })];
    const plan = planMediaPrefetch({ surface: "feed", items, activeIndex: 0 });
    expect(plan.warm.some((w) => w.index === 1)).toBe(false);
  });

  it("pins what the active item needs", () => {
    const plan = planMediaPrefetch({ surface: "reels", items: reels, activeIndex: 5 });
    expect(plan.pin).toContain(mediaCacheKey("mux:m5", "poster"));
    expect(plan.pin).toContain(mediaCacheKey("mux:m5", "manifest"));
  });
});

describe("video warm bound", () => {
  it("fetches a manifest, never a stream", () => {
    const plan = videoWarmPlan(video());
    expect(plan.kind).toBe("hls");
    if (plan.kind !== "none") expect(plan.maxBytes).toBe(MANIFEST_BYTE_CAP);
  });

  it("caps a progressive file no matter how long it is", () => {
    const long = video({
      mux_playback_id: null,
      playback_url: null,
      valid_url: null,
      media_url: "https://cdn.pulsesoc.com/uploads/ninety-minutes.mp4",
      duration: 90 * 60
    });
    const plan = videoWarmPlan(long);
    expect(plan.kind).toBe("progressive");
    if (plan.kind !== "none") {
      expect(plan.maxBytes).toBe(VIDEO_PREFETCH_BYTE_CAP);
      expect(Number.isFinite(plan.maxBytes)).toBe(true);
    }
  });

  it("reads the extension from the path, not the signature", () => {
    const signed = video({ playback_url: "https://stream.mux.com/abc.m3u8?token=x.mp4" });
    expect(videoWarmPlan(signed).kind).toBe("hls");
  });

  it("refuses to plan a video warm for a photo", () => {
    expect(videoWarmPlan(photo())).toMatchObject({ kind: "none", reason: "not_video" });
  });
});

describe("autoplay policy", () => {
  const eligible = {
    visiblePercent: 100,
    isActiveItem: true,
    routeFocused: true,
    appActive: true,
    mediaReady: true
  };

  it("plays unmuted by default", () => {
    expect(AUTOPLAY_STARTS_UNMUTED).toBe(true);
    expect(shouldAutoplay(eligible)).toEqual({ play: true, muted: false });
  });

  it("honours an explicit user mute without refusing to play", () => {
    expect(shouldAutoplay({ ...eligible, userMuted: true })).toEqual({ play: true, muted: true });
  });

  it("refuses a sliver at the edge of the screen", () => {
    expect(isSufficientlyVisible(VISIBLE_PERCENT_THRESHOLD)).toBe(true);
    expect(shouldAutoplay({ ...eligible, visiblePercent: 1 })).toMatchObject({ play: false, reason: "not_visible" });
    expect(shouldAutoplay({ ...eligible, visiblePercent: 50 })).toMatchObject({ play: false, reason: "not_visible" });
  });

  it("refuses everything that is not the active item", () => {
    expect(shouldAutoplay({ ...eligible, isActiveItem: false })).toMatchObject({ play: false, reason: "not_active_item" });
  });

  it("stops on route blur, background and overlay", () => {
    expect(shouldAutoplay({ ...eligible, routeFocused: false })).toMatchObject({ reason: "route_blurred" });
    expect(shouldAutoplay({ ...eligible, appActive: false })).toMatchObject({ reason: "app_backgrounded" });
    expect(shouldAutoplay({ ...eligible, overlayOpen: true })).toMatchObject({ reason: "overlay_open" });
  });

  it("does not autoplay during a fling", () => {
    expect(shouldAutoplay({ ...eligible, velocity: "fast" })).toMatchObject({ play: false, reason: "scrolling_fast" });
    expect(shouldAutoplay({ ...eligible, velocity: "slow" })).toMatchObject({ play: true });
  });

  it("never autoplays a Messenger preview or a grid cell", () => {
    expect(shouldAutoplay({ ...eligible, surfaceAllowsAutoplay: false })).toMatchObject({
      play: false,
      reason: "surface_disallows_autoplay"
    });
  });

  it("refuses media that is not ready", () => {
    expect(shouldAutoplay({ ...eligible, mediaReady: false })).toMatchObject({ reason: "media_not_ready" });
  });

  it("separates preloading from playing", () => {
    const offscreen = { ...eligible, isActiveItem: false };
    expect(shouldAutoplay(offscreen).play).toBe(false);
    expect(mayPrefetchWithoutPlaying(offscreen)).toBe(true);
  });
});

describe("viewport signals", () => {
  it("classifies a fling and holds it through the boundary", () => {
    let tracker = createScrollTracker(0, 1000);
    tracker = trackScroll(tracker, 0, 1000);
    tracker = trackScroll(tracker, 600, 1100); // 6 px/ms
    expect(tracker.band).toBe("fast");
    tracker = trackScroll(tracker, 700, 1200); // 1 px/ms -- above the exit floor
    expect(tracker.band).toBe("fast");
    tracker = trackScroll(tracker, 710, 1300); // 0.1 px/ms
    expect(tracker.band).toBe("slow");
  });

  it("does not divide by a sub-frame interval", () => {
    let tracker = createScrollTracker(0, 1000);
    tracker = trackScroll(tracker, 0, 1000);
    const same = trackScroll(tracker, 400, 1001);
    expect(same.band).toBe("idle");
  });

  it("reports direction", () => {
    let tracker = createScrollTracker(500, 1000);
    tracker = trackScroll(tracker, 500, 1000);
    tracker = trackScroll(tracker, 600, 1100);
    expect(tracker.direction).toBe("forward");
    tracker = trackScroll(tracker, 400, 1300);
    expect(tracker.direction).toBe("backward");
  });

  it("uses the median so one stall does not collapse the window", () => {
    let estimator = createNetworkEstimator();
    for (const ms of [100, 120, 90, 110, 9000, 100, 95, 105]) {
      estimator = observeWarmDuration(estimator, ms);
    }
    expect(estimator.tier).toBe("good");
  });

  it("degrades when the link really is slow", () => {
    let estimator = createNetworkEstimator();
    for (const ms of [2500, 3000, 2800, 2600]) estimator = observeWarmDuration(estimator, ms);
    expect(estimator.tier).toBe("weak");
  });
});
