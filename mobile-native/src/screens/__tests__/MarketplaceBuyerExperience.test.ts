import {
  marketplaceAvailabilityCopy as availabilityCopy,
  canPurchaseMarketplaceListing as canPurchaseListing,
  marketplaceFulfillmentCopy as fulfillmentCopy,
  marketplaceListingFulfillment as fulfillmentLane
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
    expect(fulfillmentLane(listing())).toBe("shipping");
  });

  it("keeps moderation signals out of the buyer listing model", () => {
    // `safety_score` is a reviewer signal. It is absent from the client model
    // so no buyer surface can render it, even by accident.
    expect("safety_score" in listing()).toBe(false);
  });
});
