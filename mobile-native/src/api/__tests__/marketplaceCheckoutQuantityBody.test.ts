/**
 * The quantity has to survive the last hop: into the request body.
 *
 * `MarketplaceCheckoutQuantityHandoff.test.tsx` proves the screen *hands*
 * `openMarketplaceCheckout` the number the buyer picked — but it does so by
 * mocking this whole module, so it cannot see what the function does with the
 * argument. Deleting the `quantity` line from the body survived that suite
 * untouched. This file closes that hole by reading the JSON actually posted.
 *
 * That is the same failure the gap being fixed here was made of: two layers
 * each covered by tests, and no test spanning the seam between them.
 */

const mockPulseApi = jest.fn();
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { openMarketplaceCheckout } from "../marketplace";

function postedBody() {
  const [, init] = mockPulseApi.mock.calls[0] as [string, { body: string }];
  return JSON.parse(init.body);
}

beforeEach(() => {
  mockPulseApi.mockReset();
  mockPulseApi.mockResolvedValue({ ok: true, transaction_id: 5 });
});

describe("the Buy Now request body", () => {
  it("carries the quantity the caller was given", async () => {
    await openMarketplaceCheckout(41, "key", "", "cash", null, 3);

    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/pulse/payments/checkout");
    expect(postedBody().quantity).toBe(3);
  });

  it("states one rather than omitting the field", async () => {
    // An absent field and a chosen 1 must not look the same on the wire. The
    // server has to assume one when it sees nothing, and that assumption is
    // exactly what silently priced every multi-unit order at a single unit.
    await openMarketplaceCheckout(41);

    const body = postedBody();
    expect(body).toHaveProperty("quantity");
    expect(body.quantity).toBe(1);
  });

  it("never sends a fractional, negative or non-numeric quantity", async () => {
    for (const bad of [0, -2, 2.7, NaN, "three" as unknown as number]) {
      mockPulseApi.mockClear();
      await openMarketplaceCheckout(41, "", "", "", null, bad);
      const { quantity } = postedBody();
      expect(Number.isInteger(quantity)).toBe(true);
      expect(quantity).toBeGreaterThanOrEqual(1);
    }
  });

  it("keeps the quantity out of the fields that decide the order's shape", async () => {
    await openMarketplaceCheckout(41, "key", "pickup", "cash", { contact_name: "A" }, 3);
    const body = postedBody();

    expect(body.item_id).toBe(41);
    expect(body.fulfillment).toBe("pickup");
    expect(body.payment_mode).toBe("cash");
    expect(body.fulfillment_details).toEqual({ contact_name: "A" });
    expect(body.quantity).toBe(3);
  });
});
