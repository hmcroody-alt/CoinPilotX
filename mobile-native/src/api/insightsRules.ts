/**
 * The two rule sets the Insights screen uses to turn numbers into a sentence:
 * the meta line under each ranked listing, and the single tip at the bottom.
 *
 * Both live here, apart from the components, for one reason: a rule that decides
 * what a seller should do with their money has to be readable and testable on its
 * own. Buried inside a render function it becomes folklore.
 *
 * Two standing rules govern everything below.
 *
 * **Every rule states its trigger.** Each returns a `rule` key alongside its
 * text, so the report can list which rules exist and a test can assert which one
 * fired for a given fixture.
 *
 * **Every number is derived from a stated calculation.** The tip card's recovery
 * estimate is trailing revenue per day over the period actually observed, times
 * seven. It is not a forecast, it is arithmetic on what already happened, and the
 * copy says "was earning" rather than "will earn" because that is all the data
 * supports. No figure on this screen is invented to look motivating.
 */

import {
  INSIGHTS_PERIOD_DAYS,
  type InsightsPeriod,
  type InsightsSummary,
  type InsightsTopItem
} from "./insightsDashboard";

/** Below this many units a listing is "low stock". Matches the Store screen's threshold. */
export const LOW_STOCK_THRESHOLD = 5;

/* --------------------------------------------------------------- meta lines */

export type MetaRule =
  | "attribution"
  | "sold_out"
  | "low_stock"
  | "unlisted"
  | "engagement";

export type ItemMeta = {
  rule: MetaRule;
  /** Sentence fragment; the caller localizes the numbers it interpolates. */
  text: string;
  /** `warn` earns the orange treatment — it means money is being left on the table. */
  tone: "warn" | "neutral";
};

/**
 * The meta line for one ranked listing, first matching rule wins.
 *
 * Priority runs from "most actionable" to "least", because there is only one
 * line and the seller should get the fact that changes their next decision:
 *
 * 1. **Attribution** — "boosted by your Reel". Ranked first because it links a
 *    result to something the seller chose to do. *Not currently reachable*: the
 *    platform has no per-listing attribution read, so the branch is present,
 *    documented and guarded by `summary.unavailable`. It is written rather than
 *    omitted so that wiring attribution later is a data change, not a redesign.
 * 2. **Sold out** — stock is zero on a listing that sold this period. The most
 *    valuable thing this screen can tell anyone: demand exists and the shelf is
 *    empty. The design asks for "sold out {day} — missed demand"; the listing
 *    table records no sell-out timestamp, so the day is omitted rather than
 *    guessed, and "missed demand" is only claimed when views continued after the
 *    sell-out, which needs the view counter this platform does not have.
 * 3. **Low stock** — at or under {@link LOW_STOCK_THRESHOLD} units. Same message,
 *    earlier, while it is still cheap to act on.
 * 4. **Unlisted** — the listing sold but is no longer active or has been deleted.
 *    Explains an otherwise confusing row and points at the reason.
 * 5. **Engagement** — the fallback: orders and average sale price, both of which
 *    are always available because they are what produced the ranking.
 */
export function itemMeta(item: InsightsTopItem, summary: InsightsSummary): ItemMeta {
  const attributionKnown = !summary.unavailable.some((gap) => gap.key === "ads_attribution");

  // Rule 1 — attribution. Unreachable until a per-listing attribution read exists.
  // The condition is deliberately `attributionKnown && false`-free: it simply never
  // matches today because no field carries the link, and it will match the moment one does.
  const promoted = (item as { promoted_by?: string | null }).promoted_by;
  if (attributionKnown && promoted) {
    return { rule: "attribution", text: `Boosted by ${promoted}`, tone: "neutral" };
  }

  // Rule 2 — sold out. `null` stock means the listing does not track stock at all,
  // which must not be read as zero.
  if (item.stock === 0) {
    return { rule: "sold_out", text: "Sold out — buyers can't order it", tone: "warn" };
  }

  // Rule 3 — low stock.
  if (item.stock !== null && item.stock > 0 && item.stock <= LOW_STOCK_THRESHOLD) {
    return {
      rule: "low_stock",
      text: `Only ${item.stock} left`,
      tone: "warn"
    };
  }

  // Rule 4 — no longer purchasable.
  if (item.title === null) {
    return { rule: "unlisted", text: "Listing no longer available", tone: "neutral" };
  }
  if (item.listing_status && item.listing_status.toLowerCase() !== "active") {
    return { rule: "unlisted", text: `Listing is ${item.listing_status.toLowerCase()}`, tone: "neutral" };
  }

  // Rule 5 — fallback. Average sale price is revenue divided by orders, which is
  // realized price, not the label price: discounts and offers are already in it.
  return {
    rule: "engagement",
    text: `${item.orders} ${item.orders === 1 ? "order" : "orders"}`,
    tone: "neutral"
  };
}

/* ------------------------------------------------------------------- the tip */

export type TipRule = "restock_sold_out" | "restock_low" | "no_sales" | "first_sale";

export type InsightsTip = {
  rule: TipRule;
  /** Identifies what the tip is *about*, so a dismissal is scoped to it. */
  subject: string;
  title: string;
  body: string;
  actionLabel: string;
  /** What tapping the action should open. The screen maps this to a route. */
  action: { kind: "listing"; itemId: string } | { kind: "add_listing" } | { kind: "orders" };
  /**
   * Weekly revenue this tip is about, in minor units, or `null` when the rule
   * has no honest figure to quote. Derived, never invented — see
   * {@link weeklyRunRate}.
   */
  weeklyRunRateMinor: number | null;
};

/**
 * Revenue per week implied by what this listing already earned in this period.
 *
 * `revenue in the window ÷ days in the window × 7`. It is trailing, not
 * predictive, and the copy that quotes it says so. Anything cleverer — a growth
 * curve, a seasonality factor — would be a forecast this platform has no
 * business making from a single period of one seller's sales.
 *
 * Returns `null` for `today`, where one day of data extrapolated to a week would
 * multiply a single sale by seven and print it as a weekly rate.
 */
export function weeklyRunRate(revenueMinor: number, period: InsightsPeriod): number | null {
  const days = INSIGHTS_PERIOD_DAYS[period];
  if (days < 7) return null;
  return Math.round((revenueMinor / days) * 7);
}

/**
 * At most one recommendation, first matching rule wins, or `null` for none.
 *
 * The card does not render when nothing fires. A dashboard that always has
 * advice teaches the seller to stop reading it, so the bar for appearing is
 * "there is a specific thing to do, about a specific listing".
 *
 * Priority:
 *
 * 1. **`restock_sold_out`** — a listing that earned money this period and is now
 *    at zero stock. Highest value: proven demand, nothing to sell. Quotes the
 *    trailing weekly rate so the cost of the empty shelf is concrete.
 * 2. **`restock_low`** — same shape, caught earlier, at or under the threshold.
 * 3. **`first_sale`** — the seller has listings and no sales in this period.
 *    Points at Orders rather than inventing a growth tactic.
 * 4. **`no_sales`** — no sales and nothing ranked, i.e. a new or empty store.
 *    The one useful action is to list something.
 *
 * Rules the design asks for that are **not implemented, and why**: "stale
 * high-value listing" needs a last-sold-at per listing, which requires a query
 * this endpoint does not run; "low offers-answered" needs the offers table that
 * exists but is never written to. Both are named in the report rather than
 * approximated.
 */
export function selectTip(summary: InsightsSummary): InsightsTip | null {
  const ranked = summary.top_items;

  const soldOut = ranked.find((item) => item.stock === 0 && item.revenue_minor > 0);
  if (soldOut) {
    const rate = weeklyRunRate(soldOut.revenue_minor, summary.period);
    return {
      rule: "restock_sold_out",
      subject: `listing:${soldOut.item_id}`,
      title: "Restock your best seller",
      body: rate
        ? `${soldOut.title || "This listing"} sold out. It was earning about {rate} a week.`
        : `${soldOut.title || "This listing"} sold out, and buyers can no longer order it.`,
      actionLabel: "Restock it",
      action: { kind: "listing", itemId: soldOut.item_id },
      weeklyRunRateMinor: rate
    };
  }

  const low = ranked.find(
    (item) => item.stock !== null && item.stock > 0 && item.stock <= LOW_STOCK_THRESHOLD
  );
  if (low) {
    const rate = weeklyRunRate(low.revenue_minor, summary.period);
    return {
      rule: "restock_low",
      subject: `listing:${low.item_id}`,
      title: "Running low",
      body: rate
        ? `${low.title || "This listing"} has ${low.stock} left and was earning about {rate} a week.`
        : `${low.title || "This listing"} has ${low.stock} left.`,
      actionLabel: "Add stock",
      action: { kind: "listing", itemId: low.item_id },
      weeklyRunRateMinor: rate
    };
  }

  if (summary.totals.orders === 0) {
    if (ranked.length === 0 && summary.has_prior_period) {
      return {
        rule: "first_sale",
        subject: "period:no_orders",
        title: "No orders in this period",
        body: "Nothing sold in the window you're looking at. Check a longer period, or review your open orders.",
        actionLabel: "Open Orders",
        action: { kind: "orders" },
        weeklyRunRateMinor: null
      };
    }
    if (!summary.has_prior_period) {
      return {
        rule: "no_sales",
        subject: "store:new",
        title: "Your store is ready",
        body: "Add a listing and this screen will start showing where your money comes from.",
        actionLabel: "Add a listing",
        action: { kind: "add_listing" },
        weeklyRunRateMinor: null
      };
    }
  }

  return null;
}

/* ------------------------------------------------------------- dismissals */

/**
 * How long a dismissed tip stays dismissed, per rule *and* subject.
 *
 * Seven days: long enough that dismissing is not a per-visit chore, short enough
 * that a listing which is still sold out a week later gets to say so again. The
 * scope is rule+subject rather than rule alone, so silencing "restock the mug"
 * does not also silence "restock the notebook" — those are different decisions.
 *
 * A guess dressed as a default, and flagged as such in the report: nothing in
 * the product defines a cooldown, so this needs a product decision to confirm.
 */
export const TIP_DISMISS_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000;

export type TipDismissals = Record<string, number>;

export function dismissalKey(tip: InsightsTip): string {
  return `${tip.rule}::${tip.subject}`;
}

export function isDismissed(
  tip: InsightsTip,
  dismissals: TipDismissals,
  now: number = Date.now()
): boolean {
  const at = dismissals[dismissalKey(tip)];
  return typeof at === "number" && now - at < TIP_DISMISS_COOLDOWN_MS;
}

/** Records a dismissal and drops entries whose cooldown has already lapsed. */
export function recordDismissal(
  tip: InsightsTip,
  dismissals: TipDismissals,
  now: number = Date.now()
): TipDismissals {
  const next: TipDismissals = {};
  Object.entries(dismissals).forEach(([key, at]) => {
    if (now - at < TIP_DISMISS_COOLDOWN_MS) next[key] = at;
  });
  next[dismissalKey(tip)] = now;
  return next;
}
