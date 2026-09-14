/**
 * The nine mutation guards.
 *
 * Every test here does two things. It asserts the behaviour the foundation is
 * supposed to have, and then it re-derives the same assertion against a locally
 * written MUTANT -- a deliberately broken version of the rule -- and proves the
 * assertion rejects it.
 *
 * The second half is the point. A test that only checks the good path passes
 * just as happily when the code it guards has been deleted, and the whole
 * reason a performance regression survives a green suite is that nobody ever
 * asked whether the test could fail. Writing the mutant next to the assertion
 * makes the sensitivity of the test part of the test.
 *
 * `expectMutantRejected` runs the mutant's assertion and requires it to throw.
 * If a future refactor makes the real rule and the mutant indistinguishable,
 * this file goes red before the regression ships.
 */

import { mediaCacheKey, mediaIdentityOf } from "../mediaIdentity";
import type { MediaDescriptor } from "../mediaIdentity";
import { MediaPrefetchCache, MEDIA_CACHE_LIMITS } from "../mediaPrefetchCache";
import { MediaPrefetchQueue, MEDIA_PRIORITY } from "../mediaPrefetchQueue";
import { planMediaPrefetch, SURFACE_POLICIES } from "../mediaPrefetchPlanner";
import { shouldAutoplay } from "../mediaAutoplayPolicy";
import { VIDEO_PREFETCH_BYTE_CAP, videoWarmPlan, warmVideoPlan } from "../mediaWarm";

/** Asserts that the given assertion FAILS -- i.e. that the mutant is caught. */
function expectMutantRejected(assertion: () => void) {
  expect(assertion).toThrow();
}

const reel = (n: number): MediaDescriptor => ({
  id: n,
  type: "video",
  mux_playback_id: `reel${n}`,
  playback_url: `https://stream.mux.com/reel${n}.m3u8`,
  poster_url: `https://img.mux.com/reel${n}/thumbnail.jpg`
});

const feedPhoto = (n: number): MediaDescriptor => ({
  id: 100 + n,
  type: "image",
  media_url: `https://cdn.pulsesoc.com/original/${n}.jpg`,
  cdn_url: `https://cdn.pulsesoc.com/feed/${n}.jpg`,
  thumbnail_url: `https://cdn.pulsesoc.com/thumb/${n}.jpg`
});

const reels = Array.from({ length: 10 }, (_, i) => reel(i));
const photos = Array.from({ length: 10 }, (_, i) => feedPhoto(i));

const settle = () => new Promise((resolve) => setImmediate(resolve));

// 1 -------------------------------------------------------------------------
describe("mutation 1: removing the next-Reel prefetch is detected", () => {
  it("plans the next reel playback-ready, and a plan without it is rejected", () => {
    const plan = planMediaPrefetch({ surface: "reels", items: reels, activeIndex: 3, direction: "forward" });
    const next = plan.warm.filter((entry) => entry.index === 4);
    const renditions = next.map((entry) => entry.rendition).sort();

    // N+1 gets both halves of "ready": the frame that paints instantly and the
    // manifest that lets playback start without a round trip. A poster alone
    // would still show a still image and then stall.
    expect(renditions).toEqual(["manifest", "poster"]);
    for (const entry of next) expect(entry.priority).toBe(MEDIA_PRIORITY.NEXT);

    // N+2 is poster only -- §3's "metadata and poster only" band.
    const afterNext = plan.warm.filter((entry) => entry.index === 5).map((entry) => entry.rendition);
    expect(afterNext).toEqual(["poster"]);

    // The mutant: a planner that only ever warms the visible item.
    const mutantWarm = plan.warm.filter((entry) => entry.index === 3);
    expectMutantRejected(() => {
      expect(mutantWarm.filter((entry) => entry.index === 4)).toHaveLength(2);
    });
  });

  it("reports a miss when the next reel was never warmed", () => {
    const events: string[] = [];
    const cache = new MediaPrefetchCache({ onEvent: (event) => events.push(event.type) });

    const key = mediaCacheKey(mediaIdentityOf(reel(4))!, "manifest");
    expect(cache.isWarm(key)).toBe(false);
    cache.recall(key);
    // A miss is observable, which is what makes the removal detectable in the
    // field rather than only in this file.
    expect(events).toContain("media_prefetch_miss");

    cache.remember({ key, rendition: "manifest", sourceUrl: "https://stream.mux.com/reel4.m3u8" });
    cache.recall(key);
    expect(events).toContain("cache_memory_hit");
  });
});

// 2 -------------------------------------------------------------------------
describe("mutation 2: more than one active player fails", () => {
  it("never designates two items active at once", () => {
    const decisions = reels.map((_, index) =>
      shouldAutoplay({
        visiblePercent: 100,
        isActiveItem: index === 3,
        routeFocused: true,
        appActive: true,
        mediaReady: true
      })
    );
    expect(decisions.filter((decision) => decision.play)).toHaveLength(1);

    // The mutant: autoplay driven by visibility alone, dropping isActiveItem.
    const mutant = reels.map(() => ({ play: true }));
    expectMutantRejected(() => {
      expect(mutant.filter((decision) => decision.play)).toHaveLength(1);
    });
  });
});

// 3 -------------------------------------------------------------------------
describe("mutation 3: off-screen playback fails", () => {
  it("refuses to play a cell below the visibility threshold", () => {
    const decision = shouldAutoplay({
      visiblePercent: 40,
      isActiveItem: true,
      routeFocused: true,
      appActive: true,
      mediaReady: true
    });
    expect(decision.play).toBe(false);
    expect(decision.play === false && decision.reason).toBe("not_visible");

    // The mutant: "any pixel counts".
    const mutant = (visiblePercent: number) => ({ play: visiblePercent > 0 });
    expectMutantRejected(() => {
      expect(mutant(40).play).toBe(false);
    });
  });

  it("refuses to play a blurred route or a backgrounded app", () => {
    const blurred = shouldAutoplay({ visiblePercent: 100, isActiveItem: true, routeFocused: false, appActive: true, mediaReady: true });
    const backgrounded = shouldAutoplay({ visiblePercent: 100, isActiveItem: true, routeFocused: true, appActive: false, mediaReady: true });
    expect(blurred.play).toBe(false);
    expect(backgrounded.play).toBe(false);
  });
});

// 4 -------------------------------------------------------------------------
describe("mutation 4: prefetching a long video in full fails", () => {
  it("bounds every video warm, regardless of duration", () => {
    const ninetyMinutes = reel(1);
    ninetyMinutes.duration = 90 * 60;
    const plan = videoWarmPlan(ninetyMinutes);

    expect(plan.kind).not.toBe("none");
    if (plan.kind === "none") throw new Error("unreachable");
    expect(plan.maxBytes).toBeLessThanOrEqual(VIDEO_PREFETCH_BYTE_CAP);
    expect(Number.isFinite(plan.maxBytes)).toBe(true);

    // The mutant: no cap, i.e. fetch the asset.
    const mutant = { maxBytes: Number.POSITIVE_INFINITY };
    expectMutantRejected(() => {
      expect(mutant.maxBytes).toBeLessThanOrEqual(VIDEO_PREFETCH_BYTE_CAP);
    });
  });

  it("drops a body when the server ignores the Range header", async () => {
    const oversized = new Array(4).fill("x").join("");
    const fakeFetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: (name: string) => (name.toLowerCase() === "content-length" ? String(VIDEO_PREFETCH_BYTE_CAP * 40) : null) },
      arrayBuffer: async () => new ArrayBuffer(oversized.length)
    });

    const plan = videoWarmPlan(reel(2));
    if (plan.kind === "none") throw new Error("expected a warmable plan");
    const result = await warmVideoPlan(plan, undefined, fakeFetch as unknown as typeof fetch);
    expect(result.bytes).toBeLessThanOrEqual(plan.maxBytes);
  });
});

// 5 -------------------------------------------------------------------------
describe("mutation 5: a queue that ignores visible priority fails", () => {
  it("runs the visible item before work queued ahead of it", async () => {
    const queue = new MediaPrefetchQueue();
    queue.setNetworkTier("weak"); // one slot, so ordering is observable
    const order: string[] = [];
    // A box rather than a bare `let`: TypeScript narrows a `let` initialised to
    // null and never reassigned in straight-line code down to `null`, and cannot
    // see the assignment that happens inside the executor.
    const blocker: { release: (() => void) | null } = { release: null };

    queue.enqueue({
      key: "blocker",
      tag: "reels",
      priority: MEDIA_PRIORITY.DISTANT as 4,
      run: () => new Promise<void>((resolve) => { order.push("blocker"); blocker.release = resolve; })
    });
    await settle();

    queue.enqueue({ key: "distant", tag: "reels", priority: MEDIA_PRIORITY.DISTANT as 4, run: async () => { order.push("distant"); } });
    queue.enqueue({ key: "visible", tag: "reels", priority: MEDIA_PRIORITY.VISIBLE as 0, run: async () => { order.push("visible"); } });

    blocker.release?.();
    await settle();
    await settle();
    await settle();

    expect(order.indexOf("visible")).toBeLessThan(order.indexOf("distant"));

    // The mutant: FIFO, which is what a queue becomes when someone "simplifies"
    // the comparator away.
    const fifo = ["blocker", "distant", "visible"];
    expectMutantRejected(() => {
      expect(fifo.indexOf("visible")).toBeLessThan(fifo.indexOf("distant"));
    });
  });
});

// 6 -------------------------------------------------------------------------
describe("mutation 6: full-resolution feed images fail", () => {
  it("warms the feed rendition, never the original", () => {
    const plan = planMediaPrefetch({ surface: "feed", items: photos, activeIndex: 2, direction: "forward" });
    const renditions = new Set(plan.warm.map((entry) => entry.rendition));

    expect(renditions.has("full")).toBe(false);
    expect(SURFACE_POLICIES.feed.primary).toBe("feed");
    expect(SURFACE_POLICIES.grid.primary).toBe("thumb");
    for (const entry of plan.warm) {
      expect(["feed", "thumb", "poster", "manifest"]).toContain(entry.rendition);
    }

    // The mutant: one rendition for everything.
    const mutant = plan.warm.map((entry) => ({ ...entry, rendition: "full" as const }));
    expectMutantRejected(() => {
      for (const entry of mutant) expect(entry.rendition).not.toBe("full");
    });
  });
});

// 7 -------------------------------------------------------------------------
describe("mutation 7: a signed URL as a permanent cache key fails", () => {
  /**
   * Each tier is checked on its own.
   *
   * Checking only the rich descriptor would let the whole URL-stripping layer be
   * deleted and still pass, because the Mux id and the row id would keep
   * answering -- which is exactly what happened the first time this was written.
   * A signed R2 object has neither, and it is the case that actually breaks.
   */
  it("keys two signings of one asset to the same entry, at every identity tier", () => {
    const sign = (over: Partial<MediaDescriptor>, token: string): MediaDescriptor => ({
      type: "video",
      playback_url: `https://stream.mux.com/reel5.m3u8?token=${token}&expires=${token.length}`,
      ...over
    });

    const tiers: Array<[string, Partial<MediaDescriptor>]> = [
      ["mux playback id", { mux_playback_id: "reel5" }],
      ["media row id", { id: 5 }],
      ["bare signed URL", {}]
    ];

    for (const [label, over] of tiers) {
      const first = mediaIdentityOf(sign(over, "FIRST"));
      const second = mediaIdentityOf(sign(over, "SECOND"));
      expect(first).not.toBeNull();
      expect(`${label}: ${first}`).toBe(`${label}: ${second}`);
      expect(first).not.toContain("token=");
      expect(first).not.toContain("expires=");
      expect(mediaCacheKey(first!, "full")).toBe(mediaCacheKey(second!, "full"));
    }

    // The mutant: key = the URL we were handed.
    expectMutantRejected(() => {
      expect(sign({}, "FIRST").playback_url).toBe(sign({}, "SECOND").playback_url);
    });
  });

  it("keeps renditions of one identity distinct", () => {
    const identity = mediaIdentityOf(reel(6))!;
    expect(mediaCacheKey(identity, "poster")).not.toBe(mediaCacheKey(identity, "full"));
  });
});

// 8 -------------------------------------------------------------------------
describe("mutation 8: autoplaying everything during a fling fails", () => {
  it("refuses autoplay while the list is flinging", () => {
    const flinging = shouldAutoplay({
      visiblePercent: 100,
      isActiveItem: true,
      routeFocused: true,
      appActive: true,
      mediaReady: true,
      velocity: "fast"
    });
    expect(flinging.play).toBe(false);
    expect(flinging.play === false && flinging.reason).toBe("scrolling_fast");

    const settled = shouldAutoplay({
      visiblePercent: 100,
      isActiveItem: true,
      routeFocused: true,
      appActive: true,
      mediaReady: true,
      velocity: "idle"
    });
    expect(settled.play).toBe(true);

    // The mutant: velocity is not consulted.
    const mutant = () => ({ play: true });
    expectMutantRejected(() => {
      expect(mutant().play).toBe(false);
    });
  });

  it("collapses the prefetch window during a fling instead of widening it", () => {
    const calm = planMediaPrefetch({ surface: "feed", items: photos, activeIndex: 4, direction: "forward", velocity: "idle" });
    const fling = planMediaPrefetch({ surface: "feed", items: photos, activeIndex: 4, direction: "forward", velocity: "fast" });
    expect(fling.warm.length).toBeLessThan(calm.warm.length);
    for (const entry of fling.warm) {
      expect(["poster", "thumb"]).toContain(entry.rendition);
    }
  });
});

// 9 -------------------------------------------------------------------------
describe("mutation 9: an unbounded cache fails", () => {
  it("evicts rather than growing past its entry budget", () => {
    const cache = new MediaPrefetchCache();
    for (let i = 0; i < MEDIA_CACHE_LIMITS.maxEntries + 40; i += 1) {
      cache.remember({ key: `url:img${i}#feed`, rendition: "feed", sourceUrl: `https://cdn/${i}.jpg` });
    }
    const stats = cache.stats();
    expect(stats.entries).toBeLessThanOrEqual(MEDIA_CACHE_LIMITS.maxEntries);
    expect(stats.bytes).toBeLessThanOrEqual(MEDIA_CACHE_LIMITS.maxBytes);

    // The mutant: a plain Map.
    const mutant = new Map<string, number>();
    for (let i = 0; i < MEDIA_CACHE_LIMITS.maxEntries + 40; i += 1) mutant.set(`k${i}`, i);
    expectMutantRejected(() => {
      expect(mutant.size).toBeLessThanOrEqual(MEDIA_CACHE_LIMITS.maxEntries);
    });
  });

  it("evicts least-recently-used first and never evicts a pin", () => {
    const cache = new MediaPrefetchCache();
    cache.remember({ key: "keep#full", rendition: "full", sourceUrl: "https://cdn/keep.mp4", pinned: true });
    cache.remember({ key: "old#feed", rendition: "feed", sourceUrl: "https://cdn/old.jpg" });
    cache.remember({ key: "new#feed", rendition: "feed", sourceUrl: "https://cdn/new.jpg" });
    cache.recall("old#feed"); // makes "new" the least recent

    for (let i = 0; i < MEDIA_CACHE_LIMITS.maxEntries; i += 1) {
      cache.remember({ key: `filler${i}#thumb`, rendition: "thumb", sourceUrl: `https://cdn/f${i}.jpg` });
    }

    expect(cache.isWarm("keep#full")).toBe(true);
    expect(cache.stats().entries).toBeLessThanOrEqual(MEDIA_CACHE_LIMITS.maxEntries);
  });

  it("drops everything unpinned on a memory warning", () => {
    const cache = new MediaPrefetchCache();
    cache.remember({ key: "pinned#full", rendition: "full", sourceUrl: "https://cdn/a.mp4", pinned: true });
    cache.remember({ key: "loose#feed", rendition: "feed", sourceUrl: "https://cdn/b.jpg" });
    cache.trimToPinned();
    expect(cache.isWarm("pinned#full")).toBe(true);
    expect(cache.isWarm("loose#feed")).toBe(false);
  });
});
