/**
 * Marketplace screen data layer.
 *
 * The same contract as `./storeDashboard`: this module turns API payloads into
 * the numbers and enums the screen renders, and it returns numbers, not strings.
 * No currency symbol, no distance unit, no relative time is produced here —
 * those are locale decisions and they belong in the screen with the existing
 * utilities. Everything takes `now` as a parameter so it is testable without
 * mocking the clock.
 *
 * ## What is real and what is not
 *
 * Marketplace has a genuine backend for the *listing* half — search, seller
 * listings, save/unsave, report, seller chat, single-item checkout — and no
 * backend at all for the *negotiation* half: offers, cart, boost, saved
 * searches, distance, ratings. `MARKETPLACE_MOCK_DATA_GAPS` below is the
 * complete, exported list, asserted in a test, so the report is generated from
 * the code rather than from memory.
 *
 * Three fields that the reference design needs *were* real all along and simply
 * were not being selected: `created_at`, `featured` and `delivery_type` exist in
 * `marketplace_listings` but were absent from every endpoint's SELECT. Adding
 * them was five words of SQL in three places, and it converts the NEW badge, the
 * FEATURED badge, listing staleness and the entire Add-to-cart-vs-Make-offer
 * split from invented data into real data. That is the difference between a
 * screen that demos and a screen that works, so it was worth the SQL.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  listMarketplaceSellerListings,
  listMarketplaceSellerOrders,
  loadCachedMarketplace,
  loadCachedSellerStore,
  normalizeMarketplaceListing,
  searchMarketplace,
  type MarketplaceListing,
  type MarketplaceSellerOrder,
  type SellerStoreSnapshot
} from "./marketplace";
import { listingHealth, type StoreListingHealth } from "./storeDashboard";

/* ------------------------------------------------------------------ *
 * Unsourced fields
 * ------------------------------------------------------------------ */

export type MarketplaceDataGap = {
  /** What the reference design asks for. */
  field: string;
  /** Where it would have to come from. */
  needs: string;
  /** The flag that keeps the surface dark until it does. */
  flag?: string;
};

/**
 * Everything the Marketplace design shows that this app has no source for.
 *
 * Exported so it can be asserted in a test: if someone later fakes one of these,
 * the count changes and the test says so.
 */
export const MARKETPLACE_MOCK_DATA_GAPS: readonly MarketplaceDataGap[] = [
  // MOCK-DATA: the entire offers domain. No `marketplace_offers` table, no
  // route, no column. The state machine in `./marketplaceOffers` is real and
  // tested; what it has nothing to talk to is a server.
  {
    field: "Offers (make, accept, counter, decline, expire)",
    needs: "marketplace_offers table + CRUD endpoints + a push notification on accept",
    flag: "MARKETPLACE_OFFERS_ENABLED"
  },
  // MOCK-DATA: cart. Single-item checkout is real (`/api/pulse/payments/checkout`);
  // a basket with line items and quantities is not.
  {
    field: "Cart and cart badge count",
    needs: "cart endpoints (add, list, remove) over the existing payments surface",
    flag: "MARKETPLACE_CART_ENABLED"
  },
  // MOCK-DATA: boost purchase. `listings.featured` is real and already orders
  // search results; nothing prices a boost or takes payment for one.
  {
    field: "Boost purchase and price",
    needs: "boost SKU + a charge that sets listings.featured, through existing payments",
    flag: "MARKETPLACE_BOOST_ENABLED"
  },
  // MOCK-DATA: per-item engagement. The seller row's "214 views · 18 saves ·
  // 1 offer" has no source; saves are stored per viewer but never aggregated.
  {
    field: "Per-listing views, saves and offer counts",
    needs: "listing impressions endpoint + a saves aggregate per listing"
  },
  // MOCK-DATA: saved searches. Search is stateless — nothing persists a query
  // or records what it has already shown you.
  {
    field: "Saved searches and new-match counts",
    needs: "saved_searches table + a matcher that records last_seen_listing_id"
  },
  // MOCK-DATA: location. There is no geo on a listing and no radius setting on
  // an account, so "Within 5 mi of Austin" and every per-card distance are
  // unsourced. This is also the safety-sensitive one: the design shows
  // distances, never coordinates, and any backend must do the same.
  {
    field: "Location strip, radius, and per-item distance",
    needs: "listing lat/lon (coarse) + account radius preference; expose distance only, never coordinates"
  },
  // MOCK-DATA: seller rating. Same missing aggregate the Store dashboard hit.
  {
    field: "Seller rating and review count",
    needs: "per-seller review aggregate (mean rating + count)"
  },
  // MOCK-DATA: the post-sale "Leave a rating for the buyer" prompt and its
  // destination screen. No review write path exists in either direction.
  {
    field: "Buyer rating flow",
    needs: "review write endpoint + a rating screen; no route exists to link to"
  },
  // MOCK-DATA: meetup spots. The safety feature the brief asks to keep linked.
  // Worth saying plainly: this one is not cosmetic.
  {
    field: "Saved meetup spots",
    needs: "meetup_spots table + a settings screen; safety feature, should not ship faked"
  },
  // MOCK-DATA: sold-history revenue. Seller orders exist and carry amounts, but
  // they cover Store purchases; a Marketplace sale closed by accepting an offer
  // has no order row at all, so "revenue this month" cannot be totalled.
  {
    field: "Sold history revenue this month",
    needs: "an order row written when an offer is accepted"
  },
  // MOCK-DATA: original price. Nothing records what a listing used to cost, so
  // the struck-through price on a drop has no source. `updated_at` says a
  // listing changed but not what changed or to what.
  {
    field: "Original price (strikethrough on drops)",
    needs: "listing price history, or a previous_price_label column"
  },
  // MOCK-DATA: unread counts for the bell and for buyer messages. The messaging
  // API can start a conversation but this client has no unread aggregate.
  {
    field: "Notification bell and buyer-message unread counts",
    needs: "unread counts endpoint scoped to marketplace conversations"
  }
] as const;

/* ------------------------------------------------------------------ *
 * Location honesty
 * ------------------------------------------------------------------ */

export const MARKETPLACE_LOCATION_FLAG = "EXPO_PUBLIC_MARKETPLACE_LOCATION_HONESTY";

/** True when a build has opted into the honest location wording. Off by default. */
export function marketplaceLocationHonestyEnabled(): boolean {
  return String(process.env[MARKETPLACE_LOCATION_FLAG] || "").trim() === "1";
}

/**
 * What the buying feed may claim about where its listings are.
 *
 * The screen headed a list "Just listed near you" over listings with no geo on
 * them, sorted by recency and nothing else. Three words of that heading were
 * false, and the strip underneath already said so — "Location not set" — which
 * means the screen contradicted itself on one page.
 *
 * The correction the brief asks for is a working "Set your location" button.
 * There is nothing to wire it to: no `expo-location` in this app, no city or
 * country on the account, no coordinates on a listing, and `distanceMeters` is
 * hard-coded `null` (see the location entry in `MARKETPLACE_MOCK_DATA_GAPS`).
 * A button that opens nothing is the dead control this tier exists to remove,
 * so the claim is dropped instead and the empty state is given an action that
 * really works — clearing the category filter, which is a filter that exists.
 *
 * `known` is an input rather than a constant so that the day geo lands, the
 * heading flips back to "near you" by passing a city in, with no edit here and
 * none in the screen.
 */
export type MarketplaceLocationState = {
  /** True when the app actually knows where the reader is. */
  known: boolean;
  /** The feed heading. Only claims "near you" when `known`. */
  feedTitle: string;
  /** The strip's sentence. */
  stripText: string;
  /**
   * Why the strip cannot be acted on, or `null` when it can. Rendered as the
   * strip's own explanation, so the row is an unavailable control with a stated
   * reason rather than a line of text that looks tappable and is not.
   */
  unavailableReason: string | null;
  /** The "show more" footer label, which claimed "nearby" for the same reason. */
  moreLabel: string;
  empty: {
    title: string;
    body: string;
    /** `null` when there is no filter to clear and nothing else would help. */
    action: { key: "clear_category"; label: string } | null;
  };
};

export function marketplaceLocation(input: {
  /** The reader's town, when one is known. Nothing supplies this yet. */
  city?: string | null;
  /** True when a category filter is narrowing the feed. */
  categoryFiltered?: boolean;
} = {}): MarketplaceLocationState {
  const city = String(input.city || "").trim();
  const known = city.length > 0;
  const filtered = Boolean(input.categoryFiltered);

  return {
    known,
    feedTitle: known ? `Just listed near ${city}` : "Just listed",
    stripText: known ? `Showing listings near ${city}` : "Showing every listing",
    unavailableReason: known
      ? null
      : "Location isn't part of the app yet, so listings aren't sorted by distance.",
    moreLabel: known ? "Show more nearby" : "Show more",
    empty: filtered
      ? {
          title: "Nothing in this category right now.",
          body: "Other categories may have something. Pull down to check again.",
          action: { key: "clear_category", label: "Show all categories" }
        }
      : {
          title: "Nothing has been listed yet.",
          body: "New listings appear here as sellers add them. Pull down to check again.",
          action: null
        }
  };
}

/* ------------------------------------------------------------------ *
 * Shared helpers
 * ------------------------------------------------------------------ */

const DAY_MS = 86_400_000;

/** Milliseconds since a listing was created, or null when unknown. */
export function listingAgeMs(listing: MarketplaceListing, now: number): number | null {
  if (!listing.created_at) return null;
  const parsed = Date.parse(String(listing.created_at));
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, now - parsed);
}

function thumbnailOf(listing: MarketplaceListing): string | null {
  const url =
    listing.thumbnail_url ||
    listing.cover_image_url ||
    listing.image_url ||
    listing.media?.[0]?.thumbnail_url ||
    listing.media?.[0]?.url ||
    null;
  return url ? String(url) : null;
}

function listingId(listing: MarketplaceListing): number {
  return Number(listing.listing_id ?? listing.id ?? 0);
}

/* ------------------------------------------------------------------ *
 * Badges
 * ------------------------------------------------------------------ */

/** Under this age a listing is "just listed" and earns the NEW badge. */
export const NEW_LISTING_MAX_AGE_MS = 3 * DAY_MS;

/**
 * FEATURED wins over NEW when a listing is both.
 *
 * Not an aesthetic tie-break: FEATURED is a paid placement and carries a
 * disclosure obligation, NEW is a convenience. Showing NEW on a boosted listing
 * would replace a required disclosure with an optional decoration.
 */
export function listingBadge(
  listing: MarketplaceListing,
  now: number
): "featured" | "new" | null {
  if (listing.featured === 1 || listing.featured === true) return "featured";
  const age = listingAgeMs(listing, now);
  if (age != null && age < NEW_LISTING_MAX_AGE_MS) return "new";
  return null;
}

/* ------------------------------------------------------------------ *
 * The fulfillment split
 * ------------------------------------------------------------------ */

export type MarketplaceFulfillment = "platform" | "local" | "both" | "unknown";

/**
 * Whether a card gets "Add to cart", "Make offer", both, or neither.
 *
 * Driven by `delivery_type` and `product_type`, per the brief's instruction to
 * drive the split from fulfillment data rather than from category guesses. A
 * listing whose fulfillment is unrecorded returns `unknown` and gets no action
 * button at all — it routes to the detail page instead. Defaulting an unknown
 * to "Add to cart" would put an item the platform may not be able to ship
 * behind a checkout, which is the one failure here that costs a buyer money.
 */
export function listingFulfillment(listing: MarketplaceListing): MarketplaceFulfillment {
  const delivery = String(listing.delivery_type || "").toLowerCase();
  const product = String(listing.product_type || "").toLowerCase();

  const local = delivery.includes("pickup") || delivery.includes("local") || delivery.includes("meetup");
  const platform =
    delivery.includes("ship") ||
    delivery.includes("digital") ||
    delivery.includes("download") ||
    product.includes("digital") ||
    product.includes("course");

  if (local && platform) return "both";
  if (local) return "local";
  if (platform) return "platform";
  return "unknown";
}

/**
 * The action a grid card offers.
 *
 * `both` prefers Add to cart, with the offer left to the detail page — exactly
 * as the brief specifies, and for a good reason: two glowing buttons on one
 * small card is a choice presented before the buyer has seen the item.
 */
export function gridCardAction(
  listing: MarketplaceListing,
  options: { cartEnabled: boolean; offersEnabled: boolean }
): "cart" | "offer" | null {
  const fulfillment = listingFulfillment(listing);
  if (fulfillment === "unknown") return null;
  if (fulfillment === "local") return options.offersEnabled ? "offer" : null;
  return options.cartEnabled ? "cart" : null;
}

/* ------------------------------------------------------------------ *
 * Categories
 * ------------------------------------------------------------------ */

export type MarketplaceCategory = { key: string; label: string; count: number };

/**
 * The category rail, derived from the listings actually returned.
 *
 * Ordered by how many listings each has, so the rail leads with what this feed
 * is mostly made of. A fixed seven-chip rail would advertise categories the
 * user's area has none of and make an accurate empty state look like a fault.
 */
export function deriveCategories(listings: readonly MarketplaceListing[]): MarketplaceCategory[] {
  const counts = new Map<string, { label: string; count: number }>();
  listings.forEach((listing) => {
    const label = String(listing.category || "").trim();
    if (!label) return;
    const key = label.toLowerCase();
    const entry = counts.get(key);
    if (entry) entry.count += 1;
    else counts.set(key, { label, count: 1 });
  });
  return Array.from(counts.entries())
    .map(([key, { label, count }]) => ({ key, label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/* ------------------------------------------------------------------ *
 * Buying feed
 * ------------------------------------------------------------------ */

export type BuyingItem = {
  id: number;
  title: string;
  priceLabel: string;
  currency: string;
  imageUrl: string | null;
  badge: "featured" | "new" | null;
  saved: boolean;
  sellerName: string;
  sellerUserId: number;
  category: string;
  fulfillment: MarketplaceFulfillment;
  action: "cart" | "offer" | null;
  // MOCK-DATA: no geo on a listing, so distance is null and the meta line falls
  // back to the fulfillment promise, which is real.
  distanceMeters: number | null;
  // MOCK-DATA: no per-seller review aggregate.
  sellerRating: number | null;
  // MOCK-DATA: no price history.
  originalPriceLabel: string | null;
};

export function deriveBuyingItems(
  listings: readonly MarketplaceListing[],
  options: { now: number; cartEnabled: boolean; offersEnabled: boolean }
): BuyingItem[] {
  return listings.map((raw) => {
    const listing = normalizeMarketplaceListing(raw);
    const fulfillment = listingFulfillment(listing);
    return {
      id: listingId(listing),
      title: String(listing.title || "Untitled listing"),
      priceLabel: String(listing.price_label || ""),
      currency: String(listing.currency || "USD"),
      imageUrl: thumbnailOf(listing),
      badge: listingBadge(listing, options.now),
      saved: Boolean(listing.saved),
      sellerName: String(listing.seller_name || "PulseSoc Seller"),
      sellerUserId: Number(listing.seller_user_id || 0),
      category: String(listing.category || ""),
      fulfillment,
      action: gridCardAction(listing, options),
      distanceMeters: null,
      sellerRating: null,
      originalPriceLabel: null
    };
  });
}

/* ------------------------------------------------------------------ *
 * Selling list
 * ------------------------------------------------------------------ */

export type SellingTabKey = "active" | "sold" | "drafts" | "expired";

/**
 * Past this age with no sale, a listing is stale.
 *
 * Thirty days is a proposal, like the offer TTL — nothing in the codebase sets a
 * staleness horizon. It is deliberately long: nagging a seller about a listing
 * that is two weeks old teaches them to ignore the flag.
 */
export const STALE_LISTING_MS = 30 * DAY_MS;

export type SellingItemFlag =
  /** Selling well. Green, encouraging, no action. */
  | "attention"
  /** Old and quiet. Amber, with Renew and Drop price actions. */
  | "stale"
  /** Sold, and the buyer has not been rated. */
  | "rate_buyer"
  | null;

export type SellingItem = {
  id: number;
  title: string;
  thumbnailUrl: string | null;
  priceLabel: string;
  currency: string;
  health: StoreListingHealth;
  tab: SellingTabKey;
  sold: boolean;
  /** Age in ms, or null when `created_at` did not arrive (old cache). */
  ageMs: number | null;
  stale: boolean;
  flag: SellingItemFlag;
  unitsSold7d: number;
  // MOCK-DATA: no impressions endpoint.
  views: number | null;
  // MOCK-DATA: no per-listing saves aggregate.
  saves: number | null;
  // MOCK-DATA: offers have no backend.
  offerCount: number | null;
  // MOCK-DATA: no price history.
  originalPriceLabel: string | null;
};

function sellingTab(health: StoreListingHealth, sold: boolean, stale: boolean): SellingTabKey {
  if (sold) return "sold";
  if (health === "draft") return "drafts";
  // "Expired" is not a status this backend has. A hidden listing that is also
  // stale is the closest honest reading, and it is the only way the tab is
  // reachable at all — better than an empty tab that implies nothing expires.
  if (health === "hidden" && stale) return "expired";
  return "active";
}

function itemFlag(item: {
  sold: boolean;
  stale: boolean;
  unitsSold7d: number;
  health: StoreListingHealth;
}): SellingItemFlag {
  if (item.sold) return "rate_buyer";
  if (item.stale && item.health !== "draft") return "stale";
  // Two or more sales in a week is the threshold for "priced well". Below that
  // the claim is not supportable from one week of a single listing's data, and
  // an encouraging flag that is wrong is worse than no flag.
  if (item.unitsSold7d >= 2) return "attention";
  return null;
}

export function deriveSellingItems(
  snapshot: SellerStoreSnapshot,
  now: number
): SellingItem[] {
  const soldCounts = unitsSoldByListing(snapshot.orders, now);
  return snapshot.listings.map((raw) => {
    const listing = normalizeMarketplaceListing(raw);
    const id = listingId(listing);
    const health = listingHealth(listing);
    const ageMs = listingAgeMs(listing, now);
    const unitsSold7d = soldCounts.get(String(id)) || 0;
    // Out of stock with sales behind it is the closest thing to "sold" this
    // backend expresses for a one-off Marketplace item.
    const sold = health === "out_of_stock" && unitsSold7d > 0;
    const stale = ageMs != null && ageMs > STALE_LISTING_MS && unitsSold7d === 0;
    const tab = sellingTab(health, sold, stale);

    return {
      id,
      title: String(listing.title || "Untitled listing"),
      thumbnailUrl: thumbnailOf(listing),
      priceLabel: String(listing.price_label || ""),
      currency: String(listing.currency || "USD"),
      health,
      tab,
      sold,
      ageMs,
      stale,
      flag: itemFlag({ sold, stale, unitsSold7d, health }),
      unitsSold7d,
      views: null,
      saves: null,
      offerCount: null,
      originalPriceLabel: null
    };
  });
}

function unitsSoldByListing(
  orders: readonly MarketplaceSellerOrder[],
  now: number
): Map<string, number> {
  const cutoff = now - 7 * DAY_MS;
  const counts = new Map<string, number>();
  orders.forEach((order) => {
    if (!order.created_at) return;
    const at = Date.parse(String(order.created_at));
    if (Number.isNaN(at) || at < cutoff) return;
    const status = String(order.status || "").toLowerCase();
    if (status.includes("cancel") || status.includes("refund")) return;
    const key = String(order.item_id ?? "");
    if (!key) return;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return counts;
}

/** Tab counts, so the tab bar and the list cannot disagree about what is where. */
export function sellingTabCounts(items: readonly SellingItem[]): Record<SellingTabKey, number> {
  const counts: Record<SellingTabKey, number> = { active: 0, sold: 0, drafts: 0, expired: 0 };
  items.forEach((item) => {
    counts[item.tab] += 1;
  });
  return counts;
}

/**
 * The one listing, if any, worth offering a Boost on.
 *
 * The stalest active listing with no sales. One at a time, never for a healthy
 * listing, and null when nothing qualifies — which is the brief's "never more
 * than one, never for healthy listings" expressed as a return type rather than
 * as a rule someone has to remember at the render site.
 */
export function boostCandidate(items: readonly SellingItem[]): SellingItem | null {
  const stale = items.filter((item) => item.tab === "active" && item.stale);
  if (stale.length === 0) return null;
  return stale.reduce((worst, item) =>
    (item.ageMs ?? 0) > (worst.ageMs ?? 0) ? item : worst
  );
}

/* ------------------------------------------------------------------ *
 * Summary chips
 * ------------------------------------------------------------------ */

export type SellingSummary = {
  activeCount: number;
  /** Offers still awaiting an answer. Zero while the offers flag is off. */
  offersWaiting: number;
  // MOCK-DATA: no per-listing saves aggregate, so this is null and the chip
  // renders a dash rather than a number.
  savesThisWeek: number | null;
};

export function deriveSellingSummary(
  items: readonly SellingItem[],
  offersWaiting: number
): SellingSummary {
  return {
    activeCount: items.filter((item) => item.tab === "active").length,
    offersWaiting,
    savesThisWeek: null
  };
}

/* ------------------------------------------------------------------ *
 * Loading
 * ------------------------------------------------------------------ */

/**
 * One half of the screen's data. Modelled as a tagged union rather than as
 * `data | null` so that "loaded and empty" and "failed to load" stay distinct
 * all the way to the render site — the brief requires per-section inline retry,
 * and a null cannot tell you what to retry.
 */
export type MarketplaceSection<T> =
  | { status: "ok"; data: T }
  | { status: "error"; message: string };

export type MarketplaceLoadResult = {
  /** The buying feed. */
  feed: MarketplaceSection<MarketplaceListing[]>;
  /** The seller's own listings. */
  listings: MarketplaceSection<MarketplaceListing[]>;
  /** Seller orders, for the trailing-7-day sale counts. */
  orders: MarketplaceSection<MarketplaceSellerOrder[]>;
  /** ISO timestamp of the cache, when serving from it. */
  cachedAt: string | null;
  offline: boolean;
};

/**
 * Load both modes at once.
 *
 * Both, not one, and deliberately: the mode toggle must not trigger a fetch, or
 * switching modes would feel like navigating — the exact thing the brief says it
 * must not feel like. Two requests on mount buys an instant toggle for the rest
 * of the session.
 *
 * `Promise.allSettled` because the three halves fail independently and a failed
 * feed must not take the seller's own listings down with it. The brief's rule
 * that "browse feed failure must not hide the category rail or search" is
 * enforced here, at the point where the failure is known, rather than left to
 * the screen to remember.
 */
export async function loadMarketplaceScreen(
  options: { query?: string; limit?: number } = {}
): Promise<MarketplaceLoadResult> {
  const [feed, listings, orders] = await Promise.allSettled([
    searchMarketplace({ query: options.query, limit: options.limit ?? 24 }),
    listMarketplaceSellerListings({ limit: 80 }),
    listMarketplaceSellerOrders()
  ]);

  const allFailed =
    feed.status === "rejected" && listings.status === "rejected" && orders.status === "rejected";

  if (allFailed) {
    // Everything is down. Serve the cache so the screen is populated and stale
    // rather than empty and correct, and let the caller label it — the brief's
    // "cached feed with a last-updated note".
    const [cachedFeed, cachedStore] = await Promise.all([
      loadCachedMarketplace().catch(() => [] as MarketplaceListing[]),
      loadCachedSellerStore().catch(() => null)
    ]);
    const haveSomething =
      cachedFeed.length > 0 || (cachedStore?.listings?.length ?? 0) > 0;
    if (haveSomething) {
      return {
        feed: { status: "ok", data: cachedFeed },
        listings: { status: "ok", data: cachedStore?.listings || [] },
        orders: { status: "ok", data: cachedStore?.orders || [] },
        cachedAt: cachedStore?.cached_at || null,
        offline: true
      };
    }
  }

  return {
    feed:
      feed.status === "fulfilled"
        ? { status: "ok", data: feed.value.items || [] }
        : { status: "error", message: "Nearby items didn't load." },
    listings:
      listings.status === "fulfilled"
        ? { status: "ok", data: listings.value.items || [] }
        : { status: "error", message: "Your items didn't load." },
    orders:
      orders.status === "fulfilled"
        ? { status: "ok", data: orders.value.orders || [] }
        : { status: "error", message: "Sales didn't load." },
    cachedAt: null,
    offline: false
  };
}

/** Assembles a seller snapshot from whichever halves succeeded. */
export function sellerSnapshotFrom(result: MarketplaceLoadResult): SellerStoreSnapshot {
  return {
    listings: result.listings.status === "ok" ? result.listings.data : [],
    orders: result.orders.status === "ok" ? result.orders.data : [],
    cached_at: result.cachedAt || undefined
  };
}

/* ------------------------------------------------------------------ *
 * Last-used mode
 * ------------------------------------------------------------------ */

const MODE_KEY = "pulsesoc.native.marketplace.mode";

/**
 * Which side the user was on last time.
 *
 * Persisted because the two modes are two different jobs, and a seller who opens
 * this screen ten times a day to check offers should not land on a buying feed
 * ten times a day. Reads default to `"selling"` — the screen is reached from the
 * *business* dashboard, so selling is the reason you are here unless you have
 * said otherwise.
 */
export async function loadLastMarketplaceMode(): Promise<"selling" | "buying"> {
  try {
    const stored = await AsyncStorage.getItem(MODE_KEY);
    return stored === "buying" ? "buying" : "selling";
  } catch {
    return "selling";
  }
}

export async function saveLastMarketplaceMode(mode: "selling" | "buying"): Promise<void> {
  try {
    await AsyncStorage.setItem(MODE_KEY, mode);
  } catch {
    // A failed preference write is not worth surfacing: the cost is landing on
    // the wrong tab once, and there is nothing the user could do about it.
  }
}
