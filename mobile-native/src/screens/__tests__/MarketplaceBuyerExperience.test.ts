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
    // Nothing configured means nothing configured. This case used to clear
    // `delivery_type` and the metadata while leaving `listing_type: "physical"`
    // in the fixture, so it described a physical listing and asserted the copy
    // for an undeclared one.
    expect(fulfillmentCopy(listing({
      listing_type: undefined, product_type: undefined, delivery_type: undefined, listing_metadata: {}
    }))).toBe("Delivery details shown at checkout");
  });

  it("says Shipping for a physical listing whose seller named no lane", () => {
    // `delivery_options` is optional — `_take_enum` returns early when the key
    // is absent — so a physical listing with no lane is an ordinary row, and the
    // checkout asks such a buyer for a delivery address. Saying "Delivery
    // details shown at checkout" above that form would be the drift this file
    // exists to prevent, pointing the other way.
    const bare = listing({ listing_metadata: { condition: "new" } });
    expect(fulfillmentCopy(bare)).toBe("Shipping");
    expect(fulfillmentLane(bare)).toBe("shipping");
  });

  it("routes the checkout lane from the same fields the buyer copy reads", () => {
    // The sentence and the lane must not drift: an item that reads "Local
    // pickup" cannot check out as shipping.
    //
    // This assertion used to read `listing({ delivery_type: "pickup" })` over a
    // fixture whose metadata said "both" — a row contradicting itself in a
    // column the database never fills with a lane at all. It passed because the
    // code read that column first, which is the defect. The seller's answer
    // lives in the metadata, so that is what is asserted.
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

  it("ignores a moderation signal even when the server sends one", () => {
    // This test used to read `expect("safety_score" in listing()).toBe(false)`,
    // which is true of the fixture three lines up and of nothing else. The
    // field was on the wire the entire time: `pulse_marketplace_listing_payload`
    // builds its response as `{**row, ...}`, so every column a caller's SELECT
    // names was serialized whether or not it was listed. A claim about the
    // server, asserted against a literal written in this file.
    //
    // What the wire carries is now pinned where it can be observed —
    // `tests/web_parity/test_marketplace_reviewer_signal_not_buyer_facing.py`
    // asserts it against the served response. What this file can honestly say
    // is the other half: if one arrives anyway, from an older server or a
    // cached payload, no presentation decision moves.
    //
    // The value matters. `marketplace_listings.safety_score` holds the
    // reviewer's *risk* number despite the name — 0 clean, 100 for the worst
    // copy the engine scores — so a helper that started consulting it "for
    // safety" would rank listings exactly backwards.
    const withSignal = (score: number) =>
      listing({ safety_score: score } as Partial<MarketplaceListing>);
    const clean = listing();
    for (const score of [0, 44, 88, 100]) {
      const item = withSignal(score);
      expect(purchaseBlock(item)).toBe(purchaseBlock(clean));
      expect(canPurchaseListing(item)).toBe(canPurchaseListing(clean));
      expect(availabilityCopy(item)).toBe(availabilityCopy(clean));
      expect(ctaCopy(item)).toBe(ctaCopy(clean));
    }
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
