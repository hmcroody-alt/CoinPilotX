/**
 * The checkout's payment sentence, over every state rather than the reachable ones.
 *
 * The contradiction this guards against shipped *through* a passing test file.
 * `MarketplaceCheckoutCardCopy.test.tsx` pins the card row's unavailable copy
 * carefully, but every one of its cases leaves the lane on cash — the default —
 * and the broken sentence only rendered on the card lane. So the literal
 * "Marketplace card payments are temporarily unavailable." sat under a live
 * "Pay securely · $40.92" with a green suite either side of it.
 *
 * Testing the decision as a function rather than through the screen is what
 * makes the matrix reachable at all. The screen cannot be driven into
 * `card selected + card unavailable`: options start closed, the row is disabled
 * while closed, and they are fetched once. That combination is defended against
 * in three places in the screen and is *unreachable through its own UI* — which
 * is precisely the kind of state that rots, because nothing can exercise it.
 * Here it is one line.
 */

import {
  CARD_UNAVAILABLE_FALLBACK,
  checkoutPaymentCopyIsConsistent,
  checkoutSettlementCopy,
  type CheckoutSettlementInput
} from "../checkoutPaymentCopy";

const PLATFORM_OFF = "Marketplace card payments are temporarily unavailable. Choose cash, local pickup, or in-person payment.";
const SELLER_OFF = "Seller has not enabled card payments yet.";

function input(over: Partial<CheckoutSettlementInput> = {}): CheckoutSettlementInput {
  return {
    lane: "card",
    cardPaymentsAvailable: true,
    cardUnavailableMessage: "",
    knowsFinalAmount: true,
    ...over
  };
}

/** Every combination the inputs can take. Four booleans' worth of states, so
 *  the matrix is small enough to enumerate and large enough that nobody would
 *  hand-write them all as cases. */
const MATRIX: CheckoutSettlementInput[] = [];
for (const lane of ["cash", "card"] as const) {
  for (const cardPaymentsAvailable of [true, false]) {
    for (const knowsFinalAmount of [true, false]) {
      for (const cardUnavailableMessage of ["", PLATFORM_OFF, SELLER_OFF]) {
        MATRIX.push({ lane, cardPaymentsAvailable, knowsFinalAmount, cardUnavailableMessage });
      }
    }
  }
}

describe("the state the bug report was about", () => {
  it("card ready and selected says the order is confirmed after payment", () => {
    const copy = checkoutSettlementCopy(input({ knowsFinalAmount: true }));
    expect(copy.text).toBe(
      "Your order will be confirmed after your card payment is successfully processed."
    );
    expect(copy.ctaEnabled).toBe(true);
    expect(copy.reason).toBe("card_confirmed_after_payment");
  });

  it("never emits the old literal in any state", () => {
    // The exact sentence that was hard-coded. Asserted over the whole matrix
    // rather than the one branch it used to live on, because the fix moved the
    // decision and a regression could put it back on a different branch.
    for (const state of MATRIX) {
      const copy = checkoutSettlementCopy(state);
      expect(copy.text).not.toBe("Marketplace card payments are temporarily unavailable.");
    }
  });
});

describe("the consistency rule", () => {
  it("never pairs a shut card lane with a live CTA, anywhere in the matrix", () => {
    for (const state of MATRIX) {
      const copy = checkoutSettlementCopy(state);
      expect(checkoutPaymentCopyIsConsistent(state, copy)).toBe(true);
    }
  });

  it("the predicate can actually fail", () => {
    // Negative control. Without it the assertion above passes for a predicate
    // that returns true unconditionally.
    const shut = input({ cardPaymentsAvailable: false, cardUnavailableMessage: SELLER_OFF });
    const tampered = { ...checkoutSettlementCopy(shut), ctaEnabled: true };
    expect(checkoutPaymentCopyIsConsistent(shut, tampered)).toBe(false);
  });

  it("disables the CTA exactly when the card lane is shut", () => {
    for (const state of MATRIX) {
      const expected = !(state.lane === "card" && !state.cardPaymentsAvailable);
      expect(checkoutSettlementCopy(state).ctaEnabled).toBe(expected);
    }
  });
});

describe("unavailable copy is the server's, verbatim", () => {
  it.each([
    ["the platform rail is off", PLATFORM_OFF],
    ["this seller cannot take cards", SELLER_OFF],
    ["some future sentence nobody has written yet", "Cards are off on Tuesdays."]
  ])("passes through the sentence for: %s", (_label, message) => {
    const copy = checkoutSettlementCopy(
      input({ cardPaymentsAvailable: false, cardUnavailableMessage: message })
    );
    expect(copy.text).toBe(message);
    expect(copy.ctaEnabled).toBe(false);
  });

  it("does not try to distinguish seller states the server deliberately collapsed", () => {
    // `buyer_view` replaces every seller-private reason with one sentence so a
    // buyer cannot infer a seller's onboarding or compliance state. Two
    // different seller states therefore arrive identical, and must render
    // identically — a client that branched on them would be reconstructing
    // exactly what the server removed.
    const setupRequired = checkoutSettlementCopy(
      input({ cardPaymentsAvailable: false, cardUnavailableMessage: SELLER_OFF })
    );
    const inProgress = checkoutSettlementCopy(
      input({ cardPaymentsAvailable: false, cardUnavailableMessage: SELLER_OFF })
    );
    expect(setupRequired).toEqual(inProgress);
  });

  it("says something rather than nothing when the server sends a blank", () => {
    for (const blank of ["", "   "]) {
      const copy = checkoutSettlementCopy(
        input({ cardPaymentsAvailable: false, cardUnavailableMessage: blank })
      );
      expect(copy.text).toBe(CARD_UNAVAILABLE_FALLBACK);
      expect(copy.ctaEnabled).toBe(false);
    }
  });
});

describe("cash is never gated on the card rail", () => {
  it("gives identical cash copy whether card is open or shut", () => {
    for (const knowsFinalAmount of [true, false]) {
      const open = checkoutSettlementCopy(
        input({ lane: "cash", cardPaymentsAvailable: true, knowsFinalAmount })
      );
      const shut = checkoutSettlementCopy(
        input({ lane: "cash", cardPaymentsAvailable: false, cardUnavailableMessage: PLATFORM_OFF, knowsFinalAmount })
      );
      expect(open).toEqual(shut);
      expect(open.ctaEnabled).toBe(true);
    }
  });

  it("never mentions card unavailability on the cash lane", () => {
    for (const state of MATRIX.filter((s) => s.lane === "cash")) {
      expect(checkoutSettlementCopy(state).text).not.toMatch(/unavailable/i);
    }
  });

  it("keeps both cash sentences distinct by whether the amount is known", () => {
    const known = checkoutSettlementCopy(input({ lane: "cash", knowsFinalAmount: true }));
    const open = checkoutSettlementCopy(input({ lane: "cash", knowsFinalAmount: false }));
    expect(known.reason).toBe("cash_known_amount");
    expect(open.reason).toBe("cash_open_amount");
    expect(known.text).not.toBe(open.text);
    expect(open.text).toMatch(/amount isn't set here/);
  });
});

describe("card with no final amount", () => {
  it("still defers the amount to the secure page when the lane is open", () => {
    const copy = checkoutSettlementCopy(input({ knowsFinalAmount: false }));
    expect(copy.reason).toBe("card_open_amount");
    expect(copy.text).toMatch(/exact amount is confirmed on the secure payment page/);
    expect(copy.ctaEnabled).toBe(true);
  });

  it("reports unavailability ahead of the unknown amount when the lane is shut", () => {
    // The old nesting asked about the amount first, so an unavailable rail
    // could only be mentioned on the branch that happened to know the total.
    const copy = checkoutSettlementCopy(
      input({ cardPaymentsAvailable: false, knowsFinalAmount: false, cardUnavailableMessage: SELLER_OFF })
    );
    expect(copy.reason).toBe("card_unavailable");
    expect(copy.text).toBe(SELLER_OFF);
  });
});
