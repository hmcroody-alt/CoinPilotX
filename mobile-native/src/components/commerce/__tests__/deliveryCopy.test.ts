/**
 * The sentences a buyer is allowed to read about delivery.
 *
 * Every rule the mission states about wording is a property of this module, so
 * this is where they are pinned:
 *
 * - **§58, §125 — never "Guaranteed".** Asserted over every state and every
 *   confidence, including the one-day window where the temptation is strongest.
 * - **§124 — no fabricated precision.** A window is two dates or it is not a
 *   window. There is no input that produces one date presented as the answer.
 * - **Never silence.** Asserted exhaustively: every reason the server can return,
 *   plus reasons it cannot, produce a non-empty line.
 * - **The old copy survives in exactly one place.** "arranged with the seller"
 *   was previously shown on every listing including CJ-fulfilled ones. It is now
 *   reachable only from `not_supplier_fulfilled`, and a test asserts it appears
 *   nowhere else — because the failure mode of this whole mission is that
 *   sentence quietly coming back as a fallback.
 * - **A retry button that lies.** `retryable` has to be false for a settled
 *   corridor, or the screen offers a buyer a lever with nothing attached.
 */

import type { DeliveryAnswer, DeliveryDestination, DeliveryEstimate } from "../../../api/delivery";
import { estimateUnavailable, parseDeliveryEstimate } from "../../../api/delivery";
import { DELIVERY_LOADING_TEXT, blocksCheckout, deliveryCopy } from "../deliveryCopy";

const WINDOW: DeliveryEstimate = parseDeliveryEstimate({
  state: "ESTIMATED",
  reason: null,
  earliest: "2026-03-16",
  latest: "2026-03-21",
  confidence: "PROVIDER_QUOTED",
  guaranteed: false,
  is_estimate: true,
  shipping_price: "FREE"
});

const KNOWN_US: DeliveryDestination = {
  country: "US",
  precision: "COUNTRY",
  tier: "SESSION",
  known: true
};
const UNKNOWN: DeliveryDestination = {
  country: null,
  precision: null,
  tier: null,
  known: false
};

function answer(delivery: DeliveryEstimate, destination: DeliveryDestination = KNOWN_US): DeliveryAnswer {
  return { delivery, destination };
}

/** Every refusal reason the server's own vocabulary contains, plus two it does
 * not — an unmapped string and an empty one — because an unrecognised reason
 * arriving from a newer server must still produce a line. */
const ALL_REASONS = [
  "fulfillment_undeclared",
  "not_supplier_fulfilled",
  "destination_unresolved",
  "supplier_unreachable",
  "supplier_error",
  "variant_data_incomplete",
  "handling_time_undeclared",
  "no_eligible_route",
  "client_unreachable",
  "client_malformed_response",
  "client_incomplete_window",
  "a_reason_from_a_newer_server",
  ""
];

describe("deliveryCopy — a window", () => {
  it("says Estimated delivery and the range", () => {
    const copy = deliveryCopy(answer(WINDOW));
    expect(copy.text).toBe("Estimated delivery 16 – 21 Mar to US");
    expect(copy.tone).toBe("ESTIMATE");
    expect(copy.icon).toBe("calendar-outline");
    expect(copy.retryable).toBe(false);
  });

  it("names the destination only when the server is sure of it", () => {
    // "Estimated 16 – 21 Mar to US" is a stronger claim than the bare window and
    // must not be made on a corridor inferred from an edge header the server does
    // not trust.
    expect(deliveryCopy(answer(WINDOW, UNKNOWN)).text).toBe("Estimated delivery 16 – 21 Mar");
    expect(
      deliveryCopy(answer(WINDOW, { ...KNOWN_US, country: null })).text
    ).toBe("Estimated delivery 16 – 21 Mar");
  });

  it("says FREE Shipping only because the server said FREE", () => {
    expect(deliveryCopy(answer(WINDOW)).shipping).toBe("FREE Shipping");
    const priced = { ...WINDOW, shippingPrice: "12.40" };
    expect(deliveryCopy(answer(priced)).shipping).toBeNull();
    const silent = { ...WINDOW, shippingPrice: null };
    expect(deliveryCopy(answer(silent)).shipping).toBeNull();
  });

  it("marks a cached quote stale without arguing with it", () => {
    // A cached provider quote is still the provider's own number. The flag is a
    // footnote for the presentation layer, not a hedge inside the sentence.
    const cached = { ...WINDOW, confidence: "PROVIDER_CACHED" as const };
    const copy = deliveryCopy(answer(cached));
    expect(copy.stale).toBe(true);
    expect(copy.text).toBe("Estimated delivery 16 – 21 Mar to US");
    expect(deliveryCopy(answer(WINDOW)).stale).toBe(false);
  });

  it("still says Estimated when the window is a single day", () => {
    // The strongest temptation to promise: one date, from the supplier, today.
    // It is still an estimate.
    const oneDay = { ...WINDOW, latest: "2026-03-16" };
    const copy = deliveryCopy(answer(oneDay));
    expect(copy.text).toBe("Estimated delivery 16 Mar to US");
    expect(copy.text).toMatch(/^Estimated /);
  });

  it("refuses to describe a window it cannot format", () => {
    // Should be unreachable — the parser catches it. If something upstream
    // changes, the fallback says nothing about dates rather than drawing a range
    // around a null.
    const broken = { ...WINDOW, earliest: null };
    const copy = deliveryCopy(answer(broken as DeliveryEstimate));
    expect(copy.tone).toBe("RETRYABLE");
    expect(copy.text).not.toMatch(/\d/);
    expect(copy.retryable).toBe(true);
  });
});

describe("deliveryCopy — no promises, ever", () => {
  it("never uses the word Guaranteed, in any state", () => {
    const states: DeliveryEstimate[] = [
      WINDOW,
      { ...WINDOW, confidence: "PROVIDER_CACHED" },
      { ...WINDOW, latest: "2026-03-16" },
      // Even if a server one day sets the flag, the copy layer does not consult
      // it, so there is no shape that earns the word.
      { ...WINDOW, guaranteed: true },
      ...ALL_REASONS.map((reason) => estimateUnavailable(reason))
    ];
    for (const delivery of states) {
      for (const destination of [KNOWN_US, UNKNOWN]) {
        const copy = deliveryCopy(answer(delivery, destination));
        expect(copy.text.toLowerCase()).not.toContain("guarantee");
        expect(copy.text.toLowerCase()).not.toContain("will arrive");
        expect(copy.text.toLowerCase()).not.toContain("arrives on");
      }
    }
    expect(DELIVERY_LOADING_TEXT.toLowerCase()).not.toContain("guarantee");
  });

  it("never renders a date outside a real window", () => {
    // §124. A refusal that leaked a date — from a stale render, a merged object, a
    // server that sent one beside an UNAVAILABLE state — is the fabricated
    // precision the whole package exists to prevent.
    for (const reason of ALL_REASONS) {
      const leaky = { ...WINDOW, state: "UNAVAILABLE" as const, reason };
      const copy = deliveryCopy(answer(parseDeliveryEstimate({
        state: "UNAVAILABLE",
        reason,
        earliest: leaky.earliest,
        latest: leaky.latest,
        confidence: "PROVIDER_QUOTED",
        guaranteed: false,
        is_estimate: true,
        shipping_price: "FREE"
      })));
      expect(copy.text).not.toContain("Mar");
      expect(copy.text).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    }
  });

  it("says something for every reason, known or not", () => {
    for (const reason of ALL_REASONS) {
      const copy = deliveryCopy(answer(estimateUnavailable(reason)));
      expect(copy.text.trim().length).toBeGreaterThan(0);
      expect(copy.tone).not.toBe("ESTIMATE");
    }
  });

  it("puts an unrecognised reason in the retryable bucket", () => {
    // Says less than it knows rather than more. A newer server's reason must not
    // fall through to a terminal sentence that tells a buyer to give up.
    const copy = deliveryCopy(answer(estimateUnavailable("a_reason_from_a_newer_server")));
    expect(copy.tone).toBe("RETRYABLE");
    expect(copy.retryable).toBe(true);
  });
});

describe("deliveryCopy — the old sentence, contained", () => {
  it("keeps 'arranged with the seller' for a seller-fulfilled listing", () => {
    // Correct here and only here: the seller really does ship it themselves.
    const copy = deliveryCopy(answer(estimateUnavailable("not_supplier_fulfilled")));
    expect(copy.text).toContain("ships this item themselves");
    expect(copy.text).toContain("arranged with them after your order is confirmed");
    expect(copy.tone).toBe("TERMINAL");
    // Not a failure, so not a retry. Asking again will not produce a supplier
    // quote for a listing no supplier fulfills.
    expect(copy.retryable).toBe(false);
  });

  it("shows it for nothing else", () => {
    // The failure mode of this entire mission is that sentence coming back as a
    // fallback and being shown on a CJ listing again.
    for (const reason of ALL_REASONS) {
      if (reason === "not_supplier_fulfilled") continue;
      const copy = deliveryCopy(answer(estimateUnavailable(reason)));
      expect(copy.text.toLowerCase()).not.toContain("arranged with");
    }
    for (const delivery of [WINDOW, { ...WINDOW, confidence: "PROVIDER_CACHED" as const }]) {
      expect(deliveryCopy(answer(delivery)).text.toLowerCase()).not.toContain("arranged with");
    }
  });
});

describe("deliveryCopy — what the buyer should do", () => {
  it("asks for a country when the server had nothing to resolve from", () => {
    const copy = deliveryCopy(answer(estimateUnavailable("destination_unresolved"), UNKNOWN));
    expect(copy.tone).toBe("ACTIONABLE");
    expect(copy.icon).toBe("location-outline");
    expect(copy.text).toContain("Choose a delivery country");
    // A retry cannot resolve a destination that does not exist yet.
    expect(copy.retryable).toBe(false);
  });

  it("offers a retry for an outage but not for a settled corridor", () => {
    for (const reason of ["supplier_unreachable", "supplier_error", "handling_time_undeclared", "client_unreachable"]) {
      expect(deliveryCopy(answer(estimateUnavailable(reason))).retryable).toBe(true);
    }
    const unserviceable = parseDeliveryEstimate({
      state: "UNSUPPORTED_ROUTE",
      reason: "no_eligible_route",
      earliest: null,
      latest: null,
      confidence: "NONE",
      guaranteed: false,
      is_estimate: true,
      shipping_price: "FREE"
    });
    expect(deliveryCopy(answer(unserviceable)).retryable).toBe(false);
    expect(deliveryCopy(answer(unserviceable)).tone).toBe("TERMINAL");
  });

  it("does not tell a buyer the platform's plumbing is misconfigured", () => {
    // `handling_time_undeclared` means an operator has not set a variable. The
    // buyer's question is whether to keep shopping, and the answer is yes.
    const copy = deliveryCopy(answer(estimateUnavailable("handling_time_undeclared")));
    expect(copy.text).toBe("An estimated arrival date is not available right now.");
    expect(copy.text.toLowerCase()).not.toContain("configur");
    expect(copy.text.toLowerCase()).not.toContain("supplier");
    expect(copy.text.toLowerCase()).not.toContain("error");
  });

  it("does not name the supplier when the supplier is the problem", () => {
    // CJ is not a brand the buyer bought from, and §33–35 keeps the supply chain
    // off the product page.
    for (const reason of ["supplier_unreachable", "supplier_error", "variant_data_incomplete"]) {
      const text = deliveryCopy(answer(estimateUnavailable(reason))).text.toLowerCase();
      expect(text).not.toContain("supplier");
      expect(text).not.toContain("cj");
      expect(text).not.toContain("warehouse");
    }
  });
});

describe("blocksCheckout", () => {
  it("blocks only an unserviceable corridor", () => {
    // §55. Blocking on an outage or a missing policy would mean an operator who
    // has not finished configuring delivery has silently closed the store.
    const unserviceable = parseDeliveryEstimate({
      state: "UNSUPPORTED_ROUTE",
      reason: "no_eligible_route",
      earliest: null,
      latest: null,
      confidence: "NONE",
      guaranteed: false,
      is_estimate: true,
      shipping_price: "FREE"
    });
    expect(blocksCheckout(unserviceable)).toBe(true);
    expect(blocksCheckout(WINDOW)).toBe(false);
    for (const reason of ALL_REASONS) {
      expect(blocksCheckout(estimateUnavailable(reason))).toBe(false);
    }
  });
});
