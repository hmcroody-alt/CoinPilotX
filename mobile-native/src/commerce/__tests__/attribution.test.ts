/**
 * §18: the funnel past the click has to point at the placement that caused it.
 *
 * The thing worth testing here is not that a map remembers what it was told —
 * it is the three ways this module is allowed to answer *null*, because every
 * one of them is a case where the alternative is actively wrong:
 *
 *   - an organic arrival, which must not be credited to discovery;
 *   - an expired window, where the server would reject the event anyway
 *     (`events.load_placement` raises `PLACEMENT_EXPIRED`), so returning it
 *     converts a clean "no attribution" into a failed request;
 *   - an evicted entry, which is the bounded-memory tradeoff made explicit.
 *
 * A test suite that only pinned the happy path would still pass if this module
 * attributed every product on the device to the last placement anyone tapped.
 */
import {
  __resetCommerceAttribution,
  attributeCommerceClick,
  commerceAttributionFor
} from "../attribution";
import type { CommercePlacement } from "../../api/commerceDiscovery";

const HOUR_MS = 60 * 60 * 1000;

function placement(listingId: number, overrides: Partial<CommercePlacement> = {}): CommercePlacement {
  return {
    placementId: `plc-${listingId}`,
    impressionToken: `tok-${listingId}`,
    surface: "feed",
    slot: 0,
    expiresAt: new Date(Date.now() + HOUR_MS).toISOString(),
    promotionClass: "organic",
    labelKey: "commerce.label.recommended",
    reason: "trending",
    rankingVersion: "v1",
    priceMinor: 2500,
    priceCurrency: "USD",
    product: {
      listingId,
      title: `Listing ${listingId}`,
      priceLabel: "$25.00",
      coverImageUrl: "",
      sellerUserId: 1,
      sellerStoreName: "Store",
      category: "shoes",
      rating: 0,
      ratingCount: 0
    },
    ...overrides
  } as CommercePlacement;
}

beforeEach(() => {
  __resetCommerceAttribution();
});

describe("commerceAttributionFor — what it answers", () => {
  it("names the placement that sent the shopper to this product", () => {
    attributeCommerceClick(placement(41));
    expect(commerceAttributionFor(41)).toEqual({ placementId: "plc-41", impressionToken: "tok-41" });
  });

  it("answers null for a product nobody clicked a placement for", () => {
    // The common case, and not an error: search, deep links, the marketplace
    // grid and shares all land here. Crediting them to discovery is the failure
    // mode that makes an attribution system worse than having none.
    attributeCommerceClick(placement(41));
    expect(commerceAttributionFor(99)).toBeNull();
  });

  it("answers null for a missing or nonsense listing id", () => {
    expect(commerceAttributionFor(0)).toBeNull();
    expect(commerceAttributionFor(null)).toBeNull();
    expect(commerceAttributionFor(undefined)).toBeNull();
  });

  it("hands back only the two fields an emit needs", () => {
    // Not the product, not the price, not the seller. Everything else about a
    // placement is read back server-side from the stored row, so carrying it
    // here would be duplicating a source of truth that already exists.
    attributeCommerceClick(placement(41));
    expect(Object.keys(commerceAttributionFor(41) || {}).sort()).toEqual([
      "impressionToken",
      "placementId"
    ]);
  });
});

describe("the window is the placement's own TTL", () => {
  it("drops an entry the server would reject rather than returning it", () => {
    const expiresAt = new Date(Date.now() + 1000).toISOString();
    attributeCommerceClick(placement(41, { expiresAt }));
    expect(commerceAttributionFor(41)).not.toBeNull();
    expect(commerceAttributionFor(41, Date.parse(expiresAt) + 1)).toBeNull();
  });

  it("treats the expiry instant itself as past, matching the server's own check", () => {
    const expiresAt = new Date(Date.now() + 1000).toISOString();
    attributeCommerceClick(placement(41, { expiresAt }));
    expect(commerceAttributionFor(41, Date.parse(expiresAt))).toBeNull();
  });

  it("forgets an expired entry instead of re-checking it forever", () => {
    const expiresAt = new Date(Date.now() + 1000).toISOString();
    attributeCommerceClick(placement(41, { expiresAt }));
    expect(commerceAttributionFor(41, Date.parse(expiresAt) + 1)).toBeNull();
    // Even asked again inside the original window, it is gone: an expiry is a
    // decision, not a transient reading of the clock.
    expect(commerceAttributionFor(41, Date.now())).toBeNull();
  });

  it("keeps a placement that sent no expiry at all", () => {
    // A malformed or absent `expires_at` must not silently disable attribution
    // for that click — the server is still the authority on rejection.
    attributeCommerceClick(placement(41, { expiresAt: "" }));
    expect(commerceAttributionFor(41, Date.now() + 10 * HOUR_MS)).not.toBeNull();
    attributeCommerceClick(placement(42, { expiresAt: "not-a-date" }));
    expect(commerceAttributionFor(42, Date.now() + 10 * HOUR_MS)).not.toBeNull();
  });
});

describe("what is refused at the door", () => {
  it("ignores a placement with nothing to attribute to", () => {
    attributeCommerceClick(null);
    attributeCommerceClick(undefined);
    attributeCommerceClick(placement(41, { placementId: "" }));
    expect(commerceAttributionFor(41)).toBeNull();
  });

  it("ignores a placement with no impression token", () => {
    // Both halves are required by `identityBody`; storing one alone would
    // produce an entry that can only ever generate a rejected request.
    attributeCommerceClick(placement(41, { impressionToken: "" }));
    expect(commerceAttributionFor(41)).toBeNull();
  });

  it("ignores a placement whose product carries no listing id", () => {
    const orphan = placement(41);
    attributeCommerceClick({ ...orphan, product: { ...orphan.product, listingId: 0 } });
    expect(commerceAttributionFor(41)).toBeNull();
  });
});

describe("bounded memory", () => {
  it("keeps the most recent clicks and evicts the oldest", () => {
    for (let id = 1; id <= 40; id += 1) attributeCommerceClick(placement(id));
    // 32 is the cap; the first eight are gone and the last thirty-two are not.
    expect(commerceAttributionFor(8)).toBeNull();
    expect(commerceAttributionFor(9)).not.toBeNull();
    expect(commerceAttributionFor(40)).not.toBeNull();
  });

  it("treats a re-clicked product as recently seen rather than as next to evict", () => {
    // This is what the `delete` before the `set` buys. A bare `Map.set` on an
    // existing key keeps the original insertion position, so a product the
    // shopper keeps returning to would be evicted ahead of one they tapped once.
    for (let id = 1; id <= 32; id += 1) attributeCommerceClick(placement(id));
    attributeCommerceClick(placement(1));
    attributeCommerceClick(placement(33));
    expect(commerceAttributionFor(1)).not.toBeNull();
    expect(commerceAttributionFor(2)).toBeNull();
  });

  it("re-attributes a product to the placement that most recently sent it", () => {
    attributeCommerceClick(placement(41));
    attributeCommerceClick(
      placement(41, { placementId: "plc-later", impressionToken: "tok-later" })
    );
    expect(commerceAttributionFor(41)).toEqual({
      placementId: "plc-later",
      impressionToken: "tok-later"
    });
  });
});
