/**
 * The commerce placement rules, asserted as arithmetic.
 *
 * Same argument as `discovery/__tests__/discoveryRows.test.ts`: the engine is a
 * pure list transform, so the rules are testable as what they are — a cadence,
 * a cap, an adjacency constraint and a dismissal policy — rather than by
 * rendering Home and counting cards, which passes for the wrong reason the
 * moment a card fails to mount.
 *
 * Each block below is one way the feed gets worse, and none of them is visible
 * in a diff:
 *
 *   - a product card overlaying or splitting a post;
 *   - a product card directly under an advert (feed as catalogue);
 *   - the feed ending on a product card, which reads as "we ran out of posts,
 *     here is something to buy";
 *   - hiding a card and watching the next-best product appear in its hole.
 */
import type { FeedRow } from "../../feed/injectAds";
import type { CommercePlacement } from "../../api/commerceDiscovery";
import type { HomeRow } from "../../discovery/discoveryRows";
import {
  COMMERCE_INTERVAL,
  COMMERCE_LEAD_IN,
  commerceRowKey,
  injectCommerceRows
} from "../commerceRows";

type Post = { id: number };

/** `n` organic post rows, in the shape `injectAds` emits. */
function postRows(n: number): FeedRow<Post>[] {
  return Array.from({ length: n }, (_, i) => ({
    type: "post" as const,
    key: `post:${i + 1}`,
    post: { id: i + 1 }
  }));
}

function placement(overrides: Partial<CommercePlacement> & { placementId: string }): CommercePlacement {
  const listingId = Number(overrides.placementId.replace(/\D/g, "")) || 1;
  return {
    impressionToken: `tok-${overrides.placementId}`,
    surface: "feed",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "popular",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    ...overrides,
    product: {
      listingId,
      title: `Product ${listingId}`,
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/x.jpg",
      sellerUserId: listingId * 10,
      sellerStoreName: `Store ${listingId}`,
      category: "shoes",
      rating: 4.8,
      ratingCount: 120,
      ...(overrides.product || {})
    }
  };
}

function placements(n: number): CommercePlacement[] {
  return Array.from({ length: n }, (_, i) => placement({ placementId: `p${i + 1}` }));
}

function typesOf(rows: readonly { type: string }[]): string[] {
  return rows.map((row) => row.type);
}

function commerceIndexes(rows: readonly { type: string }[]): number[] {
  return rows.flatMap((row, index) => (row.type === "commerce" ? [index] : []));
}

/**
 * How many posts a card sits behind — the only way to compare a card's position
 * across two different outputs.
 *
 * Raw output indexes cannot do this job. Dismissing a card removes a row from
 * the array, so every later index shifts up by one *whether or not the cards
 * moved relative to the feed*, and an assertion on indexes reports that shift as
 * if it were the compaction it was written to catch. Counting the posts in front
 * of a card is immune to it: the card either still follows the same post or it
 * does not.
 */
function postsAhead(rows: readonly { type: string }[], index: number): number {
  return rows.slice(0, index).filter((row) => row.type === "post").length;
}

describe("injectCommerceRows — the off switch", () => {
  it("returns the same rows when there are no placements", () => {
    const rows = postRows(40);
    expect(injectCommerceRows(rows, [])).toEqual(rows);
  });

  it("returns the same rows when the cap is zero", () => {
    const rows = postRows(40);
    expect(injectCommerceRows(rows, placements(4), { maxRows: 0 })).toEqual(rows);
  });

  it("returns the same rows when every placement is structurally unusable", () => {
    const rows = postRows(40);
    const broken = [
      placement({ placementId: "p1", impressionToken: "" }),
      placement({ placementId: "", impressionToken: "tok" }),
      placement({ placementId: "p3", product: { listingId: 0 } as never })
    ];
    expect(injectCommerceRows(rows, broken)).toEqual(rows);
  });

  // The whole rollback story in one assertion: a build with the engine switched
  // off server-side produces the feed that shipped, not a feed that merely
  // looks like it.
  it("preserves row identity, not just row equality, when inert", () => {
    const rows = postRows(12);
    const out = injectCommerceRows(rows, []);
    out.forEach((row, index) => expect(row).toBe(rows[index]));
  });
});

describe("injectCommerceRows — cadence", () => {
  it("places nothing before the lead-in is satisfied", () => {
    const out = injectCommerceRows(postRows(COMMERCE_LEAD_IN - 1), placements(2));
    expect(commerceIndexes(out)).toEqual([]);
  });

  it("places the first unit after `leadIn` organic posts", () => {
    const out = injectCommerceRows(postRows(30), placements(2), { maxRows: 1 });
    const [first] = commerceIndexes(out);
    // `leadIn` posts precede it, so it sits at index `leadIn` in a list of
    // nothing but posts.
    expect(first).toBe(COMMERCE_LEAD_IN);
  });

  it("spaces later units by `interval` organic posts", () => {
    const out = injectCommerceRows(postRows(40), placements(3), { maxRows: 3 });
    const indexes = commerceIndexes(out);
    expect(indexes.length).toBe(3);
    // Each commerce row shifts subsequent indexes by one, so the raw gap
    // between them is the interval plus the row that was inserted.
    expect(indexes[1] - indexes[0]).toBe(COMMERCE_INTERVAL + 1);
    expect(indexes[2] - indexes[1]).toBe(COMMERCE_INTERVAL + 1);
  });

  it("honours the per-page cap", () => {
    const out = injectCommerceRows(postRows(200), placements(20), { maxRows: 2 });
    expect(commerceIndexes(out)).toHaveLength(2);
  });

  it("treats a cadence of zero as one rather than dividing by it", () => {
    const out = injectCommerceRows(postRows(40), placements(4), { leadIn: 0, interval: 0, maxRows: 2 });
    expect(commerceIndexes(out)).toHaveLength(2);
  });
});

describe("injectCommerceRows — the four placement invariants", () => {
  it("1. emits commerce as its own row, never a property of a post", () => {
    const out = injectCommerceRows(postRows(40), placements(2));
    for (const row of out) {
      if (row.type !== "post") continue;
      // An overlay would have to travel as a field on the post row. There is no
      // such field, and this is the assertion that says so out loud.
      expect(Object.keys(row).sort()).toEqual(["key", "post", "type"]);
    }
  });

  it("2. never splits a post from the ad row that belongs to it", () => {
    // One ad attached to every third post, the shape `injectAds` produces.
    const rows: HomeRow<Post>[] = [];
    for (let i = 1; i <= 40; i += 1) {
      rows.push({ type: "post", key: `post:${i}`, post: { id: i } });
      if (i % 3 === 0) {
        rows.push({ type: "ad", key: `ad:${i}`, ad: { creativeId: i } as never });
      }
    }
    const out = injectCommerceRows(rows, placements(4), { maxRows: 4 });
    for (const index of commerceIndexes(out)) {
      // If a commerce row had landed between a post and its ad, the row after
      // it would be that orphaned ad.
      expect(out[index + 1]?.type).not.toBe("ad");
    }
  });

  it("3. never sits directly after another non-post row", () => {
    const rows: HomeRow<Post>[] = [];
    for (let i = 1; i <= 60; i += 1) {
      rows.push({ type: "post", key: `post:${i}`, post: { id: i } });
      // An ad on every post: every candidate position is preceded by an ad, so
      // a module that shifted instead of skipping would still place something.
      rows.push({ type: "ad", key: `ad:${i}`, ad: { creativeId: i } as never });
    }
    const out = injectCommerceRows(rows, placements(4), { maxRows: 4 });
    expect(commerceIndexes(out)).toEqual([]);
    expect(typesOf(out)).toEqual(typesOf(rows));
  });

  it("3b. offers the placement again at the next eligible position", () => {
    // Ads only on the posts that would otherwise carry a commerce slot, so the
    // first candidate is blocked by adjacency and the next is not.
    const rows: HomeRow<Post>[] = [];
    for (let i = 1; i <= 40; i += 1) {
      rows.push({ type: "post", key: `post:${i}`, post: { id: i } });
      if (i === COMMERCE_LEAD_IN) rows.push({ type: "ad", key: `ad:${i}`, ad: { creativeId: i } as never });
    }
    const out = injectCommerceRows(rows, placements(2), { maxRows: 1 });
    const indexes = commerceIndexes(out);
    expect(indexes).toHaveLength(1);
    // A position lost to adjacency does not spend a slot, so the *first*
    // placement is what lands — not the second.
    expect(out[indexes[0]]).toMatchObject({ slot: 0, placement: { placementId: "p1" } });
  });

  it("4. is never the last row", () => {
    // Exactly enough posts that the cadence wants a unit at the very end.
    for (let count = COMMERCE_LEAD_IN; count <= COMMERCE_LEAD_IN + COMMERCE_INTERVAL * 2; count += 1) {
      const out = injectCommerceRows(postRows(count), placements(3), { maxRows: 3 });
      expect(out[out.length - 1]?.type).toBe("post");
    }
  });
});

describe("injectCommerceRows — eligibility", () => {
  it("drops an expired placement", () => {
    const now = Date.parse("2026-01-01T00:00:00Z");
    const out = injectCommerceRows(
      postRows(40),
      [
        placement({ placementId: "p1", expiresAt: "2025-12-31T23:59:59Z" }),
        placement({ placementId: "p2", expiresAt: "2026-01-01T01:00:00Z" })
      ],
      { maxRows: 1, now }
    );
    const [index] = commerceIndexes(out);
    expect(out[index]).toMatchObject({ placement: { placementId: "p2" } });
  });

  it("keeps a placement whose expiry is unparseable rather than silently dropping it", () => {
    // An unparseable expiry is a server-side formatting bug. Dropping the card
    // would turn it into an invisible, unreported loss of inventory; the server
    // still rejects the token if it really has expired.
    const out = injectCommerceRows(postRows(40), [placement({ placementId: "p1", expiresAt: "soon" })], {
      maxRows: 1
    });
    expect(commerceIndexes(out)).toHaveLength(1);
  });

  it("shows one card per seller per page", () => {
    const sameSeller = [
      placement({ placementId: "p1", product: { listingId: 1, sellerUserId: 99 } as never }),
      placement({ placementId: "p2", product: { listingId: 2, sellerUserId: 99 } as never }),
      placement({ placementId: "p3", product: { listingId: 3, sellerUserId: 7 } as never })
    ];
    const out = injectCommerceRows(postRows(60), sameSeller, { maxRows: 3 });
    const sellers = commerceIndexes(out).map((index) => {
      const row = out[index];
      return row.type === "commerce" ? row.placement.product.sellerUserId : 0;
    });
    expect(new Set(sellers).size).toBe(sellers.length);
  });

  it("shows one card per listing even when the server repeats it", () => {
    const duplicated = [
      placement({ placementId: "p1", product: { listingId: 5, sellerUserId: 1 } as never }),
      placement({ placementId: "p2", product: { listingId: 5, sellerUserId: 2 } as never })
    ];
    const out = injectCommerceRows(postRows(60), duplicated, { maxRows: 2 });
    expect(commerceIndexes(out)).toHaveLength(1);
  });
});

describe("injectCommerceRows — a dismissed slot stays empty", () => {
  it("does not pull the next product into the hole left by a hidden one", () => {
    const all = placements(3);
    const before = injectCommerceRows(postRows(60), all, { maxRows: 3 });
    const beforeIds = commerceIndexes(before).map((index) => {
      const row = before[index];
      return row.type === "commerce" ? row.placement.placementId : "";
    });
    expect(beforeIds).toEqual(["p1", "p2", "p3"]);

    const after = injectCommerceRows(postRows(60), all, {
      maxRows: 3,
      dismissedPlacementIds: new Set(["p1"])
    });
    const afterIds = commerceIndexes(after).map((index) => {
      const row = after[index];
      return row.type === "commerce" ? row.placement.placementId : "";
    });
    // p2 and p3 keep their own slots. If dismissal compacted the list, this
    // would read ["p2", "p3"] *and* p2 would have moved up to p1's position —
    // a replacement product appearing where the hidden one was, one frame later.
    expect(afterIds).toEqual(["p2", "p3"]);

    // So the check is that p2 still sits behind the same number of posts it did
    // before. Its array index legitimately moves up by one — p1's row is gone —
    // but the post it follows must not change, because that is what the user
    // sees as "something took its place".
    expect(postsAhead(after, commerceIndexes(after)[0])).toBe(
      postsAhead(before, commerceIndexes(before)[1])
    );
  });

  it("leaves the remaining rows at the positions they already had", () => {
    const all = placements(3);
    const before = injectCommerceRows(postRows(60), all, { maxRows: 3 });
    const after = injectCommerceRows(postRows(60), all, {
      maxRows: 3,
      dismissedPlacementIds: new Set(["p2"])
    });
    const [firstBefore] = commerceIndexes(before);
    const [firstAfter] = commerceIndexes(after);
    // Hiding the middle card must not make the feed jump under the thumb.
    expect(firstAfter).toBe(firstBefore);
  });

  it("suppresses every card from a seller the user told us to stop recommending", () => {
    const all = [
      placement({ placementId: "p1", product: { listingId: 1, sellerUserId: 42 } as never }),
      placement({ placementId: "p2", product: { listingId: 2, sellerUserId: 7 } as never })
    ];
    const out = injectCommerceRows(postRows(60), all, {
      maxRows: 2,
      dismissedSellerIds: new Set([42])
    });
    const ids = commerceIndexes(out).map((index) => {
      const row = out[index];
      return row.type === "commerce" ? row.placement.placementId : "";
    });
    expect(ids).toEqual(["p2"]);
  });
});

describe("commerceRowKey", () => {
  it("is stable, unique per slot, and readable in a crash log", () => {
    expect(commerceRowKey("abc", 0)).toBe("commerce:abc:0");
    expect(commerceRowKey("abc", 1)).not.toBe(commerceRowKey("abc", 0));
  });

  it("is what the emitted rows actually carry", () => {
    const out = injectCommerceRows(postRows(40), placements(1), { maxRows: 1 });
    const [index] = commerceIndexes(out);
    const row = out[index];
    expect(row.type === "commerce" && row.key).toBe(commerceRowKey("p1", 0));
  });
});

describe("injectCommerceRows — determinism", () => {
  it("returns identical rows for identical arguments", () => {
    const rows = postRows(60);
    const all = placements(4);
    const now = 1_700_000_000_000;
    const a = injectCommerceRows(rows, all, { maxRows: 3, now });
    const b = injectCommerceRows(rows, all, { maxRows: 3, now });
    expect(a).toEqual(b);
  });
});
