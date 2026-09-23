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
    // One product per strip, so "three placements" means "three rows" and the
    // arithmetic under test is the cadence rather than the window width.
    const out = injectCommerceRows(postRows(40), placements(3), { maxRows: 3, productsPerRow: 1 });
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
    const out = injectCommerceRows(postRows(40), placements(4), {
      leadIn: 0,
      interval: 0,
      maxRows: 2,
      productsPerRow: 1
    });
    expect(commerceIndexes(out)).toHaveLength(2);
  });

  it("treats a strip width of zero as one rather than emitting empty strips", () => {
    // The same defensive floor the cadence gets. A width of 0 would cut every
    // window as `slice(0, 0)` and spend both slots on nothing.
    const out = injectCommerceRows(postRows(40), placements(4), { maxRows: 2, productsPerRow: 0 });
    expect(commerceIndexes(out)).toHaveLength(2);
  });
});

describe("injectCommerceRows — the strip", () => {
  it("puts several products in one row rather than one product in several rows", () => {
    // §2. The alternative spends the page's whole commerce budget — two rows —
    // on two products, which is the thing a rail exists to avoid.
    const out = injectCommerceRows(postRows(40), placements(4), { maxRows: 1 });
    const [index] = commerceIndexes(out);
    const row = out[index];
    expect(row.type === "commerce" && row.placements.map((p) => p.placementId)).toEqual([
      "p1",
      "p2",
      "p3",
      "p4"
    ]);
  });

  it("gives each slot its own window of the ranked list", () => {
    const out = injectCommerceRows(postRows(60), placements(8), { maxRows: 2, productsPerRow: 3 });
    const strips = commerceIndexes(out).map((index) => {
      const row = out[index];
      return row.type === "commerce" ? row.placements.map((p) => p.placementId) : [];
    });
    // Windows are cut by position, not refilled from the tail: slot 1 gets the
    // *next* three, and p7/p8 are simply not shown on this page.
    expect(strips).toEqual([
      ["p1", "p2", "p3"],
      ["p4", "p5", "p6"]
    ]);
  });

  it("draws a short strip rather than padding it out", () => {
    // A thin catalogue is the common case in production today. One product with
    // a heading and a "See all" is correct, not degraded — §9's doorway.
    const out = injectCommerceRows(postRows(40), placements(1), { maxRows: 2 });
    const indexes = commerceIndexes(out);
    expect(indexes).toHaveLength(1);
    const row = out[indexes[0]];
    expect(row.type === "commerce" && row.placements).toHaveLength(1);
  });

  it("emits no row at all for a slot whose window is empty", () => {
    // Four products, four per row: slot 1's window is `slice(4, 8)` — nothing.
    // The row must not appear as a heading and a "See all" over no products.
    const out = injectCommerceRows(postRows(60), placements(4), { maxRows: 2 });
    expect(commerceIndexes(out)).toHaveLength(1);
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
    const out = injectCommerceRows(rows, placements(4), { maxRows: 4, productsPerRow: 1 });
    expect(commerceIndexes(out).length).toBeGreaterThan(0);
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
    // A position lost to adjacency does not spend a slot, so slot 0's *own*
    // window is what lands — the strip still leads with p1 rather than starting
    // at p2 as it would if the blocked position had consumed the slot.
    const row = out[indexes[0]];
    expect(row.type === "commerce" && row.slot).toBe(0);
    expect(row.type === "commerce" && row.placements[0].placementId).toBe("p1");
  });

  it("4. is never the last row", () => {
    // Exactly enough posts that the cadence wants a unit at the very end.
    for (let count = COMMERCE_LEAD_IN; count <= COMMERCE_LEAD_IN + COMMERCE_INTERVAL * 2; count += 1) {
      const out = injectCommerceRows(postRows(count), placements(3), {
        maxRows: 3,
        productsPerRow: 1
      });
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
    const row = out[index];
    expect(row.type === "commerce" && row.placements.map((p) => p.placementId)).toEqual(["p2"]);
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

  it("does not second-guess the server on seller diversity", () => {
    // This asserts the *removal* of a client-side rule, so it is worth saying
    // why out loud. `router._SELLER_CAPS` already caps the feed at two products
    // per seller, alongside category caps and per-product cooldowns, all
    // computed against exposure data the client does not have. A second cap here
    // could only ever discard a product the server had just decided was good for
    // this feed — shortening the strip for no benefit the shopper can perceive.
    const sameSeller = [
      placement({ placementId: "p1", product: { listingId: 1, sellerUserId: 99 } as never }),
      placement({ placementId: "p2", product: { listingId: 2, sellerUserId: 99 } as never })
    ];
    const out = injectCommerceRows(postRows(60), sameSeller, { maxRows: 1 });
    const [index] = commerceIndexes(out);
    const row = out[index];
    expect(row.type === "commerce" && row.placements.map((p) => p.placementId)).toEqual(["p1", "p2"]);
  });

  it("shows one tile per listing even when the server repeats it", () => {
    // Dedup that *does* stay client-side, because the same product twice in one
    // strip is a rendering defect rather than a policy question.
    const duplicated = [
      placement({ placementId: "p1", product: { listingId: 5, sellerUserId: 1 } as never }),
      placement({ placementId: "p2", product: { listingId: 5, sellerUserId: 2 } as never })
    ];
    const out = injectCommerceRows(postRows(60), duplicated, { maxRows: 2 });
    const indexes = commerceIndexes(out);
    expect(indexes).toHaveLength(1);
    const row = out[indexes[0]];
    expect(row.type === "commerce" && row.placements).toHaveLength(1);
  });
});

/** Every strip in an output, as lists of placement ids. */
function stripsOf(rows: readonly { type: string }[]): string[][] {
  return commerceIndexes(rows).map((index) => {
    const row = rows[index] as { type: string; placements?: CommercePlacement[] };
    return (row.placements || []).map((p) => p.placementId);
  });
}

describe("injectCommerceRows — a dismissed product is never replaced", () => {
  it("shortens the strip it was in rather than refilling from the next window", () => {
    // The rule the whole window design exists for. p5 belongs to slot 1 and must
    // stay there: sliding it forward would put a product the shopper has never
    // seen exactly where the one they just hid used to be, a frame later.
    const all = placements(8);
    const before = injectCommerceRows(postRows(60), all, { maxRows: 2, productsPerRow: 4 });
    expect(stripsOf(before)).toEqual([
      ["p1", "p2", "p3", "p4"],
      ["p5", "p6", "p7", "p8"]
    ]);

    const after = injectCommerceRows(postRows(60), all, {
      maxRows: 2,
      productsPerRow: 4,
      dismissedPlacementIds: new Set(["p2"])
    });
    expect(stripsOf(after)).toEqual([
      ["p1", "p3", "p4"],
      ["p5", "p6", "p7", "p8"]
    ]);
  });

  it("does not move the rows that follow it", () => {
    const all = placements(8);
    const before = injectCommerceRows(postRows(60), all, { maxRows: 2, productsPerRow: 4 });
    const after = injectCommerceRows(postRows(60), all, {
      maxRows: 2,
      productsPerRow: 4,
      dismissedPlacementIds: new Set(["p2"])
    });
    // Both strips still follow the same posts. A strip getting shorter is a
    // change inside one row; it must not reflow the feed around it.
    expect(commerceIndexes(after).map((i) => postsAhead(after, i))).toEqual(
      commerceIndexes(before).map((i) => postsAhead(before, i))
    );
  });

  it("emits no row when every product in a window is hidden", () => {
    // §15: the strip collapses fully. Not a heading and a "See all" over an
    // empty rail, and not the next window sliding up to take the space.
    const all = placements(8);
    const after = injectCommerceRows(postRows(60), all, {
      maxRows: 2,
      productsPerRow: 4,
      dismissedPlacementIds: new Set(["p1", "p2", "p3", "p4"])
    });
    expect(stripsOf(after)).toEqual([["p5", "p6", "p7", "p8"]]);

    // And slot 1's row stays where slot 1's row was — it did not inherit slot
    // 0's position along with its survival.
    const before = injectCommerceRows(postRows(60), all, { maxRows: 2, productsPerRow: 4 });
    expect(postsAhead(after, commerceIndexes(after)[0])).toBe(
      postsAhead(before, commerceIndexes(before)[1])
    );
  });

  it("spends the slot even when its whole window was dismissed", () => {
    // The slot counter counts offers, not rows. If it counted rows, hiding a
    // whole strip would hand its window to the next eligible position and the
    // shopper would watch the thing they just dismissed reappear further down.
    const all = placements(8);
    const out = injectCommerceRows(postRows(200), all, {
      maxRows: 2,
      productsPerRow: 4,
      dismissedPlacementIds: new Set(["p1", "p2", "p3", "p4"])
    });
    expect(stripsOf(out)).toEqual([["p5", "p6", "p7", "p8"]]);
  });

  it("suppresses every product from a seller the user told us to stop recommending", () => {
    const all = [
      placement({ placementId: "p1", product: { listingId: 1, sellerUserId: 42 } as never }),
      placement({ placementId: "p2", product: { listingId: 2, sellerUserId: 7 } as never }),
      placement({ placementId: "p3", product: { listingId: 3, sellerUserId: 42 } as never })
    ];
    const out = injectCommerceRows(postRows(60), all, {
      maxRows: 1,
      dismissedSellerIds: new Set([42])
    });
    // Both of seller 42's products go, from inside the same strip — "stop
    // recommending this seller" is about the seller, not about one card.
    expect(stripsOf(out)).toEqual([["p2"]]);
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
