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
import { isFlagValueOn } from "../core/envFlag";

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
  },
  // MOCK-DATA: store restriction and suspension. `storeReadiness` has five rungs
  // where the review's ladder has seven; Restricted and Suspended are missing
  // because no seller-level enforcement flag reaches this app. Guessing them
  // from listing statuses would tell a seller they were suspended whenever a
  // moderator happened to reject their whole catalogue.
  {
    field: "Store restricted / suspended",
    needs: "a seller-level enforcement state (restricted, suspended) with the reason and the appeal route"
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
const STOCKLESS_PRODUCT_TYPES = ["digital", "course", "service", "event", "booking"];

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
  const publication = String(listing.publication_state || listing.status || "").toLowerCase();
  if (!["published", "live", "active"].includes(publication)) {
    return publication.includes("draft") ? "draft" : "hidden";
  }
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
 *
 * @deprecated Superseded by {@link storeReadiness}. `{open: true}` for a store
 * with no listings is the exact claim ADR-0003 names as wrong: an empty store
 * is not an open store, it is an unconfigured one. Kept because other callers
 * still read it, and removing a two-state answer while the five-state one is
 * behind a flag would leave those callers with nothing.
 */
export function deriveStatus(rows: StoreListingRow[]): StoreStatus {
  if (rows.length === 0) return { open: true };
  return { open: rows.some((row) => row.health === "in_stock" || row.health === "low_stock") };
}

/* ------------------------------------------------------------------ *
 * Readiness ladder
 * ------------------------------------------------------------------ */

export const STORE_READINESS_FLAG = "EXPO_PUBLIC_STORE_READINESS";

/**
 * True when a build has opted into the readiness ladder. Off by default.
 *
 * This accessor is the reason `core/envFlag.ts` exists. It shipped accepting
 * the literal `1` and nothing else, while every flag that preceded it also took
 * `true`, `on` and `yes` — so a build that set this one to `true` got a silent
 * no-op with no way to tell a stricter parser from a ladder that did not work.
 * It reads the shared set now. Still off unless somebody sets it.
 *
 * The read spells the variable out rather than passing `STORE_READINESS_FLAG`,
 * which holds the same string. `babel-preset-expo` inlines `process.env.X` only
 * when the key is a StringLiteral, so reading through the constant left a lookup
 * in the bundle that found nothing. This is the one flag `eas.json` actually
 * sets — `"1"` on development, development-simulator and preview — and preview
 * is a release build, so the ladder was dark in the exact artifact that asked
 * for it. The constant stays because the tests and the report name it.
 */
export function storeReadinessEnabled(): boolean {
  return isFlagValueOn(process.env.EXPO_PUBLIC_STORE_READINESS);
}

/**
 * Where a store actually stands, as one value from a closed ladder.
 *
 * `deriveStatus` answered a yes/no question — open, or paused — and a store
 * that had never been set up came back "open". The strip then read
 * "Open for orders" over an empty catalogue, which is the single dishonest
 * sentence this correction exists to remove.
 *
 * Five rungs, in the order a store climbs them:
 *
 * * `not_set_up` — nothing has been listed. Not paused; never started.
 * * `incomplete` — listings exist but every one is still a draft.
 * * `pending_review` — something has been sent for review and nothing is
 *   orderable yet. Nothing is required of the seller at this rung.
 * * `live` — at least one listing a buyer can order right now.
 * * `paused` — listings exist, none is a draft, none is in review, and none is
 *   orderable. This is the only rung `deriveStatus` got right.
 *
 * `restricted` and `suspended` from the review's ladder are deliberately absent:
 * there is no seller-level suspension flag anywhere in the marketplace data, so
 * a rung for it could only ever be guessed at from listing statuses. Recorded as
 * a gap rather than shipped as a state that would be wrong whenever it fired.
 */
export type StoreReadiness = "not_set_up" | "incomplete" | "pending_review" | "live" | "paused";

/**
 * Listing statuses that mean "somebody is looking at this".
 *
 * Read from `approval_status` before `status` because the two are different
 * questions — a listing can be active *and* awaiting approval — and only the
 * first one is about review.
 */
const REVIEW_STATUSES = ["pending", "review", "submitted", "awaiting"];

/**
 * Whether this listing is waiting on a decision.
 *
 * `listingHealth` cannot answer this and is deliberately left alone: it maps an
 * in-review listing to `in_stock`, which is right for the tab counts (the seller
 * has one item, with stock) and wrong for readiness (a buyer cannot order it
 * yet). Two questions, two functions, rather than one function that is subtly
 * wrong for one of its callers.
 */
export function listingAwaitsReview(listing: MarketplaceListing): boolean {
  const approval = String(listing.approval_status || "").toLowerCase();
  if (approval) return REVIEW_STATUSES.some((candidate) => approval.includes(candidate));
  const status = String(listing.status || "").toLowerCase();
  return REVIEW_STATUSES.some((candidate) => status.includes(candidate));
}

/** What a checklist step's button does. The screen maps these to navigation. */
export type StoreSetupActionKey =
  | "add_listing"
  | "open_drafts"
  | "open_out_of_stock"
  | "preview_storefront";

export type StoreSetupStep = {
  key: "add" | "publish" | "stock" | "review";
  label: string;
  detail: string;
  complete: boolean;
  /**
   * `null` when the step is done, or when waiting is the only thing left to do.
   * A checklist row whose button does nothing is the dead control this tier
   * exists to remove, so a step with no action carries no button at all.
   */
  action: { key: StoreSetupActionKey; label: string } | null;
};

export type StoreReadinessState = {
  readiness: StoreReadiness;
  /** One sentence saying where the store stands, in the seller's terms. */
  headline: string;
  /** The short form for the status strip, e.g. "Waiting on review". */
  statusLabel: string;
  /** The strip's one control. Never absent — every rung has a next move. */
  action: { key: StoreSetupActionKey; label: string };
  /** True only at `live`. This is what the green dot may be bound to. */
  openForOrders: boolean;
  steps: StoreSetupStep[];
  /** How many steps are still outstanding. 0 means the checklist can collapse. */
  remaining: number;
};

/**
 * Derive the ladder, the strip copy and the checklist from real listings.
 *
 * Everything below is read from data the screen already loads — no new call and
 * no new field. That is the point: the previous "Open for orders" was not
 * missing a source, it was ignoring the one it had.
 *
 * The raw listings are taken alongside the derived rows because review state
 * survives only on the listing (see {@link listingAwaitsReview}); rows carry
 * stock and visibility, which is what the rest of the screen needs.
 */
export function storeReadiness(input: {
  listings: readonly MarketplaceListing[];
  rows: readonly StoreListingRow[];
}): StoreReadinessState {
  const rows = input.rows;
  const listings = input.listings;

  const orderable = rows.filter((row) => row.health === "in_stock" || row.health === "low_stock");
  const drafts = rows.filter((row) => row.health === "draft");
  const inReview = listings.filter(listingAwaitsReview);

  // A listing that is both orderable and awaiting review is not yet on sale, so
  // it cannot be what makes a store live.
  const reviewIds = new Set(inReview.map((listing) => Number(listing.listing_id ?? listing.id)));
  const liveNow = orderable.filter((row) => !reviewIds.has(row.id));

  const readiness: StoreReadiness =
    rows.length === 0
      ? "not_set_up"
      : liveNow.length > 0
        ? "live"
        : inReview.length > 0
          ? "pending_review"
          : drafts.length > 0
            ? "incomplete"
            : "paused";

  const steps: StoreSetupStep[] = [
    {
      key: "add",
      label: "Add a listing",
      detail: "Your store has nothing in it until you list something.",
      complete: rows.length > 0,
      action: rows.length > 0 ? null : { key: "add_listing", label: "Add a listing" }
    },
    {
      key: "publish",
      label: "Take a listing out of draft",
      detail: "A draft is saved but hidden. Buyers only see listings you publish.",
      complete: rows.length > 0 && rows.length > drafts.length,
      action:
        rows.length > 0 && drafts.length > 0 ? { key: "open_drafts", label: "Open drafts" } : null
    },
    {
      key: "stock",
      label: "Keep something in stock",
      detail: "A listing with nothing left cannot be ordered, even while it is on show.",
      complete: orderable.length > 0,
      action:
        rows.length > 0 && orderable.length === 0
          ? { key: "open_out_of_stock", label: "Open out of stock" }
          : null
    }
  ];

  // The review step only appears while something is actually in review. A row
  // that is permanently ticked teaches the seller to stop reading the list.
  if (inReview.length > 0) {
    steps.push({
      key: "review",
      label: "Wait for the review to finish",
      detail: "Someone is checking what you sent. Nothing is needed from you.",
      complete: false,
      action: null
    });
  }

  const copy = READINESS_COPY[readiness];
  return {
    readiness,
    headline: copy.headline,
    statusLabel: copy.statusLabel,
    action: copy.action,
    openForOrders: readiness === "live",
    steps,
    remaining: steps.filter((step) => !step.complete).length
  };
}

const READINESS_COPY: Record<
  StoreReadiness,
  { headline: string; statusLabel: string; action: { key: StoreSetupActionKey; label: string } }
> = {
  not_set_up: {
    headline: "Your store isn't set up yet. There's nothing listed, so buyers can't order.",
    statusLabel: "Not set up yet",
    action: { key: "add_listing", label: "Add a listing" }
  },
  incomplete: {
    headline: "Everything you've added is still a draft, so none of it is on sale.",
    statusLabel: "Setup unfinished",
    action: { key: "open_drafts", label: "Finish setup" }
  },
  pending_review: {
    headline: "Your listings are being checked. Nothing is needed from you right now.",
    statusLabel: "Waiting on review",
    action: { key: "open_drafts", label: "Manage" }
  },
  live: {
    headline: "Your store is open. Buyers can order from you now.",
    statusLabel: "Open for orders",
    action: { key: "preview_storefront", label: "Manage" }
  },
  paused: {
    headline: "Nothing in your store can be ordered right now.",
    statusLabel: "Paused — buyers can't order",
    action: { key: "open_out_of_stock", label: "Reopen" }
  }
};

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
