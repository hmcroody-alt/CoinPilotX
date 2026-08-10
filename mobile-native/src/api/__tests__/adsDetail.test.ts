/**
 * The campaign detail screen manages live spend, so the risky logic is kept in
 * this module's pure normalizers and derivations, where it can be pinned:
 *
 *   1. `normalizeAdCampaignDetail` is the single mapping from the server's
 *      detail payload to what the screen renders. Every fallback here decides
 *      what an advertiser sees about their own money, so the defaults (daily
 *      budget, draft status, null blocker) are asserted rather than assumed.
 *   2. `availableAdAdsetActions` mirrors the server's `adset_action` table.
 *      Offering an action the server would 409 (archiving the default ad set)
 *      is the "switch that silently no-ops" failure the ads mission forbids.
 *   3. `normalizeAdInsight.requires_approval` must never default to false —
 *      the client-side rule is that recommendations are applied only after an
 *      explicit confirmation, so a missing flag has to read as "approval
 *      required", not "auto-apply allowed".
 *   4. `normalizeAdServerDraft` prefers `objective_canonical` over the stored
 *      raw objective, because the wizard can only resume from canonical keys.
 */

import {
  adInsightsForCampaign,
  availableAdAdsetActions,
  normalizeAdAdset,
  normalizeAdCampaignDetail,
  normalizeAdDailySeries,
  normalizeAdEntityMetrics,
  normalizeAdInsight,
  normalizeAdServerDraft
} from "../adsDetail";
import type { AdAdset, AdInsight } from "../adsDetail";

const adset = (overrides: Partial<AdAdset> = {}): AdAdset =>
  normalizeAdAdset({
    id: 7,
    campaign_id: 3,
    ad_account_id: 1,
    name: "Prospecting",
    status: "active",
    is_default: false,
    ...overrides
  } as never);

describe("normalizeAdCampaignDetail", () => {
  it("maps a full payload without losing the money fields", () => {
    const detail = normalizeAdCampaignDetail({
      campaign: {
        id: 3,
        ad_account_id: 1,
        campaign_name: "Launch",
        status: "active",
        objective: "sales",
        objective_raw: "conversions",
        draft_key: "abc",
        created_at: "2026-08-01",
        updated_at: "2026-08-08"
      },
      lifecycle: { status: "active", can_edit: true, blocker: null },
      budget: {
        budget_type: "lifetime",
        daily_budget_cents: 0,
        lifetime_budget_cents: 50000,
        spent_cents: 12345,
        remaining_cents: 37655
      },
      schedule: { start_at: "2026-08-01T00:00:00", end_at: "" },
      placements: ["feed", "", "reels"],
      adsets: [{ id: 7, status: "paused" }],
      creatives: [],
      totals: { impressions: 1000, clicks: 40, spend_cents: 12345, ctr: 0.04 },
      daily_series: [
        { date: "2026-08-07", impressions: 10, clicks: 1, spend_cents: 100 },
        { date: "", impressions: 5, clicks: 0, spend_cents: 50 }
      ],
      estimated_results: { metric: "clicks", value: 40 }
    } as never);

    expect(detail.budget.budget_type).toBe("lifetime");
    expect(detail.budget.spent_cents).toBe(12345);
    expect(detail.budget.remaining_cents).toBe(37655);
    // Empty placement keys are dropped, real ones kept in order.
    expect(detail.placements).toEqual(["feed", "reels"]);
    // Dateless points are noise from the serializer, not renderable bars.
    expect(detail.daily_series).toEqual([
      { date: "2026-08-07", impressions: 10, clicks: 1, spend_cents: 100 }
    ]);
    expect(detail.adsets[0]?.status).toBe("paused");
    expect(detail.estimated_results).toEqual({ metric: "clicks", value: 40 });
  });

  it("degrades an empty payload to safe defaults instead of throwing", () => {
    const detail = normalizeAdCampaignDetail(undefined);
    expect(detail.campaign.status).toBe("draft");
    expect(detail.campaign.objective).toBe("awareness");
    expect(detail.lifecycle.status).toBe("draft");
    // Editing must be opt-in from the server, never a client default.
    expect(detail.lifecycle.can_edit).toBe(false);
    expect(detail.lifecycle.blocker).toBeNull();
    expect(detail.budget.budget_type).toBe("daily");
    expect(detail.adsets).toEqual([]);
    expect(detail.estimated_results).toBeNull();
  });

  it("keeps a structured blocker but drops malformed ones", () => {
    const blocked = normalizeAdCampaignDetail({
      lifecycle: { status: "blocked", can_edit: false, blocker: { code: "wallet_empty", message: "Top up" } }
    } as never);
    expect(blocked.lifecycle.blocker).toEqual({ code: "wallet_empty", message: "Top up" });

    const malformed = normalizeAdCampaignDetail({
      lifecycle: { status: "blocked", blocker: "wallet_empty" }
    } as never);
    expect(malformed.lifecycle.blocker).toBeNull();
  });

  it("drops estimated results without a metric name", () => {
    const detail = normalizeAdCampaignDetail({ estimated_results: { metric: "", value: 9 } } as never);
    expect(detail.estimated_results).toBeNull();
  });
});

describe("availableAdAdsetActions", () => {
  it("mirrors the server's action table per status", () => {
    expect(availableAdAdsetActions(adset({ status: "active" }))).toEqual(["pause", "archive"]);
    expect(availableAdAdsetActions(adset({ status: "paused" }))).toEqual(["resume", "archive"]);
    expect(availableAdAdsetActions(adset({ status: "archived" }))).toEqual([]);
  });

  it("never offers archive for the default ad set (server 409s)", () => {
    expect(availableAdAdsetActions(adset({ status: "active", is_default: true }))).toEqual(["pause"]);
    expect(availableAdAdsetActions(adset({ status: "paused", is_default: true }))).toEqual(["resume"]);
  });

  it("returns nothing for a missing ad set", () => {
    expect(availableAdAdsetActions(null)).toEqual([]);
  });
});

describe("normalizeAdAdset", () => {
  it("coerces the SQLite 0/1 default flag to a boolean", () => {
    expect(adset({ is_default: 1 as never }).is_default).toBe(true);
    expect(adset({ is_default: 0 as never }).is_default).toBe(false);
  });

  it("falls back to active for unknown statuses", () => {
    expect(adset({ status: "deleted" as never }).status).toBe("active");
  });
});

describe("normalizeAdEntityMetrics / normalizeAdDailySeries", () => {
  it("never emits negative numbers", () => {
    const metrics = normalizeAdEntityMetrics({ impressions: -5, clicks: -1, spend_cents: -100, ctr: -0.2 } as never);
    expect(metrics).toEqual({ impressions: 0, clicks: 0, spend_cents: 0, ctr: 0 });
  });

  it("returns an empty series for non-array input", () => {
    expect(normalizeAdDailySeries(null)).toEqual([]);
    expect(normalizeAdDailySeries("nope" as never)).toEqual([]);
  });
});

describe("insights", () => {
  const insight = (overrides: Partial<AdInsight> = {}): AdInsight =>
    normalizeAdInsight({
      id: "high_cpc:3",
      kind: "high_cpc",
      severity: "warning",
      title: "CPC is high",
      why: "Clicks cost more than peers",
      campaign_id: 3,
      action: { type: "raise_budget", params: { cents: 500 } },
      requires_approval: true,
      ...overrides
    } as never);

  it("requires approval even when the server omits the flag", () => {
    expect(normalizeAdInsight({ id: "x", campaign_id: 1 } as never).requires_approval).toBe(true);
    expect(insight({ requires_approval: undefined }).requires_approval).toBe(true);
    // Only an explicit false may relax it — and the screen still confirms.
    expect(insight({ requires_approval: false }).requires_approval).toBe(false);
  });

  it("defaults unknown severities to opportunity, not warning", () => {
    expect(insight({ severity: "critical" as never }).severity).toBe("opportunity");
  });

  it("filters the account feed to one campaign and drops id-less entries", () => {
    const feed = [insight(), insight({ campaign_id: 9 }), insight({ id: "" })];
    expect(adInsightsForCampaign(feed, 3)).toHaveLength(1);
    expect(adInsightsForCampaign(feed, 3)[0]?.campaign_id).toBe(3);
    expect(adInsightsForCampaign(null, 3)).toEqual([]);
  });
});

describe("normalizeAdServerDraft", () => {
  it("prefers the canonical objective the wizard can resume from", () => {
    const draft = normalizeAdServerDraft({
      id: 12,
      draft_key: "key-1",
      ad_account_id: 1,
      objective: "conversions",
      objective_canonical: "sales"
    });
    expect(draft.objective).toBe("sales");
  });

  it("falls back to the stored objective, then awareness", () => {
    expect(normalizeAdServerDraft({ objective: "traffic" }).objective).toBe("traffic");
    expect(normalizeAdServerDraft({}).objective).toBe("awareness");
  });

  it("normalizes placements and leaves targeting null when absent", () => {
    const draft = normalizeAdServerDraft({ placements: ["feed", " ", "stories"], targeting: undefined });
    expect(draft.placements).toEqual(["feed", "stories"]);
    expect(draft.targeting).toBeNull();
  });
});
