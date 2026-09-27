/**
 * The projection, asserted where the lies would be.
 *
 * Three of these tests exist because the *reference design* asks for data this
 * product does not have, and the tempting implementation of that design is one
 * that invents it. `rating`, `reviewCount` and `sellerVerified` must stay null
 * until a real aggregate lands, and "must stay null" is only a rule if something
 * fails when it stops being true — a plausible number here would be invisible in
 * review and indistinguishable from a working card in a screenshot.
 *
 * The rest pin the two decisions that are genuinely easy to get wrong:
 * `quantity == null` means "the seller does not count stock" and not "sold out"
 * (the defect `MarketplaceListing.quantity`'s own docblock describes), and the
 * context line must never claim more than a column says.
 */
import type { MarketplaceListing } from "../../api/marketplace";
import { NEW_LISTING_MAX_AGE_MS } from "../../api/marketplaceScreen";
import {
  PRODUCT_SIGNAL_TITLE_FALLBACK,
  productSignalContext,
  productSignalFromListing,
  productSignalsFromListings
} from "../productSignal";

const NOW = Date.parse("2026-09-26T12:00:00Z");

function listing(overrides: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 41,
    listing_id: 41,
    seller_user_id: 7,
    seller_store_name: "Northwind Supply",
    seller_username: "northwind",
    title: "Aurora Desk Lamp",
    short_description: "Warm dimmable light",
    price_label: "$64.99",
    currency: "USD",
    category: "Home",
    quantity: 3,
    thumbnail_url: "https://cdn.example/lamp.jpg",
    created_at: "2026-01-01T00:00:00Z",
    listing_type: "physical",
    delivery_type: "shipping",
    ...overrides
  };
}

describe("fields with no source stay null", () => {
  it("never invents a rating or a review count", () => {
    const signal = productSignalFromListing(listing(), NOW);
    expect(signal.rating).toBeNull();
    expect(signal.reviewCount).toBeNull();
  });

  it("never invents seller verification", () => {
    expect(productSignalFromListing(listing(), NOW).sellerVerified).toBeNull();
  });

  it("does not read a rating off the listing even if one appears there", () => {
    // If the server ever starts sending an unaudited aggregate, the adapter is
    // where it has to be opted into — not somewhere it leaks in by accident.
    const signal = productSignalFromListing(
      listing({ rating: 4.8, review_count: 320 } as unknown as Partial<MarketplaceListing>),
      NOW
    );
    expect(signal.rating).toBeNull();
    expect(signal.reviewCount).toBeNull();
  });

  it("has no seller avatar, because no buyer payload carries one", () => {
    const signal = productSignalFromListing(listing(), NOW);
    expect(signal.sellerAvatarUrl).toBeNull();
    expect(signal.sellerInitial).toBe("N");
  });
});

describe("availability treats an uncounted stock level as its own state", () => {
  it("reads an absent quantity as untracked, not sold out", () => {
    expect(productSignalFromListing(listing({ quantity: undefined }), NOW).availability).toBe("untracked");
  });

  it("reads a null quantity as untracked, not sold out", () => {
    expect(productSignalFromListing(listing({ quantity: null }), NOW).availability).toBe("untracked");
  });

  it("reads zero as sold out", () => {
    expect(productSignalFromListing(listing({ quantity: 0 }), NOW).availability).toBe("sold_out");
  });

  it("reads a positive quantity as available", () => {
    expect(productSignalFromListing(listing({ quantity: 1 }), NOW).availability).toBe("available");
  });
});

describe("the context line only says what a column supports", () => {
  it("prefers featured, which carries a disclosure obligation", () => {
    const context = productSignalContext(listing({ featured: 1, created_at: new Date(NOW).toISOString() }), NOW);
    expect(context).toEqual({ kind: "featured", category: "Home" });
  });

  it("says new for a listing inside the new-listing window", () => {
    const createdAt = new Date(NOW - NEW_LISTING_MAX_AGE_MS / 2).toISOString();
    expect(productSignalContext(listing({ created_at: createdAt }), NOW)).toEqual({
      kind: "new",
      category: "Home"
    });
  });

  it("falls back to the category for an older listing", () => {
    expect(productSignalContext(listing(), NOW)).toEqual({ kind: "category", category: "Home" });
  });

  it("says nothing at all when there is neither a badge nor a category", () => {
    expect(productSignalContext(listing({ category: "" }), NOW)).toEqual({ kind: "none" });
  });
});

describe("the projection reuses the existing product model", () => {
  it("addresses the product by listing_id and carries the listing itself", () => {
    const source = listing({ listing_id: 99, id: 12 });
    const signal = productSignalFromListing(source, NOW);
    expect(signal.productId).toBe(99);
    // The detail route's optional `listing` param exists because there is no
    // fetch-one endpoint; dropping the object would make the tap re-find it.
    expect(signal.listing).toBe(source);
  });

  it("uses the store name rather than any personal name", () => {
    const signal = productSignalFromListing(listing(), NOW);
    expect(signal.sellerName).toBe("Northwind Supply");
    expect(signal.sellerHandle).toBe("northwind");
  });

  it("passes the server's formatted price through without reassembling it", () => {
    expect(productSignalFromListing(listing(), NOW).priceLabel).toBe("$64.99");
  });

  it("leaves the price null rather than printing a zero when none was sent", () => {
    expect(productSignalFromListing(listing({ price_label: "" }), NOW).priceLabel).toBeNull();
  });

  it("names an untitled listing rather than rendering a blank card", () => {
    expect(productSignalFromListing(listing({ title: "" }), NOW).productName).toBe(
      PRODUCT_SIGNAL_TITLE_FALLBACK
    );
  });
});

describe("the list projection drops what cannot be shown", () => {
  it("drops a listing with no id, because its tap has no destination", () => {
    const signals = productSignalsFromListings(
      [listing({ listing_id: 0, id: 0 }), listing({ listing_id: 5, id: 5 })],
      NOW
    );
    expect(signals.map((signal) => signal.productId)).toEqual([5]);
  });

  it("drops a sold-out listing rather than occupying a feed slot with it", () => {
    const signals = productSignalsFromListings(
      [listing({ listing_id: 5, id: 5, quantity: 0 }), listing({ listing_id: 6, id: 6 })],
      NOW
    );
    expect(signals.map((signal) => signal.productId)).toEqual([6]);
  });

  it("keeps an untracked listing, which is buyable", () => {
    const signals = productSignalsFromListings([listing({ quantity: null })], NOW);
    expect(signals).toHaveLength(1);
  });

  it("de-duplicates by product id so one product cannot fill both slots", () => {
    const signals = productSignalsFromListings(
      [listing({ listing_id: 5, id: 5 }), listing({ listing_id: 5, id: 5 }), listing({ listing_id: 6, id: 6 })],
      NOW
    );
    expect(signals.map((signal) => signal.productId)).toEqual([5, 6]);
  });

  it("returns an empty list for an empty response", () => {
    expect(productSignalsFromListings([], NOW)).toEqual([]);
  });
});
