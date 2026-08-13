jest.mock("../../api/payments", () => ({
  createPaymentIntent: jest.fn(),
  verifyApplePremiumPurchase: jest.fn()
}));

import { createPaymentIntent, verifyApplePremiumPurchase } from "../../api/payments";
import { purchasePremium } from "../appleIapPremium";
import { StoreKitAdapter } from "../appleIapAdCredits";

const JWS = "eyJhbGciOiJFUzI1NiJ9.eyJ0cmFuc2FjdGlvbklkIjoiMTIzIn0.c2lnbmF0dXJlLXNlZ21lbnQ";

describe("Premium StoreKit completion", () => {
  beforeEach(() => {
    jest.resetAllMocks();
    (createPaymentIntent as jest.Mock).mockResolvedValue({
      ok: true, provider: "apple_iap", flow: "storekit",
      appleProductId: "com.pulsesoc.premium.monthly"
    });
  });

  it("finishes only after server verification", async () => {
    const calls: string[] = [];
    const adapter: StoreKitAdapter = {
      initConnection: async () => { calls.push("init"); },
      requestPurchase: async (_sku, type) => {
        calls.push(`purchase:${type}`);
        return { productId: "com.pulsesoc.premium.monthly", jwsRepresentationIos: JWS };
      },
      finishTransaction: async (_purchase, consumable) => { calls.push(`finish:${consumable}`); },
      getAvailablePurchases: async () => []
    };
    (verifyApplePremiumPurchase as jest.Mock).mockImplementation(async () => {
      calls.push("verify");
      return { ok: true, verified: true };
    });
    await expect(purchasePremium("monthly", { adapter })).resolves.toEqual({
      status: "verified", productId: "com.pulsesoc.premium.monthly"
    });
    expect(calls).toEqual(["init", "purchase:subs", "verify", "finish:false"]);
  });

  it("leaves an unverified transaction unfinished", async () => {
    const finish = jest.fn();
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase: async () => ({ jwsRepresentationIos: JWS }),
      finishTransaction: finish,
      getAvailablePurchases: async () => []
    };
    (verifyApplePremiumPurchase as jest.Mock).mockResolvedValue({ ok: false });
    await expect(purchasePremium("monthly", { adapter })).resolves.toEqual({ status: "verification_pending" });
    expect(finish).not.toHaveBeenCalled();
  });
});
