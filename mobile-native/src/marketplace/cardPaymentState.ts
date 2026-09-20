/**
 * What the seller is told about card payments, and whether there is a way out.
 *
 * The defect this exists for was not a wrong label. It was a dead end: an
 * approved seller whose Stripe Connect account had never been created saw the
 * word "Unavailable" and nothing else — no explanation of what was unavailable,
 * no statement that their *other* payment methods still worked, and above all
 * no button. The account was one hosted onboarding flow away from taking cards
 * and the app never said so.
 *
 * So every state here answers three questions, and the third is the one that was
 * missing:
 *
 *   1. What is the state? (`labelKey` — the chip)
 *   2. What does that mean for the seller? (`bodyKey` — a sentence, not a word)
 *   3. What, if anything, do they do about it? (`ctaKey` + `action`)
 *
 * Three of the seven states have no CTA, and that is a decision rather than an
 * omission: `UNDER_REVIEW` and `READY` need nothing from the seller, and
 * `UNAVAILABLE` means the *platform* cannot offer card payments at all, so a
 * button would send them to a flow that cannot help. Those three say so in
 * their body copy instead. A CTA that leads nowhere is the original bug wearing
 * a button.
 *
 * ## Why this is not the seller-approval gate
 *
 * `card_payment_status` and `seller_application_status` are separate axes and
 * are never merged — see `api/sellerAccess.ts`. An approved seller with no
 * Connect account keeps the Store and the Selling tools and simply cannot take
 * cards; they can still sell for cash on collection. Collapsing the two is how
 * a "temporarily unavailable" payment row locks someone out of their own
 * storefront.
 *
 * ## Why the CTA navigates instead of acting
 *
 * `OPEN_ONBOARDING` routes to the existing payout-onboarding layer
 * (`MoneyLayer`, `layer: "payout_onboarding"`), which already owns the whole
 * hand-off: it calls `POST /api/pulse/payouts/connect` through
 * `connectMarketplacePayout`, distinguishes the three success-shaped failures
 * that route can return (`payoutOnboardingOutcome`), opens Stripe's own hosted
 * page, and re-checks status when the app comes back to the foreground.
 *
 * Reimplementing any of that here would give the app two doors to one flow,
 * and the second door would be the one that forgets that `ok: true` with no
 * link means "this deployment has no Stripe key", not "you're set up". The
 * money layer already carries a comment forbidding exactly that duplication.
 */

import type { CardPaymentStatus } from "../api/sellerAccess";

export type CardPaymentTone = "ready" | "progress" | "attention" | "blocked";

export type CardPaymentAction = "OPEN_ONBOARDING" | "NONE";

export type CardPaymentPresentation = {
  status: CardPaymentStatus;
  /** i18n suffix under `commerce:sellerPayments`. */
  labelKey: string;
  bodyKey: string;
  /** `null` when this state asks nothing of the seller. */
  ctaKey: string | null;
  action: CardPaymentAction;
  tone: CardPaymentTone;
};

/**
 * The presentation for one card-payment status.
 *
 * The switch is total over the union, so a status added to the server without
 * deciding what the seller is told about it is a TypeScript error rather than a
 * card that silently renders the `SETUP_REQUIRED` copy at someone whose account
 * was restricted.
 */
export function cardPaymentPresentation(status: CardPaymentStatus): CardPaymentPresentation {
  switch (status) {
    case "READY":
      return {
        status,
        labelKey: "readyLabel",
        bodyKey: "readyBody",
        ctaKey: null,
        action: "NONE",
        tone: "ready"
      };
    case "SETUP_IN_PROGRESS":
      return {
        status,
        labelKey: "setupInProgressLabel",
        bodyKey: "setupInProgressBody",
        ctaKey: "setupInProgressCta",
        action: "OPEN_ONBOARDING",
        tone: "progress"
      };
    case "ACTION_REQUIRED":
      return {
        status,
        labelKey: "actionRequiredLabel",
        bodyKey: "actionRequiredBody",
        ctaKey: "actionRequiredCta",
        action: "OPEN_ONBOARDING",
        tone: "attention"
      };
    case "UNDER_REVIEW":
      // Stripe is reading their documents. There is nothing to press, and a
      // button here would restart an onboarding flow that is already complete.
      return {
        status,
        labelKey: "underReviewLabel",
        bodyKey: "underReviewBody",
        ctaKey: null,
        action: "NONE",
        tone: "progress"
      };
    case "RESTRICTED":
      // Restricted is recoverable — Stripe states the requirement on its own
      // dashboard — so this one keeps its door open.
      return {
        status,
        labelKey: "restrictedLabel",
        bodyKey: "restrictedBody",
        ctaKey: "restrictedCta",
        action: "OPEN_ONBOARDING",
        tone: "blocked"
      };
    case "UNAVAILABLE":
      // The platform cannot offer card payments, so onboarding cannot fix it.
      // The body copy's job is to say the seller's other methods still work,
      // because the word alone reads as "your store is broken".
      return {
        status,
        labelKey: "unavailableLabel",
        bodyKey: "unavailableBody",
        ctaKey: null,
        action: "NONE",
        tone: "blocked"
      };
    case "SETUP_REQUIRED":
    default:
      return {
        status: "SETUP_REQUIRED",
        labelKey: "setupRequiredLabel",
        bodyKey: "setupRequiredBody",
        ctaKey: "setupRequiredCta",
        action: "OPEN_ONBOARDING",
        tone: "attention"
      };
  }
}

/**
 * Should the card be shown at all?
 *
 * `READY` is the one state worth hiding. A seller already taking cards does not
 * need a permanent row telling them so on a dashboard whose whole job is to
 * surface the things that need attention — and the Payments hub still reports
 * it for anyone who goes looking. Every other state is either an unfinished
 * task or a reason buyers cannot pay, and both belong on the dashboard.
 */
export function shouldShowCardPaymentCard(status: CardPaymentStatus): boolean {
  return status !== "READY";
}
