/**
 * Which placement, if any, sent the shopper to the product they are looking at.
 *
 * ## The problem this solves
 *
 * §18 wants the whole funnel — impression, click, product view, add to cart,
 * checkout, purchase — reported against the placement that started it. The first
 * two are easy because they happen on the card, which is holding the placement.
 * Everything after the tap happens on `MarketplaceProductScreen`, which is
 * reached by four different commerce surfaces *and* by search, deep links,
 * profile and the marketplace grid, and which is handed nothing but a listing
 * id. Without something like this module, the funnel is blind after the click:
 * we know a product was tapped and never whether anyone did anything with it.
 *
 * ## Why a module singleton and not a navigation param
 *
 * Threading the placement through `MarketplaceProduct` would mean every route
 * that reaches that screen grows an optional attribution field, and the ones
 * that legitimately have none — a deep link, a search result — would have to
 * pass `undefined` forever. Worse, the same argument then applies to
 * `MarketplaceCheckout`, which is a locked screen this work must not touch.
 * Keying on the listing id instead means an unattributed arrival is simply a
 * lookup that returns null, and no route signature changes at all.
 *
 * This is also how click attribution actually works everywhere else: a bounded
 * window opened by a click, closed by time, and matched on the product.
 *
 * ## Why the window is the placement's own TTL
 *
 * Not a number invented here. `events.load_placement` raises `PLACEMENT_EXPIRED`
 * for any placement past `expires_at` (`COMMERCE_DISCOVERY_PLACEMENT_TTL`,
 * one hour by default), so an attribution held past that point cannot produce an
 * event — it can only produce a failed request. Reading `expiresAt` off the
 * placement means the client and the server cannot disagree about how long a
 * click is good for, even if an operator retunes the TTL.
 */
import type { CommercePlacement } from "../api/commerceDiscovery";

/** What a downstream emit needs, and deliberately nothing more. */
export type CommerceAttribution = {
  placementId: string;
  impressionToken: string;
};

type Entry = CommerceAttribution & {
  /** Epoch ms, or 0 for a placement that sent no expiry. */
  expiresAtMs: number;
};

/**
 * A long browsing session taps a lot of products. The cap is generous next to
 * the one-hour TTL — nothing realistic reaches it — and exists so that a leak
 * is bounded rather than so that eviction is a feature.
 */
const MAX_TRACKED = 32;

/** Insertion-ordered, which is what makes the eviction below the oldest entry. */
const byListingId = new Map<number, Entry>();

function expiryMs(placement: CommercePlacement): number {
  if (!placement.expiresAt) return 0;
  const parsed = Date.parse(placement.expiresAt);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/**
 * Remember that this placement is what sent the shopper to this product.
 *
 * Called beside the click beacon, not inside it: the transport module stays a
 * transport, and a surface that wants to report a click without opening a
 * product (there is none today) would not silently start an attribution window.
 */
export function attributeCommerceClick(placement: CommercePlacement | null | undefined): void {
  const listingId = Number(placement?.product?.listingId) || 0;
  if (!placement || !listingId || !placement.placementId || !placement.impressionToken) return;

  // Re-inserting moves the entry to the end of the iteration order, so a
  // product tapped twice is treated as recently seen rather than as next to
  // evict. Deleting first is what makes that true — a bare `set` on an existing
  // key keeps the original position.
  byListingId.delete(listingId);
  byListingId.set(listingId, {
    placementId: placement.placementId,
    impressionToken: placement.impressionToken,
    expiresAtMs: expiryMs(placement)
  });

  while (byListingId.size > MAX_TRACKED) {
    const oldest = byListingId.keys().next();
    if (oldest.done) break;
    byListingId.delete(oldest.value);
  }
}

/**
 * The placement to report this listing's funnel against, or null.
 *
 * Null is the common answer and is not an error: most arrivals at a product are
 * organic navigation, and reporting those against a placement would credit
 * discovery with sales it did not cause — which is the failure mode that makes
 * an attribution system worse than none.
 *
 * An expired entry is dropped rather than returned. The server would reject it,
 * so returning it converts a clean "no attribution" into a failed request and a
 * misleading gap in the logs.
 */
export function commerceAttributionFor(
  listingId: number | null | undefined,
  now: number = Date.now()
): CommerceAttribution | null {
  const id = Number(listingId) || 0;
  if (!id) return null;
  const entry = byListingId.get(id);
  if (!entry) return null;
  if (entry.expiresAtMs && entry.expiresAtMs <= now) {
    byListingId.delete(id);
    return null;
  }
  return { placementId: entry.placementId, impressionToken: entry.impressionToken };
}

/** Test seam. The store is process-global, so a leak between tests is a lie. */
export function __resetCommerceAttribution(): void {
  byListingId.clear();
}
