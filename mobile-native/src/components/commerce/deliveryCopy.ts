/**
 * The words a delivery estimate is allowed to use, and the icon beside them.
 *
 * Split out of the component on purpose. Everything the mission forbids is a
 * *sentence*, not a layout: "Guaranteed", a single fabricated date, a number the
 * server never sent, an empty space where a refusal should be. Those are cheap
 * to pin in a plain unit test and expensive to pin through a renderer, so the
 * copy lives here as a pure function of one estimate and the component becomes a
 * thin shell around it.
 *
 * ## The rules this module enforces
 *
 * - **Never "Guaranteed".** §58 and §125. The estimate carries a `guaranteed`
 *   flag which the server always sets false; this module does not consult it at
 *   all, because there is no branch it could enable that would be allowed to say
 *   the word. Every window is prefixed "Estimated".
 * - **Never a single date.** §124. The server computes a range — a min and a max
 *   — and collapsing it to "arrives 18 Mar" is precision nobody has. When the
 *   two ends fall on the same day the range formatter says that one day, which
 *   is the server's own claim, not this module's rounding.
 * - **Never silence.** Every state produces a line. A failure that rendered
 *   nothing would leave the screen to fill the gap, and the gap is where the old
 *   "arranged with the seller after your order is confirmed" copy lived.
 * - **Say whose problem it is, without saying how.** A buyer does not need to
 *   know that a supplier API timed out or that an operator has not set
 *   `PULSE_DELIVERY_HANDLING_DAYS`. They need to know whether to wait, to pick a
 *   country, or to stop. So the reasons are grouped by *what the buyer should
 *   do*, and the raw reason stays in the estimate for support and analytics.
 *
 * ## Why the reasons are grouped rather than enumerated one-to-one
 *
 * There are ten-odd refusal reasons on the server and three useful buyer
 * outcomes: retryable ("we could not get a date just now"), actionable ("tell us
 * where"), and terminal ("this does not ship there"). A one-to-one table would
 * be ten strings that all mean "try again", and the day the server adds an
 * eleventh reason an unmapped string would fall through to nothing. Grouping
 * means an unrecognised reason lands in the retryable bucket, which is the safe
 * default: it says less than it knows rather than more.
 */

import type { DeliveryAnswer, DeliveryEstimate } from "../../api/delivery";
import { formatDeliveryWindow } from "../../api/delivery";

/** What the buyer should do about this line. Drives the icon and the tone, and
 * is what a caller consults to decide whether to offer a retry. */
export type DeliveryTone = "ESTIMATE" | "RETRYABLE" | "ACTIONABLE" | "TERMINAL";

export type DeliveryCopy = {
  tone: DeliveryTone;
  /** The delivery line itself. Never empty. */
  text: string;
  /** `FREE Shipping`, or `null` when the server did not say it is free.
   * Standardized by §3/§32/§59 — and read from the server rather than written
   * here, because a shipped binary that asserted it would keep asserting it
   * through a policy change. */
  shipping: string | null;
  /** Ionicons name. Chosen by tone, so a buyer can tell a date from a problem
   * without reading. */
  icon: "calendar-outline" | "time-outline" | "location-outline" | "close-circle-outline";
  /** Whether a retry affordance makes sense. A missing handling policy and a
   * timed-out supplier are both fixed by asking again later; an unserviceable
   * corridor is not, and a retry button beside it is a button that lies. */
  retryable: boolean;
  /**
   * The window came from a cached supplier quote rather than a live one.
   *
   * Surfaced as a separate flag rather than folded into `text` for two reasons.
   * It is a *confidence* qualifier, so it belongs in the presentation layer as a
   * muted footnote rather than inside the sentence a buyer reads first — and a
   * cached provider quote is still the provider's own number, so appending
   * "(may be out of date)" to it would talk a buyer out of a window the server
   * was willing to stand behind. §22's stale-while-revalidate is a serving
   * decision; this is the only trace of it the buyer sees.
   */
  stale: boolean;
};

/** The server's own free-shipping token. Compared, not written: this module
 * renders the phrase only when the server sent the value. */
const SHIPPING_FREE = "FREE";
const FREE_SHIPPING_LINE = "FREE Shipping";

/**
 * Reasons the buyer can act on by telling us where the parcel is going.
 *
 * `destination_unresolved` is the server saying it had nothing to resolve from —
 * no session address, no prior order, no usable edge hint. That is not an error
 * and a retry will not fix it.
 */
const ACTIONABLE_REASONS = new Set(["destination_unresolved"]);

/**
 * Reasons that are settled: no amount of waiting or retrying changes them.
 *
 * `not_supplier_fulfilled` is in here because it is not a failure at all — the
 * seller ships it themselves, and the honest line is about the seller, not about
 * a date we could not get. `fulfillment_undeclared` joins it: a listing whose
 * fulfillment type nobody has declared cannot be quoted by anyone, and telling a
 * buyer to try again invites them to keep pulling a lever with nothing attached.
 */
const TERMINAL_REASONS = new Set([
  "not_supplier_fulfilled",
  "fulfillment_undeclared"
]);

/** Per-reason lines for the ones where a generic sentence would be unhelpful.
 * Everything absent from this table falls through to its tone's default, which
 * is why an unrecognised reason is safe. */
const REASON_LINES: Record<string, string> = {
  // Not a problem — a different shipper. The seller-arranged sentence is correct
  // here and *only* here, which is the distinction the old blanket copy lost by
  // saying it on every listing.
  not_supplier_fulfilled:
    "This seller ships this item themselves — delivery is arranged with them after your order is confirmed.",
  fulfillment_undeclared: "Delivery for this item has not been set up yet.",
  destination_unresolved: "Choose a delivery country to see an estimated arrival date.",
  // Our data, not the supplier's: a variant with no weight or dimensions. Says
  // nothing about which, because "the seller has not entered a parcel weight" is
  // the platform's plumbing showing through the product page.
  variant_data_incomplete: "An estimated arrival date is not available for this item yet."
};

const TONE_DEFAULTS: Record<DeliveryTone, string> = {
  ESTIMATE: "",
  // Deliberately not "something went wrong". The buyer's question is whether to
  // keep going, and the answer is yes: the estimate is missing, the item is not.
  RETRYABLE: "An estimated arrival date is not available right now.",
  ACTIONABLE: "Choose a delivery country to see an estimated arrival date.",
  TERMINAL: "This item cannot be delivered to your location."
};

const TONE_ICONS: Record<DeliveryTone, DeliveryCopy["icon"]> = {
  ESTIMATE: "calendar-outline",
  RETRYABLE: "time-outline",
  ACTIONABLE: "location-outline",
  TERMINAL: "close-circle-outline"
};

function toneFor(estimate: DeliveryEstimate): DeliveryTone {
  if (estimate.state === "ESTIMATED") return "ESTIMATE";
  // §55: an unserviceable corridor is settled, and it is the one state that has
  // to be able to stop a checkout rather than merely disappoint a product page.
  if (estimate.state === "UNSUPPORTED_ROUTE") return "TERMINAL";
  const reason = estimate.reason || "";
  if (ACTIONABLE_REASONS.has(reason)) return "ACTIONABLE";
  if (TERMINAL_REASONS.has(reason)) return "TERMINAL";
  return "RETRYABLE";
}

/**
 * One estimate → one line of copy. Total: there is no input for which this
 * returns nothing.
 *
 * `destinationCountry` is the label the server resolved to, not the buyer's
 * guess. It is appended to a real window only when the destination is `known`,
 * because "Estimated 16 – 21 Mar to United States" is a stronger claim than
 * "Estimated 16 – 21 Mar" and must not be made on a corridor the server inferred
 * from an edge header it is not sure about.
 */
export function deliveryCopy(answer: DeliveryAnswer): DeliveryCopy {
  const { delivery, destination } = answer;
  const tone = toneFor(delivery);
  const shipping = delivery.shippingPrice === SHIPPING_FREE ? FREE_SHIPPING_LINE : null;
  const icon = TONE_ICONS[tone];

  if (tone === "ESTIMATE") {
    const window = formatDeliveryWindow(delivery);
    if (window) {
      // "Estimated", always. There is no branch in this module that reaches the
      // word "Guaranteed", and the one-day case still says "Estimated" — a
      // window that happens to be a single day is still an estimate.
      const suffix =
        destination.known && destination.country ? ` to ${destination.country}` : "";
      return {
        tone,
        text: `Estimated delivery ${window}${suffix}`,
        shipping,
        icon,
        retryable: false,
        stale: delivery.confidence === "PROVIDER_CACHED"
      };
    }
    // A state that says ESTIMATED with no formattable window should have been
    // caught by the parser. Reaching here means something upstream changed; the
    // only safe thing to say is nothing about dates.
    return {
      tone: "RETRYABLE",
      text: TONE_DEFAULTS.RETRYABLE,
      shipping,
      icon: TONE_ICONS.RETRYABLE,
      retryable: true,
      stale: false
    };
  }

  const text = REASON_LINES[delivery.reason || ""] || TONE_DEFAULTS[tone];
  // `stale` is false for every non-window state. There is nothing for a
  // freshness qualifier to qualify when there is no date.
  return { tone, text, shipping, icon, retryable: tone === "RETRYABLE", stale: false };
}

/**
 * The line shown while the request is in flight.
 *
 * A separate function rather than a fifth tone, because a pending request is not
 * an estimate with unknown fields — it is the absence of an answer, and the
 * caller knows which of the two it has without inspecting anything. Kept here so
 * the loading sentence is reviewed alongside the ones it precedes: "Checking
 * delivery…" must not read as though a date is certain to follow.
 */
export const DELIVERY_LOADING_TEXT = "Checking delivery options…";

/**
 * Whether this estimate is allowed to hold up a purchase.
 *
 * Only a settled, unserviceable corridor. Not a supplier outage, not a missing
 * handling policy, not an unresolved destination — blocking on any of those
 * would mean an operator who has not finished configuring delivery has silently
 * closed the store, which is the opposite of the shadow-mode property the server
 * side was built to have.
 */
export function blocksCheckout(estimate: DeliveryEstimate): boolean {
  return estimate.state === "UNSUPPORTED_ROUTE";
}
