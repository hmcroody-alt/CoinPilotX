/**
 * Tests for the Store dashboard derivation layer.
 *
 * Everything under test is pure and takes `now` as an argument, so none of this
 * depends on the clock, the locale or the network. The three things worth
 * stating outright:
 *
 * 1. The `LOW_STOCK_THRESHOLD` boundary is pinned on both sides. It is the one
 *    number the banner, the tab count and the row LED all read, so an off-by-one
 *    here would make the screen disagree with itself.
 * 2. `deriveKpis` compares against the *same weekday* last week, and returns
 *    `null` rather than a percentage when there is no baseline. A store's first
 *    week must not report "+100%".
 * 3. `STORE_MOCK_DATA_GAPS` has its length asserted. If someone later invents a
 *    value for one of the unsourced fields and drops it from the list, this
 *    test fails and a reviewer has to justify the change.
 */

const mockListListings = jest.fn();
const mockListOrders = jest.fn();
const mockLoadCached = jest.fn();

jest.mock("../marketplace", () => ({
  listMarketplaceSellerListings: (...args: unknown[]) => mockListListings(...args),
  listMarketplaceSellerOrders: (...args: unknown[]) => mockListOrders(...args),
  loadCachedSellerStore: (...args: unknown[]) => mockLoadCached(...args)
}));

import {
  attentionCount,
  deriveAttention,
  deriveKpis,
  deriveRows,
  deriveStatus,
  deriveTabs,
  filterRows,
  listingHealth,
  loadStoreDashboard,
  LOW_STOCK_THRESHOLD,
  snapshotFrom,
  STORE_MOCK_DATA_GAPS,
  type StoreListingRow,
  type StoreTabKey
} from "../storeDashboard";
import type {
  MarketplaceListing,
  MarketplaceSellerOrder,
  SellerStoreSnapshot
} from "../marketplace";

beforeEach(() => {
  mockListListings.mockReset();
  mockListOrders.mockReset();
  mockLoadCached.mockReset();
});

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

const NOW = new Date(2026, 6, 15, 10, 30); // Wed 15 Jul 2026, local time.

/** Local-midday on the day `offset` days before NOW — safe from DST edges. */
function daysAgo(offset: number, hour = 12): string {
  return new Date(2026, 6, 15 - offset, hour, 0).toISOString();
}

function listing(over: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 1,
    listing_id: 1,
    title: "Bright Coffee Beans",
    price_label: "12.00",
    currency: "USD",
    quantity: 20,
    status: "active",
    approval_status: "approved",
    ...over
  } as MarketplaceListing;
}

function order(over: Partial<MarketplaceSellerOrder> = {}): MarketplaceSellerOrder {
  return {
    id: 1,
    item_type: "listing",
    item_id: 1,
    amount_cents: 1200,
    gross_amount_cents: 1200,
    currency: "USD",
    status: "paid",
    created_at: daysAgo(0),
    ...over
  } as MarketplaceSellerOrder;
}

function snapshot(over: Partial<SellerStoreSnapshot> = {}): SellerStoreSnapshot {
  return { listings: [], orders: [], ...over };
}

function rowsOf(listings: MarketplaceListing[], orders: MarketplaceSellerOrder[] = []) {
  return deriveRows(snapshot({ listings, orders }), NOW);
}

/* ------------------------------------------------------------------ *
 * Unsourced fields
 * ------------------------------------------------------------------ */

describe("STORE_MOCK_DATA_GAPS", () => {
  it("names every field the design asks for that has no backend source", () => {
    // Pinned deliberately. Faking one of these changes a number a reviewer reads.
    expect(STORE_MOCK_DATA_GAPS).toHaveLength(7);
    expect(STORE_MOCK_DATA_GAPS.map((gap) => gap.field)).toEqual([
      "Views · 7 days",
      "Seller rating",
      "On-time dispatch %",
      "Open orders — N ship today",
      "Listing rating and review count",
      "Store open / paused",
      "Stock tracked / not tracked"
    ]);
  });

  it("says what backend work each gap needs", () => {
    STORE_MOCK_DATA_GAPS.forEach((gap) => {
      expect(gap.needs.length).toBeGreaterThan(10);
    });
  });

  it("leaves the unsourced KPIs null rather than inventing them", () => {
    const kpis = deriveKpis(snapshot({ orders: [order()] }), NOW);
    expect(kpis.views7d).toBeNull();
    expect(kpis.viewsTrend).toBeNull();
    expect(kpis.sellerRating).toBeNull();
    expect(kpis.onTimeDispatch).toBeNull();
    expect(kpis.shippingToday).toBeNull();
  });

  it("leaves per-listing ratings null rather than inventing them", () => {
    const [row] = rowsOf([listing()]);
    expect(row.rating).toBeNull();
    expect(row.reviewCount).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * listingHealth
 * ------------------------------------------------------------------ */

describe("listingHealth", () => {
  it("treats the low-stock threshold as inclusive", () => {
    expect(listingHealth(listing({ quantity: LOW_STOCK_THRESHOLD }))).toBe("low_stock");
    expect(listingHealth(listing({ quantity: LOW_STOCK_THRESHOLD + 1 }))).toBe("in_stock");
  });

  it("marks zero and negative quantities out of stock", () => {
    expect(listingHealth(listing({ quantity: 0 }))).toBe("out_of_stock");
    expect(listingHealth(listing({ quantity: -3 }))).toBe("out_of_stock");
  });

  it("does not mark a listing without a quantity out of stock", () => {
    // A course or a service has no stock count. Reading an absent quantity as
    // zero would hide every digital listing in the store.
    expect(listingHealth(listing({ quantity: undefined }))).toBe("in_stock");
    expect(listingHealth(listing({ quantity: null as never }))).toBe("in_stock");
  });

  it("does not mark a stockless product type out of stock at quantity zero", () => {
    // `normalizeMarketplaceListing` runs `Number(item.quantity || 0)` before any
    // listing reaches here, so an absent quantity arrives as a real-looking 0.
    // `product_type` is the only signal that survives that.
    ["digital", "course", "service"].forEach((productType) => {
      expect(listingHealth(listing({ product_type: productType, quantity: 0 }))).toBe("in_stock");
      expect(listingHealth(listing({ product_type: productType, quantity: 2 }))).toBe("in_stock");
    });
  });

  it("still counts stock for a physical listing", () => {
    expect(listingHealth(listing({ product_type: "physical", quantity: 0 }))).toBe("out_of_stock");
    expect(listingHealth(listing({ product_type: "physical", quantity: 2 }))).toBe("low_stock");
    // An unset product type is the common case and must keep counting stock.
    expect(listingHealth(listing({ product_type: "", quantity: 0 }))).toBe("out_of_stock");
  });

  it("reports no stock figure for a stockless listing rather than zero", () => {
    expect(rowsOf([listing({ product_type: "course", quantity: 0 })])[0].quantity).toBeNull();
  });

  it("reads draft before anything else", () => {
    expect(listingHealth(listing({ status: "draft", quantity: 0 }))).toBe("draft");
  });

  it("treats paused, rejected, blocked and removed as hidden", () => {
    ["paused", "rejected", "blocked", "removed"].forEach((status) => {
      expect(listingHealth(listing({ status, quantity: 50 }))).toBe("hidden");
    });
  });

  it("falls back to approval_status when status is absent", () => {
    expect(listingHealth(listing({ status: undefined, approval_status: "rejected" }))).toBe(
      "hidden"
    );
  });
});

/* ------------------------------------------------------------------ *
 * KPIs
 * ------------------------------------------------------------------ */

describe("deriveKpis", () => {
  it("sums today's gross in minor units", () => {
    const kpis = deriveKpis(
      snapshot({
        orders: [
          order({ id: 1, gross_amount_cents: 1200 }),
          order({ id: 2, gross_amount_cents: 800 }),
          order({ id: 3, gross_amount_cents: 9900, created_at: daysAgo(1) })
        ]
      }),
      NOW
    );
    expect(kpis.salesTodayMinor).toBe(2000);
  });

  it("prefers gross over net when both are present", () => {
    const kpis = deriveKpis(
      snapshot({ orders: [order({ amount_cents: 1000, gross_amount_cents: 1200 })] }),
      NOW
    );
    expect(kpis.salesTodayMinor).toBe(1200);
  });

  it("compares against the same weekday last week", () => {
    const kpis = deriveKpis(
      snapshot({
        orders: [
          order({ id: 1, gross_amount_cents: 1200 }),
          order({ id: 2, gross_amount_cents: 1000, created_at: daysAgo(7) })
        ]
      }),
      NOW
    );
    expect(kpis.salesTrend).toBeCloseTo(0.2, 6);
  });

  it("reports a fall as a negative ratio", () => {
    const kpis = deriveKpis(
      snapshot({
        orders: [
          order({ id: 1, gross_amount_cents: 500 }),
          order({ id: 2, gross_amount_cents: 1000, created_at: daysAgo(7) })
        ]
      }),
      NOW
    );
    expect(kpis.salesTrend).toBeCloseTo(-0.5, 6);
  });

  it("returns no trend when there is no last-week baseline", () => {
    // A store's first week must not read "+100%".
    const kpis = deriveKpis(snapshot({ orders: [order({ gross_amount_cents: 1200 })] }), NOW);
    expect(kpis.salesTrend).toBeNull();
  });

  it("ignores yesterday when building the trend", () => {
    const kpis = deriveKpis(
      snapshot({
        orders: [
          order({ id: 1, gross_amount_cents: 1200 }),
          order({ id: 2, gross_amount_cents: 5000, created_at: daysAgo(1) })
        ]
      }),
      NOW
    );
    expect(kpis.salesTrend).toBeNull();
  });

  it("builds a seven-slot sparkline, oldest first", () => {
    const kpis = deriveKpis(
      snapshot({
        orders: [
          order({ id: 1, gross_amount_cents: 100, created_at: daysAgo(6) }),
          order({ id: 2, gross_amount_cents: 700, created_at: daysAgo(0) })
        ]
      }),
      NOW
    );
    expect(kpis.sparkline).toHaveLength(7);
    expect(kpis.sparkline[0]).toBe(100);
    expect(kpis.sparkline[6]).toBe(700);
  });

  it("excludes orders older than the sparkline window", () => {
    const kpis = deriveKpis(
      snapshot({ orders: [order({ gross_amount_cents: 9999, created_at: daysAgo(9) })] }),
      NOW
    );
    expect(kpis.sparkline).toEqual([0, 0, 0, 0, 0, 0, 0]);
  });

  it("counts unfulfilled orders as open and excludes settled ones", () => {
    const kpis = deriveKpis(
      snapshot({
        orders: [
          order({ id: 1, status: "pending" }),
          order({ id: 2, status: "paid" }),
          order({ id: 3, status: "processing" }),
          order({ id: 4, status: "completed" }),
          order({ id: 5, status: "cancelled" }),
          order({ id: 6, status: "refunded" }),
          order({ id: 7, status: "delivered" })
        ]
      }),
      NOW
    );
    expect(kpis.openOrders).toBe(3);
  });

  it("survives an unparseable timestamp instead of producing NaN", () => {
    const kpis = deriveKpis(
      snapshot({ orders: [order({ created_at: "not a date" as never })] }),
      NOW
    );
    expect(kpis.salesTodayMinor).toBe(0);
    expect(kpis.sparkline.every((value) => Number.isFinite(value))).toBe(true);
  });

  it("returns zeroes for an empty store rather than throwing", () => {
    const kpis = deriveKpis(snapshot(), NOW);
    expect(kpis.salesTodayMinor).toBe(0);
    expect(kpis.openOrders).toBe(0);
    expect(kpis.sparkline).toEqual([0, 0, 0, 0, 0, 0, 0]);
    expect(kpis.currency).toBe("USD");
  });

  it("takes the currency from the orders rather than hardcoding one", () => {
    const kpis = deriveKpis(snapshot({ orders: [order({ currency: "NGN" })] }), NOW);
    expect(kpis.currency).toBe("NGN");
  });
});

/* ------------------------------------------------------------------ *
 * Rows
 * ------------------------------------------------------------------ */

describe("deriveRows", () => {
  it("counts units sold in the trailing seven days per listing", () => {
    const rows = rowsOf(
      [listing({ id: 1, listing_id: 1 }), listing({ id: 2, listing_id: 2, title: "Mug" })],
      [
        order({ id: 1, item_id: 1, created_at: daysAgo(0) }),
        order({ id: 2, item_id: 1, created_at: daysAgo(6) }),
        order({ id: 3, item_id: 1, created_at: daysAgo(30) }),
        order({ id: 4, item_id: 2, created_at: daysAgo(2) })
      ]
    );
    expect(rows[0].unitsSold7d).toBe(2);
    expect(rows[1].unitsSold7d).toBe(1);
  });

  it("does not count cancelled or refunded orders as units sold", () => {
    const rows = rowsOf(
      [listing()],
      [
        order({ id: 1, item_id: 1, status: "cancelled" }),
        order({ id: 2, item_id: 1, status: "refunded" }),
        order({ id: 3, item_id: 1, status: "paid" })
      ]
    );
    expect(rows[0].unitsSold7d).toBe(1);
  });

  it("prefers listing_id over id when they differ", () => {
    const rows = rowsOf([listing({ id: 999, listing_id: 42 })], [order({ item_id: 42 })]);
    expect(rows[0].id).toBe(42);
    expect(rows[0].unitsSold7d).toBe(1);
  });

  it("falls back through the thumbnail sources", () => {
    expect(rowsOf([listing({ thumbnail_url: "a.jpg" })])[0].thumbnailUrl).toBe("a.jpg");
    expect(
      rowsOf([listing({ thumbnail_url: undefined, cover_image_url: "b.jpg" })])[0].thumbnailUrl
    ).toBe("b.jpg");
    expect(rowsOf([listing()])[0].thumbnailUrl).toBeNull();
  });

  it("never renders an empty title", () => {
    expect(rowsOf([listing({ title: "", short_description: "A short one" })])[0].title).toBe(
      "A short one"
    );
    expect(rowsOf([listing({ title: "", short_description: "" })])[0].title).toBe(
      "Untitled listing"
    );
  });

  it("reports a missing quantity as null rather than zero", () => {
    // Zero would render "0 in stock" on a listing that has no stock concept.
    expect(rowsOf([listing({ quantity: undefined })])[0].quantity).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Tabs
 * ------------------------------------------------------------------ */

describe("tabs", () => {
  const mixed = () =>
    rowsOf([
      listing({ id: 1, listing_id: 1, quantity: 20 }),
      listing({ id: 2, listing_id: 2, quantity: 2 }),
      listing({ id: 3, listing_id: 3, quantity: 0 }),
      listing({ id: 4, listing_id: 4, status: "draft" }),
      listing({ id: 5, listing_id: 5, status: "paused", quantity: 9 })
    ]);

  it("counts each tab", () => {
    const counts = Object.fromEntries(
      deriveTabs(mixed()).map((tab) => [tab.key, tab.count])
    ) as Record<StoreTabKey, number>;
    expect(counts).toEqual({ all: 5, active: 2, low: 1, out: 2, drafts: 1 });
  });

  it("flags only the two problem tabs for attention colouring", () => {
    const flagged = deriveTabs(mixed())
      .filter((tab) => tab.needsAttention)
      .map((tab) => tab.key);
    // "All" being large is good news; colouring it spends the reader's alarm.
    expect(flagged).toEqual(["low", "out"]);
  });

  it("does not flag a problem tab whose count is zero", () => {
    const healthy = rowsOf([listing({ quantity: 20 })]);
    expect(deriveTabs(healthy).every((tab) => !tab.needsAttention)).toBe(true);
  });

  it("filters rows the same way it counts them", () => {
    const rows = mixed();
    deriveTabs(rows).forEach((tab) => {
      expect(filterRows(rows, tab.key)).toHaveLength(tab.count);
    });
  });

  it("groups hidden listings with out-of-stock, since buyers can order neither", () => {
    const rows = rowsOf([listing({ status: "paused", quantity: 40 })]);
    expect(filterRows(rows, "out")).toHaveLength(1);
    expect(filterRows(rows, "active")).toHaveLength(0);
  });
});

/* ------------------------------------------------------------------ *
 * Attention banner
 * ------------------------------------------------------------------ */

describe("deriveAttention", () => {
  it("returns nothing when the store is healthy", () => {
    // No banner at all — a permanent banner teaches sellers to look past it.
    expect(deriveAttention(rowsOf([listing({ quantity: 20 })]))).toBeNull();
    expect(deriveAttention([])).toBeNull();
  });

  it("ranks out-of-stock above low stock", () => {
    const rows = rowsOf([
      listing({ id: 1, listing_id: 1, quantity: 0 }),
      listing({ id: 2, listing_id: 2, quantity: 1 }),
      listing({ id: 3, listing_id: 3, quantity: 2 })
    ]);
    expect(deriveAttention(rows)).toEqual({ count: 1, target: "out", kind: "out_of_stock" });
  });

  it("reports low stock when nothing is out", () => {
    const rows = rowsOf([
      listing({ id: 1, listing_id: 1, quantity: 1 }),
      listing({ id: 2, listing_id: 2, quantity: 2 })
    ]);
    expect(deriveAttention(rows)).toEqual({ count: 2, target: "low", kind: "low_stock" });
  });

  it("does not raise a banner for drafts or hidden listings", () => {
    const rows = rowsOf([
      listing({ id: 1, listing_id: 1, status: "draft" }),
      listing({ id: 2, listing_id: 2, status: "paused", quantity: 30 })
    ]);
    expect(deriveAttention(rows)).toBeNull();
  });

  it("counts every listing needing attention regardless of kind", () => {
    const rows = rowsOf([
      listing({ id: 1, listing_id: 1, quantity: 0 }),
      listing({ id: 2, listing_id: 2, quantity: 3 }),
      listing({ id: 3, listing_id: 3, quantity: 40 })
    ]);
    expect(attentionCount(rows)).toBe(2);
  });
});

/* ------------------------------------------------------------------ *
 * Status strip
 * ------------------------------------------------------------------ */

describe("deriveStatus", () => {
  it("treats an empty store as open, not paused", () => {
    // Empty is a different screen state, with its own invitation to add a listing.
    expect(deriveStatus([])).toEqual({ open: true });
  });

  it("is open while at least one listing is orderable", () => {
    const rows = rowsOf([
      listing({ id: 1, listing_id: 1, quantity: 0 }),
      listing({ id: 2, listing_id: 2, quantity: 1 })
    ]);
    expect(deriveStatus(rows)).toEqual({ open: true });
  });

  it("is paused when nothing can be ordered", () => {
    const rows = rowsOf([
      listing({ id: 1, listing_id: 1, quantity: 0 }),
      listing({ id: 2, listing_id: 2, status: "paused" }),
      listing({ id: 3, listing_id: 3, status: "draft" })
    ]);
    expect(deriveStatus(rows)).toEqual({ open: false });
  });
});

/* ------------------------------------------------------------------ *
 * Loading
 * ------------------------------------------------------------------ */

describe("loadStoreDashboard", () => {
  it("returns both sections when both calls succeed", async () => {
    mockListListings.mockResolvedValue({ items: [listing()] });
    mockListOrders.mockResolvedValue({ orders: [order()] });

    const result = await loadStoreDashboard();
    expect(result.listings).toEqual({ status: "ok", data: [listing()] });
    expect(result.orders.status).toBe("ok");
    expect(result.offline).toBe(false);
  });

  it("keeps listings when only orders fail", async () => {
    // The point of the parallel loader: a failed half must be distinguishable
    // from an empty one, so the section can offer its own retry.
    mockListListings.mockResolvedValue({ items: [listing()] });
    mockListOrders.mockRejectedValue(new Error("500"));

    const result = await loadStoreDashboard();
    expect(result.listings.status).toBe("ok");
    expect(result.orders.status).toBe("error");
    if (result.orders.status === "error") {
      expect(result.orders.message).toMatch(/orders/i);
    }
    expect(mockLoadCached).not.toHaveBeenCalled();
  });

  it("keeps orders when only listings fail", async () => {
    mockListListings.mockRejectedValue(new Error("500"));
    mockListOrders.mockResolvedValue({ orders: [order()] });

    const result = await loadStoreDashboard();
    expect(result.listings.status).toBe("error");
    expect(result.orders.status).toBe("ok");
  });

  it("names what failed instead of saying something went wrong", async () => {
    mockListListings.mockRejectedValue(new Error("500"));
    mockListOrders.mockRejectedValue(new Error("500"));
    mockLoadCached.mockResolvedValue(null);

    const result = await loadStoreDashboard();
    [result.listings, result.orders].forEach((section) => {
      if (section.status === "error") {
        expect(section.message.toLowerCase()).not.toContain("something went wrong");
      }
    });
  });

  it("falls back to cache when both calls fail", async () => {
    mockListListings.mockRejectedValue(new Error("offline"));
    mockListOrders.mockRejectedValue(new Error("offline"));
    mockLoadCached.mockResolvedValue({
      listings: [listing()],
      orders: [],
      cached_at: "2026-07-15T08:00:00.000Z"
    });

    const result = await loadStoreDashboard();
    expect(result.offline).toBe(true);
    expect(result.cachedAt).toBe("2026-07-15T08:00:00.000Z");
    expect(result.listings.status).toBe("ok");
  });

  it("reports errors rather than an empty store when the cache is empty too", async () => {
    mockListListings.mockRejectedValue(new Error("offline"));
    mockListOrders.mockRejectedValue(new Error("offline"));
    mockLoadCached.mockResolvedValue({ listings: [], orders: [] });

    const result = await loadStoreDashboard();
    expect(result.offline).toBe(false);
    expect(result.listings.status).toBe("error");
    expect(result.orders.status).toBe("error");
  });

  it("survives a cache read that throws", async () => {
    mockListListings.mockRejectedValue(new Error("offline"));
    mockListOrders.mockRejectedValue(new Error("offline"));
    mockLoadCached.mockRejectedValue(new Error("corrupt cache"));

    await expect(loadStoreDashboard()).resolves.toMatchObject({ offline: false });
  });

  it("tolerates a response with no items or orders key", async () => {
    mockListListings.mockResolvedValue({});
    mockListOrders.mockResolvedValue({});

    const result = await loadStoreDashboard();
    expect(snapshotFrom(result)).toEqual({ listings: [], orders: [], cached_at: undefined });
  });
});

describe("snapshotFrom", () => {
  it("drops a failed section rather than propagating the error into the KPIs", async () => {
    mockListListings.mockResolvedValue({ items: [listing()] });
    mockListOrders.mockRejectedValue(new Error("500"));

    const result = await loadStoreDashboard();
    const snap = snapshotFrom(result);
    expect(snap.listings).toHaveLength(1);
    expect(snap.orders).toEqual([]);
    // Rows still render; only the order-derived figures go to zero.
    const rows: StoreListingRow[] = deriveRows(snap, NOW);
    expect(rows[0].unitsSold7d).toBe(0);
  });
});
