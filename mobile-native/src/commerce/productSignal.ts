/**
 * A marketplace listing, projected into something the social feed can render.
 *
 * ## Why a projection rather than a second product type
 *
 * `api/marketplace.ts`'s `MarketplaceListing` is this app's product model and
 * stays that way — it is what `/marketplace/search` returns, what
 * `MarketplaceProduct` takes as a route param, and what checkout reads. Nothing
 * here redefines a price, an id or a seller. What this module adds is the small
 * set of decisions a *feed card* has to make that a marketplace grid card does
 * not: which single image stands for the product, what the one line of context
 * above the product name is allowed to claim, and whether the item can be bought
 * at all. Those decisions are made once, here, as pure functions of a listing,
 * so a product Signal in Home, in Search and in a profile cannot disagree about
 * the same listing.
 *
 * The whole listing rides along on {@link ProductSignal.listing}. That is not
 * render data — nothing below the card reads it — and it exists for exactly the
 * reason `discovery/discoveryRows.ts` carries a whole `PulseReel`: the detail
 * route takes an optional `listing` param because there is no fetch-one
 * endpoint, so dropping the object here would mean the tap opens a screen that
 * has to re-find what we already had.
 *
 * ## What this deliberately does not invent
 *
 * The reference design for a shoppable post shows a star rating, a review count
 * and a verified-seller checkmark beside the store name. None of the three has a
 * source. `MARKETPLACE_MOCK_DATA_GAPS` in `api/marketplaceScreen.ts` already
 * registers the seller rating aggregate as missing; seller verification is not
 * on a buyer listing payload in any form, and grep finds no field for it
 * anywhere in the marketplace API layer. So {@link ProductSignal} declares all
 * three, types them nullable, and this adapter sets them to `null` — the same
 * convention `deriveBuyingItems` uses for `sellerRating`, and the reason the
 * gaps list is exported and asserted rather than remembered.
 *
 * Nullable rather than absent is the point. A card that renders "★★★★★ 4.8
 * (320)" from nothing is a card that lies about a stranger's product to someone
 * deciding whether to send them money, and it is indistinguishable from a
 * working one in a screenshot. When the aggregate lands, the adapter fills these
 * in and the card starts drawing them; until then the row is simply absent.
 *
 * The context line above the product name gets the same treatment. "Trending in
 * PulseSoc" would be an engagement claim, and per-listing engagement is a
 * registered gap too. So {@link productSignalContext} returns a *kind* the
 * caller localises, and every kind it can return is backed by a column:
 * FEATURED from `listings.featured`, NEW from `created_at`, or the category.
 */
import {
  listingBadge,
  listingFulfillment,
  marketplaceListingThumbnail,
  type MarketplaceFulfillment
} from "../api/marketplaceScreen";
import { sellerStoreInitial, sellerStoreName } from "../api/sellerIdentity";
import type { MarketplaceListing } from "../api/marketplace";

/**
 * Whether the item can be bought right now.
 *
 * `untracked` is a third state and not a synonym for either of the others.
 * `marketplace_listings.quantity` is nullable and the null means "the seller
 * does not count stock" — collapsing it into `sold_out` is the defect
 * `MarketplaceListing.quantity`'s own docblock describes, which told sellers
 * their untracked listings were gone. A card must render an untracked item as
 * buyable and a zero-quantity item as not.
 */
export type ProductAvailability = "available" | "sold_out" | "untracked";

/** What the line above the product name is allowed to say. */
export type ProductSignalContext =
  | { kind: "featured"; category: string | null }
  | { kind: "new"; category: string | null }
  | { kind: "category"; category: string }
  | { kind: "none" };

export type ProductSignal = {
  /** `listing_id`, the id every marketplace route addresses a product by. */
  productId: number;
  sellerId: number | null;
  /** Store name, never the account holder's personal name. Never empty. */
  sellerName: string;
  sellerHandle: string | null;
  /**
   * No buyer listing payload carries a seller avatar, so this is always null
   * today and the card falls back to {@link ProductSignal.sellerInitial}.
   */
  sellerAvatarUrl: string | null;
  /** First letter of the store name, for the avatar fallback. */
  sellerInitial: string;
  /** Unsourced: nothing in the marketplace API states seller verification. */
  sellerVerified: boolean | null;
  productName: string;
  description: string;
  /** The one image that stands for this product, or null. */
  mediaUrl: string | null;
  /** Server-formatted, e.g. "$64.99". Never assembled from a number here. */
  priceLabel: string | null;
  currency: string | null;
  /** Unsourced: no per-seller review aggregate exists. */
  rating: number | null;
  /** Unsourced: same missing aggregate. */
  reviewCount: number | null;
  availability: ProductAvailability;
  fulfillment: MarketplaceFulfillment;
  /** Drives the context line; every kind is backed by a real column. */
  context: ProductSignalContext;
  category: string | null;
  createdAt: string | null;
  /** Carried for the detail route's optional `listing` param. Not render data. */
  listing: MarketplaceListing;
};

/** The product name a card shows when a listing has no title. */
export const PRODUCT_SIGNAL_TITLE_FALLBACK = "Marketplace listing";

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function productAvailability(listing: MarketplaceListing): ProductAvailability {
  if (listing.quantity == null) return "untracked";
  const quantity = Number(listing.quantity);
  if (!Number.isFinite(quantity)) return "untracked";
  return quantity > 0 ? "available" : "sold_out";
}

/**
 * The context line's kind, from the same badge rule the marketplace grid uses.
 *
 * FEATURED outranks NEW for the reason `listingBadge` gives — it is a paid
 * placement carrying a disclosure obligation, not decoration — and this reuses
 * that function rather than re-deriving it, so the two cannot disagree about
 * which one a boosted listing shows.
 */
export function productSignalContext(
  listing: MarketplaceListing,
  now: number
): ProductSignalContext {
  const category = text(listing.category) || null;
  const badge = listingBadge(listing, now);
  if (badge === "featured") return { kind: "featured", category };
  if (badge === "new") return { kind: "new", category };
  if (category) return { kind: "category", category };
  return { kind: "none" };
}

/**
 * Project one listing. `now` is a parameter so placement and badges are
 * testable without mocking the clock, matching `api/marketplaceScreen.ts`.
 */
export function productSignalFromListing(
  listing: MarketplaceListing,
  now: number
): ProductSignal {
  const productId = Number(listing.listing_id ?? listing.id ?? 0);
  const sellerId = Number(listing.seller_user_id ?? 0) || null;
  return {
    productId,
    sellerId,
    sellerName: sellerStoreName(listing),
    sellerHandle: text(listing.seller_username) || null,
    sellerAvatarUrl: null,
    sellerInitial: sellerStoreInitial(listing),
    sellerVerified: null,
    productName: text(listing.title) || PRODUCT_SIGNAL_TITLE_FALLBACK,
    description: text(listing.short_description) || text(listing.description),
    mediaUrl: marketplaceListingThumbnail(listing),
    priceLabel: text(listing.price_label) || null,
    currency: text(listing.currency) || null,
    rating: null,
    reviewCount: null,
    availability: productAvailability(listing),
    fulfillment: listingFulfillment(listing),
    context: productSignalContext(listing, now),
    category: text(listing.category) || null,
    createdAt: text(listing.created_at) || null,
    listing
  };
}

/**
 * Project a search response into feed-ready signals, dropping the unusable.
 *
 * A listing with no id has no destination, so it is dropped rather than shipped
 * as a card that opens whatever the detail screen finds first — the rule
 * `ReelSuggestion.reelId` states for suggested reels, applied to a tap that ends
 * at a checkout. A sold-out listing is dropped too: an unbuyable product
 * occupying a feed slot is a worse outcome than one fewer product in the feed.
 */
export function productSignalsFromListings(
  listings: readonly MarketplaceListing[],
  now: number
): ProductSignal[] {
  const seen = new Set<number>();
  const out: ProductSignal[] = [];
  for (const listing of listings) {
    const signal = productSignalFromListing(listing, now);
    if (!signal.productId) continue;
    if (signal.availability === "sold_out") continue;
    if (seen.has(signal.productId)) continue;
    seen.add(signal.productId);
    out.push(signal);
  }
  return out;
}
