import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { PulseAuthor, PulseMedia, mediaDisplayUrl } from "./feed";
import { pulseApi } from "./pulseApi";
import { sellerStoreName, sellerStoreNameOrEmpty } from "./sellerIdentity";

const MARKETPLACE_CACHE_KEY = "pulsesoc.native.marketplace.search";
const SELLER_STORE_CACHE_KEY = "pulsesoc.native.marketplace.seller_store";

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
  quantity?: number;
  product_type?: string;
  /**
   * Internal moderation fields. These are never buyer-facing — `safety_score`
   * in particular is a reviewer signal, not a product attribute, and was
   * dropped from the client model entirely so no screen can render it by
   * accident. `approval_status` stays because the seller's own store rows
   * legitimately show it back to the seller.
   */
  status?: string;
  approval_status?: string;
  publication_state?: string;
  publication_label?: string;
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

export type MarketplaceListingUpdatePayload = Omit<MarketplaceListingCreatePayload, "submission_action">;

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
  created_at?: string;
};

export type MarketplaceSellerOrdersResponse = {
  ok?: boolean;
  orders?: MarketplaceSellerOrder[];
  message?: string;
};

export type SellerStoreSnapshot = {
  listings: MarketplaceListing[];
  orders: MarketplaceSellerOrder[];
  cached_at?: string;
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
  const [sellerListings, orders] = await Promise.allSettled([
    listMarketplaceSellerListings({ limit: 80 }),
    listMarketplaceSellerOrders()
  ]);
  const snapshot: SellerStoreSnapshot = {
    listings: sellerListings.status === "fulfilled" ? sellerListings.value.items || [] : [],
    orders: orders.status === "fulfilled" ? orders.value.orders || [] : [],
    cached_at: new Date().toISOString()
  };
  await cacheSellerStore(snapshot).catch(() => undefined);
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
  return pulseApi<MarketplaceListingMutationResponse>(`/api/pulse/marketplace/seller/listings/${listingId}/submit`, {
    method: "POST",
    body: JSON.stringify({})
  });
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

export async function connectMarketplacePayout() {
  const result = await pulseApi<MarketplaceActionResponse>("/api/pulse/payouts/connect", {
    method: "POST",
    body: JSON.stringify({ seller_type: "merchant" })
  });
  return result;
}

export async function listMarketplaceSellerOrders() {
  return pulseApi<MarketplaceSellerOrdersResponse>("/api/pulse/payments/seller/orders");
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

export async function openMarketplaceCheckout(
  listingId: number,
  idempotencyKey = "",
  fulfillment: "pickup" | "shipping" | "" = ""
) {
  const result = await pulseApi<MarketplaceActionResponse>("/api/pulse/payments/checkout", {
    method: "POST",
    body: JSON.stringify({
      item_type: "marketplace_product",
      item_id: listingId,
      idempotency_key: idempotencyKey,
      // Present only for a listing that offers pickup *or* shipping, where the
      // buyer's answer decides whether Stripe collects a delivery address.
      ...(fulfillment ? { fulfillment } : {})
    })
  });
  return result;
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
    price_label: String(item.price_label || "Request access"),
    quantity: Number(item.quantity || 0),
    product_type: String(item.product_type || ""),
    saved: Boolean(item.saved || item.is_saved),
    media: normalizeMarketplaceMedia(item)
  };
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
    cached_at: snapshot?.cached_at || ""
  };
}
