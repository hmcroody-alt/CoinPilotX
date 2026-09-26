import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { PulseAuthor, PulseMedia, mediaDisplayUrl } from "./feed";
import {
  readCheckoutHandoff,
  type CheckoutHandoff,
  type CheckoutResponse,
  type MarketplacePaymentMode
} from "./marketplaceCommerce";
import { pulseApi, PulseApiError } from "./pulseApi";
import { sellerStoreName, sellerStoreNameOrEmpty } from "./sellerIdentity";

const MARKETPLACE_CACHE_KEY = "pulsesoc.native.marketplace.search";
const SELLER_STORE_CACHE_KEY = "pulsesoc.native.marketplace.seller_store";

/**
 * What the listing's own merchant is told about the last review decision.
 *
 * Mirrors `services/business_os/marketplace/listing_review.seller_verdict`. The
 * field this interface does NOT declare is the point: there is no
 * `moderation_reason`, because the reviewer's free-text note is internal (§43)
 * and the server does not send it. Leaving it undeclared makes
 * `review.moderation_reason` a type error rather than a choice — the same
 * device used for `safety_score` on {@link MarketplaceListing}.
 */
export interface ListingReviewVerdict {
  /** `pending_review`, `rejected`, `changes_requested`, `restricted`, … */
  state: string;
  /** A reviewer has ruled on this version. */
  decided: boolean;
  /** The seller must change something before this can sell. */
  needs_action: boolean;
  /** The structured category, e.g. `INVALID_MEDIA`. Empty when undecided. */
  reason_code: string;
  /**
   * The sentence to show the seller — already resolved server-side, so this
   * build cannot be asked to render a code it has no words for. Empty when
   * there is no decision to report, which is NOT the same as a generic one:
   * telling a seller whose product is merely queued that it "needs a change"
   * sends them to edit something nobody has found fault with.
   */
  message: string;
  review_version: number;
  decided_at: string;
}

/**
 * The server's readiness verdict for one listing, rendered rather than derived.
 *
 * Mirrors `services/business_os/marketplace/listing_readiness.evaluate` exactly.
 * The two booleans are computed there because they are *rules*, and a rule
 * restated on the client is a copy that drifts: that is precisely how this app
 * came to believe an untracked quantity meant sold out. Render `publishable` and
 * `checkout_ready`; do not recompute them from the code arrays.
 *
 * `blockers` stop the listing being published. `warnings` are true of it but do
 * not — though some (an empty shelf, an uncounted one) still stop checkout,
 * which is why `checkout_ready` is its own boolean and not `blockers.length === 0`.
 */
/**
 * One thing the seller has to do, already written out by the server.
 *
 * `section` is which part of the editor fixes it, so a tapped blocker can open
 * the right place instead of dumping the seller at the top of a twelve-section
 * form. `label` is prose because four surfaces render these codes — the store
 * row, the Ready-to-Sell list, the bulk result and the single publish error —
 * and each one owning its own code→English table is four tables that drift.
 */
export type ListingFix = {
  code: string;
  label: string;
  section: string;
};

export type ListingReadiness = {
  publishable: boolean;
  /**
   * Whether this listing may go back to the review queue, which is a different
   * question from whether it may go live and is only ever true after a
   * rejection. A rejected listing is `publishable: false` by definition — the
   * rejection *is* the blocker — so a surface that gates the seller's button on
   * `publishable` alone locks them out of the one action the rejection is
   * asking for.
   *
   * False while anything else is still missing, and false for a product policy
   * refuses outright, so this can be rendered as an enabled button without
   * re-deriving either rule on the client.
   */
  resubmittable: boolean;
  checkout_ready: boolean;
  blockers: string[];
  warnings: string[];
  /** e.g. "2 things left", or "Ready to publish" when there are none. */
  summary: string;
  /** One entry per blocker, in blocker order. */
  fixes: ListingFix[];
  /**
   * One entry per warning, in warning order, worded and addressed exactly like
   * a fix — same table, same section map, different force.
   *
   * Kept out of `fixes` because these do not stop a publish and a surface that
   * cannot tell the two apart will either block on a low stock count or publish
   * over a missing price. Kept out of the readiness *codes* for rendering
   * because a `publishable` listing with an empty fix list draws an empty
   * Ready-to-Sell list above a green button — and if it is not
   * `checkout_ready`, that empty list is a lie the seller acts on.
   */
  notes: ListingFix[];
};

/**
 * Why a BULK action would refuse this row, or `null` when it would not.
 *
 * Readiness cannot answer this on its own, and the gap is not academic: a
 * finished, priced, already-live listing is `publishable: true` and must still
 * never be republished — doing so knocks it back into the review queue and
 * takes it off the storefront. So the state gate lives on the server beside
 * readiness, in `listing_batch.block_reason`, and the *same function* answers
 * both the preview drawn here and the batch that runs later. That is the only
 * arrangement in which "Publish 14 · 4 blocked" is a promise rather than a
 * guess.
 */
export type ListingBulkBlock = {
  code: string;
  /** Server-written, seller-facing, e.g. "Already published", "2 things left". */
  reason: string;
  blockers?: string[];
};

/** Keyed by action ("publish", "hide"). `null` means the action would apply. */
export type ListingBulkEligibility = Record<string, ListingBulkBlock | null>;

/** Verdict codes this client understands. The server may send others; readers
 *  must tolerate an unrecognised code rather than treating it as absent. */
export const READINESS_CODES = {
  MISSING_TITLE: "MISSING_TITLE",
  MISSING_DESCRIPTION: "MISSING_DESCRIPTION",
  MISSING_CATEGORY: "MISSING_CATEGORY",
  NO_VALID_MEDIA: "NO_VALID_MEDIA",
  MISSING_PRICE: "MISSING_PRICE",
  RESTRICTED_PRODUCT: "RESTRICTED_PRODUCT",
  UNKNOWN_INVENTORY: "UNKNOWN_INVENTORY",
  OUT_OF_STOCK: "OUT_OF_STOCK",
  LOW_STOCK: "LOW_STOCK"
} as const;

export type MarketplaceListing = {
  id: number;
  listing_id: number;
  seller_user_id?: number;
  /**
   * `seller_store_name` is the canonical buyer-facing identity the server
   * projects on every marketplace payload. `seller_name` is the legacy alias of
   * the same value, kept so older cached payloads still render. Read them
   * through `sellerStoreName()` in `./sellerIdentity` rather than directly.
   */
  seller_store_name?: string;
  seller_name?: string;
  seller_username?: string;
  seller_public_player_id?: string;
  title?: string;
  short_description?: string;
  description?: string;
  category?: string;
  subcategory?: string;
  price_label?: string;
  currency?: string;
  /**
   * Units in stock, or `null`/absent when the seller does not track stock.
   *
   * The null is LOAD-BEARING and must survive every hop. "Nobody counted this"
   * and "there are none left" are different facts with different fixes, and
   * `Number(x || 0)` collapses them into the second one — which is what told
   * sellers their untracked listings were sold out. `marketplace_listings.
   * quantity` is nullable, the server preserves it, and
   * `normalizeMarketplaceListing` now preserves it too.
   *
   * Prefer `readiness` over reading this directly: the server already says what
   * the number means.
   */
  quantity?: number | null;
  /**
   * The server's verdict on whether this listing can be sold —
   * `services/business_os/marketplace/listing_readiness.py`. Attached by the
   * seller route only; a buyer-facing payload never carries it, and a test
   * (`tests/marketplace/test_seller_listing_readiness_route.py`) asserts that.
   *
   * Optional because cached payloads written by older builds have none, and
   * because the public endpoints legitimately omit it. Absence means "not sent",
   * never "nothing wrong" — readers fall back to local derivation rather than
   * assuming a clean bill of health.
   */
  readiness?: ListingReadiness;
  /**
   * Why the reviewer decided what they decided. Seller route only — a buyer
   * payload never carries it, and `tests/marketplace/test_seller_review_verdict.py`
   * asserts that along with the containment of everything it deliberately
   * leaves out (the reviewer's note, the risk score, which admin decided).
   *
   * Absent means "not told", never "nothing wrong". Cached snapshots written
   * before this field existed have none, and a rejected listing in one of those
   * must keep reading as rejected rather than as fine.
   */
  review?: ListingReviewVerdict;
  /**
   * What each bulk action would do to this row. Seller route only, same as
   * `readiness`. Absent means "not sent" — a caller must treat that as not
   * eligible rather than as eligible, for the reason spelled out on
   * {@link ListingBulkBlock}.
   */
  bulk_eligibility?: ListingBulkEligibility;
  product_type?: string;
  /**
   * Internal moderation fields. `safety_score` is deliberately absent from this
   * interface: it is a reviewer signal, not a product attribute, so leaving it
   * undeclared makes `listing.safety_score` a type error rather than a choice.
   *
   * That is all it does. It is a rule about this app's source, not about the
   * response — the server sent the field regardless, for as long as
   * `pulse_marketplace_listing_payload` spread the database row into its
   * output, and the web cards printed it as "Safety N" (inverted: the column
   * holds risk, so 100 is the worst listing the engine scores). The wire is
   * pinned server-side now, in
   * `tests/web_parity/test_marketplace_reviewer_signal_not_buyer_facing.py`,
   * because that is the only place it can be measured rather than declared.
   *
   * `approval_status` stays because the seller's own store rows legitimately
   * show it back to the seller.
   */
  status?: string;
  approval_status?: string;
  publication_state?: string;
  publication_label?: string;
  /**
   * The canonical state, stamped by the server: `live`, `draft`,
   * `pending_review`, `suppressed` or `removed`. The single answer to "can a
   * buyer see this and buy it", shared with the seller metrics aggregate.
   * Absent only on seller payloads cached before the stamp existed.
   */
  listing_state?: string;
  /**
   * Why an approved, published listing is still unreachable — one of
   * `seller_approved`, `seller_named`, `in_stock`, or `""`. Derived by
   * `marketplace_listing_lifecycle.live_blocker` from the same rule table that
   * filters buyer discovery, so the client must not re-derive it: publication
   * has five conditions and only two of them are columns on the listing.
   */
  publication_blocker?: string;
  buyer_visible?: boolean;
  inventory_state?: string;
  saved?: boolean;
  is_saved?: boolean;
  cover_image_url?: string;
  image_url?: string;
  thumbnail_url?: string;
  video_url?: string;
  gallery_json?: string | string[];
  media?: PulseMedia[];
  media_assets?: PulseMedia[];
  /**
   * The four fields below live in `marketplace_listings` and were, until the
   * Marketplace screen needed them, absent from every SELECT — so they never
   * reached a client despite being real data. They are now selected by
   * `/marketplace/search`, `/seller/listings` and the seller-store query, and
   * `pulse_marketplace_listing_payload` spreads the row, so they arrive intact.
   *
   * They are optional because a cached payload written before that change has
   * none of them. Every reader must treat absence as "unknown" rather than as a
   * default — an item with no `created_at` is not new, and one with no
   * `delivery_type` gets no action button rather than a guessed one.
   */
  created_at?: string;
  updated_at?: string;
  /** 1 when the seller has an active Boost. Already drives search ordering. */
  featured?: number | boolean;
  /** 'digital' | 'physical' | 'pickup' | 'shipping' — drives the buying-card action. */
  delivery_type?: string;
  /**
   * The wizard's typed listing kind and its structured details. Optional on
   * every read path: listings created before the creation flow shipped — and
   * cached payloads from older builds — carry neither field.
   */
  listing_type?: MarketplaceListingType | string;
  listing_metadata?: ListingMetadata | Record<string, unknown>;
};

export type MarketplaceSearchResponse = {
  ok?: boolean;
  items?: MarketplaceListing[];
  listings?: MarketplaceListing[];
  query?: string;
  limit?: number;
  message?: string;
};

export type MarketplaceActionResponse = {
  ok?: boolean;
  message?: string;
  saved?: boolean;
  checkout_url?: string;
  transaction_id?: number;
  conversation_id?: number;
  thread_id?: number;
  next_url?: string;
  onboarding_url?: string;
  connected_account_id?: string;
};

export type MarketplaceSellerApplicationPayload = {
  display_name: string;
  bio: string;
};

/**
 * The five creation-flow listing types. `product_type` (below) is the older,
 * looser field the backend has always stored; `listing_type` is the typed
 * contract the native wizard and the backend agreed on together with
 * `listing_metadata`. Both are sent so older readers keep working.
 */
export type MarketplaceListingType = "physical" | "digital" | "service" | "event" | "booking";

export type MarketplaceListingVariant = { name: string; value: string };

export type PhysicalListingMetadata = {
  condition: string;
  variants: MarketplaceListingVariant[];
  delivery_options: "pickup" | "shipping" | "both";
  location: string;
  return_policy: string;
};

export type MarketplaceDigitalFile = {
  file_id: number;
  name: string;
  size_bytes: number;
};

export type DigitalListingMetadata = {
  files: MarketplaceDigitalFile[];
  delivery: "automatic";
  license: string;
  download_limit: number | null;
};

export type ServiceListingAddon = { title: string; price_label: string };

export type ServiceListingMetadata = {
  pricing_mode: "fixed" | "starting_at" | "hourly";
  delivery_time_days: number;
  service_location: "remote" | "in_person" | "both";
  location: string;
  included: string[];
  addons: ServiceListingAddon[];
};

export type EventListingTicket = { name: string; price_label: string; capacity: number };

export type EventListingMetadata = {
  event_date: string;
  start_time: string;
  end_time: string;
  venue_mode: "in_person" | "online" | "pulsesoc_live";
  location: string;
  online_url: string;
  tickets: EventListingTicket[];
};

export type BookingWeekday = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";

export type BookingTimeRange = { start: string; end: string };

export type BookingAvailability = Record<BookingWeekday, BookingTimeRange[]>;

export type BookingListingMetadata = {
  duration_minutes: number;
  meeting_mode: "video" | "audio" | "in_person";
  availability: BookingAvailability;
  buffer_minutes: number;
  cancellation_policy: string;
};

export type ListingMetadata =
  | PhysicalListingMetadata
  | DigitalListingMetadata
  | ServiceListingMetadata
  | EventListingMetadata
  | BookingListingMetadata;

export type MarketplaceListingCreatePayload = {
  submission_action?: "draft" | "submit";
  title: string;
  short_description?: string;
  description: string;
  category?: string;
  subcategory?: string;
  price_label?: string;
  currency?: string;
  quantity?: number;
  product_type?: "digital" | "physical" | "course" | "service" | "event" | "booking";
  listing_type?: MarketplaceListingType;
  listing_metadata?: ListingMetadata;
  media_ids?: number[];
  tags?: string;
  refund_policy?: string;
  estimated_delivery?: string;
  seller_notes?: string;
};

export type MarketplaceListingCreateResponse = MarketplaceActionResponse & {
  listing_id?: number;
};

/**
 * The update route is a true PATCH: a key that is absent keeps its stored
 * value, so a caller only sends what the seller actually changed.
 */
export type MarketplaceListingUpdatePayload = Partial<Omit<MarketplaceListingCreatePayload, "submission_action">>;

export type MarketplaceListingMutationResponse = MarketplaceActionResponse & {
  listing?: MarketplaceListing;
};

export type MarketplaceSellerOrder = {
  id?: number;
  item_type?: string;
  item_id?: number | string;
  amount_cents?: number;
  gross_amount_cents?: number;
  currency?: string;
  status?: string;
  /** The lane this order was placed on — see `BuyerOrder.fulfillment_kind`. */
  fulfillment_kind?: string;
  created_at?: string;
  commercial_economics?: MarketplaceOrderEconomics | null;
};

export type MarketplaceOrderEconomics = {
  merchandise_net_minor?: number; seller_shipping_credit_minor?: number;
  gross_platform_fee_minor?: number; fee_reversed_minor?: number;
  seller_reversed_minor?: number; net_seller_earnings_minor?: number;
  fee_rate_bps?: number; fee_policy_version?: string; payout_state?: string;
  blocker_code?: string | null; protection_ends_at?: string | null;
};

export type MarketplaceCommercialSummary = { seller_liability_by_state?: Record<string, number> };

export type MarketplaceSellerOrdersResponse = {
  ok?: boolean;
  orders?: MarketplaceSellerOrder[];
  commercial_summary?: MarketplaceCommercialSummary;
  message?: string;
};

/**
 * The seller's numbers, counted once, on the server.
 *
 * Every field here is the answer to a question a screen used to answer for
 * itself out of the raw row lists — Business OS by taking `.length`, the Store
 * screen by applying its own status filters. Two screens, two definitions, one
 * store, and a seller told they had 43 live listings (13) and 32 orders (0).
 *
 * `GET /api/pulse/marketplace/seller/metrics` is now the only place either
 * question is answered. Nothing below may be re-derived from `listings` or
 * `orders`; those arrays are for rendering rows, not for counting.
 */
export type SellerMetrics = {
  total_listings: number;
  live_listings: number;
  draft_listings: number;
  pending_review_listings: number;
  suppressed_listings: number;
  removed_listings: number;
  confirmed_orders: number;
  open_orders: number;
  fulfilled_orders: number;
  refunded_orders: number;
  cash_pending_orders: number;
  today_sales_minor: number;
  sold_last_7_days: number;
  /** Seven daily totals in minor units, oldest first. */
  sales_last_7_days_minor: number[];
  /**
   * Today against the same weekday last week, as a ratio (0.12 = +12%).
   * `null` when there is no baseline — a store's first week must not report
   * "+100%".
   */
  sales_trend_ratio: number | null;
  units_sold_last_7_days_by_listing: Record<string, number>;
  net_sales_minor: number;
  currency: string;
  active_campaigns: number;
  ad_spend_minor: number;
  raw_order_rows: number;
  raw_listing_rows: number;
  order_breakdown: Record<string, number>;
  listing_breakdown: Record<string, number>;
  /** Unconfirmed rows holding a PaymentIntent. A reconciliation signal. */
  unmatched_payments: number;
};

export type SellerMetricsResponse = { ok?: boolean; metrics?: SellerMetrics; message?: string };

export async function loadSellerMetrics() {
  const data = await pulseApi<SellerMetricsResponse>("/api/pulse/marketplace/seller/metrics");
  return data.metrics || null;
}

export type SellerStoreSnapshot = {
  listings: MarketplaceListing[];
  orders: MarketplaceSellerOrder[];
  /**
   * Authoritative counts. Absent only when the metrics call failed; callers
   * must render "—" in that case rather than falling back to counting the
   * arrays, because counting the arrays is the bug.
   */
  metrics?: SellerMetrics | null;
  commercial_summary?: MarketplaceCommercialSummary;
  cached_at?: string;
  /**
   * True only when both underlying requests answered. This snapshot resolves
   * even when they fail — the empty arrays below are indistinguishable from a
   * seller who genuinely has nothing — so callers that need to know whether
   * they are holding an authoritative picture have to be told explicitly.
   */
  live?: boolean;
};

export async function searchMarketplace(params: { query?: string; limit?: number; sellerUserId?: number } = {}) {
  const query = new URLSearchParams({
    q: params.query || "",
    limit: String(params.limit || 24)
  });
  if (params.sellerUserId) query.set("seller_user_id", String(params.sellerUserId));
  const data = await pulseApi<MarketplaceSearchResponse>(`/api/pulse/marketplace/search?${query.toString()}`);
  const items = normalizeMarketplaceListings(data.items || data.listings || []);
  if (!params.sellerUserId) await cacheMarketplace(items).catch(() => undefined);
  return { ...data, items };
}

/**
 * One listing, by id, for a caller that has an id and nothing else.
 *
 * Every other buyer-side read here returns a list, and for a long time that was
 * the whole buyer API — which is why `MarketplaceProductScreen` was written to
 * render only from a snapshot handed to it in navigation params. The four
 * commerce discovery surfaces navigate with an id alone (feed strip, reels chip,
 * messenger strip, marketplace shelves) and every one of them landed on "This
 * item is no longer available" for a listing that was on sale.
 *
 * Returns `null` for a listing the viewer may not see and **throws** for
 * anything else. The distinction is the whole contract: "gone" is a product
 * state the screen renders, a failed request is not, and collapsing the two
 * would tell a user on a dropped connection that a shop had removed their item.
 */
export async function fetchMarketplaceListing(listingId: number): Promise<MarketplaceListing | null> {
  const id = Number(listingId) || 0;
  if (id <= 0) return null;
  try {
    const data = await pulseApi<{ ok?: boolean; item?: MarketplaceListing; listing?: MarketplaceListing }>(
      `/api/pulse/marketplace/listings/${id}`
    );
    const raw = data?.item ?? data?.listing;
    if (!raw) return null;
    const [item] = normalizeMarketplaceListings([raw]);
    return item || null;
  } catch (error) {
    // The server says LISTING_UNAVAILABLE for both "no such listing" and "not
    // visible to you". Both are the same thing to a buyer, and neither is an
    // error worth a retry affordance. Everything else — 401, 5xx, a dropped
    // connection — is rethrown so the screen can offer a retry instead of
    // claiming the seller withdrew the item.
    const code = error instanceof PulseApiError ? error.code : undefined;
    const status = error instanceof PulseApiError ? error.status : 0;
    if (code === "LISTING_UNAVAILABLE" || status === 404) return null;
    throw error;
  }
}

export async function listMarketplaceSellerListings(params: { limit?: number } = {}) {
  const query = new URLSearchParams({
    limit: String(params.limit || 80)
  });
  const data = await pulseApi<MarketplaceSearchResponse>(`/api/pulse/marketplace/seller/listings?${query.toString()}`);
  const items = normalizeMarketplaceListings(data.items || data.listings || []);
  return { ...data, items };
}

export async function loadCachedMarketplace() {
  return (await readJsonCache<MarketplaceListing[]>(MARKETPLACE_CACHE_KEY, normalizeMarketplaceListings)) || [];
}

export async function cacheMarketplace(items: MarketplaceListing[]) {
  await writeJsonCache(MARKETPLACE_CACHE_KEY, items.slice(0, 80));
}

export async function loadCachedSellerStore() {
  return readJsonCache<SellerStoreSnapshot>(SELLER_STORE_CACHE_KEY, normalizeSellerStoreSnapshot);
}

export async function cacheSellerStore(snapshot: SellerStoreSnapshot) {
  await writeJsonCache(SELLER_STORE_CACHE_KEY, normalizeSellerStoreSnapshot(snapshot));
}

export async function loadSellerStoreSnapshot() {
  const [sellerListings, orders, metrics] = await Promise.allSettled([
    listMarketplaceSellerListings({ limit: 80 }),
    listMarketplaceSellerOrders(),
    loadSellerMetrics()
  ]);
  // `live` deliberately still turns on the two row lists. Metrics is a third
  // leg that can fail on its own, and when it does the screens show "—" for the
  // counts while still rendering the rows — a missing number is honest, a
  // locally recounted one is not.
  const live = sellerListings.status === "fulfilled" && orders.status === "fulfilled";
  const snapshot: SellerStoreSnapshot = {
    listings: sellerListings.status === "fulfilled" ? sellerListings.value.items || [] : [],
    orders: orders.status === "fulfilled" ? orders.value.orders || [] : [],
    metrics: metrics.status === "fulfilled" ? metrics.value : null,
    commercial_summary: orders.status === "fulfilled" ? orders.value.commercial_summary : undefined,
    cached_at: new Date().toISOString(),
    live
  };
  // Only persist a snapshot both requests actually backed. A failed load
  // produces empty arrays, and writing those replaced a good cache with a
  // picture of a business that has no listings and no orders — the precise
  // misreport the cache exists to prevent. The next offline open then showed
  // the seller nothing and called it saved data.
  if (live) await cacheSellerStore(snapshot).catch(() => undefined);
  return snapshot;
}

export async function applyMarketplaceSeller(payload: MarketplaceSellerApplicationPayload) {
  return pulseApi<MarketplaceActionResponse>("/api/pulse/marketplace/seller/apply", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function createMarketplaceListing(payload: MarketplaceListingCreatePayload) {
  return pulseApi<MarketplaceListingCreateResponse>("/api/pulse/marketplace/listings/create", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function submitMarketplaceSellerListing(listingId: number) {
  const result = await pulseApi<MarketplaceListingMutationResponse>(
    `/api/pulse/marketplace/seller/listings/${listingId}/submit`,
    { method: "POST", body: JSON.stringify({}) }
  );
  // Normalized like every other seller mutation. This one was raw, and the
  // response is what the editor merges over the row it is holding -- so an
  // unnormalized publish put a coerced quantity and an unchecked verdict onto a
  // row the rest of the screen reads as canonical.
  return {
    ...result,
    listing: result.listing ? normalizeMarketplaceListing(result.listing) : undefined
  };
}

export type MarketplaceDigitalFileUploadResponse = {
  ok?: boolean;
  file?: MarketplaceDigitalFile;
  message?: string;
};

/**
 * Uploads one deliverable for a digital listing. Multipart under the field name
 * "file" — `pulseApi` skips the JSON Content-Type when the body is FormData, so
 * the boundary header is set by fetch itself.
 */
export async function uploadMarketplaceDigitalFile(file: { uri: string; name: string; mimeType?: string }) {
  const form = new FormData();
  form.append("file", {
    uri: file.uri,
    name: file.name,
    type: file.mimeType || "application/octet-stream"
  } as unknown as Blob);
  return pulseApi<MarketplaceDigitalFileUploadResponse>("/api/pulse/marketplace/digital-files/upload", {
    method: "POST",
    body: form
  });
}

export type MarketplaceProductMediaAttachResponse = {
  ok?: boolean;
  message?: string;
  media?: {
    id?: number;
    product_media_id?: number;
    media_id?: number;
    media_type?: string;
    media_url?: string;
    thumbnail_url?: string;
    kind?: string;
    is_cover?: boolean;
    processing_status?: string;
  };
};

/**
 * Bind a finished direct upload to the seller's product media library. The file
 * itself already went straight to R2 (and, for video, on to Mux) — this only
 * records that the seller wants it on a listing.
 */
export async function attachMarketplaceProductMedia(mediaId: number, kind: "cover" | "gallery" | "video") {
  return pulseApi<MarketplaceProductMediaAttachResponse>("/api/pulse/marketplace/media/attach", {
    method: "POST",
    body: JSON.stringify({ media_id: mediaId, kind })
  });
}

export async function updateMarketplaceSellerListing(listingId: number, payload: MarketplaceListingUpdatePayload) {
  const result = await pulseApi<MarketplaceListingMutationResponse>(`/api/pulse/marketplace/seller/listings/${listingId}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
  return {
    ...result,
    listing: result.listing ? normalizeMarketplaceListing(result.listing) : undefined
  };
}

export async function pauseMarketplaceSellerListing(listingId: number) {
  return mutateMarketplaceSellerListingStatus(listingId, "pause");
}

export async function resumeMarketplaceSellerListing(listingId: number) {
  return mutateMarketplaceSellerListingStatus(listingId, "resume");
}

export async function deleteMarketplaceSellerListing(listingId: number) {
  return mutateMarketplaceSellerListingStatus(listingId, "delete");
}

async function mutateMarketplaceSellerListingStatus(listingId: number, action: "pause" | "resume" | "delete") {
  const result = await pulseApi<MarketplaceListingMutationResponse>(`/api/pulse/marketplace/seller/listings/${listingId}/${action}`, {
    method: "POST",
    body: JSON.stringify({})
  });
  return {
    ...result,
    listing: result.listing ? normalizeMarketplaceListing(result.listing) : undefined
  };
}

/* ------------------------------------------------------------------ *
 * Bulk actions
 * ------------------------------------------------------------------ */

export type MarketplaceBatchAction = "publish" | "hide" | "price" | "category";

/**
 * Where a bulk move files the selected products.
 *
 * `subcategory` is required rather than optional, and that is the contract.
 * A subcategory belongs to its parent, so moving "Education / Crypto Basics"
 * into "Home & Kitchen" has to say what becomes of the child; leaving the key
 * off would make the server guess, and the two available guesses — clear it, or
 * keep it — differ by whether the listing ends up filed under
 * "Home & Kitchen / Crypto Basics", which is a pair no filter or buyer can read.
 * Callers moving into a bare category send `""`, which is a decision rather
 * than an omission. `normalize_category` on the server clears it either way; the
 * requirement here is so the caller cannot be unaware it made a choice.
 */
export type MarketplaceCategoryTarget = { category: string; subcategory: string };

/**
 * The JSON body both batch calls send.
 *
 * Shared because the two used to build it separately, which is one drifted
 * `...(input.x ? …)` away from a preview that omits the payload the commit
 * sends — a dry run answering a different question than the tap it precedes,
 * which is the §21/§34 failure this whole path is shaped to prevent.
 *
 * Each payload action puts its settings under its own key rather than in one
 * generic `settings` object, mirroring the route. Sharing a key would let a
 * client send the wrong action with the right settings and be told it succeeded
 * at the other thing.
 */
function batchBody(input: {
  action: MarketplaceBatchAction;
  listingIds: number[];
  idempotencyKey: string;
  pricingRule?: MarketplacePricingRule;
  categoryTarget?: MarketplaceCategoryTarget;
  dryRun?: boolean;
}) {
  return JSON.stringify({
    action: input.action,
    listing_ids: input.listingIds,
    idempotency_key: input.idempotencyKey,
    ...(input.dryRun ? { dry_run: true } : {}),
    // Omitted rather than sent as null: the server refuses a payload on an
    // action that ignores one, and `undefined` disappears from the JSON.
    ...(input.pricingRule ? { pricing_rule: input.pricingRule } : {}),
    ...(input.categoryTarget ? { category: input.categoryTarget } : {})
  });
}

/**
 * How a bulk reprice works out each listing's new price.
 *
 * Mirrors `services/business_os/suppliers/pricing.py`, which is the one
 * authority for what a rule means — this type names the rules, it does not
 * implement them. Nothing on the phone multiplies a cost by anything: the
 * server computes every price, because a second implementation here would
 * disagree with the stored one the first time a rule landed on a fraction of a
 * cent, and it would do it across a whole storefront at once.
 *
 * `MANUAL_PRICE` is absent on purpose. It is a legitimate rule for a single
 * listing and a meaningless one for a batch — "price these forty manually" is
 * not an instruction a batch can carry out — and the server refuses it with
 * `INVALID_PRICING_RULE`.
 */
export type MarketplacePricingRule =
  | { type: "COST_PLUS_FIXED"; value: number }
  | { type: "COST_PLUS_PERCENT"; value: number }
  | { type: "MULTIPLIER"; value: number }
  | { type: "TARGET_MARGIN"; value: number };

/**
 * Why one listing in a batch did not end up where the seller aimed it.
 *
 * Three outcomes, and the middle one is the whole reason this is not a boolean.
 * `succeeded` moved. `failed` could not be attempted — the id was not found, or
 * did not belong to this seller. `blocked` means the server looked at the row
 * and it is not ready: nothing is wrong with the request, the product is
 * unfinished. Collapsing blocked into failed is what turns "4 need a price"
 * into "4 errors", and a seller cannot act on an error.
 */
export type MarketplaceBatchOutcome = "succeeded" | "blocked" | "failed";

/**
 * Everything a result entry carries except its verdict.
 *
 * Split out so a commit entry and a preview entry can share every field and
 * share *no* outcome word. The server draws the same line — `summarize` and
 * `summarize_preview` in `listing_batch.py` each raise on the other's
 * vocabulary — and the reason is the same on both sides: `would_apply` and
 * `succeeded` must never be interchangeable, because the one thing a preview
 * must not be able to do is render as a result. A single `outcome: string` here
 * would let a component built for the commit face consume a dry run and print
 * "14 products published" over fourteen products that were never touched.
 */
type MarketplaceBatchEntry = {
  listing_id: number;
  /** Present on every entry the server could name. */
  title?: string;
  /** One sentence, server-written. Present on blocked and failed. */
  reason?: string;
  error_code?: string;
  /** Readiness codes, for blocked rows the verdict refused. */
  blockers?: string[];
  /** The same blockers as prose plus an editor section. Tappable. */
  fixes?: ListingFix[];
  /** Which columns moved, for succeeded rows. */
  changes_applied?: string[];
  /**
   * The listing's own status afterwards, e.g. `"pending_review"`. Not the
   * outcome of the batch — a row can succeed into `pending_review`, and reading
   * this as "did it work" would report every successful publish as pending.
   */
  status?: string;
  /**
   * The stored price, on a reprice. On a preview it is the price that *would* be
   * stored, formatted by the server's own label builder rather than here — so
   * the number the seller approves and the number written are one string.
   */
  price_label?: string;
  /** Preview only: what the row costs today, so the sheet can draw the arrow. */
  current_price_label?: string;
  /** The stored filing, on a move; on a preview, the filing that *would* be stored. */
  category?: string;
  /**
   * The stored subcategory. `""` is meaningful and not the same as absent: a
   * move out of a parent clears the child, so an empty string here is the server
   * reporting that the old subcategory is gone, which the sheet has to be able
   * to show.
   */
  subcategory?: string;
  /** Preview only: where the row is filed today, so the sheet can draw the arrow. */
  current_category?: string;
  current_subcategory?: string;
  /**
   * The warning that matters most, and it is on **both** shapes rather than the
   * preview alone. `price_label` and `category` are both material fields, so
   * repricing *or* re-filing a live, approved product sends it back to the review
   * queue and off sale. The preview says so before the tap, which is what a
   * seller is entitled to; the commit says so afterwards, which is what a seller
   * who tapped past the warning needs. One field, one word, both faces.
   */
  returns_to_review?: boolean;
};

/** One row of a committed batch. */
export type MarketplaceBatchResult = MarketplaceBatchEntry & {
  outcome: MarketplaceBatchOutcome;
};

export type MarketplaceBatchResponse = {
  ok: boolean;
  batch_id: string;
  action: MarketplaceBatchAction;
  requested_count: number;
  successful_count: number;
  blocked_count: number;
  failed_count: number;
  results: MarketplaceBatchResult[];
  /** True when this exact request had already run and the server replayed it. */
  replayed?: boolean;
};

/**
 * Apply one action to many listings in ONE request.
 *
 * The alternative — looping the single-listing routes on the phone — is what
 * this replaces, and the difference is not performance. A loop has no batch: a
 * retry re-runs whatever half already succeeded, and the "14 published, 4 need
 * attention" summary is assembled here out of whichever replies happened to
 * arrive, so a dropped connection silently changes the count the seller is
 * shown.
 *
 * `idempotencyKey` is required rather than generated inside, and that is the
 * point of the parameter. Generated here, every retry would mint a fresh key
 * and publish everything a second time — which is exactly the double-submission
 * the key exists to prevent. The caller holds one key for one *attempt by the
 * seller*, across as many retries as that attempt needs, and the server replays
 * its original answer instead of re-applying.
 */
export async function batchMarketplaceSellerListings(input: {
  action: MarketplaceBatchAction;
  listingIds: number[];
  idempotencyKey: string;
  /** Required for `price`, refused for the others. */
  pricingRule?: MarketplacePricingRule;
  /** Required for `category`, refused for the others. */
  categoryTarget?: MarketplaceCategoryTarget;
}) {
  return pulseApi<MarketplaceBatchResponse>("/api/pulse/marketplace/seller/listings/batch", {
    method: "POST",
    body: batchBody(input)
  });
}

/**
 * A preview outcome. `would_apply` rather than `succeeded`, because a request
 * that wrote nothing must not be able to produce the word the store renders as
 * "14 products updated".
 */
export type MarketplaceBatchPreviewOutcome = "would_apply" | "blocked" | "failed";

/** One row of a dry run. Same fields as a result, deliberately not the same verdicts. */
export type MarketplaceBatchPreviewResult = MarketplaceBatchEntry & {
  outcome: MarketplaceBatchPreviewOutcome;
};

/**
 * §34. What the batch *would* do — and pointedly not shaped like what it did.
 *
 * `batch_id` and `successful_count` are absent from the server's response and
 * therefore from this type, so a component that tries to render a preview as an
 * outcome fails to compile instead of printing a confident wrong number.
 */
export type MarketplaceBatchPreview = {
  ok: boolean;
  preview: true;
  action: MarketplaceBatchAction;
  requested_count: number;
  eligible_count: number;
  blocked_count: number;
  failed_count: number;
  results: MarketplaceBatchPreviewResult[];
};

/**
 * Ask what would happen, changing nothing.
 *
 * This exists because a reprice verdict is not knowable in advance the way a
 * publish verdict is: it depends on the rule, so the server cannot attach it to
 * a listing row and the phone cannot derive it. Without this call the only way
 * for a seller to find out what "cost + 20%" does to forty listings is to do it
 * to forty listings.
 *
 * The preview and the apply are the same server decision, so the sheet is not
 * making a prediction — it is showing the answer early.
 *
 * `idempotencyKey` is required here too, and it should NOT be the one you will
 * apply with. The route answers a dry run above its own `claim`, so a preview
 * genuinely cannot spend a key and sharing one would work today — but a seller
 * previewing "cost + 20%", then "cost + 25%", then applying would hand the
 * commit a key the server had already been asked under a different rule, and a
 * key that has been seen is answered by replay rather than by reading the
 * payload. Mint a throwaway key per dry run; the attempt's key is minted when
 * the seller confirms.
 */
export async function previewMarketplaceSellerBatch(input: {
  action: MarketplaceBatchAction;
  listingIds: number[];
  idempotencyKey: string;
  pricingRule?: MarketplacePricingRule;
  categoryTarget?: MarketplaceCategoryTarget;
}) {
  return pulseApi<MarketplaceBatchPreview>("/api/pulse/marketplace/seller/listings/batch", {
    method: "POST",
    body: batchBody({ ...input, dryRun: true })
  });
}

export async function connectMarketplacePayout() {
  const result = await pulseApi<MarketplaceActionResponse>("/api/pulse/payouts/connect", {
    method: "POST",
    body: JSON.stringify({ seller_type: "merchant" })
  });
  return result;
}

export async function getMarketplaceCommercialTerms() {
  return pulseApi<any>("/api/pulse/marketplace/commercial/terms");
}

export async function acceptMarketplaceCommercialTerms() {
  return pulseApi<any>("/api/pulse/marketplace/commercial/terms", { method: "POST", body: JSON.stringify({}) });
}

export async function listMarketplaceSellerOrders() {
  return pulseApi<MarketplaceSellerOrdersResponse>("/api/pulse/payments/seller/orders");
}

export type MarketplaceCashSettlementResponse = {
  ok?: boolean;
  already_settled?: boolean;
  order_id?: number;
  status?: string;
  payment_status?: string;
  payment_method?: string;
  message?: string;
};

/** Seller-only. The server rejects any order that is not `cash_pending`. */
export async function markMarketplaceOrderCashCollected(orderId: number) {
  return pulseApi<MarketplaceCashSettlementResponse>(`/api/pulse/orders/${orderId}/cash-collected`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function saveMarketplaceListing(listingId: number) {
  return pulseApi<MarketplaceActionResponse>("/api/pulse/marketplace/listings/save", {
    method: "POST",
    body: JSON.stringify({ listing_id: listingId })
  });
}

export async function reportMarketplaceListing(listingId: number, reason = "Needs review") {
  return pulseApi<MarketplaceActionResponse>("/api/pulse/marketplace/listings/report", {
    method: "POST",
    body: JSON.stringify({ listing_id: listingId, reason })
  });
}

/**
 * Hand a listing off to the canonical direct conversation with its seller.
 *
 * The seller is identified by user id — never by listing id, store id, or
 * display name. `/api/pulse/messages/start` also accepts a username or public
 * Pulse id, so those are sent as a fallback for the case where a listing
 * snapshot reaches this screen without `seller_user_id` resolved; without it
 * the button dead-ends with "this seller cannot be messaged" even though the
 * seller is perfectly reachable. Messenger itself is untouched: this is only
 * the Marketplace → DM handoff.
 */
export async function startMarketplaceSellerChat(
  sellerUserId: number,
  fallback: { username?: string; publicPlayerId?: string } = {}
) {
  const body: Record<string, string | number> = {};
  if (Number(sellerUserId) > 0) body.user_id = Number(sellerUserId);
  if (fallback.publicPlayerId) body.public_player_id = fallback.publicPlayerId;
  if (fallback.username) body.username = fallback.username;
  if (!Object.keys(body).length) {
    throw new Error("This seller cannot be messaged yet.");
  }
  return pulseApi<MarketplaceActionResponse>("/api/pulse/messages/start", {
    method: "POST",
    body: JSON.stringify(body)
  });
}

/** Buy-Now result: the raw action response, plus a `handoff` normalised the
 * exact same way the cart path is (`readCheckoutHandoff`). The checkout screen
 * reads `handoff` alone, so Buy-Now and cart take an identical native-sheet /
 * hosted-URL branch instead of two divergent shapes. */
export type MarketplaceCheckoutResult = MarketplaceActionResponse & { handoff: CheckoutHandoff };

/**
 * Buy Now.
 *
 * `paymentMode: "payment_sheet"` asks the server for a PaymentIntent the native
 * Stripe sheet can present in-app, instead of a hosted Checkout Session whose
 * URL the phone can only open in Safari. The caller sends it only when the SDK
 * is actually present in this binary, so a build without it never creates a
 * PaymentIntent it has no way to collect on.
 */
export async function openMarketplaceCheckout(
  listingId: number,
  idempotencyKey = "",
  // The lane the buyer picked when the seller offered more than one. A string
  // rather than a union: a service offered both remotely and in person is also
  // a choice, and it is not spelled "pickup" or "shipping".
  fulfillment = "",
  paymentMode: MarketplacePaymentMode = "",
  // What the buyer told PulseSoc on the details step. The server re-derives the
  // order type from the listing row and re-validates this against it, so this is
  // the buyer's submission, not the decision.
  fulfillmentDetails: Record<string, string> | null = null,
  // How many units the buyer chose on the product screen's stepper.
  //
  // This argument did not exist. The stepper multiplied the unit price out for
  // display, the checkout summary showed the multiplied total, and then this
  // function sent no quantity at all — so the server priced one unit, charged
  // one unit, took one unit off the shelf, and wrote `quantity: 1` into the
  // order row that `fulfillment.create_intent` later compares a supplier line
  // against. The cart lane has always carried its quantity; only Buy Now
  // guessed. The server clamps this to the cart's per-line maximum and refuses
  // outright when the shelf cannot cover it.
  quantity = 1
): Promise<MarketplaceCheckoutResult> {
  const result = await pulseApi<MarketplaceActionResponse & CheckoutResponse>("/api/pulse/payments/checkout", {
    method: "POST",
    body: JSON.stringify({
      item_type: "marketplace_product",
      item_id: listingId,
      idempotency_key: idempotencyKey,
      ...(fulfillmentDetails ? { fulfillment_details: fulfillmentDetails } : {}),
      // Present only for a listing that offers pickup *or* shipping, where the
      // buyer's answer decides whether Stripe collects a delivery address.
      ...(fulfillment ? { fulfillment } : {}),
      // Always sent, not only when it is greater than one: a server that sees no
      // quantity has to assume one, and "the buyer chose one" and "this build
      // cannot say" should not arrive looking identical.
      quantity: Math.max(1, Math.floor(Number(quantity) || 1)),
      ...(paymentMode ? { payment_mode: paymentMode } : {})
    })
  });
  // Single Buy-Now returns one transaction id; the sheet bootstrap only
  // materialises when the server actually included a client secret.
  const ids = result.transaction_id ? [Number(result.transaction_id)] : [];
  return { ...result, handoff: readCheckoutHandoff(result, ids) };
}

export function marketplaceWebUrl(listingId?: number) {
  return `${PULSE_API_BASE_URL}/pulse/marketplace${listingId ? `?listing=${encodeURIComponent(String(listingId))}` : ""}`;
}

export function sellerStoreWebUrl(route: "dashboard" | "apply" | "create" | "payouts" | "profile" = "dashboard", sellerKey = "") {
  if (route === "apply") return `${PULSE_API_BASE_URL}/pulse/merchant/apply`;
  if (route === "create") return `${PULSE_API_BASE_URL}/pulse/marketplace/create`;
  if (route === "payouts") return `${PULSE_API_BASE_URL}/pulse/merchant/payouts`;
  if (route === "profile" && sellerKey) return `${PULSE_API_BASE_URL}/pulse/merchant/${encodeURIComponent(sellerKey)}`;
  return `${PULSE_API_BASE_URL}/pulse/merchant/dashboard`;
}

export function normalizeMarketplaceListings(items: MarketplaceListing[]) {
  return items.map(normalizeMarketplaceListing).filter((listing) => listing.id > 0);
}

/**
 * A stock count, or `null` when the payload does not carry one.
 *
 * Every branch that returns null is a distinct way of not knowing, and none of
 * them is a zero: absent (an older cached payload), explicitly null (the seller
 * does not track stock), empty string (some legacy rows store it that way), or
 * unparseable. `Number("")` is `0` and `Number(null)` is `0`, so each of these
 * has to be caught *before* coercion rather than after it.
 */
function normalizeQuantity(raw: MarketplaceListing["quantity"]): number | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "string" && String(raw).trim() === "") return null;
  const quantity = Number(raw);
  return Number.isFinite(quantity) ? quantity : null;
}

/**
 * A verdict, or `undefined` when the payload carries one this build cannot
 * render.
 *
 * The server writes the seller-facing prose — `summary` and one `fixes` entry
 * per blocker — precisely so no surface here owns a code→English table. A
 * cached snapshot from before that change has the codes and none of the words,
 * and there are only two ways to handle it: invent the words, which recreates
 * the table and the drift, or admit we were not told. This admits it, and every
 * reader already treats an absent verdict as "no news" rather than "good news".
 */
function normalizeReadiness(raw: ListingReadiness | undefined): ListingReadiness | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  if (typeof raw.summary !== "string" || !Array.isArray(raw.fixes)) return undefined;
  const warnings = Array.isArray(raw.warnings) ? raw.warnings.map(String) : [];
  const notes = Array.isArray(raw.notes) ? raw.notes : [];
  // A snapshot cached before the server started sending `notes` has the warning
  // codes and none of the words. Passing it through as `notes: []` would draw an
  // empty Ready-to-Sell list for a listing that has something to say — the exact
  // "absence is a clean bill of health" reading the field was added to deny — so
  // the verdict is refused whole, which every reader treats as "no news".
  if (warnings.length !== notes.length) return undefined;
  return {
    publishable: Boolean(raw.publishable),
    // Defaults false on a snapshot cached before the server sent it, which is
    // the safe direction: the seller sees the button disabled as they did
    // before rather than being offered a resubmission the server would refuse.
    resubmittable: Boolean(raw.resubmittable),
    checkout_ready: Boolean(raw.checkout_ready),
    blockers: Array.isArray(raw.blockers) ? raw.blockers.map(String) : [],
    warnings,
    summary: raw.summary,
    fixes: raw.fixes.map(normalizeFix),
    notes: notes.map(normalizeFix)
  };
}

/**
 * The server's verdict on this listing's last review decision, rendered rather
 * than derived — `services/business_os/marketplace/listing_review.seller_verdict`.
 *
 * Refused whole if the shape is wrong, for the same reason `normalizeReadiness`
 * refuses a mismatched verdict: a half-read verdict renders as a listing with a
 * decision and no words for it, which is the state this whole feature exists to
 * remove.
 *
 * `decided: false` with `needs_action: false` is the correct reading of a
 * missing or malformed verdict, and it is also what a listing waiting in the
 * queue genuinely looks like — so callers must use *absence* (`undefined`) to
 * mean "not told", never a synthesised empty verdict.
 */
function normalizeReviewVerdict(
  raw: ListingReviewVerdict | undefined
): ListingReviewVerdict | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  if (typeof raw.state !== "string") return undefined;
  const needsAction = Boolean(raw.needs_action);
  const message = String(raw.message || "");
  // A listing the seller must act on, with no sentence saying why, is the
  // defect wearing this feature's clothes: "Hidden from buyers" plus a red dot
  // and nothing to do about it. Refusing the verdict makes the row fall back to
  // its stock copy, which promises nothing, instead of raising an alarm it
  // cannot explain.
  if (needsAction && !message) return undefined;
  return {
    state: raw.state,
    decided: Boolean(raw.decided),
    needs_action: needsAction,
    reason_code: String(raw.reason_code || ""),
    message,
    review_version: Number(raw.review_version || 0),
    decided_at: String(raw.decided_at || "")
  };
}

function normalizeFix(entry: ListingFix | undefined): ListingFix {
  return {
    code: String(entry?.code || ""),
    label: String(entry?.label || ""),
    section: String(entry?.section || "overview")
  };
}

export function normalizeMarketplaceListing(item: MarketplaceListing): MarketplaceListing {
  const id = Number(item.listing_id || item.id || 0);
  return {
    ...item,
    id,
    listing_id: id,
    seller_user_id: Number(item.seller_user_id || 0),
    // Collapse the two spellings once, here, so every downstream screen and any
    // cached payload written before the server added `seller_store_name` agree
    // on a single store identity.
    seller_store_name: sellerStoreNameOrEmpty(item),
    seller_name: sellerStoreNameOrEmpty(item),
    title: String(item.title || "PulseSoc Listing"),
    short_description: String(item.short_description || ""),
    description: String(item.description || ""),
    category: String(item.category || "Education"),
    // Empty stays empty. "Request access" is one thing a seller may *choose* to
    // put here, so inventing it for a listing that has no price at all makes
    // that choice for them and then shows it to buyers as if they had made it.
    //
    // It also silently disabled every fallback downstream. Five surfaces have
    // one -- the Marketplace card's "Price at checkout", the product screen's
    // "Price shown at checkout", the Page block that renders no price line at
    // all, and the seller row that omits the price element entirely -- and none
    // of them could ever run, because this line guaranteed the field was
    // non-empty before they saw it. A dropship import leaves the price blank on
    // purpose (seeding it with the supplier cost would print the seller's own
    // margin on their storefront), so those drafts arrived in the seller's own
    // store priced "Request access", which is the one place that prose is
    // meaningless: the seller is not going to request access to their own item.
    //
    // Checkout is unaffected -- `marketplaceListingPriceMinor` already maps
    // both "" and "Request access" to null, so neither makes a dollar promise.
    price_label: String(item.price_label || ""),
    // Null survives. This line used to read `Number(item.quantity || 0)`, and
    // that coercion was the whole of GAP 22: the column is nullable, the server
    // sends the null intact, and this normalizer -- the one hop every seller
    // surface shares -- turned "no stock tracked" into a hard zero before any
    // screen could tell the difference. The seller's own store then filed those
    // listings under Out and raised the red banner over them.
    //
    // Note the same mistake is NOT made for `price_label` two lines up, for the
    // same reason spelled out in that comment: a missing value is not a zero
    // value, and inventing one makes a claim on the seller's behalf.
    quantity: normalizeQuantity(item.quantity),
    product_type: String(item.product_type || ""),
    saved: Boolean(item.saved || item.is_saved),
    readiness: normalizeReadiness(item.readiness),
    review: normalizeReviewVerdict(item.review),
    bulk_eligibility: normalizeBulkEligibility(item.bulk_eligibility),
    media: normalizeMarketplaceMedia(item)
  };
}

/**
 * Keeps only entries this build can act on: an action name mapped either to
 * `null` (eligible) or to a block carrying prose. A malformed entry is dropped
 * rather than coerced, because the two ways of coercing it are "assume eligible"
 * — which publishes something the server refused — and "assume blocked with an
 * empty reason", which is a disabled row the seller cannot be told anything
 * about. Dropping it leaves the action absent, and absence already means
 * not eligible.
 */
function normalizeBulkEligibility(
  raw: ListingBulkEligibility | undefined
): ListingBulkEligibility | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const out: ListingBulkEligibility = {};
  Object.keys(raw).forEach((action) => {
    const block = raw[action];
    if (block === null) {
      out[action] = null;
      return;
    }
    if (!block || typeof block !== "object" || typeof block.reason !== "string") return;
    out[action] = {
      code: String(block.code || ""),
      reason: block.reason,
      blockers: Array.isArray(block.blockers) ? block.blockers.map(String) : undefined
    };
  });
  return out;
}

export function marketplaceSellerAuthor(listing: MarketplaceListing): PulseAuthor {
  return {
    id: Number(listing.seller_user_id || 0),
    user_id: Number(listing.seller_user_id || 0),
    display_name: sellerStoreName(listing),
    username: listing.seller_username || "",
    public_player_id: listing.seller_public_player_id || ""
  };
}

function normalizeMarketplaceMedia(item: MarketplaceListing) {
  const media: PulseMedia[] = [...(item.media || item.media_assets || [])];
  const gallery = parseGallery(item.gallery_json);
  const cover = item.cover_image_url || item.image_url || item.thumbnail_url || "";
  if (cover) media.push({ media_type: "image", media_url: cover, thumbnail_url: item.thumbnail_url || cover });
  gallery.forEach((url) => media.push({ media_type: "image", media_url: url, thumbnail_url: url }));
  if (item.video_url) media.push({ media_type: "video", media_url: item.video_url, thumbnail_url: cover });
  return media.filter((entry, index, list) => {
    const url = mediaDisplayUrl(entry);
    return Boolean(url) && list.findIndex((candidate) => mediaDisplayUrl(candidate) === url) === index;
  });
}

function parseGallery(value?: string | string[]) {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (!value) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function normalizeSellerStoreSnapshot(snapshot: SellerStoreSnapshot): SellerStoreSnapshot {
  return {
    listings: normalizeMarketplaceListings(snapshot?.listings || []),
    orders: (snapshot?.orders || []).map((order) => ({
      ...order,
      id: Number(order.id || 0),
      item_id: order.item_id,
      amount_cents: Number(order.amount_cents || order.gross_amount_cents || 0),
      gross_amount_cents: Number(order.gross_amount_cents || order.amount_cents || 0),
      currency: String(order.currency || "USD"),
      status: String(order.status || "pending")
    })),
    // Carried through verbatim. There is nothing to normalize — the server
    // computed these and re-deriving any of them here is exactly what this
    // payload exists to stop.
    metrics: snapshot?.metrics || null,
    commercial_summary: snapshot?.commercial_summary,
    cached_at: snapshot?.cached_at || ""
  };
}
