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
  listingAwaitsReview,
  listingHealth,
  loadStoreDashboard,
  LOW_STOCK_THRESHOLD,
  snapshotFrom,
  STORE_MOCK_DATA_GAPS,
  STORE_READINESS_FLAG,
  storeReadiness,
  storeReadinessEnabled,
  type StoreListingRow,
  type StoreReadiness,
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
    expect(STORE_MOCK_DATA_GAPS).toHaveLength(8);
    expect(STORE_MOCK_DATA_GAPS.map((gap) => gap.field)).toEqual([
      "Views · 7 days",
      "Seller rating",
      "On-time dispatch %",
      "Open orders — N ship today",
      "Listing rating and review count",
      "Store open / paused",
      "Stock tracked / not tracked",
      // Added with the readiness ladder: five rungs ship, two cannot be sourced.
      "Store restricted / suspended"
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

/* ------------------------------------------------------------------ *
 * Readiness ladder
 * ------------------------------------------------------------------ */

/**
 * One real store per rung, derived rather than declared.
 *
 * The cross-rung assertions below could have read the copy table directly, but
 * then they would prove the table has five distinct entries and nothing about
 * whether a store can reach them. These go through `storeReadiness` from
 * listings, so a rung that has become unreachable fails the `toBe(rung)` check
 * here rather than passing quietly.
 */
const READINESS_FIXTURES: Record<StoreReadiness, ReturnType<typeof storeReadiness>> = (() => {
  const cases: Record<StoreReadiness, MarketplaceListing[]> = {
    not_set_up: [],
    incomplete: [listing({ status: "draft" })],
    pending_review: [listing({ status: "active", approval_status: "pending" })],
    live: [listing({ status: "active", quantity: 40 })],
    paused: [listing({ status: "active", quantity: 0 })]
  };
  const built = {} as Record<StoreReadiness, ReturnType<typeof storeReadiness>>;
  for (const [rung, listings] of Object.entries(cases) as [StoreReadiness, MarketplaceListing[]][]) {
    const state = storeReadiness({ listings, rows: rowsOf(listings) });
    // The fixture is only useful if it actually lands on the rung it claims.
    expect(state.readiness).toBe(rung);
    built[rung] = state;
  }
  return built;
})();

/**
 * The regression these tests exist for is one sentence: a seller who had never
 * listed anything opened the Store screen and read "Open for orders". The
 * screen was not missing a source — `deriveStatus` asked a yes/no question of
 * data that has at least five answers, and an empty catalogue fell through to
 * the cheerful one.
 *
 * So the assertions below are mostly about the rungs *below* live. The one
 * worth reading twice is the sweep at the end: it walks every rung the type
 * allows rather than the ones anyone thought to write a case for, because
 * falling through to a default is precisely how the original defect looked.
 */
describe("storeReadiness", () => {
  const ALL_RUNGS: StoreReadiness[] = [
    "not_set_up",
    "incomplete",
    "pending_review",
    "live",
    "paused"
  ];

  /** Build the readiness state the way the screen does: raw listings plus rows. */
  function readinessOf(listings: MarketplaceListing[]) {
    return storeReadiness({ listings, rows: rowsOf(listings) });
  }

  it('is off unless the build opts in, and accepts every spelling of "on"', () => {
    // The accepted spellings are the shared set in core/envFlag.ts, not this
    // module's own idea of one. This flag shipped taking the literal "1" alone
    // while flags on adjacent screens also took "true" — so a build that set it
    // to "true" got a silent no-op. Both work now; unset is still off.
    const original = process.env[STORE_READINESS_FLAG];
    try {
      for (const value of ["", " ", "0", "false", "off", "no", "2"]) {
        process.env[STORE_READINESS_FLAG] = value;
        expect(storeReadinessEnabled()).toBe(false);
      }
      for (const value of ["1", "true", "on", "yes", " TRUE ", "Yes"]) {
        process.env[STORE_READINESS_FLAG] = value;
        expect(storeReadinessEnabled()).toBe(true);
      }
      delete process.env[STORE_READINESS_FLAG];
      expect(storeReadinessEnabled()).toBe(false);
    } finally {
      if (original === undefined) delete process.env[STORE_READINESS_FLAG];
      else process.env[STORE_READINESS_FLAG] = original;
    }
  });

  /** The exact defect. */
  it("does not call an empty store open for orders", () => {
    const state = readinessOf([]);
    expect(state.readiness).toBe("not_set_up");
    expect(state.openForOrders).toBe(false);
    expect(state.statusLabel).not.toMatch(/open for orders/i);
    // Nor may it be called paused: nothing was ever started, let alone stopped.
    expect(state.statusLabel).not.toMatch(/paused/i);
  });

  it("separates a store with only drafts from a store that was never started", () => {
    const drafts = readinessOf([listing({ status: "draft" })]);
    expect(drafts.readiness).toBe("incomplete");
    expect(drafts.openForOrders).toBe(false);
    expect(drafts.statusLabel).not.toBe(readinessOf([]).statusLabel);
  });

  it("holds a store at review while nothing is orderable yet", () => {
    const state = readinessOf([listing({ status: "active", approval_status: "pending" })]);
    expect(state.readiness).toBe("pending_review");
    expect(state.openForOrders).toBe(false);
  });

  /**
   * The subtle one. A listing can be `active` with stock *and* awaiting
   * approval — reading stock alone would call that store live while no buyer
   * can order from it.
   */
  it("does not count a listing awaiting approval as something a buyer can order", () => {
    const pending = readinessOf([
      listing({ id: 1, listing_id: 1, status: "active", quantity: 40, approval_status: "pending" })
    ]);
    expect(pending.readiness).toBe("pending_review");

    const approved = readinessOf([
      listing({ id: 1, listing_id: 1, status: "active", quantity: 40, approval_status: "approved" })
    ]);
    expect(approved.readiness).toBe("live");
  });

  it("goes live as soon as one listing is orderable, even alongside drafts and reviews", () => {
    const state = readinessOf([
      listing({ id: 1, listing_id: 1, status: "active", quantity: 40 }),
      listing({ id: 2, listing_id: 2, status: "draft" }),
      listing({ id: 3, listing_id: 3, status: "active", approval_status: "submitted" })
    ]);
    expect(state.readiness).toBe("live");
    expect(state.openForOrders).toBe(true);
  });

  it("counts a low-stock listing as orderable, because it is", () => {
    const state = readinessOf([listing({ status: "active", quantity: LOW_STOCK_THRESHOLD })]);
    expect(state.readiness).toBe("live");
  });

  it("calls a store paused only when it has listings that none of the other rungs claim", () => {
    const state = readinessOf([listing({ status: "active", quantity: 0 })]);
    expect(state.readiness).toBe("paused");
    expect(state.openForOrders).toBe(false);
  });

  /**
   * The ladder's whole purpose is that the strip cannot say the same thing in
   * two different situations. Asserting on the set catches a future edit that
   * makes two rungs agree, which reads fine in review.
   */
  it("gives every rung its own status label and its own headline", () => {
    const labels = ALL_RUNGS.map((rung) => READINESS_FIXTURES[rung].statusLabel);
    const headlines = ALL_RUNGS.map((rung) => READINESS_FIXTURES[rung].headline);
    expect(new Set(labels).size).toBe(ALL_RUNGS.length);
    expect(new Set(headlines).size).toBe(ALL_RUNGS.length);
  });

  it("binds openForOrders to live and to nothing else", () => {
    for (const rung of ALL_RUNGS) {
      expect(READINESS_FIXTURES[rung].openForOrders).toBe(rung === "live");
    }
  });

  /** Every rung has a next move, so the strip's button is never dead. */
  it("offers a control at every rung", () => {
    for (const rung of ALL_RUNGS) {
      const action = READINESS_FIXTURES[rung].action;
      expect(action.label.length).toBeGreaterThan(0);
      expect(action.key.length).toBeGreaterThan(0);
    }
  });
});

describe("the setup checklist", () => {
  function readinessOf(listings: MarketplaceListing[]) {
    return storeReadiness({ listings, rows: rowsOf(listings) });
  }

  it("marks nothing complete for a store that has not started", () => {
    const state = readinessOf([]);
    expect(state.steps.every((step) => !step.complete)).toBe(true);
    expect(state.remaining).toBe(state.steps.length);
  });

  const CHECKLIST_CASES: MarketplaceListing[][] = [
    [],
    [listing({ status: "draft" })],
    [listing({ status: "active", quantity: 0 })],
    [listing({ status: "active", approval_status: "pending" })],
    [listing({ status: "active", quantity: 40 })]
  ];

  /**
   * The brief asks for "a working action per step", but the literal reading is
   * wrong and the code is right: an empty store's "take a listing out of draft"
   * step is outstanding and has no button, because there is nothing to publish
   * until something is added. A button there would open an empty drawer.
   *
   * So the real guarantee is the one below — the seller is never left looking
   * at an unfinished checklist with nothing to press.
   */
  it("always leaves the seller something to press while work remains", () => {
    for (const listings of CHECKLIST_CASES) {
      const state = readinessOf(listings);
      if (state.remaining === 0) continue;
      const outstanding = state.steps.filter((step) => !step.complete);
      // The one exception: waiting on a review is the whole task at that rung.
      if (outstanding.every((step) => step.key === "review")) {
        expect(outstanding.every((step) => step.action === null)).toBe(true);
        continue;
      }
      expect(outstanding.some((step) => step.action !== null)).toBe(true);
    }
  });

  /** A step is only unbuttoned when pressing one could not have helped. */
  it("withholds a button only where there is nothing for it to open", () => {
    // Nothing added: publish and stock have no subject yet.
    const empty = readinessOf([]);
    expect(empty.steps.find((step) => step.key === "add")?.action).not.toBeNull();
    expect(empty.steps.find((step) => step.key === "publish")?.action).toBeNull();

    // A draft exists: now the drafts drawer has something in it.
    const drafted = readinessOf([listing({ status: "draft" })]);
    expect(drafted.steps.find((step) => step.key === "publish")?.action).not.toBeNull();
  });

  it("never puts a button on a step that is already done", () => {
    const state = readinessOf([listing({ status: "active", quantity: 40 })]);
    for (const step of state.steps) {
      if (step.complete) expect(step.action).toBeNull();
    }
  });

  it("shows the review step only while something is actually in review", () => {
    const keys = (listings: MarketplaceListing[]) =>
      readinessOf(listings).steps.map((step) => step.key);
    expect(keys([listing({ status: "active", quantity: 40 })])).not.toContain("review");
    expect(keys([listing({ status: "active", approval_status: "pending" })])).toContain("review");
  });

  it("clears the checklist once a store is genuinely live", () => {
    const state = readinessOf([listing({ status: "active", quantity: 40 })]);
    expect(state.remaining).toBe(0);
    expect(state.steps.every((step) => step.complete)).toBe(true);
  });

  it("counts remaining as the steps that are not complete", () => {
    const state = readinessOf([listing({ status: "draft" })]);
    expect(state.remaining).toBe(state.steps.filter((step) => !step.complete).length);
    expect(state.remaining).toBeGreaterThan(0);
  });

  it("keeps the publish step outstanding while every listing is a draft", () => {
    const state = readinessOf([listing({ id: 1, listing_id: 1, status: "draft" })]);
    const publish = state.steps.find((step) => step.key === "publish");
    expect(publish?.complete).toBe(false);
    expect(publish?.action?.key).toBe("open_drafts");
  });

  it("points an out-of-stock store at its out-of-stock listings", () => {
    const state = readinessOf([listing({ status: "active", quantity: 0 })]);
    const stock = state.steps.find((step) => step.key === "stock");
    expect(stock?.complete).toBe(false);
    expect(stock?.action?.key).toBe("open_out_of_stock");
  });

  it("writes every step's label and detail as a sentence a seller could act on", () => {
    for (const listings of CHECKLIST_CASES) {
      for (const step of readinessOf(listings).steps) {
        expect(step.label.length).toBeGreaterThan(0);
        expect(step.detail.length).toBeGreaterThan(0);
        expect(step.detail).not.toMatch(/—/);
      }
    }
  });
});

describe("listingAwaitsReview", () => {
  it("reads approval status ahead of listing status, because they answer different questions", () => {
    // Active *and* pending approval: the second field is the one about review.
    expect(listingAwaitsReview(listing({ status: "active", approval_status: "pending" }))).toBe(true);
    expect(listingAwaitsReview(listing({ status: "active", approval_status: "approved" }))).toBe(false);
  });

  it("falls back to the listing status when no approval status is present", () => {
    expect(listingAwaitsReview(listing({ status: "pending_review", approval_status: "" }))).toBe(true);
    expect(listingAwaitsReview(listing({ status: "active", approval_status: "" }))).toBe(false);
  });

  it("recognises the review vocabulary the marketplace actually uses", () => {
    for (const value of ["pending", "in_review", "submitted", "awaiting_approval"]) {
      expect(listingAwaitsReview(listing({ approval_status: value }))).toBe(true);
    }
  });

  /**
   * `listingHealth` is deliberately left alone: it maps an in-review listing to
   * `in_stock`, which is right for the tab counts and wrong for readiness. Two
   * questions, two functions.
   */
  it("disagrees with listingHealth on purpose", () => {
    const pending = listing({ status: "active", quantity: 40, approval_status: "pending" });
    expect(listingHealth(pending)).toBe("in_stock");
    expect(listingAwaitsReview(pending)).toBe(true);
  });
});
