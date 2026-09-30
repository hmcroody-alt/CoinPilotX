/**
 * The picker's wire contract, and the four ways it could lie to a seller.
 *
 * This module is the one read in the commerce layer whose errors are *not*
 * swallowed, and every assertion below defends a decision that a future reader
 * would plausibly "fix" in the wrong direction:
 *
 *   1. **A failure throws.** Its sibling `commerceDiscovery.ts` promises the
 *      opposite in its header — "every read swallows its errors and returns
 *      empty" — and that is correct there, because `[]` on a viewer surface
 *      means "no products here". Here `[]` means "you have no products to tag",
 *      a claim about the seller's own store. Returning it after a failed query
 *      tells a seller with forty listings that they have none, and the client
 *      renders that as a dead end. So the first test asserts a rejection, and it
 *      is the single most important line in this file: adding a `catch` that
 *      returns an empty result would pass every other test here.
 *
 *   2. **`serves` is derived from the reason, not read alongside it.** The
 *      server sends both and they cannot disagree there. A client that trusts
 *      them independently has two sources of truth for one fact, and the
 *      asymmetry matters: believing `serves: true` over a present reason shows a
 *      product as fine *while printing why it is broken underneath*.
 *
 *   3. **An unknown code becomes `unknown_reason`, never the raw string.** The
 *      label is the second half of an i18n key. A code this build has never
 *      heard of — which is a real case, since the server can extend
 *      `INELIGIBLE_CODES` and installed clients keep running — would otherwise
 *      render as a bare server identifier under a product card in every locale.
 *
 *   4. **Both limits come off the wire.** They are server constants. A
 *      client-side copy is a divergence waiting for the constant to change, and
 *      it presents as a seller being told they may tag five when three will be
 *      stored. The fallbacks exist only for a response missing the key entirely.
 */
import { pulseApi } from "../pulseApi";
import { fetchTaggableProducts } from "../taggableProducts";

jest.mock("../pulseApi", () => ({ pulseApi: jest.fn() }));

const api = pulseApi as jest.MockedFunction<typeof pulseApi>;

/** A servable listing, in the server's own casing. */
function rawRow(overrides: Record<string, unknown> = {}) {
  return {
    listing_id: 43,
    product: {
      listing_id: 43,
      title: "Hand-thrown mug",
      price_label: "$28.00",
      cover_image_url: "https://cdn.example/mug.jpg",
      seller_user_id: 1,
      seller_store_name: "Kiln",
      category: "home",
      rating: 4.5,
      rating_count: 12
    },
    serves: true,
    blocked_reason: "",
    ...overrides
  };
}

function response(rows: Record<string, unknown>[], overrides: Record<string, unknown> = {}) {
  return { ok: true, products: rows, max_per_content: 5, request_limit: 20, ...overrides };
}

beforeEach(() => {
  api.mockReset();
});

describe("a failure is not an empty store", () => {
  it("rejects rather than resolving to an empty list", async () => {
    api.mockRejectedValueOnce(new Error("TAGGABLE_PRODUCTS_UNAVAILABLE"));
    await expect(fetchTaggableProducts()).rejects.toThrow("TAGGABLE_PRODUCTS_UNAVAILABLE");
  });

  it("still reports a genuinely empty store as empty", async () => {
    // The distinction only exists if *both* halves are reachable: "empty" and
    // "broken" have to be distinguishable by the caller, so an empty catalogue
    // must resolve normally rather than being treated as suspicious.
    api.mockResolvedValueOnce(response([]));
    await expect(fetchTaggableProducts()).resolves.toMatchObject({ products: [] });
  });
});

describe("serves is derived from the reason", () => {
  it("treats a blocked listing as blocked even when the server says it serves", async () => {
    api.mockResolvedValueOnce(response([rawRow({ serves: true, blocked_reason: "no_cover_image" })]));
    const result = await fetchTaggableProducts();
    expect(result.products[0]).toMatchObject({ serves: false, blockedReason: "no_cover_image" });
  });

  it("treats a reasonless listing as serving even when the server says it does not", async () => {
    api.mockResolvedValueOnce(response([rawRow({ serves: false, blocked_reason: "" })]));
    const result = await fetchTaggableProducts();
    expect(result.products[0]).toMatchObject({ serves: true, blockedReason: "" });
  });

  it("carries each of the six composer-reachable codes verbatim", async () => {
    // Byte-identical to `eligibility.INELIGIBLE_CODES`, minus the two per-viewer
    // codes a creator's request about their own store cannot produce. A
    // paraphrase here would type-check and render a missing-key placeholder.
    const codes = [
      "not_purchasable",
      "no_cover_image",
      "no_resolvable_price",
      "moderation_flagged",
      "listing_risk",
      "seller_risk"
    ];
    api.mockResolvedValueOnce(
      response(codes.map((code, index) => rawRow({ listing_id: 100 + index, blocked_reason: code })))
    );
    const result = await fetchTaggableProducts();
    expect(result.products.map((row) => row.blockedReason)).toEqual(codes);
  });
});

describe("an unknown code degrades to a sentence, not to the wire value", () => {
  it("maps a code this build has never heard of to unknown_reason", async () => {
    api.mockResolvedValueOnce(response([rawRow({ blocked_reason: "seller_on_holiday" })]));
    const result = await fetchTaggableProducts();
    expect(result.products[0].blockedReason).toBe("unknown_reason");
    expect(result.products[0].serves).toBe(false);
  });

  it("does not treat whitespace as a reason", async () => {
    api.mockResolvedValueOnce(response([rawRow({ blocked_reason: "   " })]));
    expect((await fetchTaggableProducts()).products[0]).toMatchObject({ serves: true, blockedReason: "" });
  });
});

describe("both limits are read, never assumed", () => {
  it("reports the server's values", async () => {
    api.mockResolvedValueOnce(response([rawRow()], { max_per_content: 3, request_limit: 7 }));
    const result = await fetchTaggableProducts();
    expect(result.maxPerContent).toBe(3);
    expect(result.requestLimit).toBe(7);
  });

  it("falls back only when the key is absent", async () => {
    const body = response([rawRow()]) as Record<string, unknown>;
    delete body.max_per_content;
    delete body.request_limit;
    api.mockResolvedValueOnce(body);
    const result = await fetchTaggableProducts();
    expect(result.maxPerContent).toBe(5);
    expect(result.requestLimit).toBe(20);
  });
});

describe("rows without an identity are dropped", () => {
  it("keeps a row whose id is only on the nested product", async () => {
    const row = rawRow() as Record<string, unknown>;
    delete row.listing_id;
    api.mockResolvedValueOnce(response([row]));
    expect((await fetchTaggableProducts()).products[0].listingId).toBe(43);
  });

  it("drops a row with no resolvable listing id", async () => {
    // An unidentifiable row cannot be tagged — there is nothing to send — so it
    // is dropped rather than rendered as a card that does nothing when pressed.
    api.mockResolvedValueOnce(response([{ product: { title: "Ghost" }, serves: true }]));
    expect((await fetchTaggableProducts()).products).toEqual([]);
  });

  it("survives a response with no products array at all", async () => {
    api.mockResolvedValueOnce({ ok: true });
    expect((await fetchTaggableProducts()).products).toEqual([]);
  });
});

describe("the request", () => {
  it("sends no limit when none is asked for, letting the server clamp", async () => {
    api.mockResolvedValueOnce(response([]));
    await fetchTaggableProducts();
    expect(api).toHaveBeenCalledWith("/api/pulse/commerce/discovery/taggable-products");
  });

  it("floors a fractional limit rather than sending it raw", async () => {
    api.mockResolvedValueOnce(response([]));
    await fetchTaggableProducts(12.7);
    expect(api).toHaveBeenCalledWith("/api/pulse/commerce/discovery/taggable-products?limit=12");
  });

  it("ignores a nonsensical limit instead of sending it", async () => {
    api.mockResolvedValueOnce(response([]));
    await fetchTaggableProducts(0);
    expect(api).toHaveBeenCalledWith("/api/pulse/commerce/discovery/taggable-products");
  });
});
