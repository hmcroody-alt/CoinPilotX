/**
 * Every marketplace read path returns listings through
 * `normalizeMarketplaceListings`, and screens are allowed to trust one thing
 * about the result: `listing.id` is a usable listing id.
 *
 * That guarantee is what lets a screen write `navigate("MarketplaceProduct", {
 * listingId: listing.id })` instead of re-deriving the id from whichever of the
 * two spellings the server happened to send. The presence Merch tab does
 * exactly that. If the collapse below ever stops holding, tapping an item on a
 * presence page starts opening listing 0 — so it is pinned here, at the one
 * place that decides it, rather than defended again in every caller.
 */
import { normalizeMarketplaceListing, normalizeMarketplaceListings } from "../marketplace";

describe("a normalized listing has one id under both spellings", () => {
  it("fills `id` from a payload that only carried `listing_id`", () => {
    const listing = normalizeMarketplaceListing({ listing_id: 12 } as never);
    expect(listing.id).toBe(12);
    expect(listing.listing_id).toBe(12);
  });

  it("fills `listing_id` from a payload that only carried `id`", () => {
    const listing = normalizeMarketplaceListing({ id: 12 } as never);
    expect(listing.id).toBe(12);
    expect(listing.listing_id).toBe(12);
  });

  it("drops a row with no usable id rather than passing 0 to a screen", () => {
    // A screen that received this would navigate to listing 0 — a product page
    // for nothing. Better that the row never arrives.
    const items = normalizeMarketplaceListings([
      { id: 4, listing_id: 4, title: "Tour Hoodie" },
      { title: "Broken row" }
    ] as never);
    expect(items.map((item) => item.id)).toEqual([4]);
  });
});

describe("a listing with no price does not leave here holding one", () => {
  it("keeps an absent price absent instead of inventing 'Request access'", () => {
    // "Request access" is a phrase a seller may choose. Supplying it for a
    // listing that has no price at all makes the choice for them, and every
    // surface downstream then presents it as theirs. A dropship import leaves
    // the price blank on purpose — seeding it with the supplier cost would
    // print the seller's own margin on their storefront — so imported drafts
    // arrived in the seller's own store priced "Request access", which is the
    // one place that prose means nothing: they will not request access to
    // their own product.
    expect(normalizeMarketplaceListing({ id: 7, price_label: "" } as never).price_label).toBe("");
    expect(normalizeMarketplaceListing({ id: 7 } as never).price_label).toBe("");
  });

  it("still carries a price the seller did choose, including an unpriced one", () => {
    expect(normalizeMarketplaceListing({ id: 7, price_label: "$18.50" } as never).price_label)
      .toBe("$18.50");
    expect(normalizeMarketplaceListing({ id: 7, price_label: "Request access" } as never).price_label)
      .toBe("Request access");
  });

  it("lets the downstream fallbacks run at all", () => {
    // Five surfaces write `listing.price_label || <their own copy>` — the
    // Marketplace card, the product screen, the Page block, and the two seller
    // rows. Fabricating here guaranteed the left side was always truthy, so
    // none of those fallbacks could ever execute. This is the property that
    // makes them reachable, asserted where it is decided.
    const imported = normalizeMarketplaceListing({ id: 7, title: "Beaded bracelet" } as never);
    expect(imported.price_label || "Price at checkout").toBe("Price at checkout");
  });
});
