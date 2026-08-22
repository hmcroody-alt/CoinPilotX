/**
 * §1, §3, §14.1, §14.3 — the card is large, and the next one always peeks.
 *
 * These are the two requirements that a rendered-tree assertion is *worst* at
 * proving. "Is the card 78% of the screen" measured through a test renderer
 * depends on the fake window size jest happens to report, and "is the next card
 * partially visible" depends on a layout pass that never runs in jsdom. Both are
 * arithmetic, so they are asserted as arithmetic, across the device widths the
 * app actually ships to.
 *
 * The device list is the point. A single 390pt check would pass for a fixed
 * pixel width, which is exactly the bug this module exists to fix.
 */
import {
  DISCOVERY_CARD_GAP,
  DISCOVERY_EDGE_INSET,
  DISCOVERY_MIN_PEEK,
  discoveryCardMetrics
} from "../discoveryCardMetrics";
import type { DiscoveryModuleKind } from "../discoveryRows";

/** iPhone SE, 13 mini, 8, 15, 15 Plus, 15 Pro Max, Pro Max landscape-ish. */
const PHONE_WIDTHS = [320, 360, 375, 390, 414, 428, 440];

/** iPad, where the width cap deliberately takes over — see the band tests. */
const WIDE_WIDTHS = [744, 1024];

const DEVICE_WIDTHS = [...PHONE_WIDTHS, ...WIDE_WIDTHS];

const MEDIA_KINDS: DiscoveryModuleKind[] = ["reels", "statuses", "groups", "people"];

describe("§14.1 — the card is a large proportion of the feed, on every device", () => {
  it("puts video-shaped cards in the 70–85% band on every phone width", () => {
    for (const width of PHONE_WIDTHS) {
      const metrics = discoveryCardMetrics(width, "reels");

      expect(metrics.widthRatio).toBeGreaterThanOrEqual(0.7);
      expect(metrics.widthRatio).toBeLessThanOrEqual(0.85);
    }
  });

  it("keeps every kind in the band, so no module reads as a thumbnail row", () => {
    for (const kind of MEDIA_KINDS) {
      for (const width of PHONE_WIDTHS) {
        const metrics = discoveryCardMetrics(width, kind);

        expect(metrics.widthRatio).toBeGreaterThanOrEqual(0.7);
        expect(metrics.widthRatio).toBeLessThanOrEqual(0.85);
      }
    }
  });

  it("caps the card on a tablet instead of building one taller than the screen", () => {
    // 744 × 0.78 = 580pt wide, which at the reels aspect is an 829pt-tall card:
    // taller than the device it is on. So the band is a phone rule and the cap
    // is what takes over above it. The card must still dominate — half the
    // screen is the floor — it just stops chasing the ratio.
    for (const width of WIDE_WIDTHS) {
      const metrics = discoveryCardMetrics(width, "reels");

      expect(metrics.widthRatio).toBeLessThan(0.78);
      expect(metrics.widthRatio).toBeGreaterThan(0.35);
      expect(metrics.mediaHeight).toBeLessThan(900);
    }
  });

  it("is a ratio, not a constant: a wider screen gets a wider card", () => {
    // The regression this catches by name. `CARD_WIDTH = 148` passes every
    // absolute assertion above on some device and fails this one everywhere.
    const narrow = discoveryCardMetrics(320, "reels").cardWidth;
    const wide = discoveryCardMetrics(440, "reels").cardWidth;

    expect(wide).toBeGreaterThan(narrow);
  });

  it("is far larger than the thumbnail it replaced", () => {
    // 148pt on a 390pt phone was 38% of the screen. Anything near that number
    // means the ratio has been quietly reverted.
    const metrics = discoveryCardMetrics(390, "reels");

    expect(metrics.cardWidth).toBeGreaterThan(148 * 1.8);
  });

  it("makes the media tall enough to be content and not a strip", () => {
    // A card wider than it is tall reads as a banner. Reels are shot vertically;
    // the frame has to agree.
    const metrics = discoveryCardMetrics(390, "reels");

    expect(metrics.mediaHeight).toBeGreaterThan(metrics.cardWidth);
  });

  it("gives groups a landscape frame rather than letterboxing cover art", () => {
    // §2 forbids letterboxing, and a landscape cover cropped to a portrait card
    // is the same defect arriving as a crop instead of as a bar.
    const metrics = discoveryCardMetrics(390, "groups");

    expect(metrics.mediaHeight).toBeLessThan(metrics.cardWidth);
  });

  it("never shapes a person card like a video frame (§10)", () => {
    const person = discoveryCardMetrics(390, "people");
    const reel = discoveryCardMetrics(390, "reels");

    expect(person.mediaHeight / person.cardWidth).toBeLessThan(reel.mediaHeight / reel.cardWidth);
  });
});

describe("§14.3 — the next card is always partially visible", () => {
  it("leaves at least the minimum peek on every device and kind", () => {
    for (const kind of MEDIA_KINDS) {
      for (const width of DEVICE_WIDTHS) {
        const metrics = discoveryCardMetrics(width, kind);

        expect(metrics.peek).toBeGreaterThanOrEqual(DISCOVERY_MIN_PEEK);
      }
    }
  });

  it("derives the peek from the same numbers the carousel is laid out with", () => {
    // If these two expressions could disagree, the carousel would snap to a
    // position no card starts at and the peek would drift by a few points per
    // card until it vanished.
    const metrics = discoveryCardMetrics(390, "reels");

    expect(metrics.stride).toBe(metrics.cardWidth + metrics.gap);
    expect(metrics.edgeInset + metrics.cardWidth + metrics.gap + metrics.peek).toBe(390);
  });

  it("clamps the card rather than hiding the neighbour on a narrow device", () => {
    // 320 × 0.82 = 262, which would leave only 36pt — this passes, but the clamp
    // is what guarantees it keeps passing if the ratio is ever raised.
    const metrics = discoveryCardMetrics(320, "groups");

    expect(metrics.cardWidth).toBeLessThanOrEqual(
      320 - DISCOVERY_EDGE_INSET - DISCOVERY_CARD_GAP - DISCOVERY_MIN_PEEK
    );
    expect(metrics.peek).toBeGreaterThanOrEqual(DISCOVERY_MIN_PEEK);
  });
});

describe("§14.2 — the edge inset is small enough that the row reads as full-bleed", () => {
  it("keeps total horizontal chrome well under what the old bordered panel used", () => {
    // The old row was margin 16 + padding 16 = 32pt before any media. Anything
    // approaching that is the panel coming back.
    expect(DISCOVERY_EDGE_INSET).toBeLessThan(16);
  });

  it("spends the overwhelming majority of the screen on media", () => {
    const metrics = discoveryCardMetrics(390, "reels");
    const chrome = 390 - metrics.cardWidth;

    expect(chrome).toBeLessThan(metrics.cardWidth / 2);
  });
});

describe("degenerate measurements", () => {
  it("falls back to a sane phone width before first layout rather than a zero card", () => {
    // `useWindowDimensions` is reliable, but a 0 here would produce a 0-width
    // card, which renders as an invisible row rather than as an obvious crash.
    for (const bad of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      const metrics = discoveryCardMetrics(bad, "reels");

      expect(metrics.cardWidth).toBeGreaterThan(0);
      expect(metrics.mediaHeight).toBeGreaterThan(0);
    }
  });

  it("returns whole points, because half-pixel strides accumulate into drift", () => {
    for (const width of DEVICE_WIDTHS) {
      const metrics = discoveryCardMetrics(width, "reels");

      expect(Number.isInteger(metrics.cardWidth)).toBe(true);
      expect(Number.isInteger(metrics.mediaHeight)).toBe(true);
      expect(Number.isInteger(metrics.stride)).toBe(true);
    }
  });
});
