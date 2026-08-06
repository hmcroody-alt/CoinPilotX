/**
 * Pure parts of the commerce client: the per-seller cart grouping that mirrors
 * how checkout charges, and the server→mobile offer mapping.
 *
 * The network functions are deliberately untested here — they are one fetch
 * each, and the contract they depend on is smoke-tested against the route packs
 * server-side. What must not regress silently is the *shape* work: grouping
 * that splits by fulfillment, totals in minor units, and a mapper that always
 * yields the required `buyerName` / `itemTitle` even from an older backend
 * build that predates hydration.
 */

import { groupCartLines, offerFromServer, type CartLine } from "../marketplaceCommerce";

function line(overrides: Partial<CartLine> = {}): CartLine {
  return {
    line_id: 1,
    listing_id: 100,
    qty: 1,
    state: "available",
    price_snapshot_minor: 2500,
    price_now_minor: 2500,
    currency: "USD",
    title: "Desk lamp",
    cover_image_url: "",
    seller_user_id: 7,
    seller_name: "Ana",
    fulfillment: "shipping",
    added_at: "2026-08-01T00:00:00Z",
    ...overrides
  };
}

describe("groupCartLines", () => {
  it("groups one bucket per seller, in first-seen order", () => {
    const groups = groupCartLines([
      line({ line_id: 1, seller_user_id: 7, seller_name: "Ana" }),
      line({ line_id: 2, seller_user_id: 9, seller_name: "Bo" }),
      line({ line_id: 3, seller_user_id: 7, seller_name: "Ana" })
    ]);
    expect(groups.map((g) => g.sellerUserId)).toEqual([7, 9]);
    expect(groups[0].sellerName).toBe("Ana");
    expect(groups[0].fulfillments.flatMap((f) => f.lines).length).toBe(2);
  });

  it("splits fulfillments inside a seller group so digital and shipping never blur", () => {
    const groups = groupCartLines([
      line({ line_id: 1, fulfillment: "digital" }),
      line({ line_id: 2, fulfillment: "shipping" }),
      line({ line_id: 3, fulfillment: "digital" })
    ]);
    expect(groups).toHaveLength(1);
    const kinds = groups[0].fulfillments.map((f) => f.fulfillment);
    expect(kinds).toEqual(["digital", "shipping"]);
    expect(groups[0].fulfillments[0].lines.map((l) => l.line_id)).toEqual([1, 3]);
  });

  it("totals snapshot price × qty in minor units — the amount checkout will charge", () => {
    const groups = groupCartLines([
      // Snapshot 2500 but current price 9900: the total must use the snapshot,
      // because a changed price blocks checkout until the buyer confirms it —
      // it never silently reprices the basket.
      line({ line_id: 1, qty: 2, price_snapshot_minor: 2500, price_now_minor: 9900 }),
      line({ line_id: 2, qty: 1, price_snapshot_minor: 1000 })
    ]);
    expect(groups[0].totalMinor).toBe(6000);
    expect(groups[0].currency).toBe("USD");
  });

  it("returns no groups for an empty cart", () => {
    expect(groupCartLines([])).toEqual([]);
  });
});

describe("offerFromServer", () => {
  const row = {
    id: 42,
    listing_id: 555,
    amount_minor: 9500,
    list_price_minor: 12000,
    currency: "USD",
    direction: "buyer_to_seller" as const,
    state: "open" as const,
    counter_of: null,
    note: "Would you take 95?",
    created_at: "2026-08-01T10:00:00Z",
    updated_at: "2026-08-01T10:00:00Z",
    item_title: "Oak dining table",
    item_thumbnail_url: "https://cdn.example/x.jpg",
    buyer_name: "Dana"
  };

  it("maps ids to strings, hydrated fields to their camelCase names, note to message", () => {
    const offer = offerFromServer(row);
    expect(offer.id).toBe("42");
    expect(offer.listingId).toBe("555");
    expect(offer.amountMinor).toBe(9500);
    expect(offer.listPriceMinor).toBe(12000);
    expect(offer.state).toBe("open");
    expect(offer.buyerName).toBe("Dana");
    expect(offer.itemTitle).toBe("Oak dining table");
    expect(offer.itemThumbnailUrl).toBe("https://cdn.example/x.jpg");
    expect(offer.message).toBe("Would you take 95?");
    expect(offer.counterOf).toBeUndefined();
    expect(offer.createdAt).toBe(Date.parse(row.created_at));
  });

  it("keeps the card renderable against a backend build that predates hydration", () => {
    const bare = offerFromServer({
      ...row,
      item_title: undefined,
      item_thumbnail_url: undefined,
      buyer_name: undefined,
      note: "",
      counter_of: 41
    });
    expect(bare.buyerName).toBe("PulseSoc buyer");
    expect(bare.itemTitle).toBe("Marketplace item");
    expect(bare.itemThumbnailUrl).toBeUndefined();
    expect(bare.message).toBeUndefined();
    expect(bare.counterOf).toBe("41");
  });
});
