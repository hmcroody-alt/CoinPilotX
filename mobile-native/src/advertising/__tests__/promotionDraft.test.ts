/**
 * Promotion draft model: content selection, per-step validation, budget/duration
 * bounds and `POST /api/promotions` payload assembly. These are the pure
 * functions the wizard trusts at the Submit boundary, so the assertions mirror
 * the server contract in `services/pulsesoc_promotions.py`.
 */

import type { PromotableContentType } from "../../api/promotions";
import {
  PROMOTION_MAX_BUDGET_CENTS,
  PROMOTION_MAX_DURATION_DAYS,
  PROMOTION_MIN_BUDGET_CENTS,
  PROMOTION_WIZARD_STEPS,
  PromotionContentSelection,
  PromotionDraft,
  buildCreatePromotionInput,
  canAdvanceFrom,
  createPromotionDraft,
  createPromotionIdempotencyKey,
  nextPromotionStep,
  normalizePromotionDraft,
  parsePromotionBudgetCents,
  previousPromotionStep,
  promotionDraftHasContent,
  promotionDraftIssueFor,
  promotionGoalsForContent,
  validatePromotionStep,
  withSelectedContent
} from "../promotionDraft";

function selection(overrides: Partial<PromotionContentSelection> = {}): PromotionContentSelection {
  return {
    contentType: "post",
    contentId: 42,
    title: "My best post",
    thumbnailUrl: "https://cdn.example/x.jpg",
    mediaKind: "image",
    ...overrides
  };
}

/** A draft that passes every step, so tests can knock out one field at a time. */
function validDraft(overrides: Partial<PromotionDraft> = {}): PromotionDraft {
  return {
    ...withSelectedContent(createPromotionDraft(), selection()),
    goal: "more_views",
    budgetType: "total",
    budgetAmount: "25",
    durationDays: 7,
    step: "review",
    ...overrides
  };
}

describe("createPromotionDraft", () => {
  it("starts on the content step with an automatic audience and a fresh idempotency key", () => {
    const draft = createPromotionDraft();
    expect(draft.step).toBe("content");
    expect(draft.content).toBeNull();
    expect(draft.audienceType).toBe("automatic");
    expect(draft.idempotencyKey).toMatch(/^promo-/);
  });

  it("mints a unique idempotency key each call", () => {
    expect(createPromotionIdempotencyKey()).not.toBe(createPromotionIdempotencyKey());
  });
});

describe("withSelectedContent", () => {
  it("attaches the content and keeps the idempotency key", () => {
    const base = createPromotionDraft();
    const next = withSelectedContent(base, selection({ contentId: 7 }));
    expect(next.content).toEqual({
      contentType: "post",
      contentId: 7,
      title: "My best post",
      thumbnailUrl: "https://cdn.example/x.jpg",
      mediaKind: "image"
    });
    expect(next.idempotencyKey).toBe(base.idempotencyKey);
  });

  it("drops a goal that isn't valid for the newly selected content type", () => {
    const base = { ...createPromotionDraft(), goal: "more_music_plays" };
    // more_music_plays is valid for reels but not posts.
    const next = withSelectedContent(base, selection({ contentType: "post" }));
    expect(next.goal).toBe("");
  });

  it("keeps a goal that stays valid for the new content type", () => {
    const base = { ...createPromotionDraft(), goal: "more_views" };
    const next = withSelectedContent(base, selection({ contentType: "reel" }));
    expect(next.goal).toBe("more_views");
  });
});

describe("validatePromotionStep", () => {
  it("requires selected content", () => {
    const issues = validatePromotionStep("content", createPromotionDraft());
    expect(promotionDraftIssueFor(issues, "content")).toBeTruthy();
  });

  it("requires a goal", () => {
    const draft = withSelectedContent(createPromotionDraft(), selection());
    expect(promotionDraftIssueFor(validatePromotionStep("goal", draft), "goal")).toBeTruthy();
  });

  it("rejects a goal that isn't offered for the content type", () => {
    const draft = { ...withSelectedContent(createPromotionDraft(), selection({ contentType: "post" })), goal: "more_music_plays" };
    expect(promotionDraftIssueFor(validatePromotionStep("goal", draft), "goal")).toBeTruthy();
  });

  it("enforces the minimum budget", () => {
    const draft = validDraft({ budgetAmount: "1" }); // $1.00 < $5.00 min
    expect(promotionDraftIssueFor(validatePromotionStep("budget", draft), "budget")).toBeTruthy();
  });

  it("enforces the maximum budget", () => {
    const draft = validDraft({ budgetAmount: "6000" }); // $6,000 > $5,000 max
    expect(promotionDraftIssueFor(validatePromotionStep("budget", draft), "budget")).toBeTruthy();
  });

  it("accepts a budget inside the bounds", () => {
    expect(validatePromotionStep("budget", validDraft({ budgetAmount: "25" }))).toHaveLength(0);
  });

  it("enforces the duration bounds", () => {
    expect(promotionDraftIssueFor(validatePromotionStep("duration", validDraft({ durationDays: 0 })), "duration")).toBeTruthy();
    expect(
      promotionDraftIssueFor(validatePromotionStep("duration", validDraft({ durationDays: 31 })), "duration")
    ).toBeTruthy();
  });

  it("rejects an end date before the start date", () => {
    const draft = validDraft({ startDate: "2026-08-20", endDate: "2026-08-10" });
    expect(promotionDraftIssueFor(validatePromotionStep("duration", draft), "endDate")).toBeTruthy();
  });

  it("passes every step for a complete draft (review runs them all)", () => {
    expect(validatePromotionStep("review", validDraft())).toHaveLength(0);
    expect(canAdvanceFrom("review", validDraft())).toBe(true);
  });

  it("review surfaces the missing content when nothing is selected", () => {
    const issues = validatePromotionStep("review", createPromotionDraft());
    expect(promotionDraftIssueFor(issues, "content")).toBeTruthy();
  });
});

describe("parsePromotionBudgetCents", () => {
  it("converts dollars to whole cents", () => {
    expect(parsePromotionBudgetCents("25")).toBe(2500);
    expect(parsePromotionBudgetCents("25.50")).toBe(2550);
  });

  it("returns 0 for unusable input", () => {
    expect(parsePromotionBudgetCents("")).toBe(0);
    expect(parsePromotionBudgetCents("abc")).toBe(0);
    expect(parsePromotionBudgetCents("-5")).toBe(0);
  });
});

describe("step order helpers", () => {
  it("walks the steps forward and back", () => {
    expect(nextPromotionStep("content")).toBe("goal");
    expect(nextPromotionStep("review")).toBeNull();
    expect(previousPromotionStep("content")).toBeNull();
    expect(previousPromotionStep("goal")).toBe("content");
  });

  it("exposes the canonical six-step order", () => {
    expect(PROMOTION_WIZARD_STEPS).toEqual(["content", "goal", "audience", "budget", "duration", "review"]);
  });
});

describe("normalizePromotionDraft", () => {
  it("falls back to a fresh draft for junk input", () => {
    const normalized = normalizePromotionDraft(null);
    expect(normalized.step).toBe("content");
    expect(normalized.content).toBeNull();
  });

  it("clamps an out-of-range duration into bounds", () => {
    expect(normalizePromotionDraft({ durationDays: 999 } as Partial<PromotionDraft>).durationDays).toBe(
      PROMOTION_MAX_DURATION_DAYS
    );
    expect(normalizePromotionDraft({ durationDays: -3 } as Partial<PromotionDraft>).durationDays).toBe(1);
  });

  it("resets the step to content when the stored draft has no content", () => {
    const normalized = normalizePromotionDraft({ step: "budget", content: null } as Partial<PromotionDraft>);
    expect(normalized.step).toBe("content");
  });

  it("preserves a stored idempotency key", () => {
    const normalized = normalizePromotionDraft({ idempotencyKey: "promo-keep-me" } as Partial<PromotionDraft>);
    expect(normalized.idempotencyKey).toBe("promo-keep-me");
  });
});

describe("promotionDraftHasContent", () => {
  it("is false for an untouched draft and true once something is entered", () => {
    expect(promotionDraftHasContent(createPromotionDraft())).toBe(false);
    expect(promotionDraftHasContent(withSelectedContent(createPromotionDraft(), selection()))).toBe(true);
    expect(promotionDraftHasContent({ ...createPromotionDraft(), budgetAmount: "10" })).toBe(true);
  });
});

describe("promotionGoalsForContent", () => {
  it("offers content-appropriate goals", () => {
    (["post", "reel", "live_replay"] as PromotableContentType[]).forEach((type) => {
      expect(promotionGoalsForContent(type)).toContain("more_views");
    });
    expect(promotionGoalsForContent("reel")).toContain("more_music_plays");
    expect(promotionGoalsForContent("post")).not.toContain("more_music_plays");
  });
});

describe("buildCreatePromotionInput", () => {
  it("assembles the create input with the draft's idempotency key", () => {
    const draft = validDraft({ budgetType: "total", budgetAmount: "25", durationDays: 10 });
    const input = buildCreatePromotionInput(draft, { launch: true });
    expect(input).toMatchObject({
      contentType: "post",
      contentId: 42,
      goal: "more_views",
      budgetType: "total",
      budgetCents: 2500,
      durationDays: 10,
      launch: true,
      idempotencyKey: draft.idempotencyKey
    });
  });

  it("includes optional start/end dates only when present", () => {
    const without = buildCreatePromotionInput(validDraft(), { launch: false });
    expect(without.startDate).toBeUndefined();
    expect(without.endDate).toBeUndefined();
    const withDates = buildCreatePromotionInput(validDraft({ startDate: "2026-09-01", endDate: "2026-09-08" }), {
      launch: false
    });
    expect(withDates.startDate).toBe("2026-09-01");
    expect(withDates.endDate).toBe("2026-09-08");
  });

  it("throws when there is no content to promote", () => {
    expect(() => buildCreatePromotionInput(createPromotionDraft(), { launch: true })).toThrow();
  });

  it("keeps the assembled budget within the server bounds", () => {
    const input = buildCreatePromotionInput(validDraft({ budgetAmount: "25" }), { launch: true });
    expect(input.budgetCents).toBeGreaterThanOrEqual(PROMOTION_MIN_BUDGET_CENTS);
    expect(input.budgetCents).toBeLessThanOrEqual(PROMOTION_MAX_BUDGET_CENTS);
  });
});
