import { Linking } from "react-native";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { PulseAuthor, PulseMedia, mediaDisplayUrl } from "./feed";
import { pulseApi } from "./pulseApi";

const MARKETPLACE_CACHE_KEY = "pulsesoc.native.marketplace.search";

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

export async function loadCachedMarketplace() {
  return (await readJsonCache<MarketplaceListing[]>(MARKETPLACE_CACHE_KEY, normalizeMarketplaceListings)) || [];
}

export async function cacheMarketplace(items: MarketplaceListing[]) {
  await writeJsonCache(MARKETPLACE_CACHE_KEY, items.slice(0, 80));
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
