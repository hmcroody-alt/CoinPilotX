/**
 * Rewards move credits and cash, so the load-bearing decisions are pinned:
 *
 *   1. The ledger delta keeps its sign — a burn rendered positive would read
 *      as a grant. `balance_after` is the server's figure, never recomputed.
 *   2. The redeem idempotency key travels in the POST body, and a replayed key
 *      reads as `duplicate: true`, never as a second burn.
 *   3. Only a cash reward in `approved` is claimable; credits grant themselves.
 *   4. Unknown statuses render their raw word — no chip label is guessed.
 */
const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => {
  const actual = jest.requireActual("../pulseApi");
  return {
    ...actual,
    pulseApi: (...args: unknown[]) => mockPulseApi(...args)
  };
});

import {
  REWARD_STATUSES,
  claimReward,
  fetchCreditLedger,
  fetchRewards,
  mintRedemptionKey,
  normalizeCreditLedgerEntry,
  normalizeCreditLedgerPage,
  normalizeReward,
  normalizeRewardsPage,
  redeemCredits,
  rewardIsClaimable,
  rewardIsUnderReview,
  rewardStatusChip
} from "../rewards";

beforeEach(() => {
  mockPulseApi.mockReset();
});

/* ------------------------------------------------------------------ *
 * Normalizers
 * ------------------------------------------------------------------ */

describe("normalizeReward", () => {
  it("carries the amount through untouched in the server's own unit", () => {
    // Credits count for pulse_credits, integer cents for cash — the server's
    // distinction, not recomputed here.
    expect(normalizeReward({ id: 1, reward_kind: "pulse_credits", amount: 50 }).amount).toBe(50);
    expect(normalizeReward({ id: 2, reward_kind: "cash", amount: 1500 }).amount).toBe(1500);
    expect(normalizeReward({ id: 3, amount: -5 }).amount).toBe(0);
  });

  it("uppercases the currency, defaults USD, and keeps raw words raw", () => {
    expect(normalizeReward({ id: 1, currency: "usd" }).currency).toBe("USD");
    expect(normalizeReward({ id: 1, status: "simmering" }).status).toBe("simmering");
    expect(normalizeReward({ id: 1, reward_kind: "confetti" }).reward_kind).toBe("confetti");
  });
});

describe("normalizeRewardsPage", () => {
  it("drops id-less rows, reads a junk cursor as the end, and keeps the server's balance", () => {
    const page = normalizeRewardsPage({
      rewards: [{ id: 4, reward_kind: "pulse_credits", amount: 10 }, { id: 0 }, {}],
      next_before_id: 4,
      has_more: true,
      credit_balance: 120
    });
    expect(page.rewards).toHaveLength(1);
    expect(page.next_before_id).toBe(4);
    expect(page.credit_balance).toBe(120);
    expect(normalizeRewardsPage({ next_before_id: 0 }).next_before_id).toBeNull();
    expect(normalizeRewardsPage(null).rewards).toEqual([]);
    expect(normalizeRewardsPage(null).credit_balance).toBe(0);
  });
});

describe("normalizeCreditLedgerEntry", () => {
  it("keeps the sign on the delta — a burn is negative and must render as one", () => {
    expect(normalizeCreditLedgerEntry({ id: 1, delta: -25 }).delta).toBe(-25);
    expect(normalizeCreditLedgerEntry({ id: 2, delta: 40 }).delta).toBe(40);
  });

  it("takes balance_after from the server and clamps it non-negative", () => {
    expect(normalizeCreditLedgerEntry({ id: 1, balance_after: 75 }).balance_after).toBe(75);
    expect(normalizeCreditLedgerEntry({ id: 1, balance_after: -3 }).balance_after).toBe(0);
  });

  it("keeps the reason sentence verbatim — it is the row's title", () => {
    expect(normalizeCreditLedgerEntry({ id: 1, reason: "Weekly streak bonus" }).reason).toBe(
      "Weekly streak bonus"
    );
  });
});

describe("normalizeCreditLedgerPage", () => {
  it("drops id-less rows and reads a junk cursor as the last page", () => {
    const page = normalizeCreditLedgerPage({
      entries: [{ id: 9, delta: -10 }, { id: 0 }],
      next_before_id: 9
    });
    expect(page.entries).toHaveLength(1);
    expect(page.next_before_id).toBe(9);
    expect(normalizeCreditLedgerPage({ next_before_id: "x" as never }).next_before_id).toBeNull();
    expect(normalizeCreditLedgerPage(null).entries).toEqual([]);
  });
});

/* ------------------------------------------------------------------ *
 * Endpoints
 * ------------------------------------------------------------------ */

describe("fetchRewards / fetchCreditLedger", () => {
  it("builds both URLs with a clamped limit and the cursor", async () => {
    mockPulseApi.mockResolvedValue({});
    await fetchRewards({ limit: 500, beforeId: 12 });
    expect(String(mockPulseApi.mock.calls[0][0])).toBe(
      "/api/pulse/rewards?limit=100&before_id=12"
    );
    await fetchCreditLedger();
    expect(String(mockPulseApi.mock.calls[1][0])).toBe(
      "/api/pulse/rewards/credits/ledger?limit=30"
    );
  });
});

describe("redeemCredits", () => {
  it("POSTs the credits, account and idempotency key in the body", async () => {
    mockPulseApi.mockResolvedValue({ credits_burned: 50 });
    await redeemCredits(50, 7, "redeem-abc");
    const [path, options] = mockPulseApi.mock.calls[0] as [string, { method: string; body: string }];
    expect(path).toBe("/api/pulse/rewards/credits/redeem");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      credits_amount: 50,
      account_id: 7,
      redemption_key: "redeem-abc"
    });
  });

  it("reads a replayed key as duplicate, never as a second burn", async () => {
    mockPulseApi.mockResolvedValue({
      duplicate: true,
      credits_burned: 50,
      promo_credit_cents: 500,
      credit_balance: 70
    });
    const result = await redeemCredits(50, 7, "redeem-abc");
    expect(result.duplicate).toBe(true);
    expect(result.credit_balance).toBe(70);
  });
});

describe("mintRedemptionKey", () => {
  it("mints a prefixed, unique key per confirmation", () => {
    const one = mintRedemptionKey();
    const two = mintRedemptionKey();
    expect(one).toMatch(/^redeem-/);
    expect(one).not.toBe(two);
  });
});

describe("claimReward", () => {
  it("POSTs to the reward's own claim route", async () => {
    mockPulseApi.mockResolvedValue({ reward: { id: 5 } });
    await claimReward(5);
    const [path, options] = mockPulseApi.mock.calls[0] as [string, { method: string }];
    expect(path).toBe("/api/pulse/rewards/5/claim");
    expect(options.method).toBe("POST");
  });

  it("carries the onboarding hand-off and the setup_required refusal through", async () => {
    mockPulseApi.mockResolvedValue({
      needs_onboarding: true,
      onboarding_url: "https://connect.stripe.com/setup/x",
      reward: { id: 5, status: "approved" }
    });
    const result = await claimReward(5);
    expect(result.needs_onboarding).toBe(true);
    expect(result.onboarding_url).toBe("https://connect.stripe.com/setup/x");
    expect(result.setup_required).toBe(false);

    mockPulseApi.mockResolvedValue({ setup_required: true, reward: { id: 5 } });
    expect((await claimReward(5)).setup_required).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * Presentation helpers
 * ------------------------------------------------------------------ */

describe("rewardStatusChip", () => {
  it("gives each of the seven server states a key, and covers them all", () => {
    for (const status of REWARD_STATUSES) {
      expect(rewardStatusChip(status).key).not.toBeNull();
    }
  });

  it("puts refusals in the error tone and settled grants in success", () => {
    expect(rewardStatusChip("denied").tone).toBe("error");
    expect(rewardStatusChip("blocked").tone).toBe("error");
    expect(rewardStatusChip("disbursed").tone).toBe("success");
    expect(rewardStatusChip("disbursing").tone).toBe("progress");
  });

  it("gives an unknown status no key and the neutral tone", () => {
    expect(rewardStatusChip("simmering")).toEqual({ key: null, tone: "neutral" });
  });
});

describe("rewardIsClaimable / rewardIsUnderReview", () => {
  it("only an approved cash reward is claimable — credits grant themselves", () => {
    expect(rewardIsClaimable({ reward_kind: "cash", status: "approved" })).toBe(true);
    expect(rewardIsClaimable({ reward_kind: "cash", status: "pending" })).toBe(false);
    expect(rewardIsClaimable({ reward_kind: "pulse_credits", status: "approved" })).toBe(false);
  });

  it("reads only the fraud pipeline's review state as 'under review'", () => {
    expect(rewardIsUnderReview({ fraud_state: "review" })).toBe(true);
    expect(rewardIsUnderReview({ fraud_state: "clear" })).toBe(false);
    expect(rewardIsUnderReview({ fraud_state: "" })).toBe(false);
  });
});
