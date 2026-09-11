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

/** The checkout's ceiling, mirrored from `MAX_PRICE_LABEL_CENTS` in `bot.py`. */
const MAX_PRICE_LABEL_MINOR = 99_999_999;

/** Labels a seller may choose that deliberately name no price. */
const UNPRICED_LABELS = ["free", "request access", "paid later", "premium later"];

/**
 * The listing price in minor units, or `null` when the label cannot be read as
 * a price ("Free", "Request access", anything unparseable).
 *
 * Mirrors `parse_price_label_to_cents` in `bot.py`, because this number is used
 * to state what the buyer will be charged and the server charges from the same
 * label. The mirror is not maintained by intention: every case lives in
 * `__tests__/fixtures/priceLabelParity.json`, which both this app's suite and
 * the backend's read, so the two implementations cannot drift quietly.
 *
 * They had already drifted. This regex used to be `[0-9]+(\.[0-9]{1,2})?` while
 * the server's was `[0-9][0-9,]*(\.[0-9]{1,2})?`, and the server *writes* labels
 * with thousands separators (`marketplace_normalize_price_label` formats with
 * `,`). So the digit run stopped at the comma: a $12,345.67 listing put "$12.00"
 * on the Pay button, above a sentence promising that figure *is* the charge, and
 * then charged $12,345.67. Every listing under $1,000 was correct, which is why
 * every test of this function was too.
 *
 * Returning `null` rather than 0 keeps "I couldn't read this" distinct from
 * "it's free": the checkout screen shows an amount on its Pay button only when
 * this returns a number, so a label this function cannot parse produces no
 * dollar promise at all.
 */
export function marketplaceListingPriceMinor(listing: MarketplaceListing): number | null {
  const text = String(listing.price_label || "").trim();
  if (!text) return null;
  if (UNPRICED_LABELS.includes(text.toLowerCase())) return null;
  // A leading minus is refused outright rather than read as its magnitude. The
  // server does the same; without it "-$5.00" promised $5.00 and then failed at
  // checkout, because the server had already read it as nothing.
  if (text.startsWith("-")) return null;
  const match = /([A-Z]{3})?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/.exec(text.toUpperCase());
  if (!match) return null;
  const minor = Math.round(Number(match[2].replace(/,/g, "")) * 100);
  if (!Number.isFinite(minor) || minor <= 0) return null;
  // The server clamps above its ceiling instead of refusing, so showing the
  // unclamped figure would understate nothing but overstate the charge.
  return Math.min(minor, MAX_PRICE_LABEL_MINOR);
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
