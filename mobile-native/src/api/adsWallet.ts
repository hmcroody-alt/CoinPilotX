/**
 * Ads wallet & billing data layer.
 *
 * Binds the flat wallet family plus the funding session:
 *
 *   • `GET  /api/pulse/ads/wallet/transactions` → `pulse_ad_payments.list_transactions`
 *     (params `account_id`, `limit` ≤ 200, `before_id`; pages via `next_before_id`,
 *     null when the page came back short).
 *   • `GET  /api/pulse/ads/wallet/invoices` → `pulse_ad_payments.list_invoices`.
 *   • `POST /api/pulse/ads/wallet/limits` → `set_spending_limits`
 *     (`account_id` in the body; null/"" clears a limit to 0).
 *   • `POST /api/pulse/ads/wallet/auto-topup` → `set_auto_topup`. The backend
 *     pins `auto_charge: false` — this setting only prompts, it never charges a
 *     card, and the screen copy must say so.
 *   • `POST /api/pulse/ads/accounts/<id>/wallet/funding-session` → Stripe
 *     checkout. On iOS-native requests the server answers 403 with a sentence
 *     explaining funding lives on the web portal; that message is surfaced
 *     verbatim, not treated as a generic failure.
 *
 * The wallet summary itself (`GET /accounts/<id>/wallet`) already has a binding
 * in `businessOs.ts` (`getAdWallet`); this module adds only what wave 2 needs.
 */
import { pulseApi } from "./pulseApi";

const nonNegInt = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

/* ------------------------------------------------------------------ *
 * Transactions
 * ------------------------------------------------------------------ */

export type AdWalletTxn = {
  id: number;
  campaign_id: number | null;
  creative_id: number | null;
  transaction_type: string;
  /** Signed integer cents — a spend row is negative in intent even when the
   *  server stores magnitude; the sign is passed through untouched. */
  amount_cents: number;
  currency: string;
  status: string;
  description: string;
  created_at: string;
};

export function normalizeAdWalletTxn(value?: Partial<AdWalletTxn> | null): AdWalletTxn {
  const campaign = Number(value?.campaign_id);
  const creative = Number(value?.creative_id);
  return {
    id: nonNegInt(value?.id),
    campaign_id: Number.isFinite(campaign) && campaign > 0 ? Math.round(campaign) : null,
    creative_id: Number.isFinite(creative) && creative > 0 ? Math.round(creative) : null,
    transaction_type: String(value?.transaction_type || ""),
    amount_cents: Math.round(Number(value?.amount_cents) || 0),
    currency: String(value?.currency || "USD").toUpperCase(),
    status: String(value?.status || ""),
    description: String(value?.description || ""),
    created_at: String(value?.created_at || "")
  };
}

export type AdWalletTxnPage = {
  transactions: AdWalletTxn[];
  /** Pass back as `before_id` for the next page; null when this was the last. */
  next_before_id: number | null;
};

export function normalizeAdWalletTxnPage(value?: {
  transactions?: Partial<AdWalletTxn>[];
  next_before_id?: number | null;
} | null): AdWalletTxnPage {
  const next = Number(value?.next_before_id);
  return {
    transactions: (Array.isArray(value?.transactions) ? value!.transactions : [])
      .map(normalizeAdWalletTxn)
      .filter((txn) => txn.id > 0),
    next_before_id: Number.isFinite(next) && next > 0 ? Math.round(next) : null
  };
}

export async function listAdWalletTransactions(
  accountId: number,
  options: { limit?: number; beforeId?: number } = {}
): Promise<AdWalletTxnPage> {
  const params = new URLSearchParams();
  params.set("account_id", String(accountId));
  params.set("limit", String(options.limit || 50));
  if (options.beforeId) params.set("before_id", String(options.beforeId));
  const data = await pulseApi<Parameters<typeof normalizeAdWalletTxnPage>[0]>(
    `/api/pulse/ads/wallet/transactions?${params.toString()}`
  );
  return normalizeAdWalletTxnPage(data);
}

/* ------------------------------------------------------------------ *
 * Invoices
 * ------------------------------------------------------------------ */

export type AdWalletInvoice = {
  id: number;
  invoice_number: string;
  amount_cents: number;
  currency: string;
  status: string;
  period_start: string;
  period_end: string;
  created_at: string;
};

export function normalizeAdWalletInvoice(value?: Partial<AdWalletInvoice> | null): AdWalletInvoice {
  return {
    id: nonNegInt(value?.id),
    invoice_number: String(value?.invoice_number || ""),
    amount_cents: nonNegInt(value?.amount_cents),
    currency: String(value?.currency || "USD").toUpperCase(),
    status: String(value?.status || ""),
    period_start: String(value?.period_start || ""),
    period_end: String(value?.period_end || ""),
    created_at: String(value?.created_at || "")
  };
}

export type AdWalletInvoicePage = {
  invoices: AdWalletInvoice[];
  next_before_id: number | null;
};

export async function listAdWalletInvoices(
  accountId: number,
  options: { limit?: number; beforeId?: number } = {}
): Promise<AdWalletInvoicePage> {
  const params = new URLSearchParams();
  params.set("account_id", String(accountId));
  params.set("limit", String(options.limit || 30));
  if (options.beforeId) params.set("before_id", String(options.beforeId));
  const data = await pulseApi<{
    invoices?: Partial<AdWalletInvoice>[];
    next_before_id?: number | null;
  }>(`/api/pulse/ads/wallet/invoices?${params.toString()}`);
  const next = Number(data?.next_before_id);
  return {
    invoices: (Array.isArray(data?.invoices) ? data.invoices : [])
      .map(normalizeAdWalletInvoice)
      .filter((invoice) => invoice.id > 0),
    next_before_id: Number.isFinite(next) && next > 0 ? Math.round(next) : null
  };
}

/* ------------------------------------------------------------------ *
 * Spending limits
 * ------------------------------------------------------------------ */

export type AdSpendingLimits = {
  /** 0 means "no limit set" — the backend clears with 0, it has no null state. */
  daily_limit_cents: number;
  lifetime_limit_cents: number;
};

export function normalizeAdSpendingLimits(value?: Partial<AdSpendingLimits> | null): AdSpendingLimits {
  return {
    daily_limit_cents: nonNegInt(value?.daily_limit_cents),
    lifetime_limit_cents: nonNegInt(value?.lifetime_limit_cents)
  };
}

/**
 * Server-validated: negatives, the supported maximum, and daily > lifetime all
 * come back as sentences via `PulseApiError.message` — show them verbatim.
 */
export async function setAdSpendingLimits(
  accountId: number,
  limits: { daily_limit_cents: number | null; lifetime_limit_cents: number | null }
): Promise<AdSpendingLimits> {
  const data = await pulseApi<Partial<AdSpendingLimits>>("/api/pulse/ads/wallet/limits", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, ...limits })
  });
  return normalizeAdSpendingLimits(data);
}

/* ------------------------------------------------------------------ *
 * Auto top-up — a prompt, never a charge
 * ------------------------------------------------------------------ */

export type AdAutoTopup = {
  enabled: boolean;
  threshold_cents: number;
  amount_cents: number;
  /** Pinned false by the backend. If a payload ever claims true, it is ignored:
   *  the product promise is that no card is charged automatically. */
  auto_charge: false;
  note: string;
};

export function normalizeAdAutoTopup(value?: Partial<AdAutoTopup> | null): AdAutoTopup {
  return {
    enabled: value?.enabled === true,
    threshold_cents: nonNegInt(value?.threshold_cents),
    amount_cents: nonNegInt(value?.amount_cents),
    auto_charge: false,
    note: String(value?.note || "")
  };
}

export async function setAdAutoTopup(
  accountId: number,
  settings: { enabled: boolean; threshold_cents?: number; amount_cents?: number }
): Promise<AdAutoTopup> {
  const data = await pulseApi<Partial<AdAutoTopup>>("/api/pulse/ads/wallet/auto-topup", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, ...settings })
  });
  return normalizeAdAutoTopup(data);
}

/* ------------------------------------------------------------------ *
 * Funding session
 * ------------------------------------------------------------------ */

export type AdFundingSession = {
  id: number;
  amount_cents: number;
  currency: string;
  status: string;
  /** The Stripe checkout URL. Opened in the browser; no in-app payment path. */
  checkout_url: string;
};

export function normalizeAdFundingSession(value?: Partial<AdFundingSession> | null): AdFundingSession {
  return {
    id: nonNegInt(value?.id),
    amount_cents: nonNegInt(value?.amount_cents),
    currency: String(value?.currency || "USD").toUpperCase(),
    status: String(value?.status || ""),
    checkout_url: String(value?.checkout_url || "")
  };
}

export async function createAdFundingSession(
  accountId: number,
  amountCents: number
): Promise<AdFundingSession> {
  const data = await pulseApi<{ funding_session?: Partial<AdFundingSession> }>(
    `/api/pulse/ads/accounts/${encodeURIComponent(String(accountId))}/wallet/funding-session`,
    { method: "POST", body: JSON.stringify({ amount_cents: amountCents }) }
  );
  return normalizeAdFundingSession(data.funding_session);
}
