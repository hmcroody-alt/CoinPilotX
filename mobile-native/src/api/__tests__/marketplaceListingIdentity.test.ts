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
