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

/**
 * What the StoreKit query asked for, and what came back.
 *
 * This exists because an empty paywall has four different causes that are
 * indistinguishable once the request is over: the server offered no catalog at
 * all, Apple answered with zero products for ids that were sent, the request
 * threw, or it never returned. Only the request itself can tell them apart, so
 * the facts are captured at that moment and carried out with the result.
 *
 * Everything here is a product identifier, a count, an error token or a build
 * marker. No receipt, no signed transaction, no app account token, no Apple ID,
 * no storefront account. That is what makes it safe to write to the device log
 * and safe to show a member as a support reference.
 */
export type PremiumOffersDiagnostics = {
  /** Product ids sent to Apple, in the order the server offered them. */
  requestedProductIds: string[];
  /** The StoreKit query kind. Auto-renewables are never fetched as `inapp`. */
  requestType: "subs";
  /** How many products Apple returned. */
  productCount: number;
  /** Product ids Apple actually returned — the set that matters for mapping. */
  returnedProductIds: string[];
  /** Short error token, or `null` when nothing threw. Never a message body. */
  errorCode: string | null;
  /** Build environment marker, e.g. `ios/release`. Never account identity. */
  environment: string;
};

export type PremiumOffers = {
  plans: PremiumPlanOffer[];
  status: "success" | "empty" | "failed" | "timeout" | "unavailable";
  missingPlans: PremiumPlan[];
  /** Why the list looks the way it does. Present on success and on failure. */
  diagnostics: PremiumOffersDiagnostics;
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

export const PREMIUM_PRODUCT_FETCH_TIMEOUT_MS = 12_000;

/**
 * One tag, one line, greppable in a device log.
 *
 * `xcrun simctl spawn <udid> log stream` and a TestFlight sysdiagnose both carry
 * JS console output in Release builds, so this is the only forensic trace of a
 * product query that survives off a real device.
 */
export const PREMIUM_STOREKIT_LOG_TAG = "PulseSocStoreKit";

/**
 * A long unbroken alphanumeric run — a JWS segment, a receipt, a bearer token.
 *
 * Real StoreKit reasons are short and word-shaped (`E_USER_CANCELLED`,
 * `SKErrorDomain`), so length alone separates an identifier from a secret that
 * an adapter interpolated into its message.
 */
const OPAQUE_RUN = /[A-Za-z0-9+/=]{20,}/g;

/**
 * A short, safe token for an error.
 *
 * StoreKit surfaces its reason on `code`; adapters sometimes only set
 * `message`. Either is stripped of opaque runs, reduced to an identifier shape
 * and truncated — an error body is the one place a receipt or token could ride
 * along into a log line, and this line is written on every failure.
 */
export function storeKitErrorCode(error: unknown): string {
  const source = (error as { code?: unknown })?.code ?? (error as { message?: unknown })?.message ?? "";
  const token = String(source)
    .trim()
    .replace(OPAQUE_RUN, "redacted")
    .replace(/\s+/g, "_")
    .replace(/[^A-Za-z0-9_.:-]/g, "");
  return token ? token.slice(0, 64) : "unknown";
}

/** Build environment, not account environment. Sandbox vs production is Apple's to know. */
function environmentMarker(platform: string): string {
  return `${platform}/${typeof __DEV__ !== "undefined" && __DEV__ ? "dev" : "release"}`;
}

function reportOffers(status: PremiumOffers["status"], diagnostics: PremiumOffersDiagnostics): void {
  try {
    console.log(`${PREMIUM_STOREKIT_LOG_TAG} ${JSON.stringify({ status, ...diagnostics })}`);
  } catch {
    // Diagnostics must never be the reason a paywall fails to render.
  }
}

function timed<T>(operation: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("premium_product_fetch_timeout")), timeoutMs);
    operation.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); }
    );
  });
}

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
    timeoutMs?: number;
  } = {}
): Promise<PremiumOffers> {
  const timeoutMs = deps.timeoutMs ?? PREMIUM_PRODUCT_FETCH_TIMEOUT_MS;
  // Filled in as the request progresses so that a timeout — the one outcome the
  // request itself never gets to describe — can still say what it was asking for.
  const probe: { requested: string[] } = { requested: [] };
  try {
    return await timed(loadPremiumOffers(deps, probe), timeoutMs);
  } catch {
    const diagnostics: PremiumOffersDiagnostics = {
      requestedProductIds: probe.requested,
      requestType: "subs",
      productCount: 0,
      returnedProductIds: [],
      errorCode: "premium_product_fetch_timeout",
      environment: environmentMarker(deps.platform ?? Platform.OS)
    };
    reportOffers("timeout", diagnostics);
    return {
      plans: [], annualSavingsPercent: null, status: "timeout",
      missingPlans: ["monthly", "annual"], diagnostics
    };
  }
}

async function loadPremiumOffers(
  deps: {
    adapter?: StoreKitAdapter | null;
    intent?: typeof createPaymentIntent;
    platform?: string;
    timeoutMs?: number;
  },
  probe: { requested: string[] } = { requested: [] }
): Promise<PremiumOffers> {
  const intent = deps.intent ?? createPaymentIntent;
  const platform = deps.platform ?? Platform.OS;
  const environment = environmentMarker(platform);
  const unavailable = (status: PremiumOffers["status"], errorCode: string | null): PremiumOffers => {
    const diagnostics: PremiumOffersDiagnostics = {
      requestedProductIds: probe.requested,
      requestType: "subs",
      productCount: 0,
      returnedProductIds: [],
      errorCode,
      environment
    };
    reportOffers(status, diagnostics);
    return { plans: [], annualSavingsPercent: null, status, missingPlans: ["monthly", "annual"], diagnostics };
  };

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
  probe.requested = catalog.map((entry) => entry.productId);
  // No ids to ask for: the server withdrew the catalog, so StoreKit is never
  // reached. Reporting this as "Apple returned nothing" would send whoever
  // reads the log to the wrong system entirely.
  if (!catalog.length) return unavailable("unavailable", "no_server_catalog");

  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  if (!adapter?.getSubscriptions) return unavailable("unavailable", "storekit_unavailable");
  let products: StoreKitProduct[];
  try {
    await adapter.initConnection();
    products = await adapter.getSubscriptions(probe.requested);
  } catch (error) {
    return unavailable("failed", storeKitErrorCode(error));
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

  const missingPlans = wanted.filter((wantedPlan) => !plans.some((offer) => offer.plan === wantedPlan));
  const status: PremiumOffers["status"] = plans.length ? "success" : "empty";
  const diagnostics: PremiumOffersDiagnostics = {
    requestedProductIds: probe.requested,
    requestType: "subs",
    productCount: products.length,
    returnedProductIds: products.map((product) => product.id),
    errorCode: null,
    environment
  };
  reportOffers(status, diagnostics);
  return {
    plans,
    annualSavingsPercent: annualSavings(plans),
    status,
    missingPlans,
    diagnostics
  };
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
 * Subscription snapshot — Apple's own facts about an existing subscription
 * ------------------------------------------------------------------ */

/**
 * What Apple can prove about this member's Premium subscription, for display
 * when the server has no billing record of its own (e.g. entitlement projected
 * before the subscription row landed, or a fresh reinstall).
 *
 * Every field is read from StoreKit — the signed transaction payload and the
 * localized product metadata. Nothing is computed from a hardcoded price, date
 * or currency; a field Apple did not supply is `null` and the screen omits it
 * rather than inventing it.
 */
export type AppleSubscriptionSnapshot = {
  productId: string;
  /** Derived from the server-owned sku suffix; `null` when it matches neither plan. */
  plan: PremiumPlan | null;
  /** Apple's formatted price in the member's own storefront currency, or `null`. */
  displayPrice: string | null;
  /** Only what a verified expiry date can prove. Never grace/billing-issue guesses. */
  status: "active" | "expired";
  /** ISO timestamp of the verified expiry (renewal boundary), from the signed transaction. */
  expiresAt: string;
  /** ISO timestamp of the original purchase, or `null` when Apple omitted it. */
  originalPurchaseAt: string | null;
};

/** Base64url alphabet → 6-bit value. Hermes has no reliable global `atob`. */
const B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

/**
 * Decode the payload segment of a StoreKit 2 signed transaction.
 *
 * Display-only. Authorization still belongs to the server, which verifies the
 * certificate chain; this decode merely reads the dates Apple signed so the
 * billing card can show them. A malformed segment returns `null`, never throws.
 */
export function decodeSignedTransactionPayload(jws: string): Record<string, unknown> | null {
  const segment = jws.split(".")[1];
  if (!segment) return null;
  try {
    const bytes: number[] = [];
    let buffer = 0;
    let bits = 0;
    for (const char of segment) {
      const value = B64URL.indexOf(char);
      if (value < 0) return null;
      buffer = (buffer << 6) | value;
      bits += 6;
      if (bits >= 8) {
        bits -= 8;
        bytes.push((buffer >> bits) & 0xff);
      }
    }
    // UTF-8 decode via the percent-encoding trick — no TextDecoder dependency.
    const text = decodeURIComponent(bytes.map((b) => `%${b.toString(16).padStart(2, "0")}`).join(""));
    const payload = JSON.parse(text);
    return payload && typeof payload === "object" ? (payload as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function transactionMillis(value: unknown): number | null {
  const millis = Number(value);
  return Number.isFinite(millis) && millis > 0 ? millis : null;
}

/** `com.pulsesoc.premium.monthly` → `monthly`. Anything unrecognized stays `null`. */
export function planFromProductId(productId: string): PremiumPlan | null {
  if (/\.monthly$/.test(productId)) return "monthly";
  if (/\.(annual|yearly)$/.test(productId)) return "annual";
  return null;
}

/**
 * Read the member's Premium subscription facts straight from StoreKit.
 *
 * Used only as a display fallback when `/api/premium/status-center` returns no
 * `subscription` row for a member who canonically holds Premium — the case that
 * used to render "we don't have billing details" at a paying member. Returns
 * `null` whenever Apple cannot prove a subscription with a verified expiry
 * date: no adapter, no premium transactions, or no signed payload. The caller
 * falls back to the honest "no billing record" copy — this function never
 * fabricates a card.
 */
export async function getAppleSubscriptionSnapshot(
  deps: { adapter?: StoreKitAdapter | null; now?: () => number } = {}
): Promise<AppleSubscriptionSnapshot | null> {
  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  if (!adapter) return null;
  const now = deps.now ?? Date.now;

  let purchases;
  try {
    await adapter.initConnection();
    purchases = await adapter.getAvailablePurchases();
  } catch {
    return null;
  }

  // Latest expiry wins: after an upgrade or crossgrade Apple can report several
  // premium transactions, and the one still governing access is the one that
  // ends last.
  let best: { productId: string; expires: number; original: number | null } | null = null;
  for (const purchase of purchases) {
    const productId = purchaseProductId(purchase);
    if (!productId.startsWith(PREMIUM_SKU_PREFIX)) continue;
    const signed = extractSignedTransaction(purchase);
    const payload = signed ? decodeSignedTransactionPayload(signed) : null;
    const expires =
      transactionMillis(payload?.expiresDate) ??
      transactionMillis((purchase as { expirationDateIos?: unknown }).expirationDateIos);
    if (expires === null) continue; // No verified expiry → nothing honest to display.
    const original =
      transactionMillis(payload?.originalPurchaseDate) ??
      transactionMillis((purchase as { originalTransactionDateIOS?: unknown }).originalTransactionDateIOS);
    if (!best || expires > best.expires) best = { productId, expires, original };
  }
  if (!best) return null;

  // Localized price is a nicety, not a requirement: a StoreKit metadata failure
  // must not take the whole card down with it.
  let displayPrice: string | null = null;
  try {
    const products = adapter.getSubscriptions ? await adapter.getSubscriptions([best.productId]) : [];
    displayPrice = products.find((product) => product.id === best!.productId)?.displayPrice || null;
  } catch {
    displayPrice = null;
  }

  return {
    productId: best.productId,
    plan: planFromProductId(best.productId),
    displayPrice,
    status: best.expires > now() ? "active" : "expired",
    expiresAt: new Date(best.expires).toISOString(),
    originalPurchaseAt: best.original !== null ? new Date(best.original).toISOString() : null
  };
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
