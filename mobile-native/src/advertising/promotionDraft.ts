/**
 * The "Promote your content" draft: shape, defaults, per-step validation and
 * the `POST /api/promotions` payload assembly.
 *
 * Pure — no React, no storage, no network — mirroring `campaignDraft.ts` so the
 * whole promotion-creation contract is unit-testable without rendering the
 * wizard. The screen only moves data between this module and the promotions
 * client (`src/api/promotions.ts`).
 *
 * A promotion always references an already-published content object (post /
 * reel / finalized live replay). There is no creative authoring here: the ad
 * *is* the existing content. The wizard only collects goal, budget and
 * duration; audience is automatic-only until privacy-safe targeting exists.
 *
 * The idempotency key is minted once, at draft creation, and survives
 * normalization — a Submit retry after a network failure reuses the same key so
 * the server dedupes instead of creating a second campaign.
 *
 * Budget/duration bounds mirror the server validator in
 * `services/pulsesoc_promotions.py` (MIN_BUDGET_CENTS / MAX_BUDGET_CENTS /
 * MAX_DURATION_DAYS). The server is still authoritative; these client checks
 * exist to keep the user out of a round-trip they'd only fail.
 */

import type { CreatePromotionInput, PromotableContentType } from "../api/promotions";

export type PromotionWizardStep = "content" | "goal" | "audience" | "budget" | "duration" | "review";

export const PROMOTION_WIZARD_STEPS: PromotionWizardStep[] = [
  "content",
  "goal",
  "audience",
  "budget",
  "duration",
  "review"
];

// Bounds mirror pulsesoc_promotions.py.
export const PROMOTION_MIN_BUDGET_CENTS = 500;
export const PROMOTION_MAX_BUDGET_CENTS = 500_000;
export const PROMOTION_MIN_DURATION_DAYS = 1;
export const PROMOTION_MAX_DURATION_DAYS = 30;

/** Display labels for every promotion goal the server can enable. */
export const PROMOTION_GOAL_LABELS: Record<string, string> = {
  more_views: "More Views",
  more_followers: "More Followers",
  more_profile_visits: "More Profile Visits",
  more_website_clicks: "More Website Clicks",
  more_messages: "More Messages",
  more_marketplace_visits: "More Marketplace Visits",
  more_music_plays: "More Music Plays",
  more_engagement: "More Engagement",
  more_event_responses: "More Event Responses",
  more_product_sales: "More Product Sales",
  more_community_joins: "More Community Joins"
};

/**
 * Goals the three promotable content types support, mirrored from
 * `GOALS_BY_CONTENT` so the wizard can offer sensible choices before the
 * eligibility round-trip. The server's `eligibility.goals` (with enabled flags)
 * remains the source of truth once a content object is selected.
 */
export const PROMOTION_GOALS_BY_CONTENT: Record<PromotableContentType, string[]> = {
  post: ["more_views", "more_followers", "more_profile_visits", "more_engagement", "more_messages"],
  reel: ["more_views", "more_followers", "more_engagement", "more_music_plays"],
  live_replay: ["more_views", "more_followers", "more_engagement"]
};

export function promotionGoalLabel(goal: string): string {
  return PROMOTION_GOAL_LABELS[goal] || "Promote";
}

export function promotionGoalsForContent(contentType: PromotableContentType): string[] {
  return PROMOTION_GOALS_BY_CONTENT[contentType] || PROMOTION_GOALS_BY_CONTENT.post;
}

/* ------------------------------------------------------------------ *
 * Draft shape
 * ------------------------------------------------------------------ */

export type PromotionContentSelection = {
  contentType: PromotableContentType;
  contentId: number;
  title: string;
  thumbnailUrl: string;
  mediaKind: string;
};

export type PromotionDraft = {
  version: 1;
  step: PromotionWizardStep;
  /** Minted at creation, persisted, reused on Submit retry. */
  idempotencyKey: string;
  updatedAt: string;
  /** Empty until the user picks content on the Content step. */
  content: PromotionContentSelection | null;
  goal: string;
  /** Only "automatic" is enabled server-side; kept explicit for forward-compat. */
  audienceType: "automatic";
  budgetType: "daily" | "total";
  /** USD amount as typed, e.g. "25" or "25.50". Converted to cents at build. */
  budgetAmount: string;
  durationDays: number;
  /** YYYY-MM-DD, optional. The server defaults start=today, end=start+days-1. */
  startDate: string;
  endDate: string;
};

export function createPromotionIdempotencyKey(): string {
  return `promo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}-${Math.random()
    .toString(36)
    .slice(2, 10)}`;
}

export function createPromotionDraft(): PromotionDraft {
  return {
    version: 1,
    step: "content",
    idempotencyKey: createPromotionIdempotencyKey(),
    updatedAt: new Date().toISOString(),
    content: null,
    goal: "",
    audienceType: "automatic",
    budgetType: "total",
    budgetAmount: "",
    durationDays: 7,
    startDate: "",
    endDate: ""
  };
}

const CONTENT_TYPES: PromotableContentType[] = ["post", "reel", "live_replay"];

function isPromotableContentType(value: unknown): value is PromotableContentType {
  return typeof value === "string" && (CONTENT_TYPES as string[]).includes(value);
}

function toPositiveInt(value: unknown, fallback = 0): number {
  const n = Math.floor(Number(value));
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/** Pre-fill a fresh draft from a picked content item; keeps the same idempotency key. */
export function withSelectedContent(draft: PromotionDraft, selection: PromotionContentSelection): PromotionDraft {
  const contentType = isPromotableContentType(selection.contentType) ? selection.contentType : "post";
  const goals = promotionGoalsForContent(contentType);
  // Keep the current goal only if it's valid for the new content type.
  const goal = goals.includes(draft.goal) ? draft.goal : "";
  return {
    ...draft,
    content: {
      contentType,
      contentId: toPositiveInt(selection.contentId),
      title: String(selection.title || ""),
      thumbnailUrl: String(selection.thumbnailUrl || ""),
      mediaKind: String(selection.mediaKind || contentType)
    },
    goal,
    updatedAt: new Date().toISOString()
  };
}

/**
 * Defensive merge over a possibly stale or truncated persisted draft. Absent or
 * malformed branches fall back to defaults; the idempotency key survives
 * verbatim when present.
 */
export function normalizePromotionDraft(value: Partial<PromotionDraft> | null | undefined): PromotionDraft {
  const base = createPromotionDraft();
  if (!value || typeof value !== "object") return base;
  const step: PromotionWizardStep = PROMOTION_WIZARD_STEPS.includes(value.step as PromotionWizardStep)
    ? (value.step as PromotionWizardStep)
    : "content";
  let content: PromotionContentSelection | null = null;
  if (value.content && typeof value.content === "object") {
    const c = value.content as Partial<PromotionContentSelection>;
    const contentType = isPromotableContentType(c.contentType) ? c.contentType : "post";
    const contentId = toPositiveInt(c.contentId);
    if (contentId > 0) {
      content = {
        contentType,
        contentId,
        title: String(c.title || ""),
        thumbnailUrl: String(c.thumbnailUrl || ""),
        mediaKind: String(c.mediaKind || contentType)
      };
    }
  }
  const goals = content ? promotionGoalsForContent(content.contentType) : Object.keys(PROMOTION_GOAL_LABELS);
  const goal = typeof value.goal === "string" && goals.includes(value.goal) ? value.goal : "";
  const durationDays = clampDuration(value.durationDays, base.durationDays);
  return {
    ...base,
    step: content ? step : "content",
    idempotencyKey:
      typeof value.idempotencyKey === "string" && value.idempotencyKey.trim()
        ? value.idempotencyKey.trim()
        : base.idempotencyKey,
    updatedAt: String(value.updatedAt || base.updatedAt),
    content,
    goal,
    audienceType: "automatic",
    budgetType: value.budgetType === "daily" ? "daily" : "total",
    budgetAmount: String(value.budgetAmount || ""),
    durationDays,
    startDate: String(value.startDate || ""),
    endDate: String(value.endDate || "")
  };
}

function clampDuration(value: unknown, fallback: number): number {
  const n = Math.floor(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(PROMOTION_MAX_DURATION_DAYS, Math.max(PROMOTION_MIN_DURATION_DAYS, n));
}

/** True when the draft carries anything a user would mind losing. */
export function promotionDraftHasContent(draft: PromotionDraft): boolean {
  return Boolean(draft.content || draft.goal || draft.budgetAmount.trim());
}

/* ------------------------------------------------------------------ *
 * Validation
 * ------------------------------------------------------------------ */

export type PromotionDraftIssue = { field: string; message: string };

const ISSUE = (field: string, message: string): PromotionDraftIssue => ({ field, message });
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Parse a typed USD amount into whole cents. Returns 0 on anything unusable. */
export function parsePromotionBudgetCents(amount: string): number {
  const value = Number(String(amount || "").trim());
  if (!Number.isFinite(value) || value <= 0) return 0;
  return Math.round(value * 100);
}

/** Validates ONE wizard step. `review` runs every step's checks. */
export function validatePromotionStep(step: PromotionWizardStep, draft: PromotionDraft): PromotionDraftIssue[] {
  if (step === "review") {
    return [
      ...validatePromotionStep("content", draft),
      ...validatePromotionStep("goal", draft),
      ...validatePromotionStep("audience", draft),
      ...validatePromotionStep("budget", draft),
      ...validatePromotionStep("duration", draft)
    ];
  }
  const issues: PromotionDraftIssue[] = [];
  switch (step) {
    case "content": {
      if (!draft.content || draft.content.contentId <= 0) {
        issues.push(ISSUE("content", "Choose something you've already posted to promote."));
      }
      break;
    }
    case "goal": {
      if (!draft.goal) {
        issues.push(ISSUE("goal", "Pick a goal for this promotion."));
      } else if (draft.content && !promotionGoalsForContent(draft.content.contentType).includes(draft.goal)) {
        issues.push(ISSUE("goal", "That goal isn't available for this content."));
      }
      break;
    }
    case "audience": {
      if (draft.audienceType !== "automatic") {
        issues.push(ISSUE("audience", "Only Automatic Audience is available right now."));
      }
      break;
    }
    case "budget": {
      const cents = parsePromotionBudgetCents(draft.budgetAmount);
      if (cents <= 0) {
        issues.push(ISSUE("budget", "Enter a budget."));
      } else if (cents < PROMOTION_MIN_BUDGET_CENTS) {
        issues.push(ISSUE("budget", "Budget must be at least $5.00."));
      } else if (cents > PROMOTION_MAX_BUDGET_CENTS) {
        issues.push(ISSUE("budget", "Budget can't exceed $5,000.00."));
      }
      break;
    }
    case "duration": {
      if (
        !Number.isFinite(draft.durationDays) ||
        draft.durationDays < PROMOTION_MIN_DURATION_DAYS ||
        draft.durationDays > PROMOTION_MAX_DURATION_DAYS
      ) {
        issues.push(ISSUE("duration", "Duration must be between 1 and 30 days."));
      }
      if (draft.startDate.trim() && !DATE_RE.test(draft.startDate.trim())) {
        issues.push(ISSUE("startDate", "Use a valid start date."));
      }
      if (draft.endDate.trim()) {
        if (!DATE_RE.test(draft.endDate.trim())) {
          issues.push(ISSUE("endDate", "Use a valid end date."));
        } else if (
          DATE_RE.test(draft.startDate.trim()) &&
          draft.endDate.trim() < draft.startDate.trim()
        ) {
          issues.push(ISSUE("endDate", "End date can't be before the start date."));
        }
      }
      break;
    }
  }
  return issues;
}

export function promotionDraftIssueFor(issues: PromotionDraftIssue[], field: string): string {
  return issues.find((issue) => issue.field === field)?.message || "";
}

/** True when every step up to and including `step` validates. */
export function canAdvanceFrom(step: PromotionWizardStep, draft: PromotionDraft): boolean {
  return validatePromotionStep(step, draft).length === 0;
}

export function nextPromotionStep(step: PromotionWizardStep): PromotionWizardStep | null {
  const idx = PROMOTION_WIZARD_STEPS.indexOf(step);
  return idx >= 0 && idx < PROMOTION_WIZARD_STEPS.length - 1 ? PROMOTION_WIZARD_STEPS[idx + 1] : null;
}

export function previousPromotionStep(step: PromotionWizardStep): PromotionWizardStep | null {
  const idx = PROMOTION_WIZARD_STEPS.indexOf(step);
  return idx > 0 ? PROMOTION_WIZARD_STEPS[idx - 1] : null;
}

/* ------------------------------------------------------------------ *
 * Payload assembly
 * ------------------------------------------------------------------ */

/**
 * Build the `createPromotion` input from a fully-validated draft. `launch: true`
 * means submit for review immediately (DRAFT → pending_review); the idempotency
 * key guards double-submit.
 */
export function buildCreatePromotionInput(draft: PromotionDraft, options: { launch: boolean }): CreatePromotionInput {
  if (!draft.content) {
    throw new Error("Cannot build a promotion without selected content.");
  }
  const input: CreatePromotionInput = {
    contentType: draft.content.contentType,
    contentId: draft.content.contentId,
    goal: draft.goal,
    budgetType: draft.budgetType,
    budgetCents: parsePromotionBudgetCents(draft.budgetAmount),
    durationDays: draft.durationDays,
    launch: options.launch,
    idempotencyKey: draft.idempotencyKey
  };
  if (draft.startDate.trim()) input.startDate = draft.startDate.trim();
  if (draft.endDate.trim()) input.endDate = draft.endDate.trim();
  return input;
}
