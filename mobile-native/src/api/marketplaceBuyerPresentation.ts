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

/**
 * The fulfillment lane the checkout screen needs, as opposed to the sentence a
 * buyer reads. Kept beside `marketplaceFulfillmentCopy` so the label and the
 * lane are derived from the same fields and cannot drift — a listing that reads
 * "Local pickup" must not check out as "shipping".
 *
 * `both` is returned as itself. Collapsing it to "shipping" here is what made
 * "Local pickup or shipping" a decorative label: the buyer read a choice and
 * then silently checked out as a shipped order. Checkout resolves `both` from
 * what the buyer actually picks.
 */
export type MarketplaceFulfillment = "digital" | "pickup" | "shipping" | "both";

export function marketplaceListingFulfillment(
  listing: MarketplaceListing
): MarketplaceFulfillment {
  const value = String(listing.delivery_type || listing.product_type || "").toLowerCase();
  const metadata = (listing.listing_metadata || {}) as Record<string, unknown>;
  const delivery = String(metadata.delivery_options || "").toLowerCase();
  if (value === "digital" || listing.listing_type === "digital") return "digital";
  if (value === "pickup" || delivery === "pickup") return "pickup";
  if (["both", "pickup_or_shipping", "shipping_or_pickup"].includes(value)) return "both";
  if (["both", "pickup_or_shipping", "shipping_or_pickup"].includes(delivery)) return "both";
  return "shipping";
}

/**
 * The listing price in minor units, or `null` when the label cannot be read as
 * a price ("Free", "Request access", anything unparseable).
 *
 * Deliberately mirrors `parse_price_label_to_cents` in `bot.py` — the same
 * regex, the same currency-prefix handling — because this number is used to
 * state what the buyer will be charged. Returning `null` rather than 0 keeps
 * "I couldn't read this" distinct from "it's free": the checkout screen shows
 * an amount on its Pay button only when this returns a number, so a label this
 * function cannot parse produces no dollar promise at all.
 */
export function marketplaceListingPriceMinor(listing: MarketplaceListing): number | null {
  const text = String(listing.price_label || "").trim();
  if (!text) return null;
  if (["free", "request access", "paid later", "premium later"].includes(text.toLowerCase())) return null;
  const match = /([A-Z]{3})?\s*\$?\s*([0-9]+(?:\.[0-9]{1,2})?)/.exec(text.toUpperCase());
  if (!match) return null;
  const minor = Math.round(Number(match[2]) * 100);
  return Number.isFinite(minor) && minor > 0 ? minor : null;
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
