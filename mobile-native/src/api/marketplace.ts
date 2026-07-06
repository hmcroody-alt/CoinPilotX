import { Linking } from "react-native";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { PulseAuthor, PulseMedia, mediaDisplayUrl } from "./feed";
import { pulseApi } from "./pulseApi";

const MARKETPLACE_CACHE_KEY = "pulsesoc.native.marketplace.search";
const SELLER_STORE_CACHE_KEY = "pulsesoc.native.marketplace.seller_store";

export type MarketplaceListing = {
  id: number;
  listing_id: number;
  seller_user_id?: number;
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
  safety_score?: number;
  status?: string;
  approval_status?: string;
  saved?: boolean;
  is_saved?: boolean;
  cover_image_url?: string;
  image_url?: string;
  thumbnail_url?: string;
  video_url?: string;
  gallery_json?: string | string[];
  media?: PulseMedia[];
  media_assets?: PulseMedia[];
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

export type MarketplaceListingCreatePayload = {
  title: string;
  short_description?: string;
  description: string;
  category?: string;
  subcategory?: string;
  price_label?: string;
  currency?: string;
  quantity?: number;
  product_type?: "digital" | "physical" | "course" | "service";
  media_ids?: number[];
  tags?: string;
  refund_policy?: string;
  estimated_delivery?: string;
  seller_notes?: string;
};

export type MarketplaceListingCreateResponse = MarketplaceActionResponse & {
  listing_id?: number;
};

export type MarketplaceListingUpdatePayload = {
  title: string;
  short_description?: string;
  description: string;
  category?: string;
  price_label?: string;
  quantity?: number;
};

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

export async function searchMarketplace(params: { query?: string; limit?: number } = {}) {
  const query = new URLSearchParams({
    q: params.query || "",
    limit: String(params.limit || 24)
  });
  const data = await pulseApi<MarketplaceSearchResponse>(`/api/pulse/marketplace/search?${query.toString()}`);
  const items = normalizeMarketplaceListings(data.items || data.listings || []);
  await cacheMarketplace(items).catch(() => undefined);
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
  if (result.onboarding_url) await Linking.openURL(result.onboarding_url).catch(() => undefined);
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

export async function startMarketplaceSellerChat(sellerUserId: number) {
  return pulseApi<MarketplaceActionResponse>("/api/pulse/messages/start", {
    method: "POST",
    body: JSON.stringify({ user_id: sellerUserId })
  });
}

export async function openMarketplaceCheckout(listingId: number) {
  const result = await pulseApi<MarketplaceActionResponse>("/api/pulse/payments/checkout", {
    method: "POST",
    body: JSON.stringify({ item_type: "marketplace_product", item_id: listingId })
  });
  if (result.checkout_url) await Linking.openURL(result.checkout_url);
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
    title: String(item.title || "PulseSoc Listing"),
    short_description: String(item.short_description || ""),
    description: String(item.description || ""),
    category: String(item.category || "Education"),
    price_label: String(item.price_label || "Request access"),
    quantity: Number(item.quantity || 0),
    product_type: String(item.product_type || ""),
    safety_score: Number(item.safety_score || 0),
    saved: Boolean(item.saved || item.is_saved),
    media: normalizeMarketplaceMedia(item)
  };
}

export function marketplaceSellerAuthor(listing: MarketplaceListing): PulseAuthor {
  return {
    id: Number(listing.seller_user_id || 0),
    user_id: Number(listing.seller_user_id || 0),
    display_name: listing.seller_name || "PulseSoc Seller",
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
