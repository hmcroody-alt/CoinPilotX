/**
 * Rewards — Pulse Credits and cash rewards, on the Stripe-first rail.
 *
 * Binds four live endpoints:
 *
 *   • `GET  /api/pulse/rewards` — the reward list plus `credit_balance`, the
 *     server's finished total. Pages via `before_id` / `next_before_id`.
 *   • `GET  /api/pulse/rewards/credits/ledger` — the credit ledger. Every
 *     `balance_after` is server-written; nothing here sums deltas.
 *   • `POST /api/pulse/rewards/credits/redeem` — burns credits into
 *     promotional ad credits on an ad account. `redemption_key` is the
 *     idempotency key, minted when the confirm step opens (the cart pattern);
 *     `duplicate: true` means the key was already processed and no second burn
 *     happened.
 *   • `POST /api/pulse/rewards/<id>/claim` — claims an approved cash reward.
 *     `needs_onboarding` hands back a Stripe onboarding URL to open in the
 *     browser; `setup_required` means the disbursement rail is not available
 *     yet and the screen says so honestly rather than spinning.
 */
import { pulseApi } from "./pulseApi";

const nonNegInt = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

/* ------------------------------------------------------------------ *
 * Rewards
 * ------------------------------------------------------------------ */

export const REWARD_STATUSES = [
  "pending",
  "granted",
  "approved",
  "disbursing",
  "disbursed",
  "denied",
  "blocked"
] as const;

export type RewardStatus = (typeof REWARD_STATUSES)[number];

export type RewardKind = "pulse_credits" | "cash";

export type Reward = {
  id: number;
  event_key: string;
  event_type: string;
  /** `pulse_credits` or `cash`; an unknown kind passes through raw. */
  reward_kind: string;
  /**
   * For `pulse_credits` this is a credit count; for `cash` it is integer cents
   * in `currency`. The distinction is the server's, carried through untouched.
   */
  amount: number;
  currency: string;
  /** One of `REWARD_STATUSES`, or the server's raw word if unknown. */
  status: string;
  /** The fraud pipeline's state, verbatim — `review` renders "under review". */
  fraud_state: string;
  created_at: string;
};

export function normalizeReward(value?: Partial<Reward> | null): Reward {
  return {
    id: nonNegInt(value?.id),
    event_key: String(value?.event_key || ""),
    event_type: String(value?.event_type || ""),
    reward_kind: String(value?.reward_kind || ""),
    amount: nonNegInt(value?.amount),
    currency: String(value?.currency || "USD").toUpperCase(),
    status: String(value?.status || ""),
    fraud_state: String(value?.fraud_state || ""),
    created_at: String(value?.created_at || "")
  };
}

export type RewardsPage = {
  rewards: Reward[];
  next_before_id: number | null;
  has_more: boolean;
  /** The server's finished credit total. Never derived from the list. */
  credit_balance: number;
};

export function normalizeRewardsPage(value?: {
  rewards?: Partial<Reward>[];
  next_before_id?: number | null;
  has_more?: boolean;
  credit_balance?: number;
} | null): RewardsPage {
  const next = Number(value?.next_before_id);
  return {
    rewards: (Array.isArray(value?.rewards) ? value!.rewards : [])
      .map(normalizeReward)
      .filter((reward) => reward.id > 0),
    next_before_id: Number.isFinite(next) && next > 0 ? Math.round(next) : null,
    has_more: value?.has_more === true,
    credit_balance: nonNegInt(value?.credit_balance)
  };
}

export async function fetchRewards(
  options: { limit?: number; beforeId?: number } = {}
): Promise<RewardsPage> {
  const params = new URLSearchParams();
  params.set("limit", String(Math.max(1, Math.min(100, options.limit || 30))));
  if (options.beforeId) params.set("before_id", String(options.beforeId));
  const data = await pulseApi<Parameters<typeof normalizeRewardsPage>[0]>(
    `/api/pulse/rewards?${params.toString()}`
  );
  return normalizeRewardsPage(data);
}

/* ------------------------------------------------------------------ *
 * Credit ledger
 * ------------------------------------------------------------------ */

export type CreditLedgerEntry = {
  id: number;
  /** Signed — a burn is negative and must render as one. */
  delta: number;
  /** Server-written running balance. Never recomputed client-side. */
  balance_after: number;
  reason: string;
  created_at: string;
};

export function normalizeCreditLedgerEntry(
  value?: Partial<CreditLedgerEntry> | null
): CreditLedgerEntry {
  return {
    id: nonNegInt(value?.id),
    delta: Math.round(Number(value?.delta) || 0),
    balance_after: nonNegInt(value?.balance_after),
    reason: String(value?.reason || ""),
    created_at: String(value?.created_at || "")
  };
}

export type CreditLedgerPage = {
  entries: CreditLedgerEntry[];
  next_before_id: number | null;
  has_more: boolean;
  credit_balance: number;
};

export function normalizeCreditLedgerPage(value?: {
  entries?: Partial<CreditLedgerEntry>[];
  next_before_id?: number | null;
  has_more?: boolean;
  credit_balance?: number;
} | null): CreditLedgerPage {
  const next = Number(value?.next_before_id);
  return {
    entries: (Array.isArray(value?.entries) ? value!.entries : [])
      .map(normalizeCreditLedgerEntry)
      .filter((entry) => entry.id > 0),
    next_before_id: Number.isFinite(next) && next > 0 ? Math.round(next) : null,
    has_more: value?.has_more === true,
    credit_balance: nonNegInt(value?.credit_balance)
  };
}

export async function fetchCreditLedger(
  options: { limit?: number; beforeId?: number } = {}
): Promise<CreditLedgerPage> {
  const params = new URLSearchParams();
  params.set("limit", String(Math.max(1, Math.min(100, options.limit || 30))));
  if (options.beforeId) params.set("before_id", String(options.beforeId));
  const data = await pulseApi<Parameters<typeof normalizeCreditLedgerPage>[0]>(
    `/api/pulse/rewards/credits/ledger?${params.toString()}`
  );
  return normalizeCreditLedgerPage(data);
}

/* ------------------------------------------------------------------ *
 * Redeem — credits → promotional ad credits
 * ------------------------------------------------------------------ */

export type RedeemResult = {
  duplicate: boolean;
  credits_burned: number;
  promo_credit_cents: number;
  credit_balance: number;
};

export async function redeemCredits(
  creditsAmount: number,
  accountId: number,
  redemptionKey: string
): Promise<RedeemResult> {
  const data = await pulseApi<Partial<RedeemResult>>("/api/pulse/rewards/credits/redeem", {
    method: "POST",
    body: JSON.stringify({
      credits_amount: nonNegInt(creditsAmount),
      account_id: accountId,
      redemption_key: redemptionKey
    })
  });
  return {
    duplicate: data?.duplicate === true,
    credits_burned: nonNegInt(data?.credits_burned),
    promo_credit_cents: nonNegInt(data?.promo_credit_cents),
    credit_balance: nonNegInt(data?.credit_balance)
  };
}

/** The redeem intent key — the cart checkout shape. */
export function mintRedemptionKey(): string {
  return `redeem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/* ------------------------------------------------------------------ *
 * Claim — approved cash rewards
 * ------------------------------------------------------------------ */

export type ClaimResult = {
  needs_onboarding: boolean;
  onboarding_url: string;
  setup_required: boolean;
  reward: Reward;
};

export async function claimReward(rewardId: number): Promise<ClaimResult> {
  const data = await pulseApi<{
    needs_onboarding?: boolean;
    onboarding_url?: string;
    setup_required?: boolean;
    reward?: Partial<Reward>;
  }>(`/api/pulse/rewards/${encodeURIComponent(String(rewardId))}/claim`, {
    method: "POST",
    body: JSON.stringify({})
  });
  return {
    needs_onboarding: data?.needs_onboarding === true,
    onboarding_url: String(data?.onboarding_url || ""),
    setup_required: data?.setup_required === true,
    reward: normalizeReward(data?.reward)
  };
}

/* ------------------------------------------------------------------ *
 * Presentation helpers — pure, tested.
 * ------------------------------------------------------------------ */

export type RewardStatusTone = "progress" | "success" | "error" | "neutral";

/**
 * i18n suffix (under `commerce:rewards`) and colour tone for a reward status.
 * An unknown status gets no key — the raw word renders — and the neutral tone.
 */
export function rewardStatusChip(status: string): { key: string | null; tone: RewardStatusTone } {
  switch (status) {
    case "pending":
      return { key: "rewardStatusPending", tone: "neutral" };
    case "granted":
      return { key: "rewardStatusGranted", tone: "success" };
    case "approved":
      return { key: "rewardStatusApproved", tone: "success" };
    case "disbursing":
      return { key: "rewardStatusDisbursing", tone: "progress" };
    case "disbursed":
      return { key: "rewardStatusDisbursed", tone: "success" };
    case "denied":
      return { key: "rewardStatusDenied", tone: "error" };
    case "blocked":
      return { key: "rewardStatusBlocked", tone: "error" };
    default:
      return { key: null, tone: "neutral" };
  }
}

/** A cash reward the seller can act on. Credits grant themselves. */
export function rewardIsClaimable(reward: Pick<Reward, "reward_kind" | "status">): boolean {
  return reward.reward_kind === "cash" && reward.status === "approved";
}

/** The fraud pipeline's hold state, shown honestly as "under review". */
export function rewardIsUnderReview(reward: Pick<Reward, "fraud_state">): boolean {
  return reward.fraud_state === "review";
}
