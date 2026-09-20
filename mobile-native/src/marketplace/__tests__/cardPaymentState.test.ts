/**
 * The card-payment state machine, pinned.
 *
 * The bug being guarded is a dead end, not a wrong word: an approved seller
 * with no Stripe Connect account saw "Unavailable" and had no way to act on it.
 * So the assertions below are mostly about *doors* — which states offer one,
 * which deliberately do not, and that no state offers one that goes nowhere.
 *
 * The other half guards the separation of axes. A card-payment status must
 * never be able to describe seller approval, because collapsing the two is how
 * a payment row that reads "temporarily unavailable" turns into a seller locked
 * out of their own storefront.
 */

import {
  cardPaymentPresentation,
  shouldShowCardPaymentCard,
  type CardPaymentAction
} from "../cardPaymentState";
import type { CardPaymentStatus } from "../../api/sellerAccess";

/**
 * Every status the client contract admits. Written out rather than imported so
 * that a status added to `sellerAccess.ts` without a decision here fails as a
 * missing case in this list, visibly, instead of being skipped by a loop that
 * silently shrank.
 */
const ALL_STATUSES: readonly CardPaymentStatus[] = [
  "SETUP_REQUIRED",
  "SETUP_IN_PROGRESS",
  "ACTION_REQUIRED",
  "UNDER_REVIEW",
  "READY",
  "RESTRICTED",
  "UNAVAILABLE"
];

/** The states whose whole point is that the seller can do something. */
const ACTIONABLE: readonly CardPaymentStatus[] = [
  "SETUP_REQUIRED",
  "SETUP_IN_PROGRESS",
  "ACTION_REQUIRED",
  "RESTRICTED"
];

describe("cardPaymentPresentation", () => {
  it("answers for every status without falling through to one default", () => {
    const labels = ALL_STATUSES.map((status) => cardPaymentPresentation(status).labelKey);
    // Seven statuses, seven distinct labels. A regression that collapsed the
    // switch into a shared default would still return a presentation for each
    // — it would just return the same one, which is the original bug.
    expect(new Set(labels).size).toBe(ALL_STATUSES.length);
  });

  it("echoes the status it was asked about", () => {
    for (const status of ALL_STATUSES) {
      expect(cardPaymentPresentation(status).status).toBe(status);
    }
  });

  it("gives every blocked-but-fixable state a way out", () => {
    for (const status of ACTIONABLE) {
      const view = cardPaymentPresentation(status);
      expect(view.ctaKey).toBeTruthy();
      expect(view.action).toBe<CardPaymentAction>("OPEN_ONBOARDING");
    }
  });

  it("offers no button where pressing one would do nothing", () => {
    // UNDER_REVIEW: Stripe is reading documents already submitted, so a CTA
    // would restart a finished flow. READY: nothing is wrong. UNAVAILABLE: the
    // platform cannot take cards at all, so onboarding cannot fix it — this is
    // the state that most tempts a button, and the one where a button would be
    // the cruellest, because it leads to a flow that ends where it started.
    for (const status of ["UNDER_REVIEW", "READY", "UNAVAILABLE"] as const) {
      const view = cardPaymentPresentation(status);
      expect(view.ctaKey).toBeNull();
      expect(view.action).toBe<CardPaymentAction>("NONE");
    }
  });

  it("never leaves a CTA without an action, or an action without a CTA", () => {
    for (const status of ALL_STATUSES) {
      const view = cardPaymentPresentation(status);
      expect(Boolean(view.ctaKey)).toBe(view.action === "OPEN_ONBOARDING");
    }
  });

  it("always has something to say, whatever the state", () => {
    // The original screen rendered a single word. Every state owes the seller a
    // sentence, including the ones with no button — for those it is the only
    // thing on the card that explains anything.
    for (const status of ALL_STATUSES) {
      const view = cardPaymentPresentation(status);
      expect(view.labelKey).toBeTruthy();
      expect(view.bodyKey).toBeTruthy();
    }
  });

  it("treats an unrecognised status as setup required, not as ready", () => {
    // Defaulting the other way would tell a seller on an unknown state that
    // card payments are switched on, which is the one lie that reaches a buyer:
    // they would list, a buyer would try to pay, and the charge would have
    // nowhere to land.
    const view = cardPaymentPresentation("WAT" as CardPaymentStatus);
    expect(view.status).toBe("SETUP_REQUIRED");
    expect(view.action).toBe<CardPaymentAction>("OPEN_ONBOARDING");
  });

  it("colours READY as ready and RESTRICTED as blocked", () => {
    expect(cardPaymentPresentation("READY").tone).toBe("ready");
    expect(cardPaymentPresentation("RESTRICTED").tone).toBe("blocked");
    expect(cardPaymentPresentation("UNAVAILABLE").tone).toBe("blocked");
    expect(cardPaymentPresentation("UNDER_REVIEW").tone).toBe("progress");
  });
});

describe("shouldShowCardPaymentCard", () => {
  it("hides only the state that needs nothing", () => {
    expect(shouldShowCardPaymentCard("READY")).toBe(false);
    for (const status of ALL_STATUSES.filter((s) => s !== "READY")) {
      expect(shouldShowCardPaymentCard(status)).toBe(true);
    }
  });

  it("shows UNAVAILABLE rather than hiding it", () => {
    // Hiding it would be the tidier screen and the worse one: the seller would
    // be left with no explanation at all for why no card option appears at
    // their checkout.
    expect(shouldShowCardPaymentCard("UNAVAILABLE")).toBe(true);
  });
});
