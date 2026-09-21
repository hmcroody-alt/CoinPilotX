/**
 * Which reel carries the chip, proved against ids rather than against indices.
 *
 * `bindReelCommerce` is the only part of the Reels placement that can be tested
 * without a renderer, a network or a clock, and it is also the part where the
 * expensive mistakes live. Two of them in particular:
 *
 *   - **Index-keyed binding.** `index % interval === 0` reads correctly and
 *     passes every test written against a static array. On a device the list
 *     mutates — refresh prepends, pagination appends, a blocked reel drops out
 *     of the middle — and every one of those shifts the indices after it, so the
 *     chip lands on a *different video* than the one it was scored against. The
 *     tests below deliberately mutate the list and assert the binding follows the
 *     id.
 *
 *   - **Refilling a dismissed slot.** Hiding a chip and immediately getting
 *     another product on the next reel is the behaviour that makes people stop
 *     trusting the hide button. A spent slot stays spent.
 */
import type { CommercePlacement } from "../../api/commerceDiscovery";
import { REELS_INTERVAL, REELS_LEAD_IN, REELS_MAX_CHIPS, bindReelCommerce } from "../reelSlots";

const NOW = Date.parse("2026-09-20T12:00:00Z");

/**
 * `product` is merged, not replaced.
 *
 * The obvious spelling — spreading `overrides` last over a literal that already
 * spread `overrides.product` — silently wins the second time and replaces the
 * whole product. A test meaning "same product, different seller" then gets a
 * product with no listing id and no title, which this module discards for a
 * completely unrelated reason. Both halves have to be spread at their own level.
 */
function placement(
  id: string,
  overrides: Partial<Omit<CommercePlacement, "product">> & {
    product?: Partial<CommercePlacement["product"]>;
  } = {}
): CommercePlacement {
  const { product: productOverrides, ...rest } = overrides;
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    surface: "reels",
    slot: 0,
    expiresAt: "",
    ...rest,
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId: 77,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120,
      ...(productOverrides || {})
    }
  };
}

/** Ids shaped like the real ones: opaque strings, not positions. */
function reelIds(count: number, prefix = "r"): string[] {
  return Array.from({ length: count }, (_, index) => `${prefix}${index + 1}`);
}

describe("bindReelCommerce — nothing is the normal answer", () => {
  it("places nothing when the engine returned nothing", () => {
    // The reels floor is the strictest in the system precisely so the server can
    // answer with an empty list. An empty list must render the Reels screen
    // exactly as it rendered before this feature existed.
    expect(bindReelCommerce(reelIds(30), [], { now: NOW }).size).toBe(0);
  });

  it("places nothing on a list shorter than the lead-in", () => {
    const bound = bindReelCommerce(reelIds(REELS_LEAD_IN), [placement("p1")], { now: NOW });
    expect(bound.size).toBe(0);
  });

  it.each([
    ["no placement id", { placementId: "" }],
    ["no impression token", { impressionToken: "" }],
    ["no listing id", { product: { listingId: 0 } }],
    ["no title", { product: { title: "" } }]
  ])("drops a placement with %s rather than drawing a broken chip", (_label, overrides) => {
    // Each case breaks exactly one field, with the rest of the placement intact,
    // so a pass means that field is genuinely the reason it was dropped.
    const bound = bindReelCommerce(reelIds(20), [placement("p1", overrides)], { now: NOW });
    expect(bound.size).toBe(0);
  });

  it("drops a placement whose serve window already closed", () => {
    const stale = placement("p1", { expiresAt: new Date(NOW - 1000).toISOString() });
    expect(bindReelCommerce(reelIds(20), [stale], { now: NOW }).size).toBe(0);
  });

  it("keeps a placement whose expiry is unparseable rather than discarding a good chip", () => {
    // Failing open here is the right direction: a malformed timestamp is a
    // server bug, and the cost of showing one extra chip is far below the cost
    // of a field-format change silently disabling the whole surface.
    const odd = placement("p1", { expiresAt: "not-a-date" });
    expect(bindReelCommerce(reelIds(20), [odd], { now: NOW }).size).toBe(1);
  });

  it("places nothing when the budget is zero", () => {
    const bound = bindReelCommerce(reelIds(40), [placement("p1")], { maxChips: 0, now: NOW });
    expect(bound.size).toBe(0);
  });
});

describe("bindReelCommerce — the budget is one", () => {
  it("never exceeds the server's chip cap even on a long list", () => {
    const bound = bindReelCommerce(reelIds(200), [placement("p1"), placement("p2"), placement("p3")], {
      now: NOW
    });
    expect(bound.size).toBe(REELS_MAX_CHIPS);
    expect(REELS_MAX_CHIPS).toBe(1);
  });

  it("honours a raised cap without stacking chips on adjacent reels", () => {
    // Reachable only when an operator raises the per-session budget. The point
    // of `interval` existing at all is that a budget of two must not put the
    // second chip on the very next reel.
    const bound = bindReelCommerce(reelIds(60), [placement("p1"), placement("p2")], {
      maxChips: 2,
      now: NOW
    });
    expect(bound.size).toBe(2);
    const positions = [...bound.keys()].map((id) => Number(id.slice(1)) - 1);
    expect(positions[1] - positions[0]).toBe(REELS_INTERVAL);
  });

  it("lands the first chip exactly at the lead-in, not before it", () => {
    const bound = bindReelCommerce(reelIds(40), [placement("p1")], { now: NOW });
    expect([...bound.keys()]).toEqual([`r${REELS_LEAD_IN + 1}`]);
  });

  it("follows an operator retune of the lead-in", () => {
    const bound = bindReelCommerce(reelIds(40), [placement("p1")], { leadIn: 9, now: NOW });
    expect([...bound.keys()]).toEqual(["r10"]);
  });
});

describe("bindReelCommerce — the binding follows the reel, not the slot", () => {
  const list = reelIds(20);
  const placements = [placement("p1")];

  it("binds to a reel id that survives a prepend", () => {
    const before = bindReelCommerce(list, placements, { now: NOW });
    const carrier = [...before.keys()][0];

    // Pull-to-refresh put two new reels on the front. An index-keyed binding
    // would now be two videos earlier than the one it was scored against.
    const after = bindReelCommerce(["new1", "new2", ...list], placements, { now: NOW });
    expect([...after.keys()][0]).not.toBe(carrier);
    // …and what it *is* bound to is still a real reel from the list, at the
    // lead-in position of the new list. The chip moved with the rhythm, not
    // with a stale index.
    expect([...after.keys()][0]).toBe(["new1", "new2", ...list][REELS_LEAD_IN]);
  });

  it("keeps the same carrier when pagination only appends", () => {
    const before = bindReelCommerce(list, placements, { now: NOW });
    const after = bindReelCommerce([...list, ...reelIds(20, "page2_")], placements, { now: NOW });
    expect([...after.keys()]).toEqual([...before.keys()]);
  });

  it("never re-binds a reel id the list repeats", () => {
    // Pagination boundaries do hand back a duplicate page. Without the seen-id
    // guard the duplicate occupies a second slot, and because a Map key can only
    // hold one value that slot *overwrites* the binding the reel already had —
    // the first placement is silently discarded after being counted against the
    // budget, and the reel shows a product it was never bound to.
    //
    // Three placements and a raised cap, so the overwrite has something to
    // overwrite with. Asserting on the ordered values rather than on `size` is
    // what makes this bite: the sizes are equal either way.
    const duplicated = [...list, ...list];
    const bound = bindReelCommerce(duplicated, [placement("p1"), placement("p2"), placement("p3")], {
      maxChips: 3,
      now: NOW
    });
    expect([...bound.values()].map((entry) => entry.placementId)).toEqual(["p1", "p2"]);
    expect([...bound.keys()]).toEqual(["r5", "r15"]);
  });

  it("ignores blank ids rather than binding a chip to nothing", () => {
    const withHoles = ["", "r1", "", "r2", "r3", "r4", "r5", "r6", "r7", "r8"];
    const bound = bindReelCommerce(withHoles, placements, { now: NOW });
    expect([...bound.keys()].every(Boolean)).toBe(true);
  });
});

describe("bindReelCommerce — a dismissed slot stays empty", () => {
  it("does not refill the slot with the next product", () => {
    // The tempting implementation filters dismissals out of the candidate list
    // before slotting, which promotes p2 into the slot p1 was going to take —
    // hide a chip, get another product one reel later. The slot is spent.
    const bound = bindReelCommerce(reelIds(40), [placement("p1"), placement("p2")], {
      dismissedPlacementIds: new Set(["p1"]),
      now: NOW
    });
    expect(bound.size).toBe(0);
  });

  it("does not refill the slot when the seller was hidden", () => {
    // The runner-up is from a *different* seller on purpose. With both products
    // owned by the muted seller this test would pass even if the module filtered
    // dismissals out and promoted the next candidate into the slot — the thing
    // it exists to forbid.
    const other = placement("p2", { product: { sellerUserId: 91 } });
    const bound = bindReelCommerce(reelIds(40), [placement("p1"), other], {
      dismissedSellerIds: new Set([77]),
      now: NOW
    });
    expect(bound.size).toBe(0);
  });

  it("leaves other sellers alone when one seller is hidden", () => {
    const other = placement("p1", { product: { sellerUserId: 91 } });
    const bound = bindReelCommerce(reelIds(40), [other], {
      dismissedSellerIds: new Set([77]),
      now: NOW
    });
    expect(bound.size).toBe(1);
  });

  it("treats a placement with no seller id as un-hideable by seller", () => {
    // A missing seller id must not be matched by `dismissedSellerIds.has(0)` or
    // by any other falsy coincidence — that would hide unrelated placements the
    // moment one seller was muted.
    const anonymous = placement("p1", { product: { sellerUserId: 0 } });
    const bound = bindReelCommerce(reelIds(40), [anonymous], {
      dismissedSellerIds: new Set([0]),
      now: NOW
    });
    expect(bound.size).toBe(1);
  });
});
