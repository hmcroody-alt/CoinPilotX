/**
 * Buyer-facing copy for Marketplace commerce failures.
 *
 * The server answers a rejection with a stable `error_code` plus human prose.
 * The code is the contract; the prose is not. Mapping here — rather than
 * rendering `error.message` — keeps three promises:
 *
 *   1. Reworded server copy never silently changes what the buyer reads.
 *   2. An unhandled server exception (which carries no code, only a trace id
 *      and "PulseSoc hit a temporary service issue") never reaches the buyer as
 *      the dominant message. The trace id is diagnostics, not an explanation.
 *   3. Every failure names the buyer's next move. "Sold out" and "the seller
 *      paused their store" are different situations and must read differently.
 *
 * Vocabulary is shared with `services/marketplace_cart_routes.py` — keep the
 * two in step. Codes not listed here fall back to the caller's own sentence.
 */

import { PulseApiError } from "./pulseApi";

export type MarketplaceErrorCode =
  | "ITEM_UNAVAILABLE"
  | "OUT_OF_STOCK"
  | "SELLER_UNAVAILABLE"
  | "INVALID_QUANTITY"
  | "ADDRESS_REQUIRED"
  | "FULFILLMENT_REQUIRED"
  | "PAYMENT_UNAVAILABLE"
  | "PAYMENT_CONFIGURATION_ERROR"
  | "PAYMENT_FAILED"
  | "ORDER_TOTAL_BELOW_MINIMUM"
  | "PRICE_CHANGED"
  | "CART_FULL"
  | "OWN_LISTING"
  | "LOGIN_REQUIRED"
  | "NETWORK_ERROR";

const COPY: Record<MarketplaceErrorCode, string> = {
  ITEM_UNAVAILABLE: "This item is no longer available.",
  OUT_OF_STOCK: "This item is out of stock.",
  SELLER_UNAVAILABLE: "This seller is not accepting orders right now.",
  INVALID_QUANTITY: "Choose a quantity the seller still has in stock.",
  ADDRESS_REQUIRED: "Add a delivery address to continue.",
  FULFILLMENT_REQUIRED: "Choose pickup or delivery before you pay.",
  PAYMENT_UNAVAILABLE: "Payment is unavailable right now. No card was charged.",
  PAYMENT_CONFIGURATION_ERROR: "Payments are temporarily unavailable. No card was charged.",
  PAYMENT_FAILED: "Your card could not be charged. No card was charged.",
  // Named as the order's problem, not the platform's: retrying, updating the
  // app or trying another card will never clear it, so copy that suggests any
  // of those sends the buyer round a loop with no exit.
  ORDER_TOTAL_BELOW_MINIMUM:
    "This order total is below the minimum amount card payments accept. No card was charged.",
  PRICE_CHANGED: "The price changed. Review the new price before you continue.",
  CART_FULL: "Your cart is full. Remove an item to add another.",
  OWN_LISTING: "This is your own listing.",
  LOGIN_REQUIRED: "Sign in to continue.",
  NETWORK_ERROR: "PulseSoc could not be reached. Check your connection and try again."
};

/** Normalize whatever the server sent into one of the codes above, or "". */
export function marketplaceErrorCode(error: unknown): MarketplaceErrorCode | "" {
  if (!(error instanceof PulseApiError)) return "";
  const raw = String(error.code || "").toUpperCase();
  if (raw in COPY) return raw as MarketplaceErrorCode;
  // `pulseApi` reports an unreachable host as `request_unreachable`; a 503 with
  // no code is the same class of problem from the buyer's side.
  if (raw === "REQUEST_UNREACHABLE" || error.status === 0) return "NETWORK_ERROR";
  return "";
}

/**
 * The sentence to show the buyer. `fallback` is the calling screen's own
 * description of what failed, used only when the server gave no usable code —
 * which is exactly the unhandled-500 case, where the server's own message is
 * about PulseSoc's health rather than about the buyer's item.
 */
export function buyerErrorCopy(error: unknown, fallback: string): string {
  const code = marketplaceErrorCode(error);
  if (code) return COPY[code];
  if (error instanceof PulseApiError && error.status >= 500) return fallback;
  // A handled 4xx with no code still carries deliberate, buyer-safe prose.
  if (error instanceof PulseApiError && error.message) return error.message;
  return fallback;
}
