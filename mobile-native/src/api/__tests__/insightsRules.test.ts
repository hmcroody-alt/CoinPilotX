/**
 * The two rule sets that turn Insights' numbers into sentences.
 *
 * These are pinned harder than most derivations because they are the only place
 * on the screen where the app tells a seller *what to do*. A wrong number is bad;
 * a wrong instruction attached to a confident number is worse, and both failure
 * modes here are silent — the card still renders, still looks designed, and is
 * advising the wrong action about the wrong listing.
 *
 * Three properties are load-bearing:
 *
 *   1. **Priority order.** Only one meta line and only one tip may appear, so
 *      "first match wins" is the whole design. A reordering would quietly swap
 *      which fact a seller sees.
 *   2. **`stock: null` is not `stock: 0`.** Null means the listing does not track
 *      stock; zero means it is sold out. Reading null as zero would tell a seller
 *      to restock something that was never counted.
 *   3. **The one quoted number.** `weeklyRunRate` is trailing arithmetic, and it
 *      refuses to extrapolate a single day into a week. Sellers act on it.
 */

import {
  LOW_STOCK_THRESHOLD,
  TIP_DISMISS_COOLDOWN_MS,
  dismissalKey,
  isDismissed,
  itemMeta,
  recordDismissal,
  selectTip,
  weeklyRunRate,
  type TipDismissals
} from "../insightsRules";
import {
  INSIGHTS_MOCK_DATA_GAPS,
  type InsightsSummary,
  type InsightsTopItem
} from "../insightsDashboard";

/* ------------------------------------------------------------------ helpers */

function item(overrides: Partial<InsightsTopItem> = {}): InsightsTopItem {
  return {
    item_id: "42",
    item_type: "listing",
    source: "marketplace",
    revenue_minor: 120_000,
    orders: 6,
    title: "Blue Mug",
    image_url: null,
    listing_status: "active",
    stock: 20,
    price_label: "$20.00",
    ...overrides
  };
}

function summary(overrides: Partial<InsightsSummary> = {}): InsightsSummary {
  return {
    period: "30d",
    days: 30,
    timezone_offset_minutes: 0,
    start: "2026-07-04 00:00:00",
    end: "2026-08-03 00:00:00",
    prior_start: "2026-06-04 00:00:00",
    prior_end: "2026-07-04 00:00:00",
    has_prior_period: true,
    currency: "USD",
    currencies: ["USD"],
    totals: { revenue_minor: 420_000, orders: 21 },
    prior_totals: { revenue_minor: 380_000, orders: 19 },
    bucket: "day",
    series: [],
    sources: [],
    top_items: [],
    followers: { gained: 4, prior_gained: 3 },
    unavailable: INSIGHTS_MOCK_DATA_GAPS.map((gap) => ({ ...gap })),
    ...overrides
  };
}

/* ---------------------------------------------------------------- metalines */

describe("itemMeta priority", () => {
  it("puts sold out ahead of everything else it could have said", () => {
    // The listing is also unlisted and has orders worth reporting. Sold out wins
    // because it is the only one of the three that costs the seller money now.
    const meta = itemMeta(item({ stock: 0, listing_status: "paused", orders: 9 }), summary());
    expect(meta.rule).toBe("sold_out");
    expect(meta.tone).toBe("warn");
  });

  it("warns at the threshold, not one unit past it", () => {
    expect(itemMeta(item({ stock: LOW_STOCK_THRESHOLD }), summary()).rule).toBe("low_stock");
    expect(itemMeta(item({ stock: LOW_STOCK_THRESHOLD + 1 }), summary()).rule).toBe("engagement");
  });

  it("does not read an untracked stock level as sold out", () => {
    // `null` means this listing does not count stock at all. Calling it sold out
    // would tell the seller to restock something that was never counted.
    const meta = itemMeta(item({ stock: null }), summary());
    expect(meta.rule).toBe("engagement");
    expect(meta.text).not.toMatch(/sold out/i);
  });

  it("explains a row whose listing has since been deleted", () => {
    // The row still counts — dropping it would understate a list headed "where
    // the money came from" — so it has to say why it has no title.
    const meta = itemMeta(item({ title: null, stock: null }), summary());
    expect(meta.rule).toBe("unlisted");
  });

  it("explains a row whose listing is no longer active", () => {
    const meta = itemMeta(item({ listing_status: "paused", stock: null }), summary());
    expect(meta.rule).toBe("unlisted");
    expect(meta.text).toContain("paused");
  });

  it("never claims a promotion drove a sale while attribution is a gap", () => {
    // The attribution branch exists so wiring a real model later is a data
    // change, not a redesign. Until then it must be unreachable, even when the
    // payload carries a promoted_by field.
    const promoted = { ...item({ stock: null }), promoted_by: "your Reel" } as InsightsTopItem;
    expect(itemMeta(promoted, summary()).rule).not.toBe("attribution");
  });

  it("uses the promotion the moment the server can attribute one", () => {
    const promoted = { ...item({ stock: null }), promoted_by: "your Reel" } as InsightsTopItem;
    const meta = itemMeta(promoted, summary({ unavailable: [] }));
    expect(meta.rule).toBe("attribution");
    expect(meta.text).toContain("your Reel");
  });

  it("falls back to the counts that produced the ranking, pluralised", () => {
    expect(itemMeta(item({ orders: 1, stock: null }), summary()).text).toBe("1 order");
    expect(itemMeta(item({ orders: 4, stock: null }), summary()).text).toBe("4 orders");
  });
});

/* -------------------------------------------------------------- run rate */

describe("weeklyRunRate", () => {
  it("is trailing arithmetic: revenue per day times seven", () => {
    // 30 days, $600.00 → $20.00/day → $140.00/week.
    expect(weeklyRunRate(60_000, "30d")).toBe(14_000);
  });

  it("refuses to extrapolate a single day into a weekly rate", () => {
    // One sale multiplied by seven, printed as "was earning about $X a week",
    // is a forecast this platform has no data to make.
    expect(weeklyRunRate(9_900, "today")).toBeNull();
  });

  it("is the identity over exactly a week", () => {
    expect(weeklyRunRate(31_500, "7d")).toBe(31_500);
  });

  it("returns whole minor units, never a fraction of a cent", () => {
    const rate = weeklyRunRate(10_000, "90d");
    expect(Number.isInteger(rate)).toBe(true);
  });
});

/* ------------------------------------------------------------------- tips */

describe("selectTip priority", () => {
  it("puts a sold-out earner ahead of a merely low one", () => {
    const tip = selectTip(
      summary({
        top_items: [item({ item_id: "low", stock: 2 }), item({ item_id: "gone", stock: 0 })]
      })
    );
    expect(tip?.rule).toBe("restock_sold_out");
    expect(tip?.subject).toBe("listing:gone");
  });

  it("ignores a sold-out listing that earned nothing this period", () => {
    // Zero stock on something nobody bought is not proven demand, so there is
    // no money being lost and nothing to recommend.
    const tip = selectTip(summary({ top_items: [item({ stock: 0, revenue_minor: 0 })] }));
    expect(tip?.rule).not.toBe("restock_sold_out");
  });

  it("quotes the listing's own run rate, not the store's", () => {
    // The seller is being told what THIS empty shelf costs. Using the store
    // total would inflate it by every other listing's revenue.
    const tip = selectTip(
      summary({
        totals: { revenue_minor: 900_000, orders: 40 },
        top_items: [item({ stock: 0, revenue_minor: 60_000 })]
      })
    );
    expect(tip?.weeklyRunRateMinor).toBe(weeklyRunRate(60_000, "30d"));
    expect(tip?.weeklyRunRateMinor).not.toBe(weeklyRunRate(900_000, "30d"));
  });

  it("drops the money clause rather than quote a day extrapolated to a week", () => {
    const tip = selectTip(
      summary({ period: "today", days: 1, top_items: [item({ stock: 0 })] })
    );
    expect(tip?.rule).toBe("restock_sold_out");
    expect(tip?.weeklyRunRateMinor).toBeNull();
    expect(tip?.body).not.toContain("{rate}");
  });

  it("leaves the rate placeholder for the screen's own currency formatter", () => {
    // The rule set returns numbers and a token; localization happens at the
    // component boundary, so no currency symbol is ever chosen here.
    const tip = selectTip(summary({ top_items: [item({ stock: 0 })] }));
    expect(tip?.body).toContain("{rate}");
    expect(tip?.body).not.toMatch(/[$€£]/);
  });

  it("points a seller with history and no orders at their orders", () => {
    const tip = selectTip(
      summary({ totals: { revenue_minor: 0, orders: 0 }, top_items: [], has_prior_period: true })
    );
    expect(tip?.rule).toBe("first_sale");
    expect(tip?.action).toEqual({ kind: "orders" });
  });

  it("invites a brand-new store to list something", () => {
    const tip = selectTip(
      summary({
        totals: { revenue_minor: 0, orders: 0 },
        prior_totals: null,
        top_items: [],
        has_prior_period: false
      })
    );
    expect(tip?.rule).toBe("no_sales");
    expect(tip?.action).toEqual({ kind: "add_listing" });
  });

  it("renders nothing when a healthy store has nothing to fix", () => {
    // A dashboard that always has advice teaches the seller to stop reading it.
    const tip = selectTip(summary({ top_items: [item({ stock: 40 }), item({ stock: 25 })] }));
    expect(tip).toBeNull();
  });

  it("gives a restock tip an action that opens the listing it names", () => {
    const tip = selectTip(summary({ top_items: [item({ item_id: "77", stock: 0 })] }));
    expect(tip?.action).toEqual({ kind: "listing", itemId: "77" });
  });
});

/* -------------------------------------------------------------- dismissals */

describe("dismissal cooldown", () => {
  const soldOut = selectTip(summary({ top_items: [item({ item_id: "mug", stock: 0 })] }))!;
  const otherListing = selectTip(
    summary({ top_items: [item({ item_id: "notebook", title: "Notebook", stock: 0 })] })
  )!;

  it("proposes seven days", () => {
    expect(TIP_DISMISS_COOLDOWN_MS).toBe(7 * 24 * 60 * 60 * 1000);
  });

  it("scopes a dismissal to the rule and the subject together", () => {
    // Silencing "restock the mug" must not also silence "restock the notebook":
    // those are two different decisions about two different products.
    const now = 1_000_000;
    const dismissals = recordDismissal(soldOut, {}, now);
    expect(isDismissed(soldOut, dismissals, now)).toBe(true);
    expect(isDismissed(otherListing, dismissals, now)).toBe(false);
  });

  it("lets the tip speak again once the cooldown lapses", () => {
    // A listing still sold out a week later has earned the right to say so.
    const now = 1_000_000;
    const dismissals = recordDismissal(soldOut, {}, now);
    expect(isDismissed(soldOut, dismissals, now + TIP_DISMISS_COOLDOWN_MS - 1)).toBe(true);
    expect(isDismissed(soldOut, dismissals, now + TIP_DISMISS_COOLDOWN_MS)).toBe(false);
  });

  it("prunes lapsed entries as it writes, so the store cannot grow forever", () => {
    const stale: TipDismissals = { "restock_low::listing:ancient": 0 };
    const next = recordDismissal(soldOut, stale, TIP_DISMISS_COOLDOWN_MS + 1);
    expect(Object.keys(next)).toEqual([dismissalKey(soldOut)]);
  });

  it("treats an unknown or corrupt entry as not dismissed", () => {
    // A tip that vanishes because of a bad cache entry is worse than one that
    // reappears: the seller never learns their best listing is empty.
    expect(isDismissed(soldOut, {}, 5_000)).toBe(false);
    expect(isDismissed(soldOut, { [dismissalKey(soldOut)]: NaN }, 5_000)).toBe(false);
  });
});
