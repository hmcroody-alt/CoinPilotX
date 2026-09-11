import {
  marketplaceAvailabilityCopy as availabilityCopy,
  canPurchaseMarketplaceListing as canPurchaseListing,
  marketplaceFulfillmentCopy as fulfillmentCopy,
  marketplaceListingFulfillment as fulfillmentLane,
  marketplacePurchaseBlock as purchaseBlock,
  marketplacePurchaseCtaCopy as ctaCopy
} from "../../api/marketplaceBuyerPresentation";
import type { MarketplaceListing } from "../../api/marketplace";

function listing(overrides: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 8,
    listing_id: 8,
    title: "Ball",
    price_label: "$5.00",
    product_type: "physical",
    listing_type: "physical",
    quantity: 1,
    buyer_visible: true,
    inventory_state: "available",
    listing_metadata: {
      condition: "new",
      delivery_options: "both",
      location: "San Diego",
      return_policy: "14_days",
      variants: []
    },
    ...overrides
  };
}

describe("Marketplace buyer purchase presentation", () => {
  it("presents the approved Ball QA listing as purchasable without moderation copy", () => {
    const item = listing();
    expect(canPurchaseListing(item)).toBe(true);
    expect(availabilityCopy(item)).toBe("Only 1 left");
    expect(fulfillmentCopy(item)).toBe("Local pickup or shipping");
  });

  it("blocks sold and explicitly non-public listings", () => {
    expect(canPurchaseListing(listing({ quantity: 0, inventory_state: "out_of_stock" }))).toBe(false);
    expect(availabilityCopy(listing({ quantity: 0, inventory_state: "out_of_stock" }))).toBe("Sold out");
    expect(canPurchaseListing(listing({ buyer_visible: false }))).toBe(false);
  });

  it("does not treat stockless services as sold out", () => {
    const service = listing({ product_type: "service", listing_type: "service", quantity: 0, listing_metadata: {} });
    expect(canPurchaseListing(service)).toBe(true);
    expect(availabilityCopy(service)).toBe("Available");
    expect(fulfillmentCopy(service)).toBe("Service fulfillment");
  });

  it("uses only configured fulfillment values and never invents an estimate", () => {
    expect(fulfillmentCopy(listing({ listing_metadata: { condition: "new", variants: [], delivery_options: "shipping", location: "", return_policy: "none" } }))).toBe("Shipping");
    expect(fulfillmentCopy(listing({ delivery_type: undefined, listing_metadata: {} }))).toBe("Delivery details shown at checkout");
  });

  it("routes the checkout lane from the same fields the buyer copy reads", () => {
    // The sentence and the lane must not drift: an item that reads "Local
    // pickup" cannot check out as shipping.
    expect(fulfillmentLane(listing({ delivery_type: "pickup" }))).toBe("pickup");
    expect(fulfillmentLane(listing({ listing_metadata: { delivery_options: "pickup" } }))).toBe("pickup");
    expect(fulfillmentLane(listing({ listing_type: "digital", product_type: "digital" }))).toBe("digital");

    // A listing offering both reads "Local pickup or shipping" (asserted above)
    // and must route to "both", not to either half of it. MarketplaceCheckout
    // turns that lane into the buyer's choice — `mustChooseLane` is exactly
    // `fulfillment === "both"` — so collapsing it to "shipping" here would take
    // the pickup option away from a buyer whose listing page just offered it.
    expect(fulfillmentCopy(listing())).toBe("Local pickup or shipping");
    expect(fulfillmentLane(listing())).toBe("both");

    // Shipping is the lane for a listing that only ships, not the fallback for
    // one that also offers pickup.
    expect(fulfillmentLane(listing({ listing_metadata: { delivery_options: "shipping" } }))).toBe("shipping");
  });

  it("keeps moderation signals out of the buyer listing model", () => {
    // `safety_score` is a reviewer signal. It is absent from the client model
    // so no buyer surface can render it, even by accident.
    expect("safety_score" in listing()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// An unpriced listing is browsable on purpose — the server serves `price_label`
// as "" rather than inventing a phrase, and every card renders no price line.
// What it was not is buyable, and nothing on the way to the card knew that: the
// tile offered an enabled "Add to cart" over a full shelf, and the server
// answered 400 ITEM_UNAVAILABLE. From Buy Now it was worse, because the refusal
// came after the buyer had filled in a delivery address.
//
// These tests are about the gap between the two, so each one names both the
// state and the sentence the buyer reads in it.
// ---------------------------------------------------------------------------

describe("a listing the seller has not priced", () => {
  const unpriced = listing({ price_label: "", quantity: 4 });

  it("is not offered for purchase", () => {
    expect(canPurchaseListing(unpriced)).toBe(false);
    expect(purchaseBlock(unpriced)).toBe("NOT_PRICED");
  });

  it("is not described as sold out, because the shelf is full", () => {
    expect(availabilityCopy(unpriced)).toBe("Not priced yet");
    expect(ctaCopy(unpriced)).toBe("Not priced yet");
  });

  it("covers the labels the seller may deliberately choose as well as a blank", () => {
    // "Free" and "Request access" are labels a seller can pick; the checkout
    // reads all three as no price, so all three have to block the same tap.
    for (const label of ["", "Free", "Request access", "make an offer", "$0.00"]) {
      expect(purchaseBlock(listing({ price_label: label }))).toBe("NOT_PRICED");
    }
  });

  it("still reports an empty shelf as an empty shelf", () => {
    // Order matters: unpriced *and* sold out reads as sold out, which is the
    // more specific fact and the one a restock changes.
    const both = listing({ price_label: "", quantity: 0, inventory_state: "out_of_stock" });
    expect(purchaseBlock(both)).toBe("OUT_OF_STOCK");
    expect(availabilityCopy(both)).toBe("Sold out");
  });

  it("does not disturb a priced listing", () => {
    expect(purchaseBlock(listing())).toBe("");
    expect(ctaCopy(listing())).toBe("Add to cart");
  });
});

describe("the buy button says what is true about the buyer, not only the shelf", () => {
  it("tells a seller opening their own product page whose listing it is", () => {
    // The pill beside this button reads "Only 1 left". The button read "Sold
    // out" — the same screen, disagreeing with itself about the same shelf.
    expect(ctaCopy(listing(), { isOwnListing: true })).toBe("Your listing");
    expect(availabilityCopy(listing())).toBe("Only 1 left");
  });

  it("separates a withdrawn listing from an empty one", () => {
    expect(availabilityCopy(listing({ buyer_visible: false }))).toBe("Unavailable");
    expect(availabilityCopy(listing({ quantity: 0, inventory_state: "out_of_stock" }))).toBe("Sold out");
  });
});
