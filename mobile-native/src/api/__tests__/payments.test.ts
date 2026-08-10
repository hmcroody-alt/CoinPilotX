/**
 * The payments bindings sit between the server's provider policy and the
 * purchase flow, so the decisions with financial meaning are pinned:
 *
 *   1. A routing 422 is a refusal-to-classify, not a crash — it comes back
 *      as a flagged decision the UI must surface.
 *   2. Unknown provider strings normalize to null; the client never invents
 *      a provider the server didn't name.
 *   3. Verification outcomes map to codes that encode the finish contract:
 *      only credited/already_credited permit finishTransaction.
 */
import {
  normalizeIapAdCreditProduct,
  normalizePaymentRouteDecision,
  verifyAppleAdCreditPurchase
} from "../payments";
import { pulseApi, PulseApiError } from "../pulseApi";

jest.mock("../pulseApi", () => {
  const actual = jest.requireActual("../pulseApi");
  return { ...actual, pulseApi: jest.fn() };
});

const mockedApi = pulseApi as jest.MockedFunction<typeof pulseApi>;

afterEach(() => jest.resetAllMocks());

describe("normalizePaymentRouteDecision", () => {
  it("passes a clean apple_iap decision through", () => {
    const decision = normalizePaymentRouteDecision({
      ok: true,
      provider: "apple_iap",
      classification: "digital",
      policy_basis: "3.1.1"
    });
    expect(decision).toEqual({
      ok: true,
      provider: "apple_iap",
      classification: "digital",
      policyBasis: "3.1.1",
      flagged: false
    });
  });

  it("nulls provider strings the client does not know", () => {
    expect(normalizePaymentRouteDecision({ ok: true, provider: "paypal" }).provider).toBeNull();
    expect(normalizePaymentRouteDecision({}).provider).toBeNull();
  });

  it("keeps the flagged bit — a refusal must stay visible", () => {
    expect(normalizePaymentRouteDecision({ ok: false, flagged: true }).flagged).toBe(true);
  });
});

describe("normalizeIapAdCreditProduct", () => {
  it("maps the server catalog row", () => {
    expect(
      normalizeIapAdCreditProduct({
        product_id: "com.pulsesoc.adcredits.tier1",
        amount_cents: 499,
        currency: "USD",
        credit_display: "$4.99"
      })
    ).toEqual({
      productId: "com.pulsesoc.adcredits.tier1",
      amountCents: 499,
      currency: "usd",
      creditDisplay: "$4.99"
    });
  });

  it("never yields a negative credit", () => {
    expect(normalizeIapAdCreditProduct({ amount_cents: -500 }).amountCents).toBe(0);
  });
});

describe("verifyAppleAdCreditPurchase — finish-contract codes", () => {
  it("maps a fresh credit", async () => {
    mockedApi.mockResolvedValueOnce({
      ok: true,
      deduped: false,
      amount_cents: 499,
      product_id: "com.pulsesoc.adcredits.tier1"
    });
    await expect(verifyAppleAdCreditPurchase(3, "a.b.c")).resolves.toEqual({
      status: "credited",
      amountCents: 499,
      productId: "com.pulsesoc.adcredits.tier1",
      deduped: false
    });
  });

  it("maps a replay to already_credited (still safe to finish)", async () => {
    mockedApi.mockResolvedValueOnce({ ok: true, deduped: true, amount_cents: 499, product_id: "x" });
    const outcome = await verifyAppleAdCreditPurchase(3, "a.b.c");
    expect(outcome.status).toBe("already_credited");
  });

  it("maps a flat 400 to rejected without crypto details", async () => {
    mockedApi.mockRejectedValueOnce(
      new PulseApiError("Transaction signature could not be verified.", 400)
    );
    await expect(verifyAppleAdCreditPurchase(3, "a.b.c")).resolves.toEqual({ status: "rejected" });
  });

  it("maps 503 to setup_required and 429 to rate_limited", async () => {
    mockedApi.mockRejectedValueOnce(new PulseApiError("anchors", 503));
    await expect(verifyAppleAdCreditPurchase(3, "a.b.c")).resolves.toEqual({ status: "setup_required" });
    mockedApi.mockRejectedValueOnce(new PulseApiError("slow down", 429));
    await expect(verifyAppleAdCreditPurchase(3, "a.b.c")).resolves.toEqual({ status: "rate_limited" });
  });

  it("rethrows genuinely unknown failures — the caller must not finish on those", async () => {
    mockedApi.mockRejectedValueOnce(new Error("socket hang up"));
    await expect(verifyAppleAdCreditPurchase(3, "a.b.c")).rejects.toThrow("socket hang up");
  });
});
