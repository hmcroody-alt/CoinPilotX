jest.mock("../../api/payments", () => ({
  createPaymentIntent: jest.fn(),
  verifyApplePremiumPurchase: jest.fn()
}));

import { createPaymentIntent, verifyApplePremiumPurchase } from "../../api/payments";
import {
  APPLE_MANAGE_SUBSCRIPTIONS_URL,
  annualSavings,
  getPremiumOffers,
  openManageSubscriptions,
  purchasePremium,
  restorePremiumPurchases
} from "../appleIapPremium";
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

  it("asks the server for the product id rather than choosing one", async () => {
    // The App Store catalog stays server-owned: the client sends a plan name and
    // receives an id, so a plan can be withdrawn without an app release.
    (createPaymentIntent as jest.Mock).mockResolvedValue({
      ok: true, provider: "apple_iap", flow: "storekit",
      appleProductId: "com.pulsesoc.premium.annual"
    });
    const requested: string[] = [];
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase: async (sku) => { requested.push(sku); return { jwsRepresentationIos: JWS }; },
      finishTransaction: async () => undefined,
      getAvailablePurchases: async () => []
    };
    (verifyApplePremiumPurchase as jest.Mock).mockResolvedValue({ ok: true, verified: true });
    await purchasePremium("annual", { adapter });
    expect((createPaymentIntent as jest.Mock).mock.calls[0][0]).toMatchObject({
      purchaseContext: "premium", plan: "annual"
    });
    expect(requested).toEqual(["com.pulsesoc.premium.annual"]);
  });

  it("reports a dismissed sheet as cancelled, not as an error", async () => {
    // "Purchase cancelled." is the required copy. Anything that reads as a
    // failure would tell a member something went wrong when they simply
    // changed their mind.
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase: async () => { throw { code: "E_USER_CANCELLED", message: "User cancelled" }; },
      finishTransaction: async () => undefined,
      getAvailablePurchases: async () => []
    };
    await expect(purchasePremium("monthly", { adapter })).resolves.toEqual({ status: "cancelled" });
  });

  it("reports unavailable when StoreKit is absent", async () => {
    // The tile and the screen must survive a device with no StoreKit at all —
    // `null` here is the simulator/unsupported case, not a crash.
    await expect(purchasePremium("monthly", { adapter: null })).resolves.toEqual({ status: "unavailable" });
  });

  it("refuses to open Apple's sheet when the server did not authorise the flow", async () => {
    (createPaymentIntent as jest.Mock).mockResolvedValue({ ok: false });
    const requestPurchase = jest.fn();
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase,
      finishTransaction: async () => undefined,
      getAvailablePurchases: async () => []
    };
    await expect(purchasePremium("monthly", { adapter })).resolves.toEqual({ status: "unavailable" });
    expect(requestPurchase).not.toHaveBeenCalled();
  });
});

/**
 * Prices are Apple's, and so is the savings figure.
 *
 * The brief forbids a hardcoded "SAVE 17%". Every number below is derived from
 * the two localized prices actually returned for this storefront, and the badge
 * disappears whenever the comparison cannot be made honestly.
 */
describe("Premium plan catalog", () => {
  const product = (id: string, price: number, displayPrice: string, currency = "USD") => ({
    id, price, displayPrice, currency, title: id
  });

  function catalogAdapter(products: ReturnType<typeof product>[]): StoreKitAdapter {
    return {
      initConnection: async () => undefined,
      requestPurchase: async () => null,
      finishTransaction: async () => undefined,
      getAvailablePurchases: async () => [],
      getSubscriptions: async () => products
    };
  }

  const intent = jest.fn();

  // Re-armed per test: the suite above calls `jest.resetAllMocks()`, which
  // strips implementations from every mock in the file, not just its own.
  beforeEach(() => {
    intent.mockReset();
    intent.mockImplementation(async ({ plan }: { plan?: string }) => ({
      ok: true, provider: "apple_iap", flow: "storekit",
      appleProductId: `com.pulsesoc.premium.${plan}`
    }));
  });

  it("shows Apple's formatted price verbatim", async () => {
    const offers = await getPremiumOffers({
      platform: "ios",
      intent: intent as never,
      adapter: catalogAdapter([
        product("com.pulsesoc.premium.monthly", 9.99, "$9.99"),
        product("com.pulsesoc.premium.annual", 99.99, "$99.99")
      ])
    });
    expect(offers.plans.map((offer) => offer.displayPrice)).toEqual(["$9.99", "$99.99"]);
    expect(offers.annualSavingsPercent).toBe(17);
  });

  it("uses the storefront's own currency and numbers", async () => {
    // A member in Japan sees Apple's yen strings, and the savings figure is
    // recomputed from those — not carried over from the US price sheet.
    const offers = await getPremiumOffers({
      platform: "ios",
      intent: intent as never,
      adapter: catalogAdapter([
        product("com.pulsesoc.premium.monthly", 1500, "￥1,500", "JPY"),
        product("com.pulsesoc.premium.annual", 15000, "￥15,000", "JPY")
      ])
    });
    expect(offers.plans[1].displayPrice).toBe("￥15,000");
    expect(offers.annualSavingsPercent).toBe(17);
  });

  it("returns no plans at all when StoreKit cannot answer", async () => {
    // Not a remembered price, not a placeholder: the screen renders
    // "temporarily unavailable" and the tile stays on the profile.
    await expect(getPremiumOffers({ platform: "ios", intent: intent as never, adapter: null }))
      .resolves.toEqual({
        plans: [], annualSavingsPercent: null, status: "unavailable", missingPlans: ["monthly", "annual"]
      });
  });

  it("maps an empty StoreKit response to a terminal empty state", async () => {
    await expect(getPremiumOffers({ platform: "ios", intent: intent as never, adapter: catalogAdapter([]) }))
      .resolves.toMatchObject({ plans: [], status: "empty", missingPlans: ["monthly", "annual"] });
  });

  it("maps a StoreKit rejection to a terminal retryable failure", async () => {
    const adapter = catalogAdapter([]);
    adapter.getSubscriptions = async () => { throw new Error("store unavailable"); };
    await expect(getPremiumOffers({ platform: "ios", intent: intent as never, adapter }))
      .resolves.toMatchObject({ plans: [], status: "failed" });
  });

  it("bounds a StoreKit request that never resolves", async () => {
    const adapter = catalogAdapter([]);
    adapter.getSubscriptions = () => new Promise(() => undefined);
    await expect(getPremiumOffers({ platform: "ios", intent: intent as never, adapter, timeoutMs: 5 }))
      .resolves.toMatchObject({ plans: [], status: "timeout" });
  });

  it("bounds the complete catalog operation when the server intent never resolves", async () => {
    const stalledIntent = jest.fn(() => new Promise(() => undefined));
    await expect(getPremiumOffers({ platform: "ios", intent: stalledIntent as never, adapter: catalogAdapter([]), timeoutMs: 5 }))
      .resolves.toMatchObject({ plans: [], status: "timeout" });
  });

  it("keeps one plan when the other is withdrawn", async () => {
    const partial = jest.fn(async ({ plan }: { plan?: string }) =>
      plan === "annual"
        ? { ok: false }
        : { ok: true, provider: "apple_iap", flow: "storekit", appleProductId: "com.pulsesoc.premium.monthly" }
    );
    const offers = await getPremiumOffers({
      platform: "ios",
      intent: partial as never,
      adapter: catalogAdapter([product("com.pulsesoc.premium.monthly", 9.99, "$9.99")])
    });
    expect(offers.plans).toHaveLength(1);
    expect(offers.missingPlans).toEqual(["annual"]);
    expect(offers.annualSavingsPercent).toBeNull();
  });

  it("keeps annual when monthly is missing", async () => {
    const annualOnly = jest.fn(async ({ plan }: { plan?: string }) =>
      plan === "monthly"
        ? { ok: false }
        : { ok: true, provider: "apple_iap", flow: "storekit", appleProductId: "com.pulsesoc.premium.annual" }
    );
    const offers = await getPremiumOffers({
      platform: "ios",
      intent: annualOnly as never,
      adapter: catalogAdapter([product("com.pulsesoc.premium.annual", 99.99, "$99.99")])
    });
    expect(offers.plans.map((offer) => offer.plan)).toEqual(["annual"]);
    expect(offers.missingPlans).toEqual(["monthly"]);
  });

  it("can retry after a failed request and return localized products", async () => {
    const failed = catalogAdapter([]);
    failed.getSubscriptions = async () => { throw new Error("temporary"); };
    await expect(getPremiumOffers({ platform: "ios", intent: intent as never, adapter: failed }))
      .resolves.toMatchObject({ status: "failed" });
    await expect(getPremiumOffers({
      platform: "ios",
      intent: intent as never,
      adapter: catalogAdapter([product("com.pulsesoc.premium.monthly", 9.99, "$9.99")])
    })).resolves.toMatchObject({ status: "success", plans: [{ displayPrice: "$9.99" }] });
  });

  it.each([
    ["a missing plan", [{ plan: "monthly" as const, productId: "m", displayPrice: "$9.99", price: 9.99, currency: "USD" }]],
    ["mismatched currencies", [
      { plan: "monthly" as const, productId: "m", displayPrice: "$9.99", price: 9.99, currency: "USD" },
      { plan: "annual" as const, productId: "a", displayPrice: "€99.99", price: 99.99, currency: "EUR" }
    ]],
    ["no actual saving", [
      { plan: "monthly" as const, productId: "m", displayPrice: "$9.99", price: 9.99, currency: "USD" },
      { plan: "annual" as const, productId: "a", displayPrice: "$129.99", price: 129.99, currency: "USD" }
    ]],
    ["a free plan", [
      { plan: "monthly" as const, productId: "m", displayPrice: "$0.00", price: 0, currency: "USD" },
      { plan: "annual" as const, productId: "a", displayPrice: "$99.99", price: 99.99, currency: "USD" }
    ]]
  ])("shows no savings badge for %s", (_label, plans) => {
    expect(annualSavings(plans)).toBeNull();
  });
});

describe("Restore Purchases", () => {
  const premiumPurchase = (id: string) => ({ productId: id, jwsRepresentationIos: JWS });

  it("re-verifies premium transactions server-side", async () => {
    const finish = jest.fn();
    const verify = jest.fn(async () => ({ ok: true, verified: true }));
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase: async () => null,
      finishTransaction: finish,
      getAvailablePurchases: async () => [premiumPurchase("com.pulsesoc.premium.annual")]
    };
    await expect(restorePremiumPurchases({ adapter, verify: verify as never }))
      .resolves.toEqual({ status: "restored", count: 1 });
    expect(verify).toHaveBeenCalledWith(JWS);
  });

  it("grants nothing extra when replayed", async () => {
    // Idempotency is enforced by the UNIQUE constraint on
    // `provider_subscription_id` server-side; what this asserts is that the
    // client re-sends the same transaction rather than minting a new one, so
    // the replay lands on that constraint.
    const verify = jest.fn(async () => ({ ok: true, verified: true }));
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase: async () => null,
      finishTransaction: async () => undefined,
      getAvailablePurchases: async () => [premiumPurchase("com.pulsesoc.premium.annual")]
    };
    const first = await restorePremiumPurchases({ adapter, verify: verify as never });
    const second = await restorePremiumPurchases({ adapter, verify: verify as never });
    expect(second).toEqual(first);
    expect(verify.mock.calls).toEqual([[JWS], [JWS]]);
  });

  it("leaves ad-credit purchases alone", async () => {
    // Consumables belong to `restoreUnfinishedAdCreditPurchases`. Finishing one
    // here would consume a purchase this flow never verified.
    const verify = jest.fn();
    const finish = jest.fn();
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase: async () => null,
      finishTransaction: finish,
      getAvailablePurchases: async () => [premiumPurchase("com.pulsesoc.adcredits.pack1")]
    };
    await expect(restorePremiumPurchases({ adapter, verify: verify as never }))
      .resolves.toEqual({ status: "empty" });
    expect(verify).not.toHaveBeenCalled();
    expect(finish).not.toHaveBeenCalled();
  });

  it("distinguishes nothing-to-restore from a failed restore", async () => {
    // Reporting a verification failure as "nothing to restore" would send a
    // member to buy something they may already own.
    const adapter: StoreKitAdapter = {
      initConnection: async () => undefined,
      requestPurchase: async () => null,
      finishTransaction: async () => undefined,
      getAvailablePurchases: async () => [premiumPurchase("com.pulsesoc.premium.monthly")]
    };
    await expect(restorePremiumPurchases({ adapter, verify: (async () => ({ ok: false })) as never }))
      .resolves.toEqual({ status: "failed" });
  });

  it("reports unavailable rather than failed with no StoreKit", async () => {
    await expect(restorePremiumPurchases({ adapter: null })).resolves.toEqual({ status: "unavailable" });
  });
});

describe("Manage Subscription", () => {
  it("hands off to Apple instead of cancelling locally", async () => {
    // There is deliberately no local Cancel button: it could only mutate our
    // copy of the state while Apple kept billing.
    const open = jest.fn(async () => undefined);
    await expect(openManageSubscriptions({ open })).resolves.toBe(true);
    expect(open).toHaveBeenCalledWith(APPLE_MANAGE_SUBSCRIPTIONS_URL);
    expect(APPLE_MANAGE_SUBSCRIPTIONS_URL).toBe("https://apps.apple.com/account/subscriptions");
  });

  it("reports a failure to open rather than claiming success", async () => {
    const open = jest.fn(async () => { throw new Error("no handler"); });
    await expect(openManageSubscriptions({ open })).resolves.toBe(false);
  });
});
