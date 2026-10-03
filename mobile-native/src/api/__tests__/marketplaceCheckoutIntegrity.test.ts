import {
  marketplaceFulfillmentCopy,
  marketplaceListingFulfillment,
  marketplaceListingPriceMinor
} from "../marketplaceBuyerPresentation";
import { buyerCanRetry, buyerErrorCopy } from "../marketplaceErrors";
import type { MarketplaceErrorCode } from "../marketplaceErrors";
import { PulseApiError } from "../pulseApi";
import type { MarketplaceListing } from "../marketplace";
import parity from "./fixtures/priceLabelParity.json";

const listing = (over: Partial<MarketplaceListing>): MarketplaceListing =>
  ({ id: 1, title: "Ball", ...over }) as MarketplaceListing;

describe("what the buyer reads about delivery matches what checkout does", () => {
  it("keeps a seller's both-lanes offer undecided instead of resolving it to shipping", () => {
    // The label already promised a choice; collapsing the lane here is what made
    // that promise decorative and sent pickup buyers to an address form.
    const both = listing({ delivery_type: "both" });
    expect(marketplaceFulfillmentCopy(both)).toBe("Local pickup or shipping");
    expect(marketplaceListingFulfillment(both)).toBe("both");
  });

  it("reads the same lane out of listing metadata as out of the column", () => {
    expect(marketplaceListingFulfillment(listing({ listing_metadata: { delivery_options: "both" } }))).toBe("both");
    expect(marketplaceListingFulfillment(listing({ listing_metadata: { delivery_options: "pickup" } }))).toBe("pickup");
  });

  it("still resolves the unambiguous lanes", () => {
    expect(marketplaceListingFulfillment(listing({ delivery_type: "pickup" }))).toBe("pickup");
    expect(marketplaceListingFulfillment(listing({ delivery_type: "shipping" }))).toBe("shipping");
    expect(marketplaceListingFulfillment(listing({ delivery_type: "digital" }))).toBe("digital");
  });
});

describe("the amount on the Pay button", () => {
  // This suite used to make the parity claim in its own name and then check two
  // labels, both under $1,000 — below the point where the server starts writing
  // a thousands separator, which was exactly the case the app misread. The
  // claim is now checked against the table the server is checked against, in
  // tests/test_marketplace_price_label_parity.py.
  it.each(parity.cases)("reads $label the way the server does — $why", ({ label, minor }) => {
    expect(marketplaceListingPriceMinor(listing({ price_label: label }))).toBe(minor);
  });

  it("understates nothing on a label carrying a thousands separator", () => {
    // The regression in its own words: the digit run stopped at the comma, so
    // this listing offered to charge $12.00 and charged $12,345.67.
    expect(marketplaceListingPriceMinor(listing({ price_label: "$12,345.67" }))).toBe(1234567);
  });

  it("returns null rather than zero when the label is not a price", () => {
    // Null is what stops the checkout CTA promising a dollar figure: an
    // unreadable label must produce no amount at all, not "$0.00".
    expect(marketplaceListingPriceMinor(listing({ price_label: "Free" }))).toBeNull();
    expect(marketplaceListingPriceMinor(listing({ price_label: "Request access" }))).toBeNull();
    expect(marketplaceListingPriceMinor(listing({ price_label: "" }))).toBeNull();
    expect(marketplaceListingPriceMinor(listing({ price_label: "make an offer" }))).toBeNull();
  });

  it("multiplies out so a quantity of two cannot display the unit price", () => {
    const unit = marketplaceListingPriceMinor(listing({ price_label: "$5.00" }));
    expect(unit).not.toBeNull();
    expect((unit as number) * 2).toBe(1000);
  });
});

describe("checkout failures name the buyer's next move", () => {
  it("has copy for an unanswered fulfillment choice", () => {
    // Worded past pickup-or-delivery: a service listing offers the same open
    // choice between remote and in person.
    const error = new PulseApiError("Choose pickup or delivery before you pay.", 400, "FULFILLMENT_REQUIRED");
    expect(buyerErrorCopy(error, "Checkout could not start.")).toBe(
      "Choose how you want this order fulfilled before you pay."
    );
  });

  it("has copy for a missing order detail and for an item that cannot share a checkout", () => {
    const missing = new PulseApiError("", 400, "FULFILLMENT_DETAILS_REQUIRED");
    expect(buyerErrorCopy(missing, "Checkout could not start.")).toContain("order details");
    const alone = new PulseApiError("", 409, "ITEM_NEEDS_OWN_CHECKOUT");
    expect(buyerErrorCopy(alone, "Checkout could not start.")).toContain("one at a time");
  });

  it("never lets an unhandled server exception become the dominant sentence", () => {
    const error = new PulseApiError("PulseSoc hit a temporary service issue. Please retry with this trace ID.", 500, "");
    expect(buyerErrorCopy(error, "Checkout could not start. No card was charged.")).toBe(
      "Checkout could not start. No card was charged."
    );
  });

  it("tells a buyer whose order is too small that the total is the problem", () => {
    // A $0.10 listing is under Stripe's USD floor, so no retry, reinstall or
    // second card will ever get past it. Falling back to the screen's generic
    // "Checkout could not start" sent the buyer round that loop with no exit.
    const error = new PulseApiError(
      "This order total is below USD 0.50, the smallest amount card payments accept. No card was charged.",
      400,
      "ORDER_TOTAL_BELOW_MINIMUM"
    );

    expect(buyerErrorCopy(error, "Checkout could not start. No card was charged.")).toBe(
      "This order total is below the minimum amount card payments accept. No card was charged."
    );
  });

  it("separates a card decline from a payment surface that would not open", () => {
    // Both used to arrive as one hard-coded 500 from Buy Now, and they call for
    // different moves — try another card, versus nothing the buyer can do — so
    // they must not share a sentence.
    const declined = new PulseApiError("Your card could not be charged.", 402, "PAYMENT_FAILED");
    const misconfigured = new PulseApiError("...", 400, "PAYMENT_CONFIGURATION_ERROR");

    expect(buyerErrorCopy(declined, "fallback")).toBe("Your card could not be charged. No card was charged.");
    expect(buyerErrorCopy(misconfigured, "fallback")).toBe(
      "We couldn't open secure payment for this order. No card was charged."
    );
  });

  it("never tells a buyer that payments are temporarily unavailable", () => {
    // The exact string a real buyer read nine times across five hours in
    // October 2026, while Stripe was healthy, the seller was chargeable and the
    // card rail was live. Both halves were false: payments were available, and
    // the fault was not temporary — our idempotency key had been burned against
    // changed parameters and would refuse that buyer for 24 hours.
    //
    // Pinned across every code rather than just the one that carried it,
    // because the sentence's appeal is that it is vague enough to fit anywhere.
    const codes: MarketplaceErrorCode[] = [
      "PAYMENT_UNAVAILABLE",
      "PAYMENT_CONFIGURATION_ERROR",
      "PAYMENT_FAILED",
      "ORDER_TOTAL_BELOW_MINIMUM",
      "NETWORK_ERROR"
    ];
    for (const code of codes) {
      const copy = buyerErrorCopy(new PulseApiError("...", 400, code), "fallback");
      expect(copy).not.toMatch(/temporarily/i);
    }
  });

  describe("buyerCanRetry", () => {
    // §20. The verdict is the server's; this only asks that the client reads it
    // rather than inferring it from prose. Inferring it from prose is what
    // produced the incident screenshot — a refusal rendered beside an active
    // "Continue to secure payment" button.
    it("keeps the CTA for a failure the server calls retryable", () => {
      const burned = new PulseApiError("...", 400, "PAYMENT_CONFIGURATION_ERROR", {
        retryable: true,
        cta: "retry"
      });
      expect(buyerCanRetry(burned)).toBe(true);
    });

    it("removes the CTA for a failure the server calls blocked", () => {
      // Same code as the case above, opposite verdict. That pair is the whole
      // reason the verdict is a separate field: PAYMENT_CONFIGURATION_ERROR
      // covers both our own burned key (retryable) and a bad API key (not), and
      // no amount of reading the code can separate them.
      const misconfigured = new PulseApiError("...", 503, "PAYMENT_CONFIGURATION_ERROR", {
        retryable: false,
        cta: "blocked"
      });
      expect(buyerCanRetry(misconfigured)).toBe(false);
    });

    it("falls back to offering a retry when the server sends no verdict", () => {
      // An older deployment, or a failure raised outside the checkout lanes.
      // Defaulting to `true` means this function can only ever remove a CTA
      // that should not have been offered, never withhold a legitimate one.
      expect(buyerCanRetry(new PulseApiError("...", 500, "PAYMENT_UNAVAILABLE"))).toBe(true);
      expect(buyerCanRetry(new Error("boom"))).toBe(true);
      expect(buyerCanRetry(undefined)).toBe(true);
    });

    it("reads cta when retryable is absent, and ignores a non-boolean verdict", () => {
      // `details` is whatever JSON the server sent, so neither field is
      // guaranteed to have the type it should.
      expect(buyerCanRetry(new PulseApiError("...", 400, "PAYMENT_FAILED", { cta: "blocked" }))).toBe(false);
      expect(buyerCanRetry(new PulseApiError("...", 400, "PAYMENT_FAILED", { cta: "retry" }))).toBe(true);
      expect(buyerCanRetry(new PulseApiError("...", 400, "PAYMENT_FAILED", { retryable: "no" }))).toBe(true);
    });
  });
});
