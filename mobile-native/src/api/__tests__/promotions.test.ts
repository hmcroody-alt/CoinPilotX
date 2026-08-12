/**
 * Promotions client — the pure mappers over `/api/promotions/*`. These translate
 * the server's snake_case, nested JSON into the typed shapes the wizard renders,
 * and enforce the mission's truthfulness rules: status is projected honestly
 * (a submitted promotion never reads as "Delivering"), absent metrics are null
 * rather than zero, and the create body matches the server validator exactly.
 */

import {
  appendPromotablePage,
  buildCreatePromotionBody,
  mapPromotableContentPage,
  mapPromotableItem,
  mapPromotion,
  mapPromotionAnalytics,
  mapPromotionEligibility,
  mapPromotionStatus,
  promotionStatusLabel,
  promotionStatusTone
} from "../promotions";

describe("mapPromotionStatus", () => {
  it("projects raw server status onto the honest UI state", () => {
    expect(mapPromotionStatus("draft")).toBe("draft");
    expect(mapPromotionStatus("pending_review")).toBe("in_review");
    expect(mapPromotionStatus("promoting")).toBe("delivering");
    expect(mapPromotionStatus("paused")).toBe("paused");
    expect(mapPromotionStatus("canceled")).toBe("ended");
    expect(mapPromotionStatus("cancelled")).toBe("ended");
    expect(mapPromotionStatus("failed")).toBe("rejected");
    expect(mapPromotionStatus("something_new")).toBe("unknown");
  });

  it("never lets a merely-submitted promotion read as active", () => {
    // The core truthfulness rule: pending_review is "In review", not delivering.
    const status = mapPromotionStatus("pending_review");
    expect(status).not.toBe("delivering");
    expect(promotionStatusLabel(status)).toBe("In review");
    expect(promotionStatusTone(status)).toBe("pending");
  });
});

describe("mapPromotableItem", () => {
  it("only reports promotable when the server boolean and verdict agree", () => {
    const promotable = mapPromotableItem({
      content_type: "reel",
      content_id: 9,
      title: "Clip",
      eligibility: "PROMOTABLE",
      promotable: true
    });
    expect(promotable.promotable).toBe(true);

    // Server says promotable but the verdict disagrees — trust the verdict.
    const conflicted = mapPromotableItem({
      content_type: "reel",
      content_id: 9,
      eligibility: "UNDER_REVIEW",
      promotable: true
    });
    expect(conflicted.promotable).toBe(false);
  });

  it("keeps absent duration as null rather than zero", () => {
    const item = mapPromotableItem({ content_type: "post", content_id: 1 });
    expect(item.durationSeconds).toBeNull();
  });

  it("normalizes an unknown content type to post", () => {
    expect(mapPromotableItem({ content_type: "story", content_id: 1 }).contentType).toBe("post");
  });
});

describe("mapPromotableContentPage / appendPromotablePage", () => {
  const page = (offset: number, ids: number[], hasMore: boolean) =>
    mapPromotableContentPage({
      items: ids.map((id) => ({ content_type: "post", content_id: id, eligibility: "PROMOTABLE", promotable: true })),
      filter: "post",
      limit: 12,
      offset,
      next_offset: offset + ids.length,
      has_more: hasMore
    });

  it("parses paging metadata", () => {
    const first = page(0, [1, 2, 3], true);
    expect(first.filter).toBe("post");
    expect(first.items).toHaveLength(3);
    expect(first.hasMore).toBe(true);
    expect(first.nextOffset).toBe(3);
  });

  it("replaces the list on an offset-0 page and appends later pages", () => {
    const first = page(0, [1, 2, 3], true);
    const second = page(3, [4, 5], false);
    const merged = appendPromotablePage(first.items, second);
    expect(merged.map((i) => i.contentId)).toEqual([1, 2, 3, 4, 5]);

    const reset = appendPromotablePage(merged, page(0, [9], false));
    expect(reset.map((i) => i.contentId)).toEqual([9]);
  });

  it("dedupes items that arrive twice across pages", () => {
    const first = page(0, [1, 2], true);
    const overlap = page(2, [2, 3], false);
    const merged = appendPromotablePage(first.items, overlap);
    expect(merged.map((i) => i.contentId)).toEqual([1, 2, 3]);
  });
});

describe("mapPromotion", () => {
  it("preserves the raw status alongside the projected one", () => {
    const promo = mapPromotion({
      promotion_id: 5,
      content_type: "post",
      content_id: 12,
      goal: "more_views",
      status: "pending_review",
      total_budget: "$25",
      analytics_available: false
    });
    expect(promo.promotionId).toBe(5);
    expect(promo.rawStatus).toBe("pending_review");
    expect(promo.status).toBe("in_review");
    expect(promo.analyticsAvailable).toBe(false);
  });
});

describe("mapPromotionEligibility", () => {
  it("keeps estimated reach null when the server can't forecast", () => {
    const elig = mapPromotionEligibility({
      eligible: true,
      content: { content_type: "post", content_id: 1, title: "Hi" },
      goals: [{ key: "more_views", label: "More Views", enabled: true }],
      estimated_reach: null,
      forecasting_state: "unavailable"
    });
    expect(elig.eligible).toBe(true);
    expect(elig.estimatedReach).toBeNull();
    expect(elig.goals[0].enabled).toBe(true);
  });
});

describe("mapPromotionAnalytics", () => {
  it("reports unavailable when there is no campaign yet", () => {
    const analytics = mapPromotionAnalytics({ available: false, promotion_id: 3 });
    expect(analytics.available).toBe(false);
    expect(analytics.metrics).toBeNull();
  });

  it("maps real metrics and keeps missing cost-per-result null", () => {
    const analytics = mapPromotionAnalytics({
      available: true,
      promotion_id: 3,
      metrics: {
        spend_cents: 1200,
        views_from_promotion: 340,
        clicks: 12,
        cost_per_result_cents: null,
        status: "promoting"
      },
      unavailable_metrics: ["cost_per_result_cents"]
    });
    expect(analytics.available).toBe(true);
    expect(analytics.metrics?.spendCents).toBe(1200);
    expect(analytics.metrics?.costPerResultCents).toBeNull();
    expect(analytics.metrics?.unavailable).toContain("cost_per_result_cents");
  });
});

describe("buildCreatePromotionBody", () => {
  it("emits the exact nested shape the server validator accepts", () => {
    const body = buildCreatePromotionBody({
      contentType: "reel",
      contentId: 77,
      goal: "more_views",
      budgetType: "daily",
      budgetCents: 1500,
      durationDays: 5,
      startDate: "2026-09-01",
      launch: true,
      idempotencyKey: "promo-abc"
    });
    expect(body).toMatchObject({
      content_type: "reel",
      content_id: 77,
      goal: "more_views",
      audience: { type: "automatic" },
      budget: { type: "daily", amount_cents: 1500 },
      duration: { days: 5, start_date: "2026-09-01" },
      launch: true,
      idempotency_key: "promo-abc"
    });
  });

  it("omits the idempotency key and dates when not provided", () => {
    const body = buildCreatePromotionBody({
      contentType: "post",
      contentId: 1,
      goal: "more_views",
      budgetType: "total",
      budgetCents: 2500,
      durationDays: 7
    });
    expect(body.idempotency_key).toBeUndefined();
    expect(body.launch).toBe(false);
    expect((body.duration as Record<string, unknown>).start_date).toBeUndefined();
  });
});
