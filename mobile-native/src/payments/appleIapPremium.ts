/**
 * Apple IAP premium subscription flow (StoreKit 2 via expo-iap).
 *
 * Contract, in order, no exceptions:
 *
 *   1. Ask the server for a payment intent. The client never picks the product
 *      id — `plan: "monthly" | "annual"` goes up, an Apple product id comes back.
 *      That is what keeps the App Store catalog server-owned.
 *   2. Show Apple's sheet.
 *   3. Send the signed transaction JWS to the server, which verifies the
 *      certificate chain and projects the entitlement.
 *   4. Only then finish the transaction.
 *
 * A client-side "success" grants nothing. `purchasePremium` returning `verified`
 * means the *server* said verified; the screen still re-reads the Status Center
 * afterwards, so what the member sees is always the server's answer and never
 * this function's optimism.
 *
 * All results are machine codes — screens own the translated copy (i18n gate).
 */
import { Platform } from "react-native";
import { Linking } from "react-native";
import { createPaymentIntent, verifyApplePremiumPurchase } from "../api/payments";
import {
  extractSignedTransaction,
  loadStoreKitAdapter,
  purchaseProductId,
  StoreKitAdapter,
  StoreKitProduct
} from "./appleIapAdCredits";

export type PremiumPlan = "monthly" | "annual";
export type PremiumPurchaseResult =
  | { status: "verified"; productId: string }
  | { status: "cancelled" | "unavailable" | "failed" | "verification_pending" };

/**
 * Apple's own subscription management screen.
 *
 * The app deliberately has no "Cancel subscription" button. Cancelling is
 * Apple's to perform: a local button could only ever mutate our copy of the
 * state, leaving the member still billed while our screen claimed otherwise —
 * the worst possible failure on a paid surface. Deep-linking here is also what
 * App Review expects.
 */
export const APPLE_MANAGE_SUBSCRIPTIONS_URL = "https://apps.apple.com/account/subscriptions";

export async function openManageSubscriptions(
  deps: { open?: (url: string) => Promise<unknown> } = {}
): Promise<boolean> {
  const open = deps.open ?? ((url: string) => Linking.openURL(url));
  try {
    await open(APPLE_MANAGE_SUBSCRIPTIONS_URL);
    return true;
  } catch {
    return false;
  }
}

function cancelled(error: unknown) {
  return /cancel/i.test(String((error as { code?: unknown; message?: unknown })?.code || "")) ||
    /cancel/i.test(String((error as { message?: unknown })?.message || ""));
}

export async function purchasePremium(
  plan: PremiumPlan,
  deps: { adapter?: StoreKitAdapter | null } = {}
): Promise<PremiumPurchaseResult> {
  const instruction = await createPaymentIntent({
    platform: Platform.OS,
    purchaseContext: "premium",
    plan
  });
  if (!instruction.ok || instruction.provider !== "apple_iap" ||
      instruction.flow !== "storekit" || !instruction.appleProductId) {
    return { status: "unavailable" };
  }
  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  if (!adapter) return { status: "unavailable" };
  try {
    await adapter.initConnection();
    const purchase = await adapter.requestPurchase(
      instruction.appleProductId, "subs", instruction.appAccountToken
    );
    if (!purchase) return { status: "failed" };
    const signed = extractSignedTransaction(purchase);
    if (!signed) return { status: "verification_pending" };
    const verified = await verifyApplePremiumPurchase(signed);
    if (!verified.ok || !verified.verified) return { status: "verification_pending" };
    await adapter.finishTransaction(purchase, false);
    return { status: "verified", productId: instruction.appleProductId };
  } catch (error) {
    return { status: cancelled(error) ? "cancelled" : "verification_pending" };
  }
}

/* ------------------------------------------------------------------ *
 * Plan catalog — localized prices
 * ------------------------------------------------------------------ */

export type PremiumPlanOffer = {
  plan: PremiumPlan;
  productId: string;
  /** Apple's formatted price string, in the member's own currency. */
  displayPrice: string;
  price: number;
  currency: string;
};

export type PremiumOffers = {
  plans: PremiumPlanOffer[];
  /**
   * Whole-percent saving of annual over twelve months of monthly, or `null`.
   *
   * Computed from the two localized prices actually returned, never hardcoded.
   * A member on a store where the annual plan is not discounted the same way
   * must not be shown a "save 17%" badge that their own numbers contradict —
   * and if either price is missing there is no honest figure to show, so `null`
   * suppresses the badge entirely.
   */
  annualSavingsPercent: number | null;
};

/**
 * Ask the server which products exist, then ask Apple what they cost here.
 *
 * Two hops on purpose. The server owns the catalog (so a plan can be withdrawn
 * without an app release) and Apple owns the price (so it is correct in every
 * storefront and currency). Neither fact is hardcoded in the app.
 *
 * Returns an empty plan list on any failure. The caller renders "temporarily
 * unavailable" — it must never fall back to a remembered price.
 */
export async function getPremiumOffers(
  deps: {
    adapter?: StoreKitAdapter | null;
    intent?: typeof createPaymentIntent;
    platform?: string;
  } = {}
): Promise<PremiumOffers> {
  const intent = deps.intent ?? createPaymentIntent;
  const platform = deps.platform ?? Platform.OS;
  const empty: PremiumOffers = { plans: [], annualSavingsPercent: null };

  const wanted: PremiumPlan[] = ["monthly", "annual"];
  const catalog: Array<{ plan: PremiumPlan; productId: string }> = [];
  for (const plan of wanted) {
    try {
      const instruction = await intent({ platform, purchaseContext: "premium", plan });
      if (instruction.ok && instruction.provider === "apple_iap" &&
          instruction.flow === "storekit" && instruction.appleProductId) {
        catalog.push({ plan, productId: instruction.appleProductId });
      }
    } catch {
      // One unavailable plan must not hide the other.
    }
  }
  if (!catalog.length) return empty;

  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  if (!adapter?.getSubscriptions) return empty;
  let products: StoreKitProduct[];
  try {
    await adapter.initConnection();
    products = await adapter.getSubscriptions(catalog.map((entry) => entry.productId));
  } catch {
    return empty;
  }

  const plans = catalog
    .map((entry) => {
      const product = products.find((candidate) => candidate.id === entry.productId);
      if (!product) return null;
      return {
        plan: entry.plan,
        productId: entry.productId,
        displayPrice: product.displayPrice,
        price: Number(product.price) || 0,
        currency: product.currency
      } satisfies PremiumPlanOffer;
    })
    .filter((offer): offer is PremiumPlanOffer => offer !== null);

  return { plans, annualSavingsPercent: annualSavings(plans) };
}

/**
 * Percent saved by paying annually, or `null` when it cannot be stated honestly.
 *
 * `null` when either plan is missing, when a price is zero or non-numeric, when
 * the two are priced in different currencies (which makes the comparison
 * meaningless), or when annual is not actually cheaper. Showing "save 0%" or a
 * negative saving would be worse than showing nothing.
 */
export function annualSavings(plans: PremiumPlanOffer[]): number | null {
  const monthly = plans.find((offer) => offer.plan === "monthly");
  const annual = plans.find((offer) => offer.plan === "annual");
  if (!monthly || !annual) return null;
  if (!(monthly.price > 0) || !(annual.price > 0)) return null;
  if (monthly.currency && annual.currency && monthly.currency !== annual.currency) return null;
  const twelveMonths = monthly.price * 12;
  const saved = twelveMonths - annual.price;
  if (saved <= 0) return null;
  return Math.round((saved / twelveMonths) * 100);
}

/* ------------------------------------------------------------------ *
 * Restore
 * ------------------------------------------------------------------ */

export const PREMIUM_SKU_PREFIX = "com.pulsesoc.premium.";

export type PremiumRestoreResult =
  | { status: "restored"; count: number }
  /** Apple had nothing to restore for this Apple ID. Not an error. */
  | { status: "empty" }
  | { status: "unavailable" }
  | { status: "failed" };

/**
 * Re-drive Apple's existing entitlements through server verification.
 *
 * Idempotent by construction, and not because this loop is careful: every
 * transaction lands on `upsert_provider_subscription`, whose UNIQUE constraint
 * on `provider_subscription_id` means a replay updates one row instead of
 * creating a second subscription. Restoring ten times grants exactly what
 * restoring once grants, which is what makes it safe to offer as a button a
 * confused member may press repeatedly.
 *
 * Only `com.pulsesoc.premium.*` transactions are touched. Ad-credit purchases
 * belong to `restoreUnfinishedAdCreditPurchases` and must not be finished here.
 */
export async function restorePremiumPurchases(
  deps: {
    adapter?: StoreKitAdapter | null;
    verify?: typeof verifyApplePremiumPurchase;
  } = {}
): Promise<PremiumRestoreResult> {
  const verify = deps.verify ?? verifyApplePremiumPurchase;
  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  if (!adapter) return { status: "unavailable" };

  let purchases;
  try {
    await adapter.initConnection();
    purchases = await adapter.getAvailablePurchases();
  } catch {
    return { status: "failed" };
  }

  const mine = purchases.filter((purchase) => purchaseProductId(purchase).startsWith(PREMIUM_SKU_PREFIX));
  if (!mine.length) return { status: "empty" };

  let restored = 0;
  let failures = 0;
  for (const purchase of mine) {
    const signed = extractSignedTransaction(purchase);
    if (!signed) {
      failures += 1;
      continue;
    }
    try {
      const outcome = await verify(signed);
      if (outcome.ok && outcome.verified) {
        restored += 1;
        try {
          await adapter.finishTransaction(purchase, false);
        } catch {
          // Entitlement exists server-side; a finish failure is recoverable.
        }
      } else {
        failures += 1;
      }
    } catch {
      failures += 1;
    }
  }
  if (restored > 0) return { status: "restored", count: restored };
  // Apple had transactions but none survived verification. That is a real
  // failure and must not be reported as "nothing to restore", which would send
  // the member to buy something they may already own.
  return failures > 0 ? { status: "failed" } : { status: "empty" };
}
