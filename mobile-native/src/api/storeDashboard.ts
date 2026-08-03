/**
 * Derivation layer for the seller Store dashboard.
 *
 * Everything the Store screen shows is computed here from the existing
 * `SellerStoreSnapshot` — the same listings and orders the current screen
 * already loads. No new endpoint is introduced.
 *
 * Two deliberate boundaries:
 *
 * 1. **This module returns numbers, not strings.** Currency, percentages,
 *    counts and dates are formatted in the component layer through
 *    `useFormatters`, so nothing here hardcodes a `$` or a decimal separator.
 *    It also makes every figure below testable without a locale.
 * 2. **A figure with no backend source is absent, not invented.** The reference
 *    design asks for storefront views, a seller rating, on-time dispatch and
 *    per-listing star ratings. None of those exist in any API this app talks
 *    to. Rather than render a plausible-looking number, the corresponding field
 *    is `null` and the UI omits the tile. Every one of those is tagged
 *    `MOCK-DATA:` below with the backend work it needs, and
 *    `STORE_MOCK_DATA_GAPS` collects them so the report can be generated from
 *    the code rather than from memory.
 */

import {
  listMarketplaceSellerListings,
  listMarketplaceSellerOrders,
  loadCachedSellerStore,
  type MarketplaceListing,
  type MarketplaceSellerOrder,
  type SellerStoreSnapshot
} from "./marketplace";

/* ------------------------------------------------------------------ *
 * Unsourced fields
 * ------------------------------------------------------------------ */

export type StoreDataGap = {
  /** What the reference design asks for. */
  field: string;
  /** Where it would have to come from. */
  needs: string;
};

/**
 * The complete list of things the reference design shows that this app has no
 * source for. Exported so it can be asserted in a test: if someone later fakes
 * one of these, the count changes and the test says so.
 */
export const STORE_MOCK_DATA_GAPS: readonly StoreDataGap[] = [
  // MOCK-DATA: storefront view counts. Needs a seller-facing impressions
  // endpoint; nothing in the marketplace API reports views.
  { field: "Views · 7 days", needs: "seller impressions endpoint (views per storefront per day)" },
  // MOCK-DATA: seller rating. Needs buyer reviews aggregated per seller.
  { field: "Seller rating", needs: "per-seller review aggregate (mean rating + count)" },
  // MOCK-DATA: on-time dispatch percentage. Needs a promised-ship-by date on
  // the order and a recorded dispatch timestamp; orders carry neither.
  { field: "On-time dispatch %", needs: "order.ship_by and order.dispatched_at" },
  // MOCK-DATA: "N ship today". Same missing field as above — without a
  // ship-by date there is no way to know which open orders are due.
  { field: "Open orders — N ship today", needs: "order.ship_by" },
  // MOCK-DATA: per-listing stars and review counts. Same aggregate as the
  // seller rating, keyed by listing.
  { field: "Listing rating and review count", needs: "per-listing review aggregate" },
  // MOCK-DATA: store open/paused. Listings can be paused individually, but
  // there is no store-level switch, so the strip reports "open" unless every
  // listing is paused. A real flag would be authoritative.
  { field: "Store open / paused", needs: "seller-level storefront status flag" },
  // MOCK-DATA: "no stock tracked" vs "zero in stock". `normalizeMarketplaceListing`
  // coerces quantity with `Number(item.quantity || 0)`, so the two collapse into
  // 0 before this module sees them. `product_type` is used as a stand-in below,
  // which is right for digital, course and service listings but cannot express a
  // physical listing whose seller simply does not track stock.
  {
    field: "Stock tracked / not tracked",
    needs: "quantity preserved as null through listing normalization, or an explicit tracks_stock flag"
  }
] as const;

/* ------------------------------------------------------------------ *
 * Listing health
 * ------------------------------------------------------------------ */

/**
 * Stock and visibility state for one listing. Named rather than derived at the
 * render site because the tab counts, the attention banner and the row LED all
 * have to agree on what "low" means.
 */
export type StoreListingHealth = "in_stock" | "low_stock" | "out_of_stock" | "hidden" | "draft";

/**
 * At or below this quantity a listing is "low". A single named threshold so the
 * banner, the tab count and the row never disagree.
 */
export const LOW_STOCK_THRESHOLD = 5;

function normalizedStatus(listing: MarketplaceListing): string {
  return String(listing.status || listing.approval_status || "draft").toLowerCase();
}

/**
 * Product types that are not shipped and therefore have no stock count. A
 * course does not run out.
 */
const STOCKLESS_PRODUCT_TYPES = ["digital", "course", "service"];

/**
 * A listing's stock count, or `null` when the listing does not have one.
 *
 * Two traps, both of which turn "this listing has no stock concept" into
 * "out of stock" and hide the listing from the seller's own active tab:
 *
 * 1. `Number(null)` is `0`, not `NaN`. A JSON payload reporting
 *    `"quantity": null` reads as zero unless it is checked before coercion.
 * 2. More importantly, `normalizeMarketplaceListing` has *already* applied
 *    `Number(item.quantity || 0)` by the time any listing reaches this module,
 *    so an absent quantity is indistinguishable from a real zero. The
 *    normalizer is shared with several screens and is left alone.
 *
 * `product_type` is the signal that survives normalization, so it is checked
 * first. A physical listing still falls through to its quantity, which is what
 * the tabs and the attention banner are counting.
 */
function stockCount(listing: MarketplaceListing): number | null {
  const productType = String(listing.product_type || "").toLowerCase();
  if (STOCKLESS_PRODUCT_TYPES.some((type) => productType.includes(type))) return null;

  const raw: unknown = listing.quantity;
  if (raw === null || raw === undefined || raw === "") return null;
  const quantity = Number(raw);
  return Number.isFinite(quantity) ? quantity : null;
}

/**
 * Follows the same status vocabulary `SellerStoreScreen.statusKey` already uses,
 * so the two screens cannot disagree about what a listing is.
 */
export function listingHealth(listing: MarketplaceListing): StoreListingHealth {
  const status = normalizedStatus(listing);
  if (status.includes("draft")) return "draft";
  if (
    status.includes("pause") ||
    status.includes("reject") ||
    status.includes("blocked") ||
    status.includes("removed") ||
    status.includes("delete")
  ) {
    return "hidden";
  }
  const quantity = stockCount(listing);
  // A digital or service listing has no meaningful stock count. Treating an
  // absent quantity as zero would mark every course in the store out of stock.
  if (quantity === null) return status.includes("stock") ? "out_of_stock" : "in_stock";
  if (quantity <= 0) return "out_of_stock";
  if (quantity <= LOW_STOCK_THRESHOLD) return "low_stock";
  return "in_stock";
}

/** Health states that need the seller to do something. Drives the banner. */
const NEEDS_ATTENTION: readonly StoreListingHealth[] = ["low_stock", "out_of_stock"];

/* ------------------------------------------------------------------ *
 * Rows
 * ------------------------------------------------------------------ */

export type StoreListingRow = {
  id: number;
  title: string;
  thumbnailUrl: string | null;
  priceLabel: string;
  currency: string;
  quantity: number | null;
  health: StoreListingHealth;
  /** Units of this listing sold in the trailing 7 days. Derived from orders. */
  unitsSold7d: number;
  // MOCK-DATA: no review aggregate exists, so these stay null and the row
  // renders without a star line rather than with an invented one.
  rating: number | null;
  reviewCount: number | null;
};

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

/* ------------------------------------------------------------------ *
 * Time helpers
 * ------------------------------------------------------------------ */

/** Local-midnight day index, so "today" means the seller's today. */
function dayIndex(date: Date): number {
  return Math.floor(
    new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime() / 86_400_000
  );
}

function orderDate(order: MarketplaceSellerOrder): Date | null {
  if (!order.created_at) return null;
  const parsed = new Date(order.created_at);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function orderMinorAmount(order: MarketplaceSellerOrder): number {
  const amount = Number(order.gross_amount_cents ?? order.amount_cents ?? 0);
  return Number.isFinite(amount) ? amount : 0;
}

/** Orders that have not been fulfilled yet. */
const OPEN_ORDER_STATUSES = ["pending", "paid", "processing", "awaiting", "confirmed"];

function isOpenOrder(order: MarketplaceSellerOrder): boolean {
  const status = String(order.status || "pending").toLowerCase();
  if (status.includes("cancel") || status.includes("refund")) return false;
  if (status.includes("complete") || status.includes("delivered") || status.includes("fulfilled")) {
    return false;
  }
  return OPEN_ORDER_STATUSES.some((candidate) => status.includes(candidate));
}

/* ------------------------------------------------------------------ *
 * KPIs
 * ------------------------------------------------------------------ */

export type StoreKpis = {
  /** Today's gross, in minor units. Formatted by the caller. */
  salesTodayMinor: number;
  currency: string;
  /**
   * Change against the *same weekday* last week, as a ratio (0.12 = +12%).
   * Same weekday rather than yesterday because a store's Saturday and its
   * Tuesday are not comparable. `null` when there is no last-week baseline —
   * a store's first week should not report "+100%".
   */
  salesTrend: number | null;
  /** Seven daily totals, oldest first, for the sparkline. */
  sparkline: number[];
  openOrders: number;
  // MOCK-DATA: needs order.ship_by.
  shippingToday: number | null;
  // MOCK-DATA: needs a seller impressions endpoint.
  views7d: number | null;
  viewsTrend: number | null;
  // MOCK-DATA: needs a per-seller review aggregate.
  sellerRating: number | null;
  // MOCK-DATA: needs order.ship_by and order.dispatched_at.
  onTimeDispatch: number | null;
};

/**
 * `now` is injected rather than read from the clock so the whole KPI block is
 * testable, and so a cached snapshot can be rendered against the time it was
 * captured rather than against the time the app was reopened.
 */
export function deriveKpis(
  snapshot: SellerStoreSnapshot,
  now: Date = new Date()
): StoreKpis {
  const today = dayIndex(now);
  const currency = String(snapshot.orders.find((order) => order.currency)?.currency || "USD");

  const byDay = new Map<number, number>();
  snapshot.orders.forEach((order) => {
    const date = orderDate(order);
    if (!date) return;
    const day = dayIndex(date);
    byDay.set(day, (byDay.get(day) || 0) + orderMinorAmount(order));
  });

  const salesTodayMinor = byDay.get(today) || 0;
  const lastWeekSameDay = byDay.get(today - 7);
  const salesTrend =
    lastWeekSameDay && lastWeekSameDay > 0
      ? (salesTodayMinor - lastWeekSameDay) / lastWeekSameDay
      : null;

  // Oldest first, so the sparkline reads left to right like a calendar.
  const sparkline = Array.from({ length: 7 }, (_, offset) => byDay.get(today - 6 + offset) || 0);

  return {
    salesTodayMinor,
    currency,
    salesTrend,
    sparkline,
    openOrders: snapshot.orders.filter(isOpenOrder).length,
    shippingToday: null,
    views7d: null,
    viewsTrend: null,
    sellerRating: null,
    onTimeDispatch: null
  };
}

/* ------------------------------------------------------------------ *
 * Listing rows and tabs
 * ------------------------------------------------------------------ */

/** Units sold per listing over the trailing 7 days, keyed by listing id. */
function unitsSoldByListing(orders: MarketplaceSellerOrder[], now: Date): Map<string, number> {
  const cutoff = dayIndex(now) - 6;
  const counts = new Map<string, number>();
  orders.forEach((order) => {
    const date = orderDate(order);
    if (!date || dayIndex(date) < cutoff) return;
    const status = String(order.status || "").toLowerCase();
    if (status.includes("cancel") || status.includes("refund")) return;
    const key = String(order.item_id ?? "");
    if (!key) return;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return counts;
}

export function deriveRows(
  snapshot: SellerStoreSnapshot,
  now: Date = new Date()
): StoreListingRow[] {
  const sold = unitsSoldByListing(snapshot.orders, now);
  return snapshot.listings.map((listing) => {
    const id = Number(listing.listing_id ?? listing.id);
    return {
      id,
      title: String(listing.title || listing.short_description || "Untitled listing"),
      thumbnailUrl: thumbnailOf(listing),
      priceLabel: String(listing.price_label || ""),
      currency: String(listing.currency || "USD"),
      quantity: stockCount(listing),
      health: listingHealth(listing),
      unitsSold7d: sold.get(String(id)) || 0,
      rating: null,
      reviewCount: null
    };
  });
}

export type StoreTabKey = "all" | "active" | "low" | "out" | "drafts";

export type StoreTab = {
  key: StoreTabKey;
  count: number;
  /** True when the count is a problem, so the UI can colour it. */
  needsAttention: boolean;
};

const TAB_MATCHERS: Record<StoreTabKey, (row: StoreListingRow) => boolean> = {
  all: () => true,
  active: (row) => row.health === "in_stock" || row.health === "low_stock",
  low: (row) => row.health === "low_stock",
  out: (row) => row.health === "out_of_stock" || row.health === "hidden",
  drafts: (row) => row.health === "draft"
};

export function filterRows(rows: StoreListingRow[], tab: StoreTabKey): StoreListingRow[] {
  return rows.filter(TAB_MATCHERS[tab]);
}

export function deriveTabs(rows: StoreListingRow[]): StoreTab[] {
  return (Object.keys(TAB_MATCHERS) as StoreTabKey[]).map((key) => ({
    key,
    count: rows.filter(TAB_MATCHERS[key]).length,
    // Only the two problem tabs colour their count. "All" being large is good
    // news, and colouring it would spend the reader's alarm on nothing.
    needsAttention: (key === "low" || key === "out") && rows.some(TAB_MATCHERS[key])
  }));
}

/* ------------------------------------------------------------------ *
 * Attention banner
 * ------------------------------------------------------------------ */

export type StoreAttention = {
  count: number;
  /** The tab the "Fix now" link should open. */
  target: StoreTabKey;
  kind: "out_of_stock" | "low_stock";
};

/**
 * Returns `null` when nothing needs attention, and the screen then renders no
 * banner at all — the spec is explicit that a permanently present banner is
 * worse than none, because a seller learns to look past it.
 *
 * Out-of-stock outranks low-stock: a listing buyers cannot order is a live loss,
 * where a low one is a warning.
 */
export function deriveAttention(rows: StoreListingRow[]): StoreAttention | null {
  const out = rows.filter((row) => row.health === "out_of_stock").length;
  if (out > 0) return { count: out, target: "out", kind: "out_of_stock" };
  const low = rows.filter((row) => row.health === "low_stock").length;
  if (low > 0) return { count: low, target: "low", kind: "low_stock" };
  return null;
}

/** Convenience for tests and for the banner copy. */
export function attentionCount(rows: StoreListingRow[]): number {
  return rows.filter((row) => NEEDS_ATTENTION.includes(row.health)).length;
}

export type StoreHealthCounts = {
  /** Listings a buyer can order right now — in stock or low, but not zero. */
  active: number;
  low: number;
  out: number;
};

/**
 * The three numbers any summary of this store needs. `deriveTabs` already
 * produces them, but keyed by tab, and a caller that wants "how is the store
 * doing" should not have to know the tab vocabulary to find out.
 *
 * Added for the Business Hub's Store card, which states the same thing in one
 * line that the Store dashboard states in a banner and a tab bar. Sharing this
 * function is what keeps the two from disagreeing.
 */
export function storeHealthCounts(rows: readonly StoreListingRow[]): StoreHealthCounts {
  let active = 0;
  let low = 0;
  let out = 0;
  for (const row of rows) {
    if (row.health === "low_stock") low += 1;
    if (row.health === "out_of_stock") out += 1;
    if (row.health === "in_stock" || row.health === "low_stock") active += 1;
  }
  return { active, low, out };
}

/* ------------------------------------------------------------------ *
 * Store status
 * ------------------------------------------------------------------ */

export type StoreStatus = { open: boolean };

/**
 * MOCK-DATA: there is no seller-level storefront switch in the API. The closest
 * honest reading is that a store with listings, none of which are orderable, is
 * effectively paused. A store with no listings at all is not paused — it is
 * empty, which is a different screen state.
 */
export function deriveStatus(rows: StoreListingRow[]): StoreStatus {
  if (rows.length === 0) return { open: true };
  return { open: rows.some((row) => row.health === "in_stock" || row.health === "low_stock") };
}

/* ------------------------------------------------------------------ *
 * Loading, with per-section outcomes
 * ------------------------------------------------------------------ */

export type StoreSectionState<T> =
  | { status: "ok"; data: T }
  | { status: "error"; message: string };

export type StoreLoadResult = {
  listings: StoreSectionState<MarketplaceListing[]>;
  orders: StoreSectionState<MarketplaceSellerOrder[]>;
  /** Set when the payload came from cache because the network was unavailable. */
  cachedAt: string | null;
  offline: boolean;
};

/**
 * Loads the two halves of the snapshot *separately*.
 *
 * `loadSellerStoreSnapshot` uses `Promise.allSettled` and turns a failed call
 * into an empty array, which makes "orders failed" indistinguishable from "no
 * orders yet". The spec requires per-section inline retry and forbids a vague
 * "Something went wrong", so this needs to know which half broke. The existing
 * loader is left alone — other screens depend on its behaviour.
 */
export async function loadStoreDashboard(): Promise<StoreLoadResult> {
  const [listings, orders] = await Promise.allSettled([
    listMarketplaceSellerListings({ limit: 80 }),
    listMarketplaceSellerOrders()
  ]);

  const bothFailed = listings.status === "rejected" && orders.status === "rejected";
  if (bothFailed) {
    // Everything is down. Fall back to the cache so the seller sees their store
    // rather than an error page, and let the caller label it as stale.
    const cached = await loadCachedSellerStore().catch(() => null);
    if (cached && (cached.listings.length > 0 || cached.orders.length > 0)) {
      return {
        listings: { status: "ok", data: cached.listings },
        orders: { status: "ok", data: cached.orders },
        cachedAt: cached.cached_at || null,
        offline: true
      };
    }
  }

  return {
    listings:
      listings.status === "fulfilled"
        ? { status: "ok", data: listings.value.items || [] }
        : { status: "error", message: "Listings didn't load." },
    orders:
      orders.status === "fulfilled"
        ? { status: "ok", data: orders.value.orders || [] }
        : { status: "error", message: "Orders didn't load." },
    cachedAt: null,
    offline: false
  };
}

/** Assembles a snapshot from whichever halves succeeded. */
export function snapshotFrom(result: StoreLoadResult): SellerStoreSnapshot {
  return {
    listings: result.listings.status === "ok" ? result.listings.data : [],
    orders: result.orders.status === "ok" ? result.orders.data : [],
    cached_at: result.cachedAt || undefined
  };
}
