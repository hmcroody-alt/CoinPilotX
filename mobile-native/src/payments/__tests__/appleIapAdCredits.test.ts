/**
 * The purchase orchestrator moves real money, so the ordering contract is
 * pinned hard:
 *
 *   1. finishTransaction fires ONLY after the server confirms the credit —
 *      fresh or deduped. A finish before that consumes Apple's only proof of
 *      purchase with no ledger row to show for it.
 *   2. A server rejection or network failure leaves the transaction
 *      unfinished so restore can retry (server dedupe makes retry safe).
 *   3. The client obeys the server's provider decision — no StoreKit call
 *      when the server says stripe, no guessing when the server flags.
 *   4. Restore only ever touches ad-credit skus; subscriptions and anything
 *      else in the queue pass through untouched.
 */
import {
  extractSignedTransaction,
  purchaseAdCredits,
  restoreUnfinishedAdCreditPurchases,
  StoreKitAdapter,
  StoreKitPurchase
} from "../appleIapAdCredits";
import type { AppleVerifyOutcome, PaymentRouteDecision } from "../../api/payments";

const JWS = "eyJhbGciOiJFUzI1NiJ9.eyJ0cmFuc2FjdGlvbklkIjoiMTIzIn0.c2lnbmF0dXJlLXNlZ21lbnQ";

const iosDecision: PaymentRouteDecision = {
  ok: true,
  provider: "apple_iap",
  classification: "digital",
  policyBasis: "3.1.1",
  flagged: false
};

const CATALOG = [
  { productId: "com.pulsesoc.adcredits.tier1", amountCents: 499, currency: "usd", creditDisplay: "$4.99" }
];

function fakeAdapter(overrides: Partial<StoreKitAdapter> = {}) {
  const calls: string[] = [];
  const purchase: StoreKitPurchase = {
    productId: "com.pulsesoc.adcredits.tier1",
    jwsRepresentationIos: JWS
  };
  const adapter: StoreKitAdapter = {
    initConnection: async () => {
      calls.push("init");
    },
    requestPurchase: async () => {
      calls.push("purchase");
      return purchase;
    },
    finishTransaction: async () => {
      calls.push("finish");
    },
    getAvailablePurchases: async () => [],
    ...overrides
  };
  return { adapter, calls, purchase };
}

const credited: AppleVerifyOutcome = {
  status: "credited",
  amountCents: 499,
  productId: "com.pulsesoc.adcredits.tier1",
  deduped: false
};

describe("purchaseAdCredits — finish-after-credit contract", () => {
  it("finishes the transaction only after the server credits", async () => {
    const { adapter, calls } = fakeAdapter();
    const order: string[] = [];
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => {
        order.push("verify");
        return credited;
      }
    });
    expect(result).toEqual({
      status: "credited",
      amountCents: 499,
      productId: "com.pulsesoc.adcredits.tier1",
      deduped: false
    });
    // verify happened, and finish came after purchase (never before verify)
    expect(order).toEqual(["verify"]);
    expect(calls).toEqual(["init", "purchase", "finish"]);
  });

  it("treats a deduped credit as success and still finishes", async () => {
    const { adapter, calls } = fakeAdapter();
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => ({ ...credited, status: "already_credited", deduped: true })
    });
    expect(result.status).toBe("credited");
    expect((result as { deduped: boolean }).deduped).toBe(true);
    expect(calls).toContain("finish");
  });

  it("does NOT finish when the server rejects — transaction must survive for retry", async () => {
    const { adapter, calls } = fakeAdapter();
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => ({ status: "rejected" })
    });
    expect(result).toEqual({ status: "verification_pending", reason: "rejected" });
    expect(calls).not.toContain("finish");
  });

  it("does NOT finish when verification never reaches the server", async () => {
    const { adapter, calls } = fakeAdapter();
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => {
        throw new Error("network down");
      }
    });
    expect(result).toEqual({ status: "verification_pending", reason: "network" });
    expect(calls).not.toContain("finish");
  });

  it("still reports credited when the credit landed but finish throws (restore recovers)", async () => {
    const { adapter } = fakeAdapter({
      finishTransaction: async () => {
        throw new Error("storekit hiccup");
      }
    });
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => credited
    });
    expect(result.status).toBe("credited");
  });
});

describe("purchaseAdCredits — the server decides the provider", () => {
  it("makes no StoreKit call when the server routes to another provider", async () => {
    const { adapter, calls } = fakeAdapter();
    const decision: PaymentRouteDecision = { ...iosDecision, provider: "stripe" };
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter,
      route: async () => decision,
      catalog: async () => CATALOG,
      verify: async () => credited
    });
    expect(result).toEqual({ status: "use_other_provider", decision });
    expect(calls).toEqual([]);
  });

  it("surfaces a flagged routing refusal instead of guessing", async () => {
    const { adapter, calls } = fakeAdapter();
    const decision: PaymentRouteDecision = {
      ok: false,
      provider: null,
      classification: "ambiguous",
      policyBasis: "",
      flagged: true
    };
    const result = await purchaseAdCredits(7, "mystery_item", {
      adapter,
      route: async () => decision,
      catalog: async () => CATALOG,
      verify: async () => credited
    });
    expect(result).toEqual({ status: "routing_flagged", decision });
    expect(calls).toEqual([]);
  });

  it("refuses skus that are not in the server catalog", async () => {
    const { adapter, calls } = fakeAdapter();
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier99", {
      adapter,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => credited
    });
    expect(result).toEqual({ status: "unknown_product" });
    expect(calls).toEqual([]);
  });

  it("degrades cleanly when the IAP module is unavailable", async () => {
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter: null,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => credited
    });
    expect(result).toEqual({ status: "iap_unavailable" });
  });

  it("maps a user cancellation to cancelled, not an error", async () => {
    const { adapter } = fakeAdapter({
      requestPurchase: async () => {
        const err = new Error("user cancelled") as Error & { code: string };
        err.code = "E_USER_CANCELLED";
        throw err;
      }
    });
    const result = await purchaseAdCredits(7, "com.pulsesoc.adcredits.tier1", {
      adapter,
      route: async () => iosDecision,
      catalog: async () => CATALOG,
      verify: async () => credited
    });
    expect(result).toEqual({ status: "cancelled" });
  });
});

describe("extractSignedTransaction", () => {
  it("finds the JWS by shape across the field names expo-iap has used", () => {
    expect(extractSignedTransaction({ jwsRepresentationIos: JWS })).toBe(JWS);
    expect(extractSignedTransaction({ purchaseToken: JWS })).toBe(JWS);
    expect(extractSignedTransaction({ transactionReceipt: JWS })).toBe(JWS);
  });

  it("rejects things that are not a three-segment JWS", () => {
    expect(extractSignedTransaction({ transactionReceipt: '{"json":"receipt"}' })).toBeNull();
    expect(extractSignedTransaction({ purchaseToken: "short.tok" })).toBeNull();
    expect(extractSignedTransaction({})).toBeNull();
    expect(extractSignedTransaction(null)).toBeNull();
  });
});

describe("restoreUnfinishedAdCreditPurchases", () => {
  const adCredit: StoreKitPurchase = {
    productId: "com.pulsesoc.adcredits.tier1",
    jwsRepresentationIos: JWS
  };
  const subscription: StoreKitPurchase = {
    productId: "com.pulsesoc.premium.monthly",
    jwsRepresentationIos: JWS
  };

  it("verifies then finishes ad-credit purchases; never touches other skus", async () => {
    const finished: string[] = [];
    const { adapter } = fakeAdapter({
      getAvailablePurchases: async () => [adCredit, subscription],
      finishTransaction: async (p) => {
        finished.push(String(p.productId));
      }
    });
    const result = await restoreUnfinishedAdCreditPurchases(7, {
      adapter,
      verify: async () => ({ ...credited, status: "already_credited", deduped: true })
    });
    expect(result).toEqual({ checked: 1, credited: 1, pending: 0 });
    expect(finished).toEqual(["com.pulsesoc.adcredits.tier1"]);
  });

  it("leaves unverifiable transactions unfinished for the next attempt", async () => {
    const finished: string[] = [];
    const { adapter } = fakeAdapter({
      getAvailablePurchases: async () => [adCredit],
      finishTransaction: async (p) => {
        finished.push(String(p.productId));
      }
    });
    const result = await restoreUnfinishedAdCreditPurchases(7, {
      adapter,
      verify: async () => ({ status: "setup_required" })
    });
    expect(result).toEqual({ checked: 1, credited: 0, pending: 1 });
    expect(finished).toEqual([]);
  });

  it("is a no-op without the IAP module", async () => {
    const result = await restoreUnfinishedAdCreditPurchases(7, { adapter: null });
    expect(result).toEqual({ checked: 0, credited: 0, pending: 0 });
  });
});
