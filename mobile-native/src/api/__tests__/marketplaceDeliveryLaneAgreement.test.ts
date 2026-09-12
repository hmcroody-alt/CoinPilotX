/**
 * One listing, one lane — measured across every place the app decides it.
 *
 * Four functions in this app answered "how is this listing fulfilled" and they
 * read different fields, so on a real row they gave different answers:
 *
 *   grid card   `listingFulfillment`          -> "unknown"   (no buy button)
 *   detail page `marketplaceListingFulfillment`-> "pickup"
 *   detail copy `marketplaceFulfillmentCopy`  -> "Local pickup"
 *   checkout    `resolveFulfillmentKind`      -> "shipping"
 *
 * `MarketplaceProductScreen.handleBuyNow` sends two of those to the checkout
 * screen in the same navigation payload, two lines apart — `fulfillment:` from
 * the third, `fulfillmentKind:` from the fourth — and `kindFromParams` prefers
 * the one that was wrong. So a buyer read "Local pickup", tapped Buy Now, and
 * was asked for a delivery address for an item they were collecting in person.
 *
 * Every existing test of these functions supplied a lane word in
 * `delivery_type`. The database has never contained one: the publish route's
 * INSERT writes `product_type` into that column. So the fixtures described rows
 * that cannot exist, and the suites were green on inputs production never
 * produces.
 *
 * The fixture below is therefore not invented. It is the row shape
 * `tests/test_marketplace_delivery_lane.py` reads back out of the table after
 * publishing through the real route.
 */
import { listingFulfillment, gridCardAction } from "../marketplaceScreen";
import { marketplaceFulfillmentCopy, marketplaceListingFulfillment } from "../marketplaceBuyerPresentation";
import { UNDECIDED_KINDS, deliveryLane, resolveFulfillmentKind } from "../marketplaceFulfillment";
import type { MarketplaceListing } from "../marketplace";

/** A published physical listing, exactly as the server stores one. */
const published = (delivery_options?: string): MarketplaceListing =>
  ({
    id: 8,
    listing_id: 8,
    title: "Ball",
    price_label: "$25.00",
    currency: "USD",
    quantity: 5,
    status: "published",
    approval_status: "approved",
    buyer_visible: true,
    inventory_state: "available",
    // The product type, in all three columns. This is not a contrived fixture:
    // `delivery_type` is bound to `product_type` in the publish INSERT.
    listing_type: "physical",
    product_type: "physical",
    delivery_type: "physical",
    listing_metadata: {
      condition: "new",
      location: "Testville",
      ...(delivery_options ? { delivery_options } : {})
    }
  }) as MarketplaceListing;

const ACTIONS = { cartEnabled: true, offersEnabled: true };

describe("every surface agrees on the lane the seller chose", () => {
  it("reads a pickup-only listing as pickup everywhere", () => {
    const item = published("pickup");

    expect(resolveFulfillmentKind(item)).toBe("pickup");
    expect(marketplaceListingFulfillment(item)).toBe("pickup");
    expect(marketplaceFulfillmentCopy(item)).toBe("Local pickup");
    expect(listingFulfillment(item)).toBe("local");
  });

  it("reads a shipping-only listing as shipping everywhere", () => {
    const item = published("shipping");

    expect(resolveFulfillmentKind(item)).toBe("shipping");
    expect(marketplaceListingFulfillment(item)).toBe("shipping");
    expect(marketplaceFulfillmentCopy(item)).toBe("Shipping");
    expect(listingFulfillment(item)).toBe("platform");
  });

  it("carries a listing offering both through as a choice, not as half of one", () => {
    const item = published("both");

    expect(resolveFulfillmentKind(item)).toBe("shipping_or_pickup");
    expect(marketplaceListingFulfillment(item)).toBe("both");
    expect(marketplaceFulfillmentCopy(item)).toBe("Local pickup or shipping");
    expect(listingFulfillment(item)).toBe("both");
  });

  it("never prints a lane it will not check out with", () => {
    // The comparison the old suites never made: the sentence the buyer reads
    // and the kind the checkout uses, on the same row, asserted against each
    // other rather than each against its own expectation.
    const sentenceFor: Record<string, string> = {
      pickup: "Local pickup",
      shipping: "Shipping",
      shipping_or_pickup: "Local pickup or shipping"
    };
    for (const option of ["pickup", "shipping", "both"]) {
      const item = published(option);
      expect(marketplaceFulfillmentCopy(item)).toBe(sentenceFor[resolveFulfillmentKind(item)]);
    }
  });

  it("cannot hand the checkout two lanes that disagree", () => {
    // `MarketplaceProductScreen.handleBuyNow` puts both of these in one
    // navigation payload, two lines apart — `fulfillment:` and
    // `fulfillmentKind:` — and the checkout screen reads the first for its lane
    // chooser and the second for its fields. Two fields carrying one fact is
    // only safe while they cannot differ, so that is what is asserted, rather
    // than each against its own expected value.
    const foldDown: Record<string, string> = {
      pickup: "pickup", shipping: "shipping", shipping_or_pickup: "both", digital: "digital"
    };
    for (const option of ["pickup", "shipping", "both", undefined]) {
      const item = published(option);
      const kind = resolveFulfillmentKind(item);
      expect(marketplaceListingFulfillment(item)).toBe(foldDown[kind]);
      // And the chooser appears exactly when the kind is undecided: never on
      // top of a settled lane, never missing from an open one.
      expect(marketplaceListingFulfillment(item) === "both")
        .toBe(UNDECIDED_KINDS.includes(kind));
    }
  });
});

describe("the grid card offers an action on a physical listing", () => {
  it("gives a shipped listing Add to cart", () => {
    // This returned `null`. `listingFulfillment` substring-matched
    // `delivery_type` for "ship"/"pickup"/"digital" and the column read
    // "physical", which contains none of them — so every physical card in the
    // marketplace fell to "unknown" and offered no buy action at all.
    expect(gridCardAction(published("shipping"), ACTIONS)).toBe("cart");
    expect(gridCardAction(published("both"), ACTIONS)).toBe("cart");
  });

  it("gives a pickup listing Make offer", () => {
    expect(gridCardAction(published("pickup"), ACTIONS)).toBe("offer");
  });

  it("gives a listing whose seller named no lane Add to cart", () => {
    // `delivery_options` is optional. A physical listing without one ships, and
    // shipping is an action the platform can offer.
    expect(gridCardAction(published(), ACTIONS)).toBe("cart");
  });

  it("still refuses to guess for a row that declared nothing at all", () => {
    // `unknown` has to stay reachable, or the guard it exists to be is an
    // enumeration member no input can produce.
    const bare = { id: 1, title: "Mystery" } as MarketplaceListing;
    expect(deliveryLane(bare)).toBe("");
    expect(listingFulfillment(bare)).toBe("unknown");
    expect(gridCardAction(bare, ACTIONS)).toBeNull();
    expect(marketplaceFulfillmentCopy(bare)).toBe("Delivery details shown at checkout");
  });
});

describe("the column cannot outvote the seller", () => {
  it("ignores a delivery column that disagrees with the metadata", () => {
    for (const delivery_type of ["physical", "shipping", "digital", "", undefined]) {
      const item = { ...published("pickup"), delivery_type } as MarketplaceListing;
      expect(resolveFulfillmentKind(item)).toBe("pickup");
      expect(marketplaceListingFulfillment(item)).toBe("pickup");
    }
  });

  it("does not let the column's 'digital' default make a parcel a download", () => {
    // A digital kind asks for no address and reserves no stock. Reaching it from
    // a DDL default rather than from a seller's declaration would sell the same
    // physical unit repeatedly.
    const item = { ...published(), delivery_type: "digital" } as MarketplaceListing;
    expect(resolveFulfillmentKind(item)).toBe("shipping");
  });

  it("keeps a genuinely digital listing digital", () => {
    const item = {
      ...published(), listing_type: "digital", product_type: "digital", delivery_type: "digital"
    } as MarketplaceListing;
    expect(resolveFulfillmentKind(item)).toBe("digital");
    expect(marketplaceListingFulfillment(item)).toBe("digital");
    expect(marketplaceFulfillmentCopy(item)).toBe("Digital delivery");
  });
});
