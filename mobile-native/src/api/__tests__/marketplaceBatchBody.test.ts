/**
 * What the bulk sheet actually puts on the wire.
 *
 * `StoreDashboardPrice.test.tsx` and `StoreDashboardCategory.test.tsx` prove the
 * screen *hands* these two functions the right payload — but both mock this whole
 * module, so neither can see what the functions do with it. Deleting the
 * `category` spread from `batchBody` left every one of those tests green: the
 * screen still passed a `categoryTarget`, the function still resolved, and the
 * server would have answered `INVALID_CATEGORY` for a request the seller had just
 * reviewed row by row.
 *
 * The same shape of hole as `marketplaceCheckoutQuantityBody.test.ts`: two layers
 * each covered, and nothing spanning the seam.
 *
 * The two functions are tested together and asserted to agree, because the
 * dangerous drift is not either one being wrong on its own — it is the dry run
 * describing a different request than the write that follows it. A preview that
 * omits the aisle answers "what happens if I change nothing", and the seller
 * reads the answer as consent for the move.
 */

const mockPulseApi = jest.fn();
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  batchMarketplaceSellerListings,
  previewMarketplaceSellerBatch,
  type MarketplaceCategoryTarget,
  type MarketplacePricingRule
} from "../marketplace";

const ROUTE = "/api/pulse/marketplace/seller/listings/batch";
const TO_HOME: MarketplaceCategoryTarget = { category: "Home & Kitchen", subcategory: "" };
const PLUS_20: MarketplacePricingRule = { type: "COST_PLUS_PERCENT", value: 20 };

function posted(call = 0) {
  const [route, init] = mockPulseApi.mock.calls[call] as [string, { method: string; body: string }];
  return { route, method: init.method, body: JSON.parse(init.body) };
}

beforeEach(() => {
  mockPulseApi.mockReset();
  mockPulseApi.mockResolvedValue({ ok: true, results: [] });
});

describe("the bulk write body", () => {
  it("posts the action, the ids and the key to the one batch route", async () => {
    await batchMarketplaceSellerListings({
      action: "publish",
      listingIds: [3, 1],
      idempotencyKey: "k-1"
    });

    const { route, method, body } = posted();
    expect(route).toBe(ROUTE);
    expect(method).toBe("POST");
    expect(body).toEqual({ action: "publish", listing_ids: [3, 1], idempotency_key: "k-1" });
  });

  it("carries the aisle under `category`, the key the route reads", async () => {
    await batchMarketplaceSellerListings({
      action: "category",
      listingIds: [1],
      idempotencyKey: "k-2",
      categoryTarget: TO_HOME
    });

    expect(posted().body.category).toEqual({ category: "Home & Kitchen", subcategory: "" });
  });

  it("sends the cleared child as an empty string, not as a missing key", async () => {
    // `""` is the instruction "drop whatever child these products have". An
    // absent key is the instruction "leave it alone", and the difference is
    // whether "Home & Kitchen / Crypto Basics" exists afterwards.
    await batchMarketplaceSellerListings({
      action: "category",
      listingIds: [1],
      idempotencyKey: "k-3",
      categoryTarget: TO_HOME
    });

    expect(posted().body.category).toHaveProperty("subcategory");
    expect(posted().body.category.subcategory).toBe("");
  });

  it("carries a pricing rule under `pricing_rule`", async () => {
    await batchMarketplaceSellerListings({
      action: "price",
      listingIds: [1],
      idempotencyKey: "k-4",
      pricingRule: PLUS_20
    });

    expect(posted().body.pricing_rule).toEqual({ type: "COST_PLUS_PERCENT", value: 20 });
  });

  it("omits the payload an action does not take, rather than sending null", async () => {
    // The route refuses a setting the action ignores — that check is deliberate,
    // and a client that sends `pricing_rule: null` on a publish trips it.
    await batchMarketplaceSellerListings({
      action: "hide",
      listingIds: [1],
      idempotencyKey: "k-5"
    });

    const { body } = posted();
    expect(body).not.toHaveProperty("pricing_rule");
    expect(body).not.toHaveProperty("category");
  });

  it("never claims to be a dry run", async () => {
    // The one flag that must not leak into the write: a `dry_run: true` on the
    // commit means the seller taps Apply, the sheet says done, and nothing moved.
    await batchMarketplaceSellerListings({
      action: "category",
      listingIds: [1],
      idempotencyKey: "k-6",
      categoryTarget: TO_HOME
    });

    expect(posted().body).not.toHaveProperty("dry_run");
  });
});

describe("the preview body", () => {
  it("marks itself a dry run", async () => {
    await previewMarketplaceSellerBatch({
      action: "category",
      listingIds: [1],
      idempotencyKey: "k-7",
      categoryTarget: TO_HOME
    });

    expect(posted().body.dry_run).toBe(true);
  });

  it("goes to the same route, because there is one authority", async () => {
    // §21. A separate /preview endpoint is a second implementation of every
    // eligibility rule, and it agrees with the writer right up until it doesn't.
    await previewMarketplaceSellerBatch({
      action: "category",
      listingIds: [1],
      idempotencyKey: "k-8",
      categoryTarget: TO_HOME
    });

    expect(posted().route).toBe(ROUTE);
  });

  it("describes the same request the write will make, field for field", async () => {
    const input = {
      action: "category" as const,
      listingIds: [4, 9],
      idempotencyKey: "k-9",
      categoryTarget: { category: "Education", subcategory: "Trading" }
    };

    await previewMarketplaceSellerBatch(input);
    await batchMarketplaceSellerListings(input);

    const { dry_run, ...preview } = posted(0).body;
    expect(dry_run).toBe(true);
    // Everything except the flag is identical, so the rows the seller read about
    // are the rows the write touches.
    expect(preview).toEqual(posted(1).body);
  });

  it("agrees with the write on a price rule too", async () => {
    const input = {
      action: "price" as const,
      listingIds: [4],
      idempotencyKey: "k-10",
      pricingRule: PLUS_20
    };

    await previewMarketplaceSellerBatch(input);
    await batchMarketplaceSellerListings(input);

    const { dry_run, ...preview } = posted(0).body;
    expect(preview).toEqual(posted(1).body);
  });
});
