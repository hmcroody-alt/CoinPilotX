/**
 * The native sheet adapter's contract, proven without a native module.
 *
 * Two things must never regress, because both decide whether a buyer is told
 * the truth about their money:
 *
 *   1. Availability gates the whole native path. A binary without the SDK must
 *      report `unavailable` so the checkout screen stays on the hosted page and
 *      never creates a PaymentIntent it cannot collect on.
 *   2. Outcome mapping. A dismissal is `canceled`, not `failed` — telling a
 *      buyer their payment failed when they simply changed their mind is both
 *      wrong and alarming. A real error is `failed` with buyer-readable copy.
 *
 * `__setStripeSdkForTests` injects a fake SDK so none of this needs the real
 * `@stripe/stripe-react-native` (which isn't in this binary anyway).
 */

import {
  APPLE_PAY_MERCHANT_ID,
  __setStripeSdkForTests,
  isPaymentSheetAvailable,
  presentPaymentSheet,
  type PaymentSheetBootstrap
} from "../stripePaymentSheet";

const bootstrap = (over: Partial<PaymentSheetBootstrap> = {}): PaymentSheetBootstrap => ({
  clientSecret: "pi_123_secret_456",
  paymentIntentId: "pi_123",
  publishableKey: "pk_live_abc",
  merchantDisplayName: "M&W Store",
  applePayMerchantId: "",
  amountCents: 500,
  currency: "USD",
  transactionIds: [1],
  ...over
});

type InitCall = Record<string, unknown>;

function fakeSdk(behaviour: {
  init?: { error?: { message?: string; code?: string } };
  present?: { error?: { message?: string; code?: string } };
  withInitStripe?: boolean;
}) {
  const calls: { initStripe: InitCall[]; initPaymentSheet: InitCall[]; presented: number } = {
    initStripe: [],
    initPaymentSheet: [],
    presented: 0
  };
  const sdk: Record<string, unknown> = {
    initPaymentSheet: async (opts: InitCall) => {
      calls.initPaymentSheet.push(opts);
      return behaviour.init ?? {};
    },
    presentPaymentSheet: async () => {
      calls.presented += 1;
      return behaviour.present ?? {};
    }
  };
  if (behaviour.withInitStripe) {
    sdk.initStripe = async (params: InitCall) => {
      calls.initStripe.push(params);
    };
  }
  return { sdk, calls };
}

afterEach(() => __setStripeSdkForTests(undefined));

describe("availability gates the native path", () => {
  it("is false when the SDK is absent", () => {
    __setStripeSdkForTests(null);
    expect(isPaymentSheetAvailable()).toBe(false);
  });

  it("is true when a conforming SDK is present", () => {
    const { sdk } = fakeSdk({});
    __setStripeSdkForTests(sdk as never);
    expect(isPaymentSheetAvailable()).toBe(true);
  });
});

describe("presentPaymentSheet outcome mapping", () => {
  it("returns unavailable — never a failure — when the SDK is absent", async () => {
    __setStripeSdkForTests(null);
    const outcome = await presentPaymentSheet(bootstrap());
    expect(outcome.result).toBe("unavailable");
  });

  it("refuses to present without a client secret", async () => {
    const { sdk, calls } = fakeSdk({});
    __setStripeSdkForTests(sdk as never);
    const outcome = await presentPaymentSheet(bootstrap({ clientSecret: "" }));
    expect(outcome).toMatchObject({ result: "failed", code: "missing_client_secret" });
    expect(calls.initPaymentSheet).toHaveLength(0);
  });

  it("maps a clean present() to completed", async () => {
    const { sdk } = fakeSdk({});
    __setStripeSdkForTests(sdk as never);
    expect(await presentPaymentSheet(bootstrap())).toEqual({ result: "completed" });
  });

  it("reads a dismissal as canceled, not failed", async () => {
    const { sdk } = fakeSdk({ present: { error: { code: "Canceled", message: "The payment has been canceled" } } });
    __setStripeSdkForTests(sdk as never);
    expect(await presentPaymentSheet(bootstrap())).toEqual({ result: "canceled" });
  });

  it("surfaces a real decline as failed with buyer-readable copy", async () => {
    const { sdk } = fakeSdk({ present: { error: { code: "card_declined", message: "Your card was declined." } } });
    __setStripeSdkForTests(sdk as never);
    const outcome = await presentPaymentSheet(bootstrap());
    expect(outcome).toEqual({ result: "failed", message: "Your card was declined.", code: "card_declined" });
  });

  it("reports an init failure as failed rather than presenting a dead sheet", async () => {
    const { sdk, calls } = fakeSdk({ init: { error: { message: "No such PaymentIntent", code: "resource_missing" } } });
    __setStripeSdkForTests(sdk as never);
    const outcome = await presentPaymentSheet(bootstrap());
    expect(outcome).toMatchObject({ result: "failed", code: "resource_missing" });
    expect(calls.presented).toBe(0);
  });
});

describe("publishable key wiring", () => {
  it("applies the server-supplied key before init when the SDK exposes initStripe", async () => {
    const { sdk, calls } = fakeSdk({ withInitStripe: true });
    __setStripeSdkForTests(sdk as never);
    await presentPaymentSheet(bootstrap({ publishableKey: "pk_live_xyz" }));
    expect(calls.initStripe).toHaveLength(1);
    expect(calls.initStripe[0]).toMatchObject({ publishableKey: "pk_live_xyz" });
  });

  it("uses the canonical Apple Pay merchant id in Stripe init and PaymentSheet", async () => {
    const { sdk, calls } = fakeSdk({ withInitStripe: true });
    __setStripeSdkForTests(sdk as never);
    await presentPaymentSheet(bootstrap({ applePayMerchantId: APPLE_PAY_MERCHANT_ID }));
    expect(calls.initStripe[0]).toMatchObject({ merchantIdentifier: APPLE_PAY_MERCHANT_ID });
    expect(calls.initPaymentSheet[0]).toMatchObject({
      merchantIdentifier: APPLE_PAY_MERCHANT_ID,
      applePay: { merchantCountryCode: "US" }
    });
  });

  it("refuses a server merchant id that differs from the signed entitlement", async () => {
    const { sdk, calls } = fakeSdk({ withInitStripe: true });
    __setStripeSdkForTests(sdk as never);
    const outcome = await presentPaymentSheet(bootstrap({ applePayMerchantId: "merchant.invalid" }));
    expect(outcome).toMatchObject({ result: "failed", code: "merchant_id_mismatch" });
    expect(calls.initStripe).toHaveLength(0);
    expect(calls.initPaymentSheet).toHaveLength(0);
  });

  it("still initialises the sheet when the SDK has no initStripe (provider-only builds)", async () => {
    const { sdk, calls } = fakeSdk({ withInitStripe: false });
    __setStripeSdkForTests(sdk as never);
    expect(await presentPaymentSheet(bootstrap())).toEqual({ result: "completed" });
    expect(calls.initPaymentSheet).toHaveLength(1);
  });

  it("collectAddress asks the sheet for a full billing address on the shipping lane", async () => {
    const { sdk, calls } = fakeSdk({});
    __setStripeSdkForTests(sdk as never);
    await presentPaymentSheet(bootstrap(), { collectAddress: true });
    expect(calls.initPaymentSheet[0]).toMatchObject({
      billingDetailsCollectionConfiguration: { name: "always", address: "full" }
    });
  });
});
