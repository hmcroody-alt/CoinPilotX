jest.mock("../../api/payments", () => ({
  createPaymentIntent: jest.fn(),
  verifyApplePremiumPurchase: jest.fn()
}));

import { createPaymentIntent, verifyApplePremiumPurchase } from "../../api/payments";
import {
  APPLE_MANAGE_SUBSCRIPTIONS_URL,
  annualSavings,
  decodeSignedTransactionPayload,
  getAppleSubscriptionSnapshot,
  getPremiumOffers,
  planFromProductId,
  openManageSubscriptions,
  PREMIUM_STOREKIT_LOG_TAG,
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

  /**
   * The regression this whole file exists to prevent.
   *
   * The paywall shipped reading "The App Store didn't return any products"
   * because App Store Connect held no subscription products at all — the query
   * was correct and the answer was empty. That failure is invisible unless the
   * request itself is asserted, so these four pin the request: the exact ids,
   * the query kind, and the fact that the response is matched by id rather than
   * by position.
   */
  it("asks Apple for exactly the two premium product ids", async () => {
    let asked: string[] = [];
    const adapter = catalogAdapter([]);
    adapter.getSubscriptions = async (ids: string[]) => { asked = ids; return []; };
    await getPremiumOffers({ platform: "ios", intent: intent as never, adapter });
    expect(asked).toEqual(["com.pulsesoc.premium.monthly", "com.pulsesoc.premium.annual"]);
  });

  it("queries the subscription catalog, never the one-time product catalog", async () => {
    // Auto-renewables do not come back from a consumable query. Asking the
    // wrong catalog returns zero products and looks exactly like this bug.
    const adapter = catalogAdapter([product("com.pulsesoc.premium.monthly", 9.99, "$9.99")]);
    const getProducts = jest.fn(async () => []);
    (adapter as unknown as { getProducts: unknown }).getProducts = getProducts;
    const offers = await getPremiumOffers({ platform: "ios", intent: intent as never, adapter });
    expect(getProducts).not.toHaveBeenCalled();
    expect(offers.diagnostics.requestType).toBe("subs");
  });

  it("maps each plan by product id, not by the order Apple answered in", async () => {
    // StoreKit gives no ordering guarantee. Reading products[0] as "monthly"
    // would price the annual plan at the monthly figure — a real overcharge
    // risk that a same-order fixture can never catch.
    const offers = await getPremiumOffers({
      platform: "ios",
      intent: intent as never,
      adapter: catalogAdapter([
        product("com.pulsesoc.premium.annual", 99.99, "$99.99"),
        product("com.pulsesoc.premium.monthly", 9.99, "$9.99")
      ])
    });
    expect(offers.plans).toEqual([
      expect.objectContaining({ plan: "monthly", productId: "com.pulsesoc.premium.monthly", displayPrice: "$9.99" }),
      expect.objectContaining({ plan: "annual", productId: "com.pulsesoc.premium.annual", displayPrice: "$99.99" })
    ]);
  });

  it("ignores a product Apple returned that nobody asked for", async () => {
    const offers = await getPremiumOffers({
      platform: "ios",
      intent: intent as never,
      adapter: catalogAdapter([
        product("com.pulsesoc.adcredits.tier1", 4.99, "$4.99"),
        product("com.pulsesoc.premium.monthly", 9.99, "$9.99")
      ])
    });
    expect(offers.plans.map((offer) => offer.productId)).toEqual(["com.pulsesoc.premium.monthly"]);
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
      .resolves.toMatchObject({
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

/**
 * Diagnostics: the four empty paywalls must stay distinguishable.
 *
 * An empty plan list has four causes with four different remedies, and on
 * screen they look identical. During the incident the only way to tell them
 * apart was a device log stream; these assertions move that evidence into the
 * result itself, and pin the one property that makes it safe to log — nothing
 * but ids, counts, an error token and a build marker ever appears in it.
 */
describe("Premium catalog diagnostics", () => {
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
  beforeEach(() => {
    intent.mockReset();
    intent.mockImplementation(async ({ plan }: { plan?: string }) => ({
      ok: true, provider: "apple_iap", flow: "storekit",
      appleProductId: `com.pulsesoc.premium.${plan}`
    }));
  });

  it("records what was asked and what came back on success", async () => {
    const offers = await getPremiumOffers({
      platform: "ios",
      intent: intent as never,
      adapter: catalogAdapter([
        product("com.pulsesoc.premium.monthly", 9.99, "$9.99"),
        product("com.pulsesoc.premium.annual", 99.99, "$99.99")
      ])
    });
    expect(offers.diagnostics).toEqual({
      requestedProductIds: ["com.pulsesoc.premium.monthly", "com.pulsesoc.premium.annual"],
      requestType: "subs",
      productCount: 2,
      returnedProductIds: ["com.pulsesoc.premium.monthly", "com.pulsesoc.premium.annual"],
      errorCode: null,
      // Build environment, not account environment: `dev` under jest, `release`
      // in a shipped binary. It never names the Apple ID or the storefront.
      environment: expect.stringMatching(/^ios\/(dev|release)$/)
    });
  });

  it("separates 'Apple answered with nothing' from 'Apple was never asked'", async () => {
    // This is the exact distinction the shipped paywall could not make. The
    // first is an App Store Connect problem; the second never leaves the app.
    const answeredNothing = await getPremiumOffers({
      platform: "ios", intent: intent as never, adapter: catalogAdapter([])
    });
    expect(answeredNothing.status).toBe("empty");
    expect(answeredNothing.diagnostics.requestedProductIds).toHaveLength(2);
    expect(answeredNothing.diagnostics.productCount).toBe(0);
    expect(answeredNothing.diagnostics.errorCode).toBeNull();

    const neverAsked = await getPremiumOffers({
      platform: "ios",
      intent: (async () => ({ ok: false })) as never,
      adapter: catalogAdapter([])
    });
    expect(neverAsked.status).toBe("unavailable");
    expect(neverAsked.diagnostics.requestedProductIds).toEqual([]);
    expect(neverAsked.diagnostics.errorCode).toBe("no_server_catalog");
  });

  it("carries the StoreKit error token through a failed request", async () => {
    const adapter = catalogAdapter([]);
    adapter.getSubscriptions = async () => { throw { code: "SKErrorDomain:0" }; };
    const offers = await getPremiumOffers({ platform: "ios", intent: intent as never, adapter });
    expect(offers.status).toBe("failed");
    expect(offers.diagnostics.errorCode).toBe("SKErrorDomain:0");
  });

  it("still says what it was asking for when the request times out", async () => {
    const adapter = catalogAdapter([]);
    adapter.getSubscriptions = () => new Promise(() => undefined);
    const offers = await getPremiumOffers({
      platform: "ios", intent: intent as never, adapter, timeoutMs: 5
    });
    expect(offers.status).toBe("timeout");
    expect(offers.diagnostics.requestedProductIds).toEqual([
      "com.pulsesoc.premium.monthly", "com.pulsesoc.premium.annual"
    ]);
    expect(offers.diagnostics.errorCode).toBe("premium_product_fetch_timeout");
  });

  it("never lets a receipt or token ride along in the error code", async () => {
    // The one place a secret could reach a log line is an error body, and this
    // line is written on every failure.
    const adapter = catalogAdapter([]);
    adapter.getSubscriptions = async () => {
      throw new Error(`failed with receipt ${JWS} for token 8a7c-secret/value?q=1`);
    };
    const offers = await getPremiumOffers({ platform: "ios", intent: intent as never, adapter });
    const code = offers.diagnostics.errorCode || "";
    expect(code).not.toContain(JWS);
    // Not even a prefix: every opaque run is dropped, so no fragment of a
    // signed transaction survives truncation into the log.
    expect(code).not.toContain(JWS.split(".")[0]);
    expect(code).not.toMatch(/[A-Za-z0-9+/=]{20,}/);
    expect(code.length).toBeLessThanOrEqual(64);
    expect(code).toMatch(/^[A-Za-z0-9_.:-]+$/);
  });

  it("writes one greppable line per query and no purchase data", async () => {
    const log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    try {
      await getPremiumOffers({
        platform: "ios",
        intent: intent as never,
        adapter: catalogAdapter([product("com.pulsesoc.premium.monthly", 9.99, "$9.99")])
      });
      const lines = log.mock.calls.map((call) => String(call[0]))
        .filter((line) => line.startsWith(PREMIUM_STOREKIT_LOG_TAG));
      expect(lines).toHaveLength(1);
      expect(lines[0]).toContain("com.pulsesoc.premium.monthly");
      expect(lines[0]).not.toMatch(/jws|receipt|token|appAccountToken/i);
    } finally {
      log.mockRestore();
    }
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

/**
 * The billing-card fallback: Apple's own facts about an existing subscription.
 *
 * Everything asserted here comes out of a signed-transaction payload or the
 * localized product metadata. No test — and no code path — supplies a price,
 * a date or a status the fake Apple did not.
 */
describe("Apple subscription snapshot", () => {
  const NOW = Date.parse("2026-08-25T12:00:00Z");

  function signedJws(payload: Record<string, unknown>): string {
    const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
    return `eyJhbGciOiJFUzI1NiJ9.${body}.c2lnbmF0dXJlLXNlZ21lbnQ`;
  }

  function snapshotAdapter(
    purchases: Record<string, unknown>[],
    products: { id: string; displayPrice: string }[] = []
  ): StoreKitAdapter {
    return {
      initConnection: async () => undefined,
      requestPurchase: async () => null,
      finishTransaction: async () => undefined,
      getAvailablePurchases: async () => purchases,
      getSubscriptions: async () =>
        products.map((p) => ({ ...p, price: 1, currency: "USD", title: p.id }))
    };
  }

  it("decodes only what Apple signed", () => {
    const payload = { productId: "com.pulsesoc.premium.annual", expiresDate: 123 };
    expect(decodeSignedTransactionPayload(signedJws(payload))).toEqual(payload);
    expect(decodeSignedTransactionPayload("not-a-jws")).toBeNull();
    expect(decodeSignedTransactionPayload("a.!!!!.c")).toBeNull();
  });

  it("maps the server-owned sku suffix to a plan and nothing else", () => {
    expect(planFromProductId("com.pulsesoc.premium.monthly")).toBe("monthly");
    expect(planFromProductId("com.pulsesoc.premium.annual")).toBe("annual");
    expect(planFromProductId("com.pulsesoc.premium.mystery")).toBeNull();
  });

  it("reports an active subscription from the verified expiry date", async () => {
    const expires = NOW + 20 * 24 * 3600 * 1000;
    const original = NOW - 200 * 24 * 3600 * 1000;
    const adapter = snapshotAdapter(
      [{
        productId: "com.pulsesoc.premium.annual",
        jwsRepresentationIos: signedJws({
          productId: "com.pulsesoc.premium.annual",
          expiresDate: expires,
          originalPurchaseDate: original
        })
      }],
      [{ id: "com.pulsesoc.premium.annual", displayPrice: "€99,99" }]
    );
    await expect(getAppleSubscriptionSnapshot({ adapter, now: () => NOW })).resolves.toEqual({
      productId: "com.pulsesoc.premium.annual",
      plan: "annual",
      displayPrice: "€99,99",
      status: "active",
      expiresAt: new Date(expires).toISOString(),
      originalPurchaseAt: new Date(original).toISOString()
    });
  });

  it("reports expired — never active — when the verified date has passed", async () => {
    const adapter = snapshotAdapter([{
      productId: "com.pulsesoc.premium.monthly",
      jwsRepresentationIos: signedJws({ expiresDate: NOW - 1000 })
    }]);
    const snapshot = await getAppleSubscriptionSnapshot({ adapter, now: () => NOW });
    expect(snapshot?.status).toBe("expired");
    expect(snapshot?.plan).toBe("monthly");
  });

  it("prefers the transaction that governs access after an upgrade", async () => {
    const adapter = snapshotAdapter([
      { productId: "com.pulsesoc.premium.monthly", jwsRepresentationIos: signedJws({ expiresDate: NOW + 1000 }) },
      { productId: "com.pulsesoc.premium.annual", jwsRepresentationIos: signedJws({ expiresDate: NOW + 9000 }) }
    ]);
    const snapshot = await getAppleSubscriptionSnapshot({ adapter, now: () => NOW });
    expect(snapshot?.productId).toBe("com.pulsesoc.premium.annual");
  });

  it("omits the price rather than inventing one when metadata fails", async () => {
    const adapter = snapshotAdapter([{
      productId: "com.pulsesoc.premium.annual",
      jwsRepresentationIos: signedJws({ expiresDate: NOW + 1000 })
    }]);
    adapter.getSubscriptions = async () => { throw new Error("store down"); };
    const snapshot = await getAppleSubscriptionSnapshot({ adapter, now: () => NOW });
    expect(snapshot?.displayPrice).toBeNull();
    expect(snapshot?.originalPurchaseAt).toBeNull();
  });

  it("returns null — no fabricated card — without a verified expiry", async () => {
    // A premium transaction with no readable expiry proves nothing displayable.
    const noExpiry = snapshotAdapter([{ productId: "com.pulsesoc.premium.annual", jwsRepresentationIos: signedJws({}) }]);
    await expect(getAppleSubscriptionSnapshot({ adapter: noExpiry, now: () => NOW })).resolves.toBeNull();
    // Other products are not premium's to display.
    const otherSku = snapshotAdapter([{ productId: "com.pulsesoc.adcredits.small", jwsRepresentationIos: signedJws({ expiresDate: NOW + 1 }) }]);
    await expect(getAppleSubscriptionSnapshot({ adapter: otherSku, now: () => NOW })).resolves.toBeNull();
    // No StoreKit at all degrades to the honest none-state, not a crash.
    await expect(getAppleSubscriptionSnapshot({ adapter: null })).resolves.toBeNull();
  });
});
