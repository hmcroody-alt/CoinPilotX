/**
 * The two reasons a reel is never offered a Marketplace chip.
 *
 * The assertions that matter here are the *position* ones. Anyone can make a
 * chip not appear on a reel; the bug this module exists to prevent is making it
 * not appear and also not appear anywhere else, because the binder counted the
 * excluded reel, spent the slot on it, and the renderer then hid it. Every test
 * below therefore checks what the remaining ids are, not merely that the
 * excluded id is gone.
 */
import fs from "fs";
import path from "path";
import type { PulseReel } from "../../api/reels";
import {
  chipEligibleReelIds,
  reelIsLive,
  reelSellsItsOwnProduct
} from "../reelChipEligibility";

const reel = (id: number, extra: Partial<PulseReel> = {}): PulseReel =>
  ({ id, ...extra }) as PulseReel;

/**
 * The shape `isPulseCommerceOverlay` accepts; see `api/pulseCommerceOverlay`.
 *
 * `publication_id` is part of the guard and not decoration: it is what makes
 * this a PulseDrop *publication* rather than any object carrying a product.
 */
const overlay = (listingId: number) => ({
  pulsedrop: true,
  publication_id: 900 + listingId,
  product: { listing_id: listingId, title: "A product", route: "/pulse/marketplace/1" },
  cta: { action: "view_product", i18n_key: "", fallback: "View product" }
});

describe("chipEligibleReelIds", () => {
  it("offers every ordinary reel, in list order", () => {
    expect(chipEligibleReelIds([reel(1), reel(2), reel(3)])).toEqual(["1", "2", "3"]);
  });

  it("withholds a Live, and does not renumber the reels after it", () => {
    const ids = chipEligibleReelIds([reel(1), reel(2, { live_session_id: 88 }), reel(3)]);
    expect(ids).toEqual(["1", "3"]);
  });

  it("withholds a Live declared under the nested `live` object too", () => {
    const ids = chipEligibleReelIds([reel(1), reel(2, { live: { live_session_id: 88 } }), reel(3)]);
    expect(ids).toEqual(["1", "3"]);
  });

  it("withholds a reel that already sells its own product", () => {
    // The PulseDrop case. The overlay on reel 2 is about the product the video
    // demonstrates; a chip there would advertise a different one over it.
    const ids = chipEligibleReelIds([reel(1), reel(2, { commerce: overlay(42) as never }), reel(3)]);
    expect(ids).toEqual(["1", "3"]);
  });

  it("leaves the slot available to the next reel rather than spending it", () => {
    // The point of filtering the input instead of hiding the output. With the
    // excluded reel absent from the list the binder hands its slot to whatever
    // comes next; if it were suppressed at render the list would be one chip
    // short and nothing would say why.
    const withPulseDrop = chipEligibleReelIds([reel(1, { commerce: overlay(7) as never }), reel(2)]);
    const withoutIt = chipEligibleReelIds([reel(2)]);
    expect(withPulseDrop).toEqual(withoutIt);
  });

  it("drops the absent-id sentinels, which would all key to one slot", () => {
    expect(chipEligibleReelIds([reel(0), reel(1)])).toEqual(["1"]);
  });

  it("is empty, not undefined, when nothing is eligible", () => {
    expect(chipEligibleReelIds([])).toEqual([]);
    expect(chipEligibleReelIds([reel(1, { live_session_id: 5 })])).toEqual([]);
  });

  it("treats a reel that is both Live and shoppable as excluded once", () => {
    const ids = chipEligibleReelIds([
      reel(1, { live_session_id: 9, commerce: overlay(3) as never }),
      reel(2)
    ]);
    expect(ids).toEqual(["2"]);
  });
});

describe("the predicates, separately", () => {
  // Exported so a caller can say *why* a reel was excluded. Asserted apart from
  // the filter so that conflating the two conditions turns one of these red.
  it("reelIsLive is about broadcasting, not about commerce", () => {
    expect(reelIsLive(reel(1, { live_session_id: 4 }))).toBe(true);
    expect(reelIsLive(reel(1, { commerce: overlay(1) as never }))).toBe(false);
  });

  it("reelSellsItsOwnProduct is about commerce, not about broadcasting", () => {
    expect(reelSellsItsOwnProduct(reel(1, { commerce: overlay(1) as never }))).toBe(true);
    expect(reelSellsItsOwnProduct(reel(1, { live_session_id: 4 }))).toBe(false);
  });

  it("is what ReelsScreen actually feeds the binder", () => {
    // Everything above tests a function nothing is obliged to call. This module
    // only prevents anything if `ReelsScreen` uses it, and reverting to an
    // inline filter there would leave all eleven tests above green. Asserted
    // over source text for the same reason `commerceOverlayWiring` is: the
    // failure mode is an omission, and an omission raises nothing at runtime.
    const screen = fs.readFileSync(
      path.resolve(__dirname, "..", "..", "screens", "ReelsScreen.tsx"),
      "utf8"
    );
    expect(screen).toContain("chipEligibleReelIds");
    expect(screen).toMatch(/reelIds:\s*commerceReelIds/);
    expect(screen).toMatch(/commerceReelIds\s*=\s*useMemo\(\(\)\s*=>\s*chipEligibleReelIds\(reels\)/);
  });

  it("does not treat a malformed commerce payload as a product", () => {
    // A payload that fails the type guard must not silently cost the reel its
    // chip: the overlay will not render either, so the reel would end up with
    // no commerce surface at all.
    expect(reelSellsItsOwnProduct(reel(1, { commerce: {} as never }))).toBe(false);
    expect(reelSellsItsOwnProduct(reel(1, { commerce: null as never }))).toBe(false);
  });
});
