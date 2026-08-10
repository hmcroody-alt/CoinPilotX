/**
 * Unified payments data layer.
 *
 * The server decides the payment provider — the client reports facts
 * (platform, item type) and obeys the decision. This mirrors the backend
 * policy module `services/pulse_payment_router.py`:
 *
 *   • `POST /api/pulse/payments/route` → provider decision. A 422 is not a
 *     transport failure — it is the server refusing to classify an item type
 *     (mission rule: never guess between IAP and Stripe). That refusal is
 *     surfaced as a `flagged` decision, not thrown.
 *   • `GET  /api/pulse/ads/iap/products` → server-truth ad-credit catalog
 *     (product ids + the credit each grants). Display only; the credited
 *     amount on verification always comes from the server-side table.
 *   • `POST /api/pulse/ads/accounts/<id>/wallet/apple-iap/verify` → submits a
 *     StoreKit 2 signed transaction JWS. The server verifies the signature
 *     chain and credits the wallet at most once per Apple transaction.
 *
 * Verification outcomes are returned as codes, never thrown, because the
 * caller must decide whether to `finishTransaction`: finishing before the
 * server has durably credited would destroy the only proof of purchase.
 */
import { pulseApi, PulseApiError } from "./pulseApi";

const intCents = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

/* ------------------------------------------------------------------ *
 * Provider routing
 * ------------------------------------------------------------------ */

export type PaymentProvider = "apple_iap" | "stripe" | "stripe_connect" | "internal_ledger";

export type PaymentRouteDecision = {
  ok: boolean;
  provider: PaymentProvider | null;
  classification: string;
  policyBasis: string;
  /** True when the server refused to classify — surface this, never guess. */
  flagged: boolean;
};

export function normalizePaymentRouteDecision(value?: Record<string, unknown> | null): PaymentRouteDecision {
  const provider = String(value?.provider || "");
  const known: PaymentProvider[] = ["apple_iap", "stripe", "stripe_connect", "internal_ledger"];
  return {
    ok: value?.ok === true,
    provider: (known as string[]).includes(provider) ? (provider as PaymentProvider) : null,
    classification: String(value?.classification || ""),
    policyBasis: String(value?.policy_basis || ""),
    flagged: value?.flagged === true
  };
}

export async function routePayment(params: { platform: string; itemType: string }): Promise<PaymentRouteDecision> {
  try {
    const resp = await pulseApi<Record<string, unknown>>("/api/pulse/payments/route", {
      method: "POST",
      body: JSON.stringify({ platform: params.platform, item_type: params.itemType })
    });
    return normalizePaymentRouteDecision(resp);
  } catch (error) {
    // 422 carries the refusal decision in the body — that is an answer, not an error.
    if (error instanceof PulseApiError && error.status === 422) {
      return normalizePaymentRouteDecision({ ...(error.details || {}), ok: false, flagged: true });
    }
    throw error;
  }
}

/* ------------------------------------------------------------------ *
 * Ad-credit IAP catalog
 * ------------------------------------------------------------------ */

export type IapAdCreditProduct = {
  productId: string;
  amountCents: number;
  currency: string;
  creditDisplay: string;
};

export function normalizeIapAdCreditProduct(value?: Record<string, unknown> | null): IapAdCreditProduct {
  return {
    productId: String(value?.product_id || ""),
    amountCents: intCents(value?.amount_cents),
    currency: String(value?.currency || "usd").toLowerCase(),
    creditDisplay: String(value?.credit_display || "")
  };
}

export async function getIapAdCreditProducts(): Promise<IapAdCreditProduct[]> {
  const resp = await pulseApi<{ ok?: boolean; products?: Record<string, unknown>[] }>(
    "/api/pulse/ads/iap/products"
  );
  const rows = Array.isArray(resp?.products) ? resp.products : [];
  return rows.map(normalizeIapAdCreditProduct).filter((p) => p.productId && p.amountCents > 0);
}

/* ------------------------------------------------------------------ *
 * Apple purchase verification
 * ------------------------------------------------------------------ */

/**
 * Machine codes only — screens map these to translated copy. The code also
 * encodes the finish-transaction contract:
 *   credited / already_credited  → the wallet credit exists durably; it is
 *                                  safe (and required) to finishTransaction.
 *   anything else                → do NOT finish; keep the transaction
 *                                  unfinished so restore can retry later.
 */
export type AppleVerifyOutcome =
  | { status: "credited"; amountCents: number; productId: string; deduped: false }
  | { status: "already_credited"; amountCents: number; productId: string; deduped: true }
  | { status: "rejected" } // signature or policy failure — flat, no crypto details
  | { status: "setup_required" }
  | { status: "rate_limited" };

export async function verifyAppleAdCreditPurchase(
  accountId: number,
  signedTransaction: string
): Promise<AppleVerifyOutcome> {
  try {
    const resp = await pulseApi<Record<string, unknown>>(
      `/api/pulse/ads/accounts/${accountId}/wallet/apple-iap/verify`,
      { method: "POST", body: JSON.stringify({ signed_transaction: signedTransaction }) }
    );
    if (resp?.ok === true) {
      const amountCents = intCents(resp.amount_cents);
      const productId = String(resp.product_id || "");
      return resp.deduped === true
        ? { status: "already_credited", amountCents, productId, deduped: true }
        : { status: "credited", amountCents, productId, deduped: false };
    }
    return { status: "rejected" };
  } catch (error) {
    if (error instanceof PulseApiError) {
      if (error.status === 503) return { status: "setup_required" };
      if (error.status === 429) return { status: "rate_limited" };
      // 400 invalid signature/policy, 403 write check, 404 not the account
      // owner, 422 malformed — none of these may finish the transaction.
      if ([400, 403, 404, 422].includes(error.status)) return { status: "rejected" };
    }
    throw error; // network / auth — genuinely unknown, caller must not finish
  }
}
