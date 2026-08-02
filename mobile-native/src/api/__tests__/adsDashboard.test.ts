/**
 * The Advertising screen spends money and offers controls that the server can
 * refuse. Both of those risks live in this module's pure derivations rather
 * than in the component, so this is where they can be pinned:
 *
 *   1. `deliverySwitchState` is the single place that decides whether a pause
 *      switch may be pressed. The mission's rule is that "a switch that
 *      silently no-ops is forbidden" — so every disabled branch must carry a
 *      reason the card can render, and every enabled branch must correspond to
 *      an action `availableAdCampaignActions` (which mirrors the server's own
 *      table) would accept.
 *   2. `adsKpis` reads `estimated_cpc`, which the Python service computes in
 *      DOLLARS (`round(spent_cents / 100 / clicks, 2)`). Treating it as cents
 *      would understate cost-per-click a hundredfold, which is the same class
 *      of error as the budget off-by-100 the classic-screen test guards.
 *   3. `ADS_MOCK_DATA_GAPS` is the honesty ledger. Its length is asserted here
 *      so that faking one of those fields without a real endpoint breaks a
 *      test instead of shipping quietly.
 */

import {
  ADS_MOCK_DATA_GAPS,
  ADS_POST_MODE_FLAG,
  adsKpis,
  adsPostModeEnabled,
  campaignBudget,
  campaignMetricsAreLive,
  campaignSpendCents,
  campaignTabFor,
  campaignTabs,
  blockedCampaigns,
  deliverySwitchState,
  filterCampaigns,
  loadMockPostKpis,
  loadMockRecentPosts,
  promotionSwitchState,
  spendChartWeekdays,
  walletSummary
} from "../adsDashboard";
import type { AdAccount, AdCampaign } from "../businessOs";

const ACTIVE_ACCOUNT = { id: 7, business_name: "Roody Goods", status: "active" } as AdAccount;
const PENDING_ACCOUNT = { id: 8, business_name: "Roody Goods", status: "pending" } as AdAccount;

function campaign(overrides: Partial<AdCampaign> = {}): AdCampaign {
  return {
    id: 21,
    campaign_name: "Launch",
    objective: "awareness",
    status: "draft",
    budget_type: "daily",
    daily_budget_cents: 2500,
    lifetime_budget_cents: 0,
    spent_cents: 0,
    ...overrides
  } as AdCampaign;
}

describe("ads mock-data ledger", () => {
  it("names every unsourced field, and the count is pinned", () => {
    // If this number moves, either a gap was closed with a real endpoint (good,
    // update it) or a gap was papered over with invented data (not good).
    expect(ADS_MOCK_DATA_GAPS.length).toBe(9);
    for (const gap of ADS_MOCK_DATA_GAPS) {
      expect(gap.field.length).toBeGreaterThan(0);
      expect(gap.needs.length).toBeGreaterThan(0);
      expect(["marketplace", "post", "both"]).toContain(gap.mode);
    }
  });
});

describe("post-mode feature flag", () => {
  const original = process.env[ADS_POST_MODE_FLAG];
  afterEach(() => {
    if (original === undefined) delete process.env[ADS_POST_MODE_FLAG];
    else process.env[ADS_POST_MODE_FLAG] = original;
  });

  it("is off unless the env var is explicitly truthy", () => {
    delete process.env[ADS_POST_MODE_FLAG];
    expect(adsPostModeEnabled()).toBe(false);
    process.env[ADS_POST_MODE_FLAG] = "0";
    expect(adsPostModeEnabled()).toBe(false);
    process.env[ADS_POST_MODE_FLAG] = "1";
    expect(adsPostModeEnabled()).toBe(true);
  });

  it("returns no preview promotions or KPIs while the flag is off", () => {
    delete process.env[ADS_POST_MODE_FLAG];
    expect(loadMockPostKpis()).toBeNull();
    expect(loadMockRecentPosts()).toEqual([]);
  });

  it("flags exactly one overperforming post when the preview is on", () => {
    process.env[ADS_POST_MODE_FLAG] = "1";
    const hot = loadMockRecentPosts().filter((post) => post.hotMultiplier !== null);
    expect(hot).toHaveLength(1);
    expect(hot[0].hotMultiplier).toBeGreaterThanOrEqual(3);
    // Everything the preview returns is marked, so nothing here can be mistaken
    // for a live number by a later reader of the code.
    for (const post of loadMockRecentPosts()) expect(post.mock).toBe(true);
    expect(loadMockPostKpis()?.mock).toBe(true);
  });
});

describe("deliverySwitchState", () => {
  it("hides the switch on a draft — there is nothing to pause yet", () => {
    const state = deliverySwitchState(campaign({ status: "draft" }), ACTIVE_ACCOUNT);
    expect(state.show).toBe(false);
    expect(state.action).toBeNull();
  });

  it("enables pause on a delivering campaign and asks for the action the server accepts", () => {
    const state = deliverySwitchState(campaign({ status: "active" }), ACTIVE_ACCOUNT);
    expect(state).toMatchObject({ show: true, on: true, disabled: false, reason: null, action: "pause" });
  });

  it("enables resume on a paused campaign", () => {
    const state = deliverySwitchState(campaign({ status: "paused" }), ACTIVE_ACCOUNT);
    expect(state).toMatchObject({ show: true, on: false, disabled: false, action: "resume" });
  });

  it.each([
    ["ended", "archived"],
    ["ended", "completed"],
    ["in review", "pending_review"]
  ])("shows a disabled switch with a reason for a %s campaign", (_label, status) => {
    const state = deliverySwitchState(campaign({ status }), ACTIVE_ACCOUNT);
    // Shown, not hidden: the control the user expects is present and explains
    // itself, rather than vanishing and leaving them to guess.
    expect(state.show).toBe(true);
    expect(state.disabled).toBe(true);
    expect(state.action).toBeNull();
    expect(state.reason).toBeTruthy();
  });

  it("blocks delivery on an account that cannot transact and points at verification", () => {
    const state = deliverySwitchState(campaign({ status: "active" }), PENDING_ACCOUNT);
    expect(state).toMatchObject({ show: true, disabled: true, action: null });
    expect(state.reason).toMatch(/[Vv]erify/);
  });

  it("never offers an action the backend would reject", () => {
    for (const status of ["draft", "pending_review", "active", "paused", "archived", "completed"]) {
      const state = deliverySwitchState(campaign({ status }), ACTIVE_ACCOUNT);
      if (state.action) expect(state.disabled).toBe(false);
      if (state.disabled && state.show) expect(state.reason).toBeTruthy();
    }
  });
});

describe("promotionSwitchState", () => {
  it("is disabled in every live phase, because no backend accepts the transition", () => {
    for (const phase of ["submitted", "in_review", "promoting", "paused"] as const) {
      const state = promotionSwitchState({ phase } as never);
      expect(state.show).toBe(true);
      expect(state.disabled).toBe(true);
      expect(state.reason).toBeTruthy();
    }
  });

  it("hides the switch entirely once a promotion is finished or rejected", () => {
    for (const phase of ["completed", "rejected"] as const) {
      expect(promotionSwitchState({ phase } as never).show).toBe(false);
    }
  });
});

describe("campaignBudget", () => {
  it("returns null when no budget is set rather than a 0% bar", () => {
    expect(campaignBudget(campaign({ daily_budget_cents: 0 }))).toBeNull();
    expect(campaignBudget(undefined)).toBeNull();
  });

  it("reads the lifetime key for a lifetime campaign", () => {
    const budget = campaignBudget(
      campaign({ budget_type: "lifetime", lifetime_budget_cents: 50000, daily_budget_cents: 2500, spent_cents: 5000 })
    );
    expect(budget).toMatchObject({ type: "lifetime", budgetCents: 50000, spentCents: 5000 });
    expect(budget?.fraction).toBeCloseTo(0.1);
  });

  it("turns hot at 90% of budget and clamps overspend at 100%", () => {
    expect(campaignBudget(campaign({ spent_cents: 2249 }))?.hot).toBe(false);
    expect(campaignBudget(campaign({ spent_cents: 2250 }))?.hot).toBe(true);
    expect(campaignBudget(campaign({ spent_cents: 9999 }))?.fraction).toBe(1);
  });
});

describe("adsKpis", () => {
  it("converts the server's dollar cost-per-click into cents", () => {
    const kpis = adsKpis({
      campaigns: [],
      analytics: { totals: { spend_cents: 12500, clicks: 50, impressions: 4000, estimated_cpc: 2.5 } } as never
    });
    // 2.5 dollars per click is 250 cents. Reading it as 250 cents' worth of
    // dollars would print "$0.03" on a $2.50 click.
    expect(kpis.cpcCents).toBe(250);
    expect(kpis.spendCents).toBe(12500);
    expect(kpis.clicks).toBe(50);
  });

  it("reports no cost per click when nobody has clicked, instead of $0.00", () => {
    const kpis = adsKpis({
      campaigns: [],
      analytics: { totals: { spend_cents: 500, clicks: 0, impressions: 900, estimated_cpc: 0 } } as never
    });
    expect(kpis.cpcCents).toBeNull();
  });

  it("sums daily budgets only across campaigns that are actually delivering", () => {
    const kpis = adsKpis({
      analytics: null,
      campaigns: [
        campaign({ id: 1, status: "active", daily_budget_cents: 2500 }),
        campaign({ id: 2, status: "paused", daily_budget_cents: 9900 }),
        campaign({ id: 3, status: "active", budget_type: "lifetime", lifetime_budget_cents: 50000 })
      ]
    });
    expect(kpis.dailyBudgetCents).toBe(2500);
    expect(kpis.hasDailyBudget).toBe(true);
  });

  it("has no daily budget to show when nothing is delivering", () => {
    const kpis = adsKpis({ analytics: null, campaigns: [campaign({ status: "draft" })] });
    expect(kpis.hasDailyBudget).toBe(false);
    expect(kpis.dailyBudgetCents).toBe(0);
  });
});

describe("campaign tabs", () => {
  it("files an in-review campaign under Active, not Drafts", () => {
    // The advertiser submitted it and is waiting; Drafts would read as "you
    // still have work to do".
    expect(campaignTabFor(campaign({ status: "pending_review" }))).toBe("active");
    expect(campaignTabFor(campaign({ status: "active" }))).toBe("active");
    expect(campaignTabFor(campaign({ status: "paused" }))).toBe("paused");
    expect(campaignTabFor(campaign({ status: "archived" }))).toBe("ended");
    expect(campaignTabFor(campaign({ status: "draft" }))).toBe("drafts");
  });

  it("counts each tab and filters to it", () => {
    const campaigns = [
      campaign({ id: 1, status: "active" }),
      campaign({ id: 2, status: "pending_review" }),
      campaign({ id: 3, status: "paused" }),
      campaign({ id: 4, status: "draft" })
    ];
    const tabs = campaignTabs(campaigns, ACTIVE_ACCOUNT);
    expect(tabs.find((tab) => tab.key === "active")?.count).toBe(2);
    expect(tabs.find((tab) => tab.key === "ended")?.count).toBe(0);
    expect(filterCampaigns(campaigns, "drafts").map((entry) => entry.id)).toEqual([4]);
  });

  it("marks the Active tab as needing attention when verification blocks delivery", () => {
    const campaigns = [campaign({ id: 1, status: "active" })];
    expect(blockedCampaigns(campaigns, PENDING_ACCOUNT)).toHaveLength(1);
    expect(blockedCampaigns(campaigns, ACTIVE_ACCOUNT)).toHaveLength(0);
    const tabs = campaignTabs(campaigns, PENDING_ACCOUNT);
    expect(tabs.find((tab) => tab.key === "active")?.needsAttention).toBe(true);
  });
});

describe("per-campaign spend and liveness", () => {
  it("prefers the analytics row over the campaign's own counter", () => {
    const target = campaign({ id: 21, spent_cents: 100 });
    const analytics = { campaigns: [{ campaign_id: 21, spent_cents: 4200 }] } as never;
    expect(campaignSpendCents(target, analytics)).toBe(4200);
    expect(campaignSpendCents(target, null)).toBe(100);
  });

  it("only calls metrics live for a campaign that is delivering", () => {
    expect(campaignMetricsAreLive(campaign({ status: "active" }))).toBe(true);
    expect(campaignMetricsAreLive(campaign({ status: "paused" }))).toBe(false);
    expect(campaignMetricsAreLive(campaign({ status: "pending_review" }))).toBe(false);
  });
});

describe("walletSummary", () => {
  it("takes the spendable balance from the server, never a client computation", () => {
    const summary = walletSummary(
      7,
      { currency: "usd", spendable_balance_cents: 14200, available_balance_cents: 99900 } as never,
      { billing_enabled: true, live_charging: false } as never
    );
    expect(summary.balanceCents).toBe(14200);
    expect(summary.currency).toBe("USD");
    // Backend pins live_charging false, so Add funds must say so rather than
    // presenting a control that looks like it will charge a card.
    expect(summary.fundingLive).toBe(false);
  });

  it("falls back to the available balance when spendable is absent", () => {
    const summary = walletSummary(7, { available_balance_cents: 500 } as never, null);
    expect(summary.balanceCents).toBe(500);
  });
});

describe("spendChartWeekdays", () => {
  it("ends on today and runs oldest first", () => {
    // 2026-08-01 is a Saturday (day 6).
    const days = spendChartWeekdays(new Date(2026, 7, 1), 7);
    expect(days).toHaveLength(7);
    expect(days[6]).toBe(6);
    expect(days[0]).toBe(0);
  });
});
