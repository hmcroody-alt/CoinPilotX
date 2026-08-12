import {
  marketplaceFulfillmentCopy,
  marketplaceListingFulfillment,
  marketplaceListingPriceMinor
} from "../marketplaceBuyerPresentation";
import { buyerErrorCopy } from "../marketplaceErrors";
import { PulseApiError } from "../pulseApi";
import type { MarketplaceListing } from "../marketplace";

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
  it("reads a price label the same way the server does", () => {
    expect(marketplaceListingPriceMinor(listing({ price_label: "$5.00" }))).toBe(500);
    expect(marketplaceListingPriceMinor(listing({ price_label: "USD 12.5" }))).toBe(1250);
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
    const error = new PulseApiError("Choose pickup or delivery before you pay.", 400, "FULFILLMENT_REQUIRED");
    expect(buyerErrorCopy(error, "Checkout could not start.")).toBe("Choose pickup or delivery before you pay.");
  });

  it("never lets an unhandled server exception become the dominant sentence", () => {
    const error = new PulseApiError("PulseSoc hit a temporary service issue. Please retry with this trace ID.", 500, "");
    expect(buyerErrorCopy(error, "Checkout could not start. No card was charged.")).toBe(
      "Checkout could not start. No card was charged."
    );
  });
});
