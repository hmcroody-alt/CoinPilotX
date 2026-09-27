/**
 * Where shoppable product Signals sit in a social feed.
 *
 * ## Why a third module rather than a third case in an existing one
 *
 * This is the pattern `discovery/discoveryRows.ts` set out and the reasoning
 * carries over unchanged: `injectAds` owns the sponsored cadence, discovery owns
 * the suggestion cadence, and commerce owns this one. Each composes over the
 * last — `injectAds` → `injectDiscoveryRows` → `injectProductSignalRows` — so a
 * change to one cadence cannot move another's rows, and with commerce off the
 * third call never happens and the feed is byte-for-byte what it is today.
 *
 * The union widens the same way. `HomeRow<TPost>` is `FeedRow | DiscoveryRow`;
 * {@link CommerceFeedRow} is that plus {@link ProductSignalRow}. Discovery does
 * not learn about commerce, and a surface that wants products but not
 * suggestions composes only what it wants.
 *
 * ## The cadence, and why these numbers
 *
 * Three injectors placing rows into one list can collide, and a feed that reads
 * post, ad, suggestion, product, post is not a social feed. The defaults are
 * chosen against the other two's actual defaults on Home — ads at leadIn 3 /
 * interval 5, discovery at leadIn 5 / interval 7 — so the organic positions each
 * claims are disjoint:
 *
 *   ads         after organic post 3, 8, 13, 18, 23
 *   discovery   after organic post 5, 12, 19, 26
 *   products    after organic post 4, 14
 *
 * Changing any of the three means re-checking that table. It is written down
 * here because the collision is invisible in a unit test of any one module: each
 * passes in isolation and the feed still stacks two commercial rows together.
 *
 * ## What is pure about this
 *
 * No fetching, no flags, no navigation, no clock — the same contract the other
 * two injectors hold, which is what lets placement be asserted as arithmetic
 * instead of read off a rendered screen. Flag gating happens at the call site,
 * so a feed with commerce off never builds the rows in the first place.
 */
import type { HomeRow } from "../discovery/discoveryRows";
import type { ProductSignal } from "./productSignal";

export type ProductSignalRow = {
  type: "product";
  key: string;
  /** 0-based position among product rows in this feed. */
  slot: number;
  signal: ProductSignal;
};

/** What a feed with ads, discovery and commerce actually renders. */
export type CommerceFeedRow<TPost> = HomeRow<TPost> | ProductSignalRow;

export type ProductSignalPlacementOptions = {
  /** Organic posts that must render before the first product. */
  leadIn?: number;
  /** Organic posts between products after the first. */
  interval?: number;
  /** Hard cap on product rows per feed page. */
  maxRows?: number;
};

/** See the cadence table above before changing either of these. */
export const PRODUCT_SIGNAL_LEAD_IN = 4;
export const PRODUCT_SIGNAL_INTERVAL = 10;

/**
 * Two, not more.
 *
 * A product Signal is a commercial row in a social feed, and the feed already
 * carries up to five ads and four suggestion rows per page. The cap is the only
 * thing standing between "the feed sells things too" and "the feed is a
 * catalogue", and it is a product decision rather than a technical limit, which
 * is why it is a named constant and not a magic number at the call site.
 */
export const PRODUCT_SIGNAL_MAX_ROWS = 2;

/** Stable, unique, and readable in a `keyExtractor` crash log. */
export function productSignalRowKey(productId: number, slot: number): string {
  return `product:${productId}:${slot}`;
}

/**
 * Thread product rows through an existing feed row list.
 *
 * The same three invariants discovery holds, for the same reasons:
 *
 *  1. **A product never splits a post from the rows it earned.** `injectAds`
 *     puts an ad immediately after its post and discovery appends after the
 *     whole group; inserting inside that group would read as the product
 *     belonging to the ad. So insertion happens after the group.
 *  2. **A product row is never adjacent to another product row.** Structural,
 *     from counting only organic posts with `interval >= 1` — not a lookback,
 *     which is the kind of check that survives a refactor as a no-op.
 *  3. **A product is never the last row.** A commercial card with nothing under
 *     it reads as the feed having ended at an advertisement.
 *
 * Pure and deterministic: same arguments, same rows, every time.
 */
export function injectProductSignalRows<TPost>(
  rows: readonly HomeRow<TPost>[],
  signals: readonly ProductSignal[],
  options: ProductSignalPlacementOptions = {}
): CommerceFeedRow<TPost>[] {
  const leadIn = Math.max(options.leadIn ?? PRODUCT_SIGNAL_LEAD_IN, 1);
  const interval = Math.max(options.interval ?? PRODUCT_SIGNAL_INTERVAL, 1);
  const maxRows = Math.max(options.maxRows ?? PRODUCT_SIGNAL_MAX_ROWS, 0);

  if (signals.length === 0 || maxRows === 0) return [...rows];

  const out: CommerceFeedRow<TPost>[] = [];
  let organicCount = 0;
  let placed = 0;

  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    out.push(row);
    if (row.type !== "post") continue;

    organicCount += 1;

    // Keep everything this post earned with it, and do not let any of it count
    // toward the organic cadence.
    while (index + 1 < rows.length && rows[index + 1].type !== "post") {
      index += 1;
      out.push(rows[index]);
    }

    if (placed >= maxRows) continue;
    if (placed >= signals.length) continue;
    if (organicCount < leadIn) continue;
    if ((organicCount - leadIn) % interval !== 0) continue;
    if (index + 1 >= rows.length) continue;

    const signal = signals[placed];
    out.push({
      type: "product",
      key: productSignalRowKey(signal.productId, placed),
      slot: placed,
      signal
    });
    placed += 1;
  }

  return out;
}
