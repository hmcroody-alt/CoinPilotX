/**
 * §29: the attached track is warm before the reel is swiped to.
 *
 * The mission's phrasing is the test plan: "It is useless for video to start
 * instantly if music starts 800ms later." A reel whose manifest is warm and
 * whose track is cold opens with moving picture over silence, which is a worse
 * artefact than a slightly late video because the user reads it as broken audio
 * rather than as a slow network.
 *
 * These assert on the PLAN, not on fetches, for the same reason the planner is
 * pure: what to warm is a policy decision and belongs in a test that cannot be
 * made green by a faster network.
 */

import { planMediaPrefetch } from "../mediaPrefetchPlanner";
import { reelPrefetchMediaList, reelPrefetchMediaOf } from "../mediaDescriptors";
import type { MediaDescriptor } from "../mediaIdentity";

const TRACK = "https://cdn.example/sounds/trending.m4a";

/** A reel as the serializer emits it: media array plus a sibling audio block. */
function reel(id: number, audio?: Record<string, unknown> | null) {
  return {
    media: [{ id, mux_playback_id: `pb${id}`, type: "video", mux_status: "ready", playback_url: `https://stream.mux.com/pb${id}.m3u8` }],
    audio: audio === undefined ? { attached_audio_url: TRACK, audio_baked_in: false } : audio
  } as any;
}

function audioWarms(plan: ReturnType<typeof planMediaPrefetch>) {
  return plan.warm.filter((w) => w.rendition === "audio");
}

describe("folding the track onto the reel's descriptor", () => {
  it("carries the attached track alongside the picture", () => {
    const media = reelPrefetchMediaOf(reel(1));
    expect(media?.attached_audio_url).toBe(TRACK);
    // The video identity must be unchanged by the fold, or every reel with
    // music would miss the cache entry its own video already warmed.
    expect(media?.mux_playback_id).toBe("pb1");
  });

  it("carries nothing for a reel whose audio is baked into the video", () => {
    // Baked-in audio is not a second asset. Warming a track for it would spend
    // budget on bytes no player will ever open.
    const media = reelPrefetchMediaOf(reel(2, { attached_audio_url: TRACK, audio_baked_in: true }));
    expect(media?.attached_audio_url).toBeUndefined();
  });

  it("carries nothing for a reel with no music at all", () => {
    expect(reelPrefetchMediaOf(reel(3, null))?.attached_audio_url).toBeUndefined();
  });

  it("returns null for a text-only item rather than an empty descriptor", () => {
    expect(reelPrefetchMediaOf({ media: [], audio: null } as any)).toBeNull();
  });
});

describe("scheduling the track with the picture", () => {
  const reels = [reel(1), reel(2), reel(3), reel(4), reel(5)];
  const items = reelPrefetchMediaList(reels);

  it("warms the active reel's track and pins it", () => {
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 0, direction: "forward" });
    const warms = audioWarms(plan);
    expect(warms.length).toBeGreaterThan(0);
    // Pinned, because the track is not a nice-to-have for the reel on screen:
    // it is half of that reel's audio.
    expect(plan.pin).toContain(warms[0].key);
  });

  it("warms the NEXT reel's track, which is the whole point of §29", () => {
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 0, direction: "forward" });
    expect(audioWarms(plan).map((w) => w.index)).toEqual(expect.arrayContaining([0, 1]));
  });

  it("does not warm tracks for reels that only get a poster", () => {
    // reels policy is aggressiveAhead: 1, so index 2+ is poster-only. Warming
    // their music would pay for a second stream on a bet the poster rule
    // already declined to make.
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 0, direction: "forward" });
    expect(audioWarms(plan).map((w) => w.index)).not.toContain(2);
  });

  it("keys a shared track once, so a trending sound is not fetched per reel", () => {
    // Every fixture reel carries the SAME track. Keyed under each reel's video
    // identity there would be one entry per reel, all holding identical bytes,
    // evicting things still needed to store the duplicates.
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 0, direction: "forward" });
    const keys = new Set(audioWarms(plan).map((w) => w.key));
    expect(keys.size).toBe(1);
  });

  it("keys the track on the track, never on the reel's video identity", () => {
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 0, direction: "forward" });
    const key = audioWarms(plan)[0].key;
    expect(key).not.toContain("mux:pb1");
    expect(key).toBe("url:https://cdn.example/sounds/trending.m4a#audio");
  });

  it("strips a signature from the track's key (§17)", () => {
    // A signed music URL re-signed on the next feed page must hit the entry the
    // previous page warmed, not look like a brand-new asset.
    const signed: MediaDescriptor[] = [
      { id: 9, mux_playback_id: "pb9", type: "video", mux_status: "ready", playback_url: "https://stream.mux.com/pb9.m3u8", attached_audio_url: `${TRACK}?token=abc123` }
    ];
    const plan = planMediaPrefetch({ surface: "reels", items: signed, activeIndex: 0 });
    expect(audioWarms(plan)[0].key).toBe("url:https://cdn.example/sounds/trending.m4a#audio");
  });

  it("warms no track under Data Saver (§19)", () => {
    // Data Saver's promise is that nothing speculative happens. The active
    // reel's video is still fetched -- the user is looking at it -- but the
    // planner drops to the poster band, and music follows the same rule.
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 0, dataSaver: true });
    expect(audioWarms(plan)).toHaveLength(0);
  });

  it("warms no track during a fling (§24)", () => {
    const plan = planMediaPrefetch({ surface: "reels", items, activeIndex: 2, velocity: "fast" });
    expect(audioWarms(plan)).toHaveLength(0);
  });

  it("warms no track for a reel whose video is still transcoding (§56)", () => {
    // A Mux asset mid-transcode has a poster and no segments, so the planner
    // stops before the playable band -- and the track must stop with it rather
    // than warming music for a reel that cannot play.
    const pending: MediaDescriptor[] = [
      { id: 7, mux_playback_id: "pb7", type: "video", mux_status: "preparing", playback_url: "https://stream.mux.com/pb7.m3u8", attached_audio_url: TRACK }
    ];
    const plan = planMediaPrefetch({ surface: "reels", items: pending, activeIndex: 0 });
    expect(audioWarms(plan)).toHaveLength(0);
  });

  it("leaves surfaces without attached music untouched", () => {
    // The feed shares this planner. A change that made every surface start
    // emitting audio warms would be a budget regression nothing in Reels asked
    // for.
    const plan = planMediaPrefetch({
      surface: "feed",
      items: [{ id: 1, type: "image", media_url: "https://cdn.example/a.jpg" }],
      activeIndex: 0
    });
    expect(audioWarms(plan)).toHaveLength(0);
  });
});
