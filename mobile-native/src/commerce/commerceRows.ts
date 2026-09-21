/**
 * Where a Marketplace recommendation goes in Home's row list, and nothing else.
 *
 * ## Why a third module rather than widening the second
 *
 * `discovery/discoveryRows.ts` makes this argument once already, against
 * widening `feed/injectAds.ts`, and every clause of it applies one level up.
 * Commerce rows have their own cadence (server-configured, not Advertising's
 * and not Discovery's), their own eligibility (a relevance floor that can
 * legitimately yield nothing), and a dismissal that outlives the session —
 * "don't recommend this seller" is written to the server, not held in a
 * `Set` until the app restarts. Folding those into `injectDiscoveryRows` would
 * make every change to a suggestion carousel a change to the blast radius of
 * the commerce placement rules, and vice versa.
 *
 * So the composition is a chain, each step taking the previous one's output:
 *
 *     injectAds(posts, ads)            → post / ad rows
 *     injectDiscoveryRows(rows, mods)  → + discovery rows
 *     injectCommerceRows(rows, places) → + commerce rows
 *
 * With no placements the third call returns the list unchanged, so a feed with
 * commerce discovery off is byte-for-byte the feed that shipped.
 *
 * ## The rule this file exists to make structural
 *
 * **A commerce unit is its own row between two complete posts. It never
 * overlays one.** Nothing here can produce an overlay, because nothing here can
 * express one — the output is a flat row list and a commerce row is a sibling
 * of a post, never a property of it. That is the whole reason placement is a
 * pure list transform rather than a prop on the post card: a renderer that
 * receives `post.commerceCard` will eventually draw it on top of the post,
 * covering a caption or a reaction bar, and no test of *this* module would see
 * it happen.
 *
 * ## Why a dismissed slot stays empty
 *
 * Placements are bound to slots by index, and a dismissed placement leaves its
 * slot empty rather than pulling the next one forward. Re-packing is the
 * obvious implementation and it produces exactly the behaviour the brief
 * forbids: hide a product, and another product appears in the hole a frame
 * later. Leaving the gap also keeps the remaining rows at stable positions, so
 * hiding a card does not make the rest of the feed jump under the user's thumb.
 */
import type { CommercePlacement } from "../api/commerceDiscovery";
import type { HomeRow } from "../discovery/discoveryRows";

export type CommerceRow = {
  type: "commerce";
  key: string;
  /** 0-based position among commerce rows in this feed, for analytics. */
  slot: number;
  placement: CommercePlacement;
};

/** What Home's FlatList renders once commerce discovery is on. */
export type HomeRowWithCommerce<TPost> = HomeRow<TPost> | CommerceRow;

export type CommercePlacementOptions = {
  /** Organic posts before the first commerce unit. Server default: 6. */
  leadIn?: number;
  /** Organic posts between commerce units after the first. Server default: 8. */
  interval?: number;
  /** Hard cap per feed page. Server default: 2. */
  maxRows?: number;
  /** Placement ids the user hid this session, before the refetch catches up. */
  dismissedPlacementIds?: ReadonlySet<string>;
  /** Seller ids the user has told us to stop recommending, same window. */
  dismissedSellerIds?: ReadonlySet<number>;
  /** Injected so placement stays a pure function of its arguments. */
  now?: number;
};

/**
 * Defaults mirror `services/commerce_discovery/config.py`.
 *
 * Duplicated rather than fetched because placement must work on the very first
 * frame, before any response has arrived, and a cadence that changes when the
 * network answers would move rows under a user mid-scroll. The server's values
 * win when a response carries them; these are what the list looks like until
 * then. 6 and 8 sit inside the brief's 6–10 band and interleave with — rather
 * than collide with — the ad cadence (3/5) and the discovery cadence (5/7).
 */
export const COMMERCE_LEAD_IN = 6;
export const COMMERCE_INTERVAL = 8;
export const COMMERCE_MAX_ROWS = 2;

/** At most one card per seller per page, regardless of how well they ranked. */
const MAX_PER_SELLER_PER_PAGE = 1;

/** Stable, unique, and readable in a `keyExtractor` crash log. */
export function commerceRowKey(placementId: string, slot: number): string {
  return `commerce:${placementId}:${slot}`;
}

function expired(placement: CommercePlacement, now: number): boolean {
  if (!placement.expiresAt) return false;
  const expiry = Date.parse(placement.expiresAt);
  if (Number.isNaN(expiry)) return false;
  return expiry <= now;
}

/**
 * Placements this page may draw from, in rank order.
 *
 * Structural validity only — expiry, a missing token, a duplicate listing, a
 * seller already represented. Dismissals are deliberately **not** applied here:
 * filtering them out would compact the list, and a compacted list is exactly
 * how the next-best product slides into the hole left by the one the user just
 * hid. Dismissal is checked at slot-binding time instead, where skipping leaves
 * the gap.
 */
function usablePlacements(placements: CommercePlacement[], now: number): CommercePlacement[] {
  const seenListings = new Set<number>();
  const sellerCounts = new Map<number, number>();
  const out: CommercePlacement[] = [];

  for (const placement of placements) {
    if (!placement?.placementId || !placement.impressionToken) continue;
    if (!placement.product?.listingId) continue;
    if (expired(placement, now)) continue;

    const sellerId = placement.product.sellerUserId;
    if (seenListings.has(placement.product.listingId)) continue;
    const sellerCount = sellerId ? sellerCounts.get(sellerId) || 0 : 0;
    if (sellerId && sellerCount >= MAX_PER_SELLER_PER_PAGE) continue;

    seenListings.add(placement.product.listingId);
    if (sellerId) sellerCounts.set(sellerId, sellerCount + 1);
    out.push(placement);
  }

  return out;
}

/** Has the user told us to stop showing this exact card, or this seller? */
function dismissed(placement: CommercePlacement, options: CommercePlacementOptions): boolean {
  if (options.dismissedPlacementIds?.has(placement.placementId)) return true;
  const sellerId = placement.product.sellerUserId;
  return Boolean(sellerId && options.dismissedSellerIds?.has(sellerId));
}

/**
 * Thread commerce rows through an existing Home row list.
 *
 * Four placement invariants, each with a test:
 *
 *  1. **Never inside a post.** Structurally impossible — see the header.
 *  2. **Never splits a post from the rows that belong to it.** The whole post
 *     group (post, then any ad or discovery row `injectAds` /
 *     `injectDiscoveryRows` attached to it) is emitted before a commerce row
 *     can be considered.
 *  3. **Never adjacent to another non-post row.** The row immediately before a
 *     commerce unit must be a post. An ad followed by a product card is two
 *     commercial units back to back, which is the precise experience — feed as
 *     catalogue — the cadence exists to prevent. A slot that would land there is
 *     skipped rather than shifted, because shifting it re-creates the adjacency
 *     one row later.
 *  4. **Never the last row.** A product card with nothing under it reads as the
 *     feed having ended on an advert.
 *
 * Pure and deterministic: same arguments, same rows, every time.
 */
export function injectCommerceRows<TPost>(
  rows: HomeRow<TPost>[],
  placements: CommercePlacement[],
  options: CommercePlacementOptions = {}
): HomeRowWithCommerce<TPost>[] {
  const leadIn = Math.max(options.leadIn ?? COMMERCE_LEAD_IN, 1);
  const interval = Math.max(options.interval ?? COMMERCE_INTERVAL, 1);
  const maxRows = Math.max(options.maxRows ?? COMMERCE_MAX_ROWS, 0);
  const now = options.now ?? Date.now();

  if (!Array.isArray(placements) || placements.length === 0 || maxRows === 0) {
    return [...rows];
  }

  const usable = usablePlacements(placements, now);
  if (usable.length === 0) return [...rows];

  const out: HomeRowWithCommerce<TPost>[] = [];
  let organicCount = 0;
  // Counts slots *offered*, not rows placed. A slot skipped for adjacency or
  // dismissal is spent, which is what stops the next eligible position from
  // becoming an immediate replacement for the card that was just hidden.
  let slot = 0;

  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    out.push(row);
    if (row.type !== "post") continue;

    organicCount += 1;

    // Keep everything the injectors attached to this post with it, and do not
    // let those rows count toward the organic cadence.
    while (index + 1 < rows.length && rows[index + 1].type !== "post") {
      index += 1;
      out.push(rows[index]);
    }

    if (slot >= maxRows) continue;
    if (organicCount < leadIn) continue;
    if ((organicCount - leadIn) % interval !== 0) continue;
    if (index + 1 >= rows.length) continue;

    // Adjacency is a property of the list, not of the user's wishes, so a
    // position lost to it does not spend a slot: the same placement is offered
    // at the next eligible position instead of being silently dropped.
    const previous = out[out.length - 1];
    if (!previous || previous.type !== "post") continue;

    const placement = usable[slot];
    slot += 1;
    if (!placement) continue;
    // Spent, not filled. The gap is the point.
    if (dismissed(placement, options)) continue;

    out.push({
      type: "commerce",
      key: commerceRowKey(placement.placementId, slot - 1),
      slot: slot - 1,
      placement
    });
  }

  return out;
}
