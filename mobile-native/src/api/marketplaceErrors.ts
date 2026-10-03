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
  | "FULFILLMENT_DETAILS_REQUIRED"
  | "ITEM_NEEDS_OWN_CHECKOUT"
  | "PAYMENT_UNAVAILABLE"
  | "PAYMENT_CONFIGURATION_ERROR"
  | "PAYMENT_FAILED"
  | "ORDER_TOTAL_BELOW_MINIMUM"
  | "PRICE_CHANGED"
  | "NO_AUTHORITATIVE_PRICE"
  | "VARIANT_SELECTION_REQUIRED"
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
  FULFILLMENT_REQUIRED: "Choose how you want this order fulfilled before you pay.",
  // The server names the offending field in `field`; this is the fallback for
  // a client that is a build behind and does not know the field it names.
  FULFILLMENT_DETAILS_REQUIRED: "Some order details are still missing. Check the order details step.",
  ITEM_NEEDS_OWN_CHECKOUT: "Bookings, services and events are checked out one at a time. Buy this item on its own.",
  PAYMENT_UNAVAILABLE: "Payment is unavailable right now. No card was charged.",
  // Was "Payments are temporarily unavailable. No card was charged." — both
  // halves false in the October 2026 incident, where payments were entirely
  // available and the fault was one order's setup. "Temporarily" was the worse
  // half: it promised that waiting would fix a thing waiting cannot fix.
  //
  // This code covers both a retryable cause (our idempotency key burned against
  // changed parameters) and unretryable ones (a bad key, a destination Stripe
  // will not accept). A code-keyed map cannot tell them apart, so this sentence
  // is written to be true of both: it names the stage that failed, promises
  // nothing about retrying, and forbids nothing either. `buyerCanRetry` below
  // reads the server's actual verdict for the surfaces that need to act on it.
  PAYMENT_CONFIGURATION_ERROR: "We couldn't open secure payment for this order. No card was charged.",
  PAYMENT_FAILED: "Your card could not be charged. No card was charged.",
  // Named as the order's problem, not the platform's: retrying, updating the
  // app or trying another card will never clear it, so copy that suggests any
  // of those sends the buyer round a loop with no exit.
  ORDER_TOTAL_BELOW_MINIMUM:
    "This order total is below the minimum amount card payments accept. No card was charged.",
  PRICE_CHANGED: "The price changed. Review the new price before you continue.",
  // The seller has not priced this listing. Nothing the buyer can do clears it,
  // so the copy does not suggest retrying.
  NO_AUTHORITATIVE_PRICE: "This item does not have a price yet, so it cannot be bought.",
  // The listing is for sale at more than one price and no option was chosen.
  // Distinct from "unavailable": the buyer's next move exists.
  VARIANT_SELECTION_REQUIRED: "Choose an option before you check out.",
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

/**
 * Whether a payment CTA may stay live after this failure.
 *
 * Read from `details.retryable`, which the three checkout lanes now return
 * alongside `error_code`. It is a separate field rather than a new code on
 * purpose: `MarketplaceErrorCode` is a closed union with a copy map beside it,
 * so a new code on a server talking to an older build arrives with no copy at
 * all. Meaning rides on new fields; codes stay stable.
 *
 * Defaults to `true` when the server sends no verdict — an older deployment, or
 * a failure from outside the checkout lanes. That preserves the behaviour every
 * screen already had, so this function can only ever *remove* a CTA that should
 * not have been offered, never withhold one that should.
 *
 * The incident this exists for: a buyer held on a screen showing a refusal and
 * an active "Continue to secure payment" button for five hours, because
 * nothing on the wire said which of the two the server meant.
 */
export function buyerCanRetry(error: unknown): boolean {
  if (!(error instanceof PulseApiError)) return true;
  const verdict = error.details?.retryable;
  if (typeof verdict === "boolean") return verdict;
  const cta = error.details?.cta;
  if (typeof cta === "string") return cta !== "blocked";
  return true;
}
