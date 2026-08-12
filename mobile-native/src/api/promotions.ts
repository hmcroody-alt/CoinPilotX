/**
 * Promotions client — "Promote your content" (Post Ads).
 *
 * Thin, typed wrapper over the canonical `/api/promotions/*` surface backed by
 * `services/pulsesoc_promotions.py`. That backend is the single production ad
 * engine + single ad wallet: a promotion references an already-published
 * content object (post / reel / finalized live replay) — it never duplicates or
 * reposts the organic content, and it launches a `promoted_content` campaign
 * against the same wallet Marketplace Ads uses.
 *
 * Everything here is server-authoritative. Eligibility, payment routing,
 * review state, dedupe/idempotency and source-integrity revocation are decided
 * by the server; the client only renders what the server returns and forwards
 * the user's choices. The mapper functions below are pure (snake_case →
 * camelCase, server status → UI state, page merge) so they can be unit tested
 * without a network.
 */

import { pulseApi } from "./pulseApi";

export type PromotableContentType = "post" | "reel" | "live_replay";
export type PromotableFilter = "all" | "post" | "reel" | "live_replay";

/**
 * Server eligibility verdict for a single content object. The server owns this
 * enum; the client must not invent additional states or override a verdict.
 */
export type PromotableEligibility =
  | "PROMOTABLE"
  | "ACTIVE_PROMOTION"
  | "UNDER_REVIEW"
  | "PRIVATE"
  | "REPLAY_PROCESSING"
  | "PROCESSING"
  | "MODERATION_BLOCKED"
  | "NOT_ELIGIBLE";

export type PromotableContentItem = {
  contentType: PromotableContentType;
  contentId: number;
  title: string;
  snippet: string;
  mediaKind: string;
  thumbnailUrl: string;
  durationSeconds: number | null;
  createdAt: string;
  eligibility: PromotableEligibility;
  eligibilityReason: string;
  /** Server-decided: true only when the content can start a new promotion now. */
  promotable: boolean;
};

export type PromotableContentPage = {
  items: PromotableContentItem[];
  filter: PromotableFilter;
  limit: number;
  offset: number;
  nextOffset: number;
  hasMore: boolean;
};

/**
 * Promotion lifecycle as the UI narrates it. This is a projection of the raw
 * server status — the mission requires truthful status, so a promotion that is
 * merely submitted must never read as "Active"/"Delivering".
 *
 *   server status   → UI state
 *   draft           → draft
 *   pending_review  → in_review   (Submitted → In review)
 *   promoting       → delivering
 *   paused          → paused
 *   canceled        → ended
 *   failed          → rejected
 */
export type PromotionStatus =
  | "draft"
  | "in_review"
  | "delivering"
  | "paused"
  | "ended"
  | "rejected"
  | "unknown";

export type PromotionStatusTone = "neutral" | "pending" | "positive" | "warning" | "negative";

export type Promotion = {
  promotionId: number;
  contentType: string;
  contentId: number;
  goal: string;
  audienceType: string;
  budgetType: string;
  dailyBudgetCents: number;
  totalBudgetCents: number;
  dailyBudgetLabel: string;
  totalBudgetLabel: string;
  startDate: string;
  endDate: string;
  durationDays: number;
  /** Raw server status, preserved for callers that need the exact value. */
  rawStatus: string;
  status: PromotionStatus;
  policyStatus: string;
  billingStatus: string;
  adCampaignId: number | null;
  analyticsAvailable: boolean;
  createdAt: string;
  updatedAt: string;
};

export type PromotionGoalOption = {
  key: string;
  label: string;
  enabled: boolean;
  reason: string;
};

export type PromotionAudienceOption = {
  key: string;
  label: string;
  enabled: boolean;
  reason: string;
};

export type PromotionEligibility = {
  eligible: boolean;
  reason: string;
  content: {
    contentType: string;
    contentId: number;
    title: string;
    destinationUrl: string;
  };
  goals: PromotionGoalOption[];
  audiences: PromotionAudienceOption[];
  /** Null when no approved forecasting provider is configured — never fabricate. */
  estimatedReach: number | null;
  forecastingState: string;
  forecastingMessage: string;
  billing: Record<string, unknown>;
};

export type PromotionMetrics = {
  spendCents: number;
  viewsFromPromotion: number;
  clicks: number;
  costPerResultCents: number | null;
  status: string;
  startDate: string;
  endDate: string;
  /** Metrics the server explicitly cannot supply yet; render as unavailable, not zero. */
  unavailable: string[];
};

export type PromotionAnalytics = {
  available: boolean;
  promotionId: number;
  message: string;
  metrics: PromotionMetrics | null;
};

export type CreatePromotionInput = {
  contentType: PromotableContentType;
  contentId: number;
  goal: string;
  budgetType: "daily" | "total";
  budgetCents: number;
  durationDays: number;
  startDate?: string;
  endDate?: string;
  /** When true the server submits for review immediately; otherwise a draft is saved. */
  launch?: boolean;
  /** Client-supplied dedupe key. Double-tap Submit must not create two campaigns. */
  idempotencyKey?: string;
};

export type CreatePromotionResult = {
  promotion: Promotion;
  message: string;
};

// --------------------------------------------------------------------------- //
// Pure mappers — no network, unit-testable.
// --------------------------------------------------------------------------- //

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function toInt(value: unknown, fallback = 0): number {
  const n = typeof value === "number" ? value : parseInt(String(value ?? ""), 10);
  return Number.isFinite(n) ? n : fallback;
}

function toStr(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : value == null ? fallback : String(value);
}

function toNumberOrNull(value: unknown): number | null {
  if (value == null) return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

const PROMOTABLE_CONTENT_TYPES: PromotableContentType[] = ["post", "reel", "live_replay"];
const PROMOTABLE_ELIGIBILITY: PromotableEligibility[] = [
  "PROMOTABLE",
  "ACTIVE_PROMOTION",
  "UNDER_REVIEW",
  "PRIVATE",
  "REPLAY_PROCESSING",
  "PROCESSING",
  "MODERATION_BLOCKED",
  "NOT_ELIGIBLE"
];

function normalizeContentType(value: unknown): PromotableContentType {
  const raw = toStr(value).toLowerCase();
  return (PROMOTABLE_CONTENT_TYPES as string[]).includes(raw) ? (raw as PromotableContentType) : "post";
}

function normalizeEligibility(value: unknown): PromotableEligibility {
  const raw = toStr(value).toUpperCase();
  return (PROMOTABLE_ELIGIBILITY as string[]).includes(raw) ? (raw as PromotableEligibility) : "NOT_ELIGIBLE";
}

export function mapPromotableItem(raw: unknown): PromotableContentItem {
  const row = asRecord(raw);
  const eligibility = normalizeEligibility(row.eligibility);
  return {
    contentType: normalizeContentType(row.content_type),
    contentId: toInt(row.content_id),
    title: toStr(row.title),
    snippet: toStr(row.snippet),
    mediaKind: toStr(row.media_kind),
    thumbnailUrl: toStr(row.thumbnail_url),
    durationSeconds: toNumberOrNull(row.duration_seconds),
    createdAt: toStr(row.created_at),
    eligibility,
    eligibilityReason: toStr(row.eligibility_reason),
    // Trust the server's boolean, but keep it consistent with the verdict.
    promotable: row.promotable === true && eligibility === "PROMOTABLE"
  };
}

export function mapPromotableContentPage(raw: unknown): PromotableContentPage {
  const body = asRecord(raw);
  const items = Array.isArray(body.items) ? body.items.map(mapPromotableItem) : [];
  const filterRaw = toStr(body.filter, "all").toLowerCase();
  const filter = (["all", "post", "reel", "live_replay"] as string[]).includes(filterRaw)
    ? (filterRaw as PromotableFilter)
    : "all";
  const limit = toInt(body.limit, items.length);
  const offset = toInt(body.offset, 0);
  return {
    items,
    filter,
    limit,
    offset,
    nextOffset: toInt(body.next_offset, offset + items.length),
    hasMore: body.has_more === true
  };
}

/** Merge a freshly-fetched page onto an existing list for "load more". Dedupes by content identity. */
export function appendPromotablePage(
  existing: PromotableContentItem[],
  page: PromotableContentPage
): PromotableContentItem[] {
  if (page.offset === 0) return page.items;
  const seen = new Set(existing.map((item) => `${item.contentType}:${item.contentId}`));
  const merged = existing.slice();
  for (const item of page.items) {
    const key = `${item.contentType}:${item.contentId}`;
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(item);
    }
  }
  return merged;
}

export function mapPromotionStatus(rawStatus: unknown): PromotionStatus {
  switch (toStr(rawStatus).toLowerCase()) {
    case "draft":
      return "draft";
    case "pending_review":
      return "in_review";
    case "promoting":
      return "delivering";
    case "paused":
      return "paused";
    case "canceled":
    case "cancelled":
      return "ended";
    case "failed":
      return "rejected";
    default:
      return "unknown";
  }
}

const STATUS_LABELS: Record<PromotionStatus, string> = {
  draft: "Draft",
  in_review: "In review",
  delivering: "Delivering",
  paused: "Paused",
  ended: "Ended",
  rejected: "Rejected",
  unknown: "Unknown"
};

const STATUS_TONES: Record<PromotionStatus, PromotionStatusTone> = {
  draft: "neutral",
  in_review: "pending",
  delivering: "positive",
  paused: "warning",
  ended: "neutral",
  rejected: "negative",
  unknown: "neutral"
};

export function promotionStatusLabel(status: PromotionStatus): string {
  return STATUS_LABELS[status] ?? STATUS_LABELS.unknown;
}

export function promotionStatusTone(status: PromotionStatus): PromotionStatusTone {
  return STATUS_TONES[status] ?? "neutral";
}

export function mapPromotion(raw: unknown): Promotion {
  const row = asRecord(raw);
  const rawStatus = toStr(row.status);
  return {
    promotionId: toInt(row.promotion_id ?? row.id),
    contentType: toStr(row.content_type),
    contentId: toInt(row.content_id),
    goal: toStr(row.goal),
    audienceType: toStr(row.audience_type),
    budgetType: toStr(row.budget_type),
    dailyBudgetCents: toInt(row.daily_budget_cents),
    totalBudgetCents: toInt(row.total_budget_cents),
    dailyBudgetLabel: toStr(row.daily_budget),
    totalBudgetLabel: toStr(row.total_budget),
    startDate: toStr(row.start_date),
    endDate: toStr(row.end_date),
    durationDays: toInt(row.duration_days),
    rawStatus,
    status: mapPromotionStatus(rawStatus),
    policyStatus: toStr(row.policy_status),
    billingStatus: toStr(row.billing_status),
    adCampaignId: toNumberOrNull(row.ad_campaign_id),
    analyticsAvailable: row.analytics_available === true,
    createdAt: toStr(row.created_at),
    updatedAt: toStr(row.updated_at)
  };
}

function mapGoalOption(raw: unknown): PromotionGoalOption {
  const row = asRecord(raw);
  return {
    key: toStr(row.key),
    label: toStr(row.label),
    enabled: row.enabled === true,
    reason: toStr(row.reason)
  };
}

function mapAudienceOption(raw: unknown): PromotionAudienceOption {
  const row = asRecord(raw);
  return {
    key: toStr(row.key),
    label: toStr(row.label),
    enabled: row.enabled === true,
    reason: toStr(row.reason)
  };
}

export function mapPromotionEligibility(raw: unknown): PromotionEligibility {
  const body = asRecord(raw);
  const content = asRecord(body.content);
  return {
    eligible: body.eligible === true,
    reason: toStr(body.reason),
    content: {
      contentType: toStr(content.content_type),
      contentId: toInt(content.content_id),
      title: toStr(content.title),
      destinationUrl: toStr(content.destination_url)
    },
    goals: Array.isArray(body.goals) ? body.goals.map(mapGoalOption) : [],
    audiences: Array.isArray(body.audiences) ? body.audiences.map(mapAudienceOption) : [],
    estimatedReach: toNumberOrNull(body.estimated_reach),
    forecastingState: toStr(body.forecasting_state, "unavailable"),
    forecastingMessage: toStr(body.forecasting_message),
    billing: asRecord(body.billing)
  };
}

export function mapPromotionAnalytics(raw: unknown): PromotionAnalytics {
  const body = asRecord(raw);
  const promotionId = toInt(body.promotion_id);
  if (body.available !== true || !body.metrics) {
    return {
      available: false,
      promotionId,
      message: toStr(body.message, "Promotion analytics are not available yet."),
      metrics: null
    };
  }
  const metrics = asRecord(body.metrics);
  return {
    available: true,
    promotionId,
    message: "",
    metrics: {
      spendCents: toInt(metrics.spend_cents),
      viewsFromPromotion: toInt(metrics.views_from_promotion),
      clicks: toInt(metrics.clicks),
      costPerResultCents: toNumberOrNull(metrics.cost_per_result_cents),
      status: toStr(metrics.status),
      startDate: toStr(metrics.start_date),
      endDate: toStr(metrics.end_date),
      unavailable: Array.isArray(body.unavailable_metrics) ? body.unavailable_metrics.map((v) => toStr(v)) : []
    }
  };
}

/** Build the create payload in the exact nested shape the server validator accepts. */
export function buildCreatePromotionBody(input: CreatePromotionInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    content_type: input.contentType,
    content_id: input.contentId,
    goal: input.goal,
    audience: { type: "automatic" },
    budget: { type: input.budgetType, amount_cents: Math.round(input.budgetCents) },
    duration: { days: input.durationDays },
    launch: input.launch === true
  };
  const duration = body.duration as Record<string, unknown>;
  if (input.startDate) duration.start_date = input.startDate;
  if (input.endDate) duration.end_date = input.endDate;
  if (input.idempotencyKey) body.idempotency_key = input.idempotencyKey;
  return body;
}

// --------------------------------------------------------------------------- //
// Network calls
// --------------------------------------------------------------------------- //

function encode(value: string | number): string {
  return encodeURIComponent(String(value));
}

/** Owner's promotable content, one page. Server decides eligibility per item. */
export async function listPromotableContent(
  params: { filter?: PromotableFilter; limit?: number; offset?: number } = {}
): Promise<PromotableContentPage> {
  const search = new URLSearchParams();
  search.set("type", params.filter || "all");
  search.set("limit", String(params.limit ?? 20));
  search.set("offset", String(params.offset ?? 0));
  const raw = await pulseApi<unknown>(`/api/promotions/content?${search.toString()}`);
  return mapPromotableContentPage(raw);
}

/** Server eligibility + goal/audience/billing readiness for one content object. */
export async function getPromotionEligibility(
  contentType: PromotableContentType,
  contentId: number
): Promise<PromotionEligibility> {
  const search = new URLSearchParams({ content_type: contentType, content_id: String(contentId) });
  const raw = await pulseApi<unknown>(`/api/promotions/eligibility?${search.toString()}`);
  return mapPromotionEligibility(raw);
}

/** All promotions for the owner (most recent first). */
export async function listPromotions(): Promise<Promotion[]> {
  const raw = await pulseApi<{ promotions?: unknown[] }>(`/api/promotions`);
  return Array.isArray(raw?.promotions) ? raw.promotions.map(mapPromotion) : [];
}

export async function getPromotion(promotionId: number): Promise<Promotion> {
  const raw = await pulseApi<{ promotion?: unknown }>(`/api/promotions/${encode(promotionId)}`);
  return mapPromotion(raw?.promotion);
}

/** Create (and optionally launch) a promotion. Idempotency key guards double-submit. */
export async function createPromotion(input: CreatePromotionInput): Promise<CreatePromotionResult> {
  const raw = await pulseApi<{ promotion?: unknown; message?: string }>(`/api/promotions`, {
    method: "POST",
    body: JSON.stringify(buildCreatePromotionBody(input))
  });
  return { promotion: mapPromotion(raw?.promotion), message: toStr(raw?.message) };
}

async function transitionPromotion(promotionId: number, action: "pause" | "resume" | "cancel"): Promise<Promotion> {
  const raw = await pulseApi<{ promotion?: unknown }>(`/api/promotions/${encode(promotionId)}/${action}`, {
    method: "POST"
  });
  return mapPromotion(raw?.promotion);
}

export function pausePromotion(promotionId: number): Promise<Promotion> {
  return transitionPromotion(promotionId, "pause");
}

export function resumePromotion(promotionId: number): Promise<Promotion> {
  return transitionPromotion(promotionId, "resume");
}

export function cancelPromotion(promotionId: number): Promise<Promotion> {
  return transitionPromotion(promotionId, "cancel");
}

export async function getPromotionAnalytics(promotionId: number): Promise<PromotionAnalytics> {
  const raw = await pulseApi<unknown>(`/api/promotions/${encode(promotionId)}/analytics`);
  return mapPromotionAnalytics(raw);
}
