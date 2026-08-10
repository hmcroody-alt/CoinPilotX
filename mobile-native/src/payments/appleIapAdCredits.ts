/**
 * Apple IAP ad-credit purchase orchestrator (StoreKit 2 via expo-iap).
 *
 * Money-flow contract, in order, no exceptions:
 *
 *   1. Ask the server which provider to use (`routePayment`). The client
 *      NEVER decides — if the answer is not `apple_iap`, no StoreKit call is
 *      made and the caller is told which provider the server chose.
 *   2. The product must exist in the server-truth catalog. The local sku list
 *      is never trusted on its own.
 *   3. `requestPurchase` shows the native Apple payment sheet.
 *   4. The signed transaction JWS goes to the server, which verifies the
 *      certificate chain and credits the Ad Wallet at most once per Apple
 *      transaction (DB-unique idempotency key).
 *   5. `finishTransaction` runs ONLY after the server confirms the credit
 *      (fresh or deduped). Finishing earlier would consume Apple's only proof
 *      of purchase before the money exists in the ledger; on any failure the
 *      transaction stays unfinished and `restoreUnfinishedAdCreditPurchases`
 *      retries it later (server dedupe makes the retry safe).
 *
 * expo-iap is loaded lazily and defensively: on Android/web builds, in tests,
 * or before `npm install` lands the dependency, every entry point degrades to
 * a clean `iap_unavailable` result instead of crashing.
 *
 * All results are machine codes — screens own the translated copy (i18n gate).
 */
import { Platform } from "react-native";
import {
  getIapAdCreditProducts,
  routePayment,
  verifyAppleAdCreditPurchase,
  AppleVerifyOutcome,
  IapAdCreditProduct,
  PaymentRouteDecision
} from "../api/payments";

export const AD_CREDIT_SKU_PREFIX = "com.pulsesoc.adcredits.";

/* ------------------------------------------------------------------ *
 * StoreKit adapter — the seam between this flow and expo-iap
 * ------------------------------------------------------------------ */

/** Raw purchase object from the native module; shape varies by version. */
export type StoreKitPurchase = Record<string, unknown>;

export interface StoreKitAdapter {
  initConnection(): Promise<void>;
  requestPurchase(sku: string): Promise<StoreKitPurchase | null>;
  finishTransaction(purchase: StoreKitPurchase): Promise<void>;
  getAvailablePurchases(): Promise<StoreKitPurchase[]>;
}

let cachedAdapter: StoreKitAdapter | null | undefined;

/**
 * Wrap expo-iap if it is installed; otherwise null. Never throws.
 * expo-iap v2+ is StoreKit 2 only, which is exactly what the server verifies.
 */
export function loadStoreKitAdapter(): StoreKitAdapter | null {
  if (cachedAdapter !== undefined) return cachedAdapter;
  cachedAdapter = null;
  if (Platform.OS !== "ios") return cachedAdapter;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const iap = require("expo-iap");
    if (!iap || typeof iap.requestPurchase !== "function") return cachedAdapter;
    cachedAdapter = {
      initConnection: async () => {
        if (typeof iap.initConnection === "function") await iap.initConnection();
      },
      requestPurchase: async (sku: string) => {
        // expo-iap v4 request shape; `type` distinguishes in-app from subs.
        const result = await iap.requestPurchase({
          request: { ios: { sku }, sku },
          type: "in-app"
        });
        const purchase = Array.isArray(result) ? result[0] : result;
        return purchase && typeof purchase === "object" ? (purchase as StoreKitPurchase) : null;
      },
      finishTransaction: async (purchase: StoreKitPurchase) => {
        await iap.finishTransaction({ purchase, isConsumable: true });
      },
      getAvailablePurchases: async () => {
        if (typeof iap.getAvailablePurchases !== "function") return [];
        const rows = await iap.getAvailablePurchases();
        return Array.isArray(rows) ? (rows as StoreKitPurchase[]) : [];
      }
    };
  } catch {
    cachedAdapter = null; // module not installed — degrade, don't crash
  }
  return cachedAdapter;
}

/** Test hook: clear the module-level adapter cache. */
export function resetStoreKitAdapterCache(): void {
  cachedAdapter = undefined;
}

/* ------------------------------------------------------------------ *
 * Purchase-object field extraction (version tolerant)
 * ------------------------------------------------------------------ */

const JWS_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

/**
 * Pull the StoreKit 2 signed-transaction JWS out of a purchase object.
 * expo-iap has carried it under different names across versions
 * (`jwsRepresentationIos`, `purchaseToken`, `transactionReceipt`); the server
 * only accepts a real three-segment JWS, so shape — not field name — decides.
 */
export function extractSignedTransaction(purchase?: StoreKitPurchase | null): string | null {
  if (!purchase) return null;
  const candidates = [
    purchase.jwsRepresentationIos,
    purchase.jwsRepresentation,
    purchase.purchaseToken,
    purchase.transactionReceipt
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.length > 20 && JWS_SHAPE.test(candidate)) {
      return candidate;
    }
  }
  return null;
}

export function purchaseProductId(purchase?: StoreKitPurchase | null): string {
  return String(purchase?.productId ?? purchase?.id ?? "");
}

function isUserCancellation(error: unknown): boolean {
  const code = String((error as { code?: unknown })?.code ?? "");
  const message = String((error as { message?: unknown })?.message ?? "");
  return /cancel/i.test(code) || /cancel/i.test(message);
}

/* ------------------------------------------------------------------ *
 * Purchase flow
 * ------------------------------------------------------------------ */

export type AdCreditPurchaseResult =
  | { status: "credited"; amountCents: number; productId: string; deduped: boolean }
  /** Server routed this purchase to another provider — obey it (e.g. Stripe on web). */
  | { status: "use_other_provider"; decision: PaymentRouteDecision }
  | { status: "routing_flagged"; decision: PaymentRouteDecision }
  | { status: "unknown_product" }
  | { status: "iap_unavailable" }
  | { status: "cancelled" }
  | { status: "purchase_failed" }
  /** Purchase happened but could not be credited yet — transaction left
   *  unfinished on purpose; restore will retry. */
  | { status: "verification_pending"; reason: AppleVerifyOutcome["status"] | "missing_jws" | "network" };

export type PurchaseDeps = {
  adapter?: StoreKitAdapter | null;
  route?: typeof routePayment;
  catalog?: typeof getIapAdCreditProducts;
  verify?: typeof verifyAppleAdCreditPurchase;
  platform?: string;
};

export async function purchaseAdCredits(
  accountId: number,
  productId: string,
  deps: PurchaseDeps = {}
): Promise<AdCreditPurchaseResult> {
  const route = deps.route ?? routePayment;
  const catalog = deps.catalog ?? getIapAdCreditProducts;
  const verify = deps.verify ?? verifyAppleAdCreditPurchase;
  const platform = deps.platform ?? Platform.OS;

  // 1. Server decides the provider.
  const decision = await route({ platform, itemType: "ad_credits" });
  if (!decision.ok || decision.flagged) return { status: "routing_flagged", decision };
  if (decision.provider !== "apple_iap") return { status: "use_other_provider", decision };

  // 2. Product must exist in the server catalog.
  const products: IapAdCreditProduct[] = await catalog();
  if (!products.some((p) => p.productId === productId)) return { status: "unknown_product" };

  // 3. Native sheet.
  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  if (!adapter) return { status: "iap_unavailable" };
  let purchase: StoreKitPurchase | null;
  try {
    await adapter.initConnection();
    purchase = await adapter.requestPurchase(productId);
  } catch (error) {
    return isUserCancellation(error) ? { status: "cancelled" } : { status: "purchase_failed" };
  }
  if (!purchase) return { status: "purchase_failed" };

  const jws = extractSignedTransaction(purchase);
  if (!jws) return { status: "verification_pending", reason: "missing_jws" };

  // 4. Server verification — the only thing that creates money.
  let outcome: AppleVerifyOutcome;
  try {
    outcome = await verify(accountId, jws);
  } catch {
    return { status: "verification_pending", reason: "network" };
  }

  // 5. Finish ONLY after the credit exists.
  if (outcome.status === "credited" || outcome.status === "already_credited") {
    try {
      await adapter.finishTransaction(purchase);
    } catch {
      // Credit exists; a finish failure is recoverable via restore + dedupe.
    }
    return {
      status: "credited",
      amountCents: outcome.amountCents,
      productId: outcome.productId || productId,
      deduped: outcome.deduped
    };
  }
  return { status: "verification_pending", reason: outcome.status };
}

/* ------------------------------------------------------------------ *
 * Restore — crash / kill / offline recovery
 * ------------------------------------------------------------------ */

export type RestoreResult = {
  checked: number;
  credited: number;
  /** Transactions that stay unfinished for a later retry. */
  pending: number;
};

/**
 * Re-drive unfinished ad-credit transactions through server verification.
 * Server-side idempotency makes this safe to run on every app start: a
 * transaction that already credited comes back `already_credited` and is
 * finished; anything unverifiable stays unfinished.
 */
export async function restoreUnfinishedAdCreditPurchases(
  accountId: number,
  deps: Pick<PurchaseDeps, "adapter" | "verify"> = {}
): Promise<RestoreResult> {
  const verify = deps.verify ?? verifyAppleAdCreditPurchase;
  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  const result: RestoreResult = { checked: 0, credited: 0, pending: 0 };
  if (!adapter) return result;

  let purchases: StoreKitPurchase[];
  try {
    await adapter.initConnection();
    purchases = await adapter.getAvailablePurchases();
  } catch {
    return result;
  }

  for (const purchase of purchases) {
    if (!purchaseProductId(purchase).startsWith(AD_CREDIT_SKU_PREFIX)) continue; // not ours — never touch
    result.checked += 1;
    const jws = extractSignedTransaction(purchase);
    if (!jws) {
      result.pending += 1;
      continue;
    }
    try {
      const outcome = await verify(accountId, jws);
      if (outcome.status === "credited" || outcome.status === "already_credited") {
        await adapter.finishTransaction(purchase);
        result.credited += 1;
      } else {
        result.pending += 1;
      }
    } catch {
      result.pending += 1;
    }
  }
  return result;
}
