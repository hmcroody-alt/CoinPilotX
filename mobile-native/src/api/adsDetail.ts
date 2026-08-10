/**
 * Ads OS management data layer — the campaign-detail contract.
 *
 * Binds the per-campaign management surfaces under `/api/pulse/ads`:
 * the campaign detail read (`GET /campaign/:id/detail`), ad set CRUD and
 * lifecycle actions, creative→ad-set assignment, the insights feed and its
 * approve-to-apply endpoint, and the server-side wizard drafts
 * (`POST /campaign/draft`, `GET /campaign/drafts`).
 *
 * Conventions follow `adsOs.ts` / `businessOs.ts`: `pulseApi` wrapper,
 * `normalize*` coercions, integer cents everywhere. Field names mirror the
 * backend builders (`services/pulse_ads_adsets.py::campaign_detail`,
 * `services/pulse_ads_insights.py::build_insights`,
 * `services/pulse_ads_os.py::save_campaign_draft`) — they are not guessed.
 *
 * Deliberately NOT bound: `GET /campaign/:id/adsets`. The detail endpoint
 * already returns the ad-set list with metrics, and the standalone list
 * handler has a serialization bug server-side; every mutation here returns
 * the affected ad set directly, so the list read is never needed.
 */
import { pulseApi } from "./pulseApi";
import type { AdTargetingPayload } from "./adsOs";
import { normalizeAdTargeting } from "./adsOs";

/* ------------------------------------------------------------------ *
 * Shared metric shapes
 * ------------------------------------------------------------------ */

export type AdEntityMetrics = {
  impressions: number;
  clicks: number;
  spend_cents: number;
  /** Fraction (0–1), not percent — mirrors the backend's `round(clicks/impr, 4)`. */
  ctr: number;
};

export function normalizeAdEntityMetrics(value?: Partial<AdEntityMetrics> | null): AdEntityMetrics {
  return {
    impressions: nonNegInt(value?.impressions),
    clicks: nonNegInt(value?.clicks),
    spend_cents: nonNegInt(value?.spend_cents),
    ctr: Math.max(0, Number(value?.ctr) || 0)
  };
}

export type AdDailyPoint = {
  /** YYYY-MM-DD, oldest first. */
  date: string;
  impressions: number;
  clicks: number;
  spend_cents: number;
};

export function normalizeAdDailySeries(value?: AdDailyPoint[] | null): AdDailyPoint[] {
  return (Array.isArray(value) ? value : [])
    .map((point) => ({
      date: String(point?.date || ""),
      impressions: nonNegInt(point?.impressions),
      clicks: nonNegInt(point?.clicks),
      spend_cents: nonNegInt(point?.spend_cents)
    }))
    .filter((point) => point.date.length > 0);
}

/* ------------------------------------------------------------------ *
 * Ad sets
 * ------------------------------------------------------------------ */

export type AdAdsetStatus = "active" | "paused" | "archived";

export type AdAdset = {
  id: number;
  campaign_id: number;
  ad_account_id: number;
  name: string;
  status: AdAdsetStatus;
  targeting: AdTargetingPayload;
  /** The default ad set can pause but never archive (server 409s). */
  is_default: boolean;
  created_at: string;
  updated_at: string;
  metrics: AdEntityMetrics;
};

export function normalizeAdAdset(value?: Partial<AdAdset> | null): AdAdset {
  const status: AdAdsetStatus =
    value?.status === "paused" || value?.status === "archived" ? value.status : "active";
  return {
    id: nonNegInt(value?.id),
    campaign_id: nonNegInt(value?.campaign_id),
    ad_account_id: nonNegInt(value?.ad_account_id),
    name: String(value?.name || ""),
    status,
    targeting: normalizeAdTargeting(value?.targeting),
    is_default: value?.is_default === true || Number(value?.is_default) === 1,
    created_at: String(value?.created_at || ""),
    updated_at: String(value?.updated_at || ""),
    metrics: normalizeAdEntityMetrics(value?.metrics)
  };
}

export type AdAdsetAction = "pause" | "resume" | "archive";

/**
 * Which lifecycle actions the server would accept for this ad set. Mirrors
 * `pulse_ads_adsets.adset_action`: pause needs `active`, resume needs
 * `paused`, archive needs a non-archived, non-default ad set.
 */
export function availableAdAdsetActions(adset?: Pick<AdAdset, "status" | "is_default"> | null): AdAdsetAction[] {
  if (!adset) return [];
  const actions: AdAdsetAction[] = [];
  if (adset.status === "active") actions.push("pause");
  if (adset.status === "paused") actions.push("resume");
  if (adset.status !== "archived" && !adset.is_default) actions.push("archive");
  return actions;
}

export async function createAdCampaignAdset(
  campaignId: number,
  payload: { name: string; status?: "active" | "paused"; targeting?: AdTargetingPayload }
) {
  const data = await pulseApi<{ ok?: boolean; adset?: Partial<AdAdset> }>(
    `/api/pulse/ads/campaign/${encodeURIComponent(String(campaignId))}/adsets`,
    { method: "POST", body: JSON.stringify(payload) }
  );
  return { ...data, adset: data.adset ? normalizeAdAdset(data.adset) : null };
}

export async function updateAdCampaignAdset(
  adsetId: number,
  payload: { name?: string; status?: "active" | "paused"; targeting?: AdTargetingPayload }
) {
  const data = await pulseApi<{ ok?: boolean; adset?: Partial<AdAdset> }>(
    `/api/pulse/ads/adset/${encodeURIComponent(String(adsetId))}/update`,
    { method: "POST", body: JSON.stringify(payload) }
  );
  return { ...data, adset: data.adset ? normalizeAdAdset(data.adset) : null };
}

/** Action responses carry a slim `{adset_id, status, action}` echo, not a full row. */
export async function runAdAdsetAction(adsetId: number, action: AdAdsetAction) {
  return pulseApi<{ ok?: boolean; adset?: { adset_id?: number; status?: string; action?: string } }>(
    `/api/pulse/ads/adset/${encodeURIComponent(String(adsetId))}/${action}`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

/** `adsetId` 0 assigns the creative back to the campaign's default ad set. */
export async function assignAdCreativeAdset(creativeId: number, adsetId: number) {
  return pulseApi<{ ok?: boolean; creative?: { creative_id?: number; adset_id?: number } }>(
    `/api/pulse/ads/creative/${encodeURIComponent(String(creativeId))}/assign-adset`,
    { method: "POST", body: JSON.stringify({ adset_id: adsetId }) }
  );
}

/* ------------------------------------------------------------------ *
 * Campaign detail
 * ------------------------------------------------------------------ */

export type AdDetailCreative = {
  id: number;
  campaign_id: number;
  adset_id: number;
  creative_type: string;
  title: string;
  headline: string;
  moderation_status: string;
  rejection_reason: string;
  media_url: string;
  media_ready: boolean;
  metrics: AdEntityMetrics;
};

export function normalizeAdDetailCreative(value?: Partial<AdDetailCreative> | null): AdDetailCreative {
  return {
    id: nonNegInt(value?.id),
    campaign_id: nonNegInt(value?.campaign_id),
    adset_id: nonNegInt(value?.adset_id),
    creative_type: String(value?.creative_type || "text"),
    title: String(value?.title || ""),
    headline: String(value?.headline || ""),
    moderation_status: String(value?.moderation_status || "draft"),
    rejection_reason: String(value?.rejection_reason || ""),
    media_url: String(value?.media_url || ""),
    media_ready: value?.media_ready !== false,
    metrics: normalizeAdEntityMetrics(value?.metrics)
  };
}

export type AdCampaignDetail = {
  campaign: {
    id: number;
    ad_account_id: number;
    campaign_name: string;
    status: string;
    /** Canonical objective (one of the 11). `objective_raw` keeps the stored value. */
    objective: string;
    objective_raw: string;
    draft_key: string;
    created_at: string;
    updated_at: string;
  };
  lifecycle: {
    status: string;
    can_edit: boolean;
    blocker: { code: string; message: string } | null;
  };
  budget: {
    budget_type: "daily" | "lifetime";
    daily_budget_cents: number;
    lifetime_budget_cents: number;
    spent_cents: number;
    remaining_cents: number;
  };
  schedule: { start_at: string; end_at: string };
  targeting: AdTargetingPayload;
  placements: string[];
  adsets: AdAdset[];
  creatives: AdDetailCreative[];
  totals: AdEntityMetrics;
  /** Seven entries, oldest first, ending today. */
  daily_series: AdDailyPoint[];
  /** Present only when the objective maps to a metric the system records. */
  estimated_results: { metric: string; value: number } | null;
};

export function normalizeAdCampaignDetail(value?: Partial<AdCampaignDetail> | null): AdCampaignDetail {
  const campaign = (value?.campaign || {}) as Partial<AdCampaignDetail["campaign"]>;
  const lifecycle = (value?.lifecycle || {}) as Partial<AdCampaignDetail["lifecycle"]>;
  const budget = (value?.budget || {}) as Partial<AdCampaignDetail["budget"]>;
  const schedule = (value?.schedule || {}) as Partial<AdCampaignDetail["schedule"]>;
  const blocker = lifecycle.blocker;
  const estimated = value?.estimated_results;
  return {
    campaign: {
      id: nonNegInt(campaign.id),
      ad_account_id: nonNegInt(campaign.ad_account_id),
      campaign_name: String(campaign.campaign_name || ""),
      status: String(campaign.status || "draft"),
      objective: String(campaign.objective || "awareness"),
      objective_raw: String(campaign.objective_raw || ""),
      draft_key: String(campaign.draft_key || ""),
      created_at: String(campaign.created_at || ""),
      updated_at: String(campaign.updated_at || "")
    },
    lifecycle: {
      status: String(lifecycle.status || campaign.status || "draft"),
      can_edit: lifecycle.can_edit === true,
      blocker:
        blocker && typeof blocker === "object"
          ? { code: String(blocker.code || ""), message: String(blocker.message || "") }
          : null
    },
    budget: {
      budget_type: budget.budget_type === "lifetime" ? "lifetime" : "daily",
      daily_budget_cents: nonNegInt(budget.daily_budget_cents),
      lifetime_budget_cents: nonNegInt(budget.lifetime_budget_cents),
      spent_cents: nonNegInt(budget.spent_cents),
      remaining_cents: nonNegInt(budget.remaining_cents)
    },
    schedule: {
      start_at: String(schedule.start_at || ""),
      end_at: String(schedule.end_at || "")
    },
    targeting: normalizeAdTargeting(value?.targeting),
    placements: (Array.isArray(value?.placements) ? value!.placements : [])
      .map((key) => String(key || "").trim())
      .filter(Boolean),
    adsets: (Array.isArray(value?.adsets) ? value!.adsets : []).map(normalizeAdAdset),
    creatives: (Array.isArray(value?.creatives) ? value!.creatives : []).map(normalizeAdDetailCreative),
    totals: normalizeAdEntityMetrics(value?.totals),
    daily_series: normalizeAdDailySeries(value?.daily_series),
    estimated_results:
      estimated && typeof estimated === "object" && estimated.metric
        ? { metric: String(estimated.metric), value: nonNegInt(estimated.value) }
        : null
  };
}

export async function getAdCampaignDetail(campaignId: number) {
  const data = await pulseApi<{ ok?: boolean; detail?: Partial<AdCampaignDetail> }>(
    `/api/pulse/ads/campaign/${encodeURIComponent(String(campaignId))}/detail`
  );
  return { ...data, detail: normalizeAdCampaignDetail(data.detail) };
}

/* ------------------------------------------------------------------ *
 * Insights — recommendations that require explicit approval
 * ------------------------------------------------------------------ */

export type AdInsightSeverity = "opportunity" | "warning";

export type AdInsight = {
  /** Stable id like "high_cpc:123" — replayed verbatim to the apply endpoint. */
  id: string;
  kind: string;
  severity: AdInsightSeverity;
  title: string;
  why: string;
  campaign_id: number;
  action: { type: string; params: Record<string, unknown> };
  /** Always true server-side; the client must never apply without approval. */
  requires_approval: boolean;
};

export function normalizeAdInsight(value?: Partial<AdInsight> | null): AdInsight {
  const action = (value?.action || {}) as Partial<AdInsight["action"]>;
  return {
    id: String(value?.id || ""),
    kind: String(value?.kind || ""),
    severity: value?.severity === "warning" ? "warning" : "opportunity",
    title: String(value?.title || ""),
    why: String(value?.why || ""),
    campaign_id: nonNegInt(value?.campaign_id),
    action: {
      type: String(action.type || ""),
      params: action.params && typeof action.params === "object" ? (action.params as Record<string, unknown>) : {}
    },
    requires_approval: value?.requires_approval !== false
  };
}

/** Filter the account feed down to one campaign, dropping malformed entries. */
export function adInsightsForCampaign(insights: AdInsight[] | null | undefined, campaignId: number): AdInsight[] {
  return (Array.isArray(insights) ? insights : []).filter(
    (insight) => insight.id.length > 0 && insight.campaign_id === campaignId
  );
}

export type AdInsightsResponse = {
  ok?: boolean;
  recommendations?: Partial<AdInsight>[];
  data_status?: { campaigns?: number; active_campaigns?: number; impressions?: number; clicks?: number; note?: string };
  generated_at?: string;
};

export async function listAdInsights(accountId: number) {
  const data = await pulseApi<AdInsightsResponse>(
    `/api/pulse/ads/insights?account_id=${encodeURIComponent(String(accountId))}`
  );
  return {
    ...data,
    recommendations: (Array.isArray(data.recommendations) ? data.recommendations : []).map(normalizeAdInsight)
  };
}

export type AdInsightApplyResult = {
  ok?: boolean;
  applied?: boolean;
  insight_id?: string;
  kind?: string;
  action?: { type?: string; params?: Record<string, unknown> };
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
  error?: string;
};

/**
 * Applies one recommendation. `approve: true` is the explicit consent bit the
 * server requires; callers must gather user confirmation first — never call
 * this from an automatic path. A stale insight returns 409.
 */
export async function applyAdInsight(accountId: number, insightId: string) {
  return pulseApi<AdInsightApplyResult>("/api/pulse/ads/insights/apply", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, insight_id: insightId, approve: true })
  });
}

/* ------------------------------------------------------------------ *
 * Server-side wizard drafts (autosave / resume)
 * ------------------------------------------------------------------ */

export type AdServerDraftPayload = {
  /** The wizard's idempotency key doubles as the draft key. */
  draft_key: string;
  ad_account_id: number;
  campaign?: Record<string, unknown>;
  targeting?: Partial<AdTargetingPayload>;
  creative?: Record<string, unknown>;
  placements?: string[];
};

export type AdServerDraft = {
  id: number;
  draft_key: string;
  ad_account_id: number;
  campaign_name: string;
  objective: string;
  budget_type: string;
  daily_budget_cents: number;
  lifetime_budget_cents: number;
  start_at: string;
  end_at: string;
  updated_at: string;
  creative_count: number;
  targeting: AdTargetingPayload | null;
  placements: string[];
};

export function normalizeAdServerDraft(value?: Record<string, unknown> | null): AdServerDraft {
  const source = (value || {}) as Partial<AdServerDraft> & Record<string, unknown>;
  return {
    id: nonNegInt(source.id),
    draft_key: String(source.draft_key || ""),
    ad_account_id: nonNegInt(source.ad_account_id),
    campaign_name: String(source.campaign_name || ""),
    objective: String((source as Record<string, unknown>).objective_canonical || source.objective || "awareness"),
    budget_type: String(source.budget_type || "daily"),
    daily_budget_cents: nonNegInt(source.daily_budget_cents),
    lifetime_budget_cents: nonNegInt(source.lifetime_budget_cents),
    start_at: String(source.start_at || ""),
    end_at: String(source.end_at || ""),
    updated_at: String(source.updated_at || ""),
    creative_count: nonNegInt(source.creative_count),
    targeting: source.targeting ? normalizeAdTargeting(source.targeting as Partial<AdTargetingPayload>) : null,
    placements: (Array.isArray(source.placements) ? source.placements : [])
      .map((key) => String(key || "").trim())
      .filter(Boolean)
  };
}

/**
 * Fire-and-forget autosave. Idempotent upsert keyed by
 * `(ad_account_id, draft_key)`; a 409 means the draft was already submitted
 * as a real campaign, at which point autosave should stop.
 */
export async function saveAdServerCampaignDraft(payload: AdServerDraftPayload) {
  return pulseApi<{ ok?: boolean; draft_key?: string; status?: string; creative_error?: string }>(
    "/api/pulse/ads/campaign/draft",
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export async function listAdServerCampaignDrafts() {
  const data = await pulseApi<{ ok?: boolean; drafts?: Record<string, unknown>[] }>(
    "/api/pulse/ads/campaign/drafts"
  );
  return {
    ...data,
    drafts: (Array.isArray(data.drafts) ? data.drafts : [])
      .map(normalizeAdServerDraft)
      .filter((draft) => draft.draft_key.length > 0)
  };
}

/* ------------------------------------------------------------------ *
 * Small coercions
 * ------------------------------------------------------------------ */

function nonNegInt(value: unknown): number {
  const num = Math.floor(Number(value));
  return Number.isFinite(num) && num > 0 ? num : 0;
}
