/**
 * Seller payout initiation & history — the Stripe-first payout rail.
 *
 * Binds three live endpoints (services side: seller payout service):
 *
 *   • `GET  /api/pulse/payments/seller/payouts` — payout history plus the
 *     server-computed balance block. Pages via `before_id` / `next_before_id`.
 *   • `POST /api/pulse/payments/seller/payouts` — initiates a payout. The
 *     `payout_key` is the idempotency key: minted when the confirm step opens,
 *     reused for every retry of that confirmation, discarded when it closes,
 *     exactly like the cart checkout intent key. A duplicate submission replays
 *     the same payout (`duplicate: true`) instead of moving money twice.
 *   • `GET  /api/pulse/payments/seller/connect/status` — the Stripe Connect
 *     account state the withdraw affordance gates on. `payouts_enabled` is the
 *     server's word, never inferred client-side.
 *
 * Same house rule as `paymentsHub`: no balance is computed here. The only
 * arithmetic is display formatting, done elsewhere.
 */
import { pulseApi, PulseApiError } from "./pulseApi";

const nonNegInt = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

/* ------------------------------------------------------------------ *
 * Payout records
 * ------------------------------------------------------------------ */

/** The seven states the server writes. Anything else renders as its raw word. */
export const SELLER_PAYOUT_STATUSES = [
  "pending",
  "payout_created",
  "in_transit",
  "paid",
  "failed",
  "canceled",
  "returned"
] as const;

export type SellerPayoutStatus = (typeof SELLER_PAYOUT_STATUSES)[number];

export type SellerPayout = {
  id: number;
  payout_key: string;
  amount_cents: number;
  currency: string;
  /** One of `SELLER_PAYOUT_STATUSES`, or the server's raw word if unknown. */
  status: string;
  stripe_payout_id: string;
  failure_code: string;
  failure_message: string;
  created_at: string;
  updated_at: string;
};

export function normalizeSellerPayout(value?: Partial<SellerPayout> | null): SellerPayout {
  return {
    id: nonNegInt(value?.id),
    payout_key: String(value?.payout_key || ""),
    amount_cents: nonNegInt(value?.amount_cents),
    currency: String(value?.currency || "USD").toUpperCase(),
    status: String(value?.status || ""),
    stripe_payout_id: String(value?.stripe_payout_id || ""),
    failure_code: String(value?.failure_code || ""),
    failure_message: String(value?.failure_message || ""),
    created_at: String(value?.created_at || ""),
    updated_at: String(value?.updated_at || "")
  };
}

/**
 * The server's own balance block, passed through whole. It is normalized, not
 * recomputed, and the screen's hero keeps reading the money overview — this
 * block exists so the payout surface can echo what the payout endpoint saw.
 */
export type SellerPayoutBalance = {
  available_cents: number;
  payout_pending_cents: number;
  processing_cents: number;
  processing_source: string;
  computed_at: string;
};

export function normalizeSellerPayoutBalance(
  value?: Partial<SellerPayoutBalance> | null
): SellerPayoutBalance | null {
  if (!value || typeof value !== "object") return null;
  return {
    available_cents: nonNegInt(value.available_cents),
    payout_pending_cents: nonNegInt(value.payout_pending_cents),
    processing_cents: nonNegInt(value.processing_cents),
    processing_source: String(value.processing_source || ""),
    computed_at: String(value.computed_at || "")
  };
}

export type SellerPayoutPage = {
  payouts: SellerPayout[];
  /** Pass back as `before_id` for the next page; null when this was the last. */
  next_before_id: number | null;
  has_more: boolean;
  balance: SellerPayoutBalance | null;
};

export function normalizeSellerPayoutPage(value?: {
  payouts?: Partial<SellerPayout>[];
  next_before_id?: number | null;
  has_more?: boolean;
  balance?: Partial<SellerPayoutBalance> | null;
} | null): SellerPayoutPage {
  const next = Number(value?.next_before_id);
  return {
    payouts: (Array.isArray(value?.payouts) ? value!.payouts : [])
      .map(normalizeSellerPayout)
      .filter((payout) => payout.id > 0),
    next_before_id: Number.isFinite(next) && next > 0 ? Math.round(next) : null,
    has_more: value?.has_more === true,
    balance: normalizeSellerPayoutBalance(value?.balance)
  };
}

export async function fetchSellerPayouts(
  options: { limit?: number; beforeId?: number } = {}
): Promise<SellerPayoutPage> {
  const params = new URLSearchParams();
  params.set("limit", String(Math.max(1, Math.min(100, options.limit || 20))));
  if (options.beforeId) params.set("before_id", String(options.beforeId));
  const data = await pulseApi<Parameters<typeof normalizeSellerPayoutPage>[0]>(
    `/api/pulse/payments/seller/payouts?${params.toString()}`
  );
  return normalizeSellerPayoutPage(data);
}

/* ------------------------------------------------------------------ *
 * Payout initiation
 * ------------------------------------------------------------------ */

export type SellerPayoutRequestResult = {
  payout: SellerPayout;
  /** True when the payout_key had already been processed — no second payout. */
  duplicate: boolean;
  stripe: {
    submitted: boolean;
    /** Populated when submitted. */
    stripe_payout_id: string;
    /** Populated when not submitted — the server's reason, verbatim. */
    reason: string;
  };
};

export async function requestSellerPayout(
  amountCents: number,
  payoutKey: string
): Promise<SellerPayoutRequestResult> {
  const data = await pulseApi<{
    payout?: Partial<SellerPayout>;
    duplicate?: boolean;
    stripe?: { submitted?: boolean; stripe_payout_id?: string; reason?: string };
  }>("/api/pulse/payments/seller/payouts", {
    method: "POST",
    body: JSON.stringify({ amount_cents: nonNegInt(amountCents), payout_key: payoutKey })
  });
  return {
    payout: normalizeSellerPayout(data?.payout),
    duplicate: data?.duplicate === true,
    stripe: {
      submitted: data?.stripe?.submitted === true,
      stripe_payout_id: String(data?.stripe?.stripe_payout_id || ""),
      reason: String(data?.stripe?.reason || "")
    }
  };
}

/**
 * The intent key for one confirmation, in the cart checkout shape: minted when
 * the confirm step opens, reused across retries of it, discarded on close.
 */
export function mintPayoutKey(): string {
  return `payout-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Maps the endpoint's declared error codes onto i18n key suffixes under
 * `commerce:payments`. Unknown codes fall through to the generic sentence —
 * the server's own message (`PulseApiError.message`) is still preferred by the
 * screen when it is a full sentence.
 */
export type PayoutErrorKey =
  | "errInsufficient"
  | "errPayoutsDisabled"
  | "errNoAccount"
  | "errGeneric";

export function payoutErrorKey(error: unknown): PayoutErrorKey {
  const code = error instanceof PulseApiError ? error.code : undefined;
  switch (code) {
    case "insufficient_balance":
      return "errInsufficient";
    case "payouts_disabled":
      return "errPayoutsDisabled";
    case "no_connected_account":
      return "errNoAccount";
    default:
      return "errGeneric";
  }
}

/* ------------------------------------------------------------------ *
 * Stripe Connect status
 * ------------------------------------------------------------------ */

export type ConnectAccountState = {
  connected_account_id: string;
  charges_enabled: boolean;
  details_submitted: boolean;
  disabled_reason: string;
  last_synced_at: string;
};

export type ConnectStatus = {
  connected: boolean;
  payouts_enabled: boolean;
  state: ConnectAccountState | null;
};

export function normalizeConnectStatus(value?: {
  connected?: boolean;
  payouts_enabled?: boolean;
  state?: Partial<ConnectAccountState> | null;
} | null): ConnectStatus {
  const state = value?.state;
  return {
    connected: value?.connected === true,
    payouts_enabled: value?.payouts_enabled === true,
    state:
      state && typeof state === "object"
        ? {
            connected_account_id: String(state.connected_account_id || ""),
            charges_enabled: state.charges_enabled === true,
            details_submitted: state.details_submitted === true,
            disabled_reason: String(state.disabled_reason || ""),
            last_synced_at: String(state.last_synced_at || "")
          }
        : null
  };
}

export async function fetchConnectStatus(): Promise<ConnectStatus> {
  const data = await pulseApi<Parameters<typeof normalizeConnectStatus>[0]>(
    "/api/pulse/payments/seller/connect/status"
  );
  return normalizeConnectStatus(data);
}

/**
 * A masked reference for the connected account — the last four characters of
 * the Stripe account id, which is a reference, never a bank number. The screen
 * prefers the server's own `destination_masked` when the money overview carries
 * one; this exists for when only the connect status has loaded.
 */
export function maskedConnectRef(status: ConnectStatus | null | undefined): string {
  const id = status?.state?.connected_account_id || "";
  if (id.length < 4) return "";
  return `····${id.slice(-4)}`;
}

/* ------------------------------------------------------------------ *
 * Status chips — label key + tone, decided in one place.
 * ------------------------------------------------------------------ */

export type PayoutStatusTone = "progress" | "success" | "error" | "neutral";

/**
 * The i18n suffix (under `commerce:payments`) and colour tone for a payout
 * status chip. An unknown status gets no key — the screen renders the raw
 * status word rather than guessing a label — and the neutral tone, because a
 * colour would assert a direction nobody decided.
 */
export function payoutStatusChip(status: string): { key: string | null; tone: PayoutStatusTone } {
  switch (status) {
    case "pending":
      return { key: "statusPending", tone: "progress" };
    case "payout_created":
      return { key: "statusCreated", tone: "progress" };
    case "in_transit":
      return { key: "statusInTransit", tone: "progress" };
    case "paid":
      return { key: "statusPaid", tone: "success" };
    case "failed":
      return { key: "statusFailed", tone: "error" };
    case "canceled":
      return { key: "statusCanceled", tone: "neutral" };
    case "returned":
      return { key: "statusReturned", tone: "error" };
    default:
      return { key: null, tone: "neutral" };
  }
}
