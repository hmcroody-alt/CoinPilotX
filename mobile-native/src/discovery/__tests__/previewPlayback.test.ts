/**
 * §6, §14.6 — play once, and replay only on a real return.
 *
 * The requirement this file exists for is the subtle half of §6: *"prefer replay
 * only when the user scrolls away long enough and returns, not on small
 * viewability jitter."* Those two situations are indistinguishable from inside
 * the card — both arrive as `active` going false and then true — so the
 * distinction has to be drawn by a policy that remembers, and the policy has to
 * be tested directly rather than through a component that would need a fake
 * scroll to exercise it.
 *
 * The memory is module-scope, not component state, because FlatList unmounts the
 * card during exactly the gesture the memory must survive. That makes the reset
 * helper below load-bearing for test isolation, not a convenience.
 */
import {
  DISCOVERY_PREVIEW_DURATION_MS,
  DISCOVERY_PREVIEW_REPLAY_COOLDOWN_MS,
  markPreviewCompleted,
  resetDiscoveryPreviewPlayback,
  shouldStartPreview
} from "../previewPlayback";

beforeEach(() => {
  resetDiscoveryPreviewPlayback();
});

describe("§14.6 — a preview plays once, for a bounded time", () => {
  it("runs for the 3–4 seconds the requirement names", () => {
    expect(DISCOVERY_PREVIEW_DURATION_MS).toBeGreaterThanOrEqual(3000);
    expect(DISCOVERY_PREVIEW_DURATION_MS).toBeLessThanOrEqual(4000);
  });

  it("lets a card that has never played start", () => {
    expect(shouldStartPreview("reel:1")).toBe(true);
  });

  it("refuses to restart a preview that just finished", () => {
    // The loop this prevents: preview ends, the effect re-runs on the very next
    // render, and the card plays forever. §4 says play once.
    markPreviewCompleted("reel:1", 1_000);

    expect(shouldStartPreview("reel:1", 1_000)).toBe(false);
  });

  it("keeps refusing for the whole cooldown, so jitter cannot restart it", () => {
    // A card wobbling across the 60% threshold produces a burst of
    // active/inactive transitions. Every one of them lands inside this window.
    markPreviewCompleted("reel:1", 1_000);

    for (const elapsed of [1, 100, 1_000, DISCOVERY_PREVIEW_REPLAY_COOLDOWN_MS - 1]) {
      expect(shouldStartPreview("reel:1", 1_000 + elapsed)).toBe(false);
    }
  });

  it("allows a replay once the user has genuinely been away", () => {
    markPreviewCompleted("reel:1", 1_000);

    expect(shouldStartPreview("reel:1", 1_000 + DISCOVERY_PREVIEW_REPLAY_COOLDOWN_MS)).toBe(true);
  });

  it("sets the cooldown long enough to outlast a scroll wobble", () => {
    // Shorter than a couple of seconds and the cooldown stops distinguishing
    // "came back" from "never really left".
    expect(DISCOVERY_PREVIEW_REPLAY_COOLDOWN_MS).toBeGreaterThan(DISCOVERY_PREVIEW_DURATION_MS);
  });
});

describe("§6 — an interrupted preview is not a completed one", () => {
  it("replays immediately for a card that left before its timer fired", () => {
    // Nothing marked it complete, so the memory has no entry and the card gets
    // its full preview the next time it is the primary card. A card the user
    // only half-saw should not be permanently spent.
    expect(shouldStartPreview("reel:99", 50_000)).toBe(true);
  });
});

describe("identity", () => {
  it("tracks each card separately, so one finishing does not silence its neighbour", () => {
    markPreviewCompleted("reel:1", 1_000);

    expect(shouldStartPreview("reel:1", 1_000)).toBe(false);
    expect(shouldStartPreview("reel:2", 1_000)).toBe(true);
  });

  it("refuses an empty key rather than sharing one bucket across every card", () => {
    // Non-video kinds pass "" — they have nothing to play, and if "" were a
    // real key the first status card to render would claim it for all of them.
    expect(shouldStartPreview("")).toBe(false);
  });

  it("ignores a completion recorded against an empty key", () => {
    markPreviewCompleted("", 1_000);

    expect(shouldStartPreview("reel:1", 1_000)).toBe(true);
  });
});

describe("§12 — the memory cannot grow without bound", () => {
  it("evicts old entries during a long scrolling session", () => {
    // A user scrolling for an hour must not accumulate an entry per reel they
    // passed. Eviction is oldest-first, so the cards still on screen survive.
    for (let index = 0; index < 500; index += 1) {
      markPreviewCompleted(`reel:${index}`, 1_000 + index);
    }

    // The earliest card is long gone from the map, which reads as "eligible".
    expect(shouldStartPreview("reel:0", 1_500)).toBe(true);
    // The most recent one is still remembered and still on cooldown.
    expect(shouldStartPreview("reel:499", 1_500)).toBe(false);
  });
});
