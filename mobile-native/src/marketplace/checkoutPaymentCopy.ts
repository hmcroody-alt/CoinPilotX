/**
 * What the checkout says under "Total to pay", and whether the CTA may fire.
 *
 * ## The bug this exists for
 *
 * The screen showed, at the same time and inches apart:
 *
 *   Card / Stripe — "Pay by card now. The seller is paid after the order completes."
 *   Total to pay  — $40.92
 *                   "Marketplace card payments are temporarily unavailable."
 *   [ Pay securely · $40.92 ]
 *
 * Every other sentence on that screen had already been moved to the server,
 * because there is more than one reason card can be off and they are not
 * interchangeable — a paused platform rail is the operator's doing, a seller who
 * never finished Connect onboarding is not, and telling a buyer "temporarily"
 * about the second is a lie about when it will change. One sentence was missed
 * in that migration and stayed a literal. A literal cannot follow a flag, so
 * when the rail opened the sentence went on claiming it was shut.
 *
 * ## Why a function rather than three more ternaries
 *
 * The settlement line answers two independent questions — *is the card lane
 * open* (the server's fact) and *do we know the final amount* (this screen's
 * fact) — and the old code answered the first from a constant while nesting it
 * inside the second. Pulling the decision out makes the pairing explicit, and
 * makes the invariant below something a test can assert over the whole matrix
 * instead of over the handful of combinations a rendered screen can be coaxed
 * into.
 *
 * ## The invariant
 *
 * A lane that is shut must never be presented with a live CTA. `ctaEnabled` and
 * the unavailable sentence are produced by the same call, so they cannot drift:
 * there is no input for which this returns the "temporarily unavailable"
 * sentence *and* `ctaEnabled: true`. `checkoutPaymentCopyIsConsistent` states it
 * as an assertion so the test does not have to restate the rule and get it
 * subtly different.
 *
 * ## What this does NOT decide
 *
 * Not eligibility. The server owns that, twice — the platform flag and the
 * seller's Connect state — and refuses a card start on its own regardless of
 * what this returns. Nothing here weakens that; `ctaEnabled: false` only stops
 * the buyer walking into a refusal they could have been warned about.
 *
 * Not *why* a seller cannot take cards. `payment_unavailable_message` arrives
 * already collapsed: `marketplace_card_capability.buyer_view` deliberately
 * replaces every seller-private reason with one sentence so a buyer cannot
 * learn a seller's onboarding or compliance state. This module passes that
 * sentence through verbatim and must never try to refine it — the granular
 * states (SETUP_REQUIRED, SETUP_IN_PROGRESS, RESTRICTED, …) belong to the
 * seller's own surfaces, where `cardPaymentState.ts` renders them.
 */

export type CheckoutPaymentLane = "cash" | "card";

/** Which branch produced the sentence. Present so tests name a state rather
 *  than matching prose, and so a copy edit does not silently retarget a test. */
export type CheckoutSettlementReason =
  | "cash_known_amount"
  | "cash_open_amount"
  | "card_unavailable"
  | "card_confirmed_after_payment"
  | "card_open_amount";

export type CheckoutSettlementCopy = {
  /** The sentence rendered under the total. */
  text: string;
  /** Whether the lane's primary CTA may be pressed. Busy/validation state is
   *  the screen's to add; this is the lane-capability half only. */
  ctaEnabled: boolean;
  reason: CheckoutSettlementReason;
};

export type CheckoutSettlementInput = {
  lane: CheckoutPaymentLane;
  /** The server's verdict for this seller. Never inferred locally. */
  cardPaymentsAvailable: boolean;
  /** The server's sentence for why card is shut. Passed through verbatim. */
  cardUnavailableMessage: string;
  /** True when this screen knows the exact charge before the payment page. */
  knowsFinalAmount: boolean;
};

/** Shown when the server says card is shut but sends no sentence. Should not
 *  happen — `fetchCheckoutOptions` substitutes its own fallback — but a blank
 *  line under a disabled button explains nothing, and silence is the failure
 *  mode this whole change exists to remove. */
export const CARD_UNAVAILABLE_FALLBACK = "Card payments are temporarily unavailable.";

export function checkoutSettlementCopy(input: CheckoutSettlementInput): CheckoutSettlementCopy {
  const { lane, cardPaymentsAvailable, cardUnavailableMessage, knowsFinalAmount } = input;

  if (lane === "cash") {
    // Cash is never gated on the card rail. A buyer who chose cash is not being
    // refused anything, and the card rail's state is none of this line's
    // business — saying otherwise here is how the footnote used to announce a
    // pause to people who had simply preferred cash.
    return knowsFinalAmount
      ? {
          text: "No card or Stripe charge will start. Pay the seller directly when you pick up or meet in person.",
          ctaEnabled: true,
          reason: "cash_known_amount"
        }
      : {
          text: "No card or Stripe charge will start. The amount isn't set here — agree it with the seller when you pick up or meet in person.",
          ctaEnabled: true,
          reason: "cash_open_amount"
        };
  }

  // Availability is asked before the amount. The old code nested it the other
  // way round, which is why an unavailable rail could only be mentioned on the
  // one branch that happened to know the total.
  if (!cardPaymentsAvailable) {
    return {
      text: (cardUnavailableMessage || "").trim() || CARD_UNAVAILABLE_FALLBACK,
      ctaEnabled: false,
      reason: "card_unavailable"
    };
  }

  return knowsFinalAmount
    ? {
        text: "Your order will be confirmed after your card payment is successfully processed.",
        ctaEnabled: true,
        reason: "card_confirmed_after_payment"
      }
    : {
        text: "The exact amount is confirmed on the secure payment page before you authorize anything.",
        ctaEnabled: true,
        reason: "card_open_amount"
      };
}

/**
 * The consistency rule, as a predicate.
 *
 * False for any result that both refuses the lane and leaves the CTA live, or
 * that enables the CTA while showing the server's unavailable sentence. Stated
 * here rather than inside the test so there is one wording of the rule.
 */
export function checkoutPaymentCopyIsConsistent(
  input: CheckoutSettlementInput,
  copy: CheckoutSettlementCopy
): boolean {
  const laneIsShut = input.lane === "card" && !input.cardPaymentsAvailable;
  if (laneIsShut && copy.ctaEnabled) return false;
  if (laneIsShut && copy.reason !== "card_unavailable") return false;
  // An open lane must not be described with the refusal sentence.
  if (!laneIsShut && copy.reason === "card_unavailable") return false;
  return true;
}
