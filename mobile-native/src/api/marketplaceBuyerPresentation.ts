import type { MarketplaceListing } from "./marketplace";

export function isStocklessMarketplaceListing(listing: MarketplaceListing) {
  return ["digital", "course", "service", "event", "booking"].includes(
    String(listing.product_type || listing.listing_type || "").toLowerCase()
  );
}

export function canPurchaseMarketplaceListing(listing: MarketplaceListing) {
  if (listing.buyer_visible === false) return false;
  if (String(listing.inventory_state || "").toLowerCase() === "out_of_stock") return false;
  return isStocklessMarketplaceListing(listing) || Number(listing.quantity || 0) > 0;
}

export function marketplaceAvailabilityCopy(listing: MarketplaceListing) {
  if (!canPurchaseMarketplaceListing(listing)) return "Sold out";
  if (isStocklessMarketplaceListing(listing)) return "Available";
  const quantity = Number(listing.quantity || 0);
  if (quantity === 1) return "Only 1 left";
  if (quantity > 10) return "In stock 10+";
  return `${quantity} available`;
}

export function marketplaceFulfillmentCopy(listing: MarketplaceListing) {
  const metadata = (listing.listing_metadata || {}) as Record<string, unknown>;
  const raw = metadata.delivery_options;
  const configured = (typeof raw === "string" ? raw.trim() : "") || String(listing.delivery_type || "");
  if (configured === "both") return "Local pickup or shipping";
  if (configured === "pickup") return "Local pickup";
  if (configured === "shipping" || configured === "physical") return "Shipping";
  const kind = String(listing.product_type || listing.listing_type || "");
  if (kind === "digital") return "Digital delivery";
  if (kind === "service") return "Service fulfillment";
  return "Delivery details shown at checkout";
}
