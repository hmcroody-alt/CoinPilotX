/**
 * Native Stripe PaymentSheet adapter.
 *
 * The buyer taps "Pay securely · $5.00" and must stay inside PulseSoc. That
 * rules out `Linking.openURL(checkout_url)`, which is a Safari handoff no
 * matter how it is dressed up. The in-app sheet needs a PaymentIntent client
 * secret instead, which is what `/api/pulse/marketplace/cart/checkout` and
 * `/api/pulse/payments/checkout` now return when asked for
 * `payment_mode: "payment_sheet"`.
 *
 * Why the SDK is resolved at runtime
 * ----------------------------------
 * `@stripe/stripe-react-native` contains native iOS and Android code. Adding it
 * to `package.json` does not put it in the app: the binary has to be rebuilt
 * (`npx expo prebuild` + a new EAS build) before `initPaymentSheet` exists at
 * all. A static `import` would therefore turn "the SDK isn't in this binary
 * yet" into a red-screen crash on launch for every user, including on screens
 * that have nothing to do with payments.
 *
 * So the module is looked up through `require` inside a try, and its absence is
 * reported as a value — `available: false` — that the checkout screen can act
 * on. That keeps the current TestFlight build working (it falls back to the
 * hosted page it uses today) while the native path ships in the next binary.
 * The fallback is a bridge, not the destination: once the SDK is in the build,
 * `available` is true and the sheet is the only path taken.
 *
 * This file deliberately contains no `any` leaking outward and no UI. It is the
 * single place that knows the SDK's shape, so if that shape changes there is
 * one file to fix rather than three screens.
 */

/** What the server hands back for a sheet-mode checkout. */
export type PaymentSheetBootstrap = {
  /** `pi_..._secret_...`. Without this there is nothing to present. */
  clientSecret: string;
  paymentIntentId: string;
  publishableKey: string;
  /** The *store* name — never the account holder's personal name. Shown in the
   * sheet header, so getting it wrong leaks a seller's real identity. */
  merchantDisplayName: string;
  /** Empty means this binary has no Apple Pay entitlement. Announcing a
   * merchant id the app cannot honour makes the sheet fail at presentation
   * instead of quietly offering the card form, so empty must stay empty. */
  applePayMerchantId: string;
  /** Echoed by the server so the sheet is never presented against a number the
   * review screen did not show. */
  amountCents: number;
  currency: string;
  transactionIds: readonly number[];
};

export type PaymentSheetOutcome =
  /** The sheet reported success. This is *not* financial authority — the order
   * is only paid once the webhook says so. The caller must still confirm. */
  | { result: "completed" }
  /** The buyer dismissed the sheet. Nothing was charged; say so plainly. */
  | { result: "canceled" }
  /** Stripe declined or errored. `message` is buyer-readable; `code` is the
   * Stripe error code for logs. */
  | { result: "failed"; message: string; code: string }
  /** The SDK is not in this binary. The caller falls back to the hosted page. */
  | { result: "unavailable" };

type StripeSdk = {
  initPaymentSheet: (options: Record<string, unknown>) => Promise<{ error?: { message?: string; code?: string } }>;
  presentPaymentSheet: () => Promise<{ error?: { message?: string; code?: string } }>;
  /**
   * Optional because this app has no `<StripeProvider>`: the checkout screen is
   * the only Stripe surface, so the publishable key travels with the server's
   * sheet bootstrap and is applied here rather than at app root. Older SDK
   * builds that only expose it through the provider simply won't have this, so
   * the call site guards on `typeof`.
   */
  initStripe?: (params: { publishableKey: string; merchantIdentifier?: string }) => Promise<void> | void;
};

let cached: StripeSdk | null | undefined;

/** Resolve the SDK once. `undefined` means "not looked up yet"; `null` means
 * "looked up and absent", which is a stable answer worth caching — retrying a
 * failed require on every tap would just repeat the same miss. */
function loadStripe(): StripeSdk | null {
  if (cached !== undefined) return cached;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = require("@stripe/stripe-react-native") as Partial<StripeSdk>;
    cached =
      typeof mod?.initPaymentSheet === "function" && typeof mod?.presentPaymentSheet === "function"
        ? (mod as StripeSdk)
        : null;
  } catch {
    cached = null;
  }
  return cached;
}

/** Whether this binary can present the native sheet at all. The checkout screen
 * asks before requesting sheet-mode from the server, so a build without the SDK
 * never creates a PaymentIntent it cannot collect on. */
export function isPaymentSheetAvailable(): boolean {
  return loadStripe() !== null;
}

/** Test seam. Not used by the app; it exists so the contract tests can prove
 * the availability check and the outcome mapping without a native module. */
export function __setStripeSdkForTests(sdk: StripeSdk | null | undefined): void {
  cached = sdk;
}

function isCancellation(code: string, message: string): boolean {
  const value = `${code} ${message}`.toLowerCase();
  return value.includes("canceled") || value.includes("cancelled");
}

export type PaymentSheetOptions = {
  /**
   * Ask the sheet for a full postal address rather than just the minimum the
   * card network needs.
   *
   * This is set for the shipping lane and nothing else. A hosted Checkout
   * Session could be told to collect a delivery address; the native sheet has
   * no equivalent, so without this a shipping order would settle with no
   * address anywhere — the seller would have a paid order and nowhere to send
   * it. The review screen therefore promises an address step only when this is
   * true, and this is the step it is promising.
   */
  collectAddress?: boolean;
};

/**
 * Initialise and present the sheet. One call, because the two steps are never
 * useful apart: an init without a present leaves the buyer looking at a spinner.
 *
 * Apple Pay is requested only when the server supplied a merchant id, and its
 * absence must never suppress the card form — a buyer without Apple Pay still
 * has to be able to pay with a card.
 */
export async function presentPaymentSheet(
  bootstrap: PaymentSheetBootstrap,
  options: PaymentSheetOptions = {}
): Promise<PaymentSheetOutcome> {
  const stripe = loadStripe();
  if (!stripe) return { result: "unavailable" };
  if (!bootstrap.clientSecret) {
    return { result: "failed", message: "Payment could not be prepared. No card was charged.", code: "missing_client_secret" };
  }

  // The key must be set before the sheet is initialised. With no StripeProvider
  // at app root, this is the only place it happens — and the server, not the
  // client, decides which key (live vs test) is in play, so the sheet can never
  // present against a different account than the PaymentIntent was created on.
  if (bootstrap.publishableKey && typeof stripe.initStripe === "function") {
    await stripe.initStripe({
      publishableKey: bootstrap.publishableKey,
      ...(bootstrap.applePayMerchantId ? { merchantIdentifier: bootstrap.applePayMerchantId } : {})
    });
  }

  const init = await stripe.initPaymentSheet({
    merchantDisplayName: bootstrap.merchantDisplayName || "PulseSoc Marketplace",
    paymentIntentClientSecret: bootstrap.clientSecret,
    // Card entry is always available. Apple Pay is additive.
    ...(bootstrap.applePayMerchantId
      ? { applePay: { merchantCountryCode: "US" }, merchantIdentifier: bootstrap.applePayMerchantId }
      : {}),
    ...(options.collectAddress
      ? { billingDetailsCollectionConfiguration: { name: "always", address: "full" } }
      : {}),
    allowsDelayedPaymentMethods: false,
    returnURL: "pulsesoc://payments/return"
  });
  if (init.error) {
    return {
      result: "failed",
      message: init.error.message || "Payment could not be prepared. No card was charged.",
      code: init.error.code || "init_failed"
    };
  }

  const presented = await stripe.presentPaymentSheet();
  if (presented.error) {
    const code = presented.error.code || "";
    const message = presented.error.message || "";
    // A dismissal is not a failure and must not be reported as one — telling
    // someone their payment failed when they simply changed their mind is both
    // wrong and alarming.
    if (isCancellation(code, message)) return { result: "canceled" };
    return { result: "failed", message: message || "Your payment was not completed. No card was charged.", code: code || "payment_failed" };
  }
  return { result: "completed" };
}
