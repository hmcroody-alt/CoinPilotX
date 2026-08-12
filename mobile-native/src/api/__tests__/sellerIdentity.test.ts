import { groupCartLines, type CartLine } from "../marketplaceCommerce";
import { normalizeMarketplaceListing } from "../marketplace";
import { sellerStoreInitial, sellerStoreName, sellerStoreNameOrEmpty } from "../sellerIdentity";

describe("seller identity is the store, never the owner", () => {
  it("prefers the canonical field and treats the legacy alias as the same value", () => {
    expect(sellerStoreName({ seller_store_name: "Roody's Shop", seller_name: "Roody's Shop" })).toBe("Roody's Shop");
    // Payloads cached before the server added the canonical field still render.
    expect(sellerStoreName({ seller_name: "Roody's Shop" })).toBe("Roody's Shop");
  });

  it("falls back to a generic store, never to something person-shaped", () => {
    expect(sellerStoreName({})).toBe("PulseSoc Store");
    expect(sellerStoreName({ seller_store_name: "   " })).toBe("PulseSoc Store");
    expect(sellerStoreNameOrEmpty({ seller_store_name: "  " })).toBe("");
  });

  it("draws the avatar initial from the store name", () => {
    expect(sellerStoreInitial({ seller_store_name: "roody's shop" })).toBe("R");
    expect(sellerStoreInitial({})).toBe("P");
  });
});

describe("the store name survives the whole buying flow", () => {
  const line = (over: Partial<CartLine>): CartLine => ({
    line_id: 1,
    listing_id: 10,
    qty: 1,
    state: "available",
    price_snapshot_minor: 500,
    price_now_minor: 500,
    currency: "USD",
    title: "Ball",
    cover_image_url: "",
    seller_user_id: 7,
    seller_store_name: "Roody's Shop",
    seller_name: "Roody's Shop",
    fulfillment: "shipping",
    added_at: "",
    ...over
  });

  it("names the cart group after the store the buyer saw on the product page", () => {
    const [group] = groupCartLines([line({}), line({ line_id: 2, listing_id: 11 })]);
    expect(group.sellerName).toBe("Roody's Shop");
  });

  it("normalizes a listing to one store identity under both keys", () => {
    // A discovery payload that only carried the legacy alias must not leave the
    // canonical field empty — checkout reads it downstream.
    const listing = normalizeMarketplaceListing({ id: 3, listing_id: 3, seller_name: "Roody's Shop" });
    expect(listing.seller_store_name).toBe("Roody's Shop");
    expect(listing.seller_name).toBe("Roody's Shop");
  });
});
