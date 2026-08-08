/**
 * Ads OS data layer — the campaign-creation contract.
 *
 * This module types and binds the five campaign-creation surfaces under
 * `/api/pulse/ads`: canonical objectives, campaign targeting (+ reach
 * estimate), audiences, the content inventory the creative picker browses,
 * and the single-shot `POST /campaigns/full` composer endpoint. It follows
 * `businessOs.ts` conventions — `pulseApi` wrapper, `normalize*` coercions,
 * integer cents everywhere — and adds nothing that module already owns
 * (accounts, wallet, analytics live there).
 *
 * Some of these endpoints may not be deployed yet; the types here ARE the
 * contract, written to spec so the backend lands against them.
 */
import { pulseApi } from "./pulseApi";

/* ------------------------------------------------------------------ *
 * Canonical objectives
 * ------------------------------------------------------------------ */

/** The 11 canonical objectives. Campaign rows carry `objective_canonical`. */
export const AD_CANONICAL_OBJECTIVES = [
  "awareness",
  "engagement",
  "video_views",
  "website_traffic",
  "messages",
  "marketplace_sales",
  "app_activity",
  "lead_generation",
  "event_promotion",
  "profile_growth",
  "live_promotion"
] as const;
export type AdCanonicalObjective = (typeof AD_CANONICAL_OBJECTIVES)[number];

export function isAdCanonicalObjective(value: unknown): value is AdCanonicalObjective {
  return AD_CANONICAL_OBJECTIVES.includes(value as AdCanonicalObjective);
}

/* ------------------------------------------------------------------ *
 * Placements
 * ------------------------------------------------------------------ */

/** The 8 placement keys the delivery engine serves. Empty selection = automatic. */
export const AD_PLACEMENT_KEYS = [
  "feed_inline",
  "feed_inline_ufo_mobile",
  "marketplace_sponsor",
  "search_sponsored_result",
  "video_pre_roll",
  "profile_sponsor",
  "status_interstitial",
  "pulse_radio_sponsor"
] as const;
export type AdPlacementKey = (typeof AD_PLACEMENT_KEYS)[number];

export function isAdPlacementKey(value: unknown): value is AdPlacementKey {
  return AD_PLACEMENT_KEYS.includes(value as AdPlacementKey);
}

/* ------------------------------------------------------------------ *
 * Targeting
 * ------------------------------------------------------------------ */

export type AdAudienceMode = "everyone" | "followers" | "non_followers" | "engaged";
export type AdDeviceType = "all" | "mobile" | "desktop";
export type AdEstimateBand = "narrow" | "good" | "broad";

export type AdTargetingPayload = {
  countries: string[];
  languages: string[];
  min_age: number;
  max_age: number;
  device_type: AdDeviceType;
  interests: string[];
  keywords: string[];
  audience_mode: AdAudienceMode;
  saved_audience_ids: number[];
  excluded_audience_ids: number[];
};

export type AdReachEstimate = {
  estimated_min: number;
  estimated_max: number;
  band: AdEstimateBand;
};

export type AdTargetingResponse = {
  ok?: boolean;
  targeting?: Partial<AdTargetingPayload>;
  estimate?: AdReachEstimate;
};

export function normalizeAdTargeting(value?: Partial<AdTargetingPayload> | null): AdTargetingPayload {
  const source = value || {};
  const deviceType: AdDeviceType =
    source.device_type === "mobile" || source.device_type === "desktop" ? source.device_type : "all";
  const audienceMode: AdAudienceMode =
    source.audience_mode === "followers" ||
    source.audience_mode === "non_followers" ||
    source.audience_mode === "engaged"
      ? source.audience_mode
      : "everyone";
  return {
    countries: stringList(source.countries),
    languages: stringList(source.languages),
    min_age: clampInt(source.min_age, 13, 65, 13),
    max_age: clampInt(source.max_age, 13, 65, 65),
    device_type: deviceType,
    interests: stringList(source.interests),
    keywords: stringList(source.keywords),
    audience_mode: audienceMode,
    saved_audience_ids: idList(source.saved_audience_ids),
    excluded_audience_ids: idList(source.excluded_audience_ids)
  };
}

export function normalizeAdReachEstimate(value?: Partial<AdReachEstimate> | null): AdReachEstimate | null {
  if (!value || typeof value !== "object") return null;
  const band: AdEstimateBand = value.band === "narrow" || value.band === "broad" ? value.band : "good";
  return {
    estimated_min: Math.max(0, Math.floor(Number(value.estimated_min || 0))),
    estimated_max: Math.max(0, Math.floor(Number(value.estimated_max || 0))),
    band
  };
}

export async function putCampaignTargeting(campaignId: number, targeting: AdTargetingPayload) {
  const data = await pulseApi<AdTargetingResponse>(
    `/api/pulse/ads/campaigns/${encodeURIComponent(String(campaignId))}/targeting`,
    { method: "PUT", body: JSON.stringify(targeting) }
  );
  return {
    ...data,
    targeting: normalizeAdTargeting(data.targeting),
    estimate: normalizeAdReachEstimate(data.estimate)
  };
}

export async function getCampaignTargeting(campaignId: number) {
  const data = await pulseApi<AdTargetingResponse>(
    `/api/pulse/ads/campaigns/${encodeURIComponent(String(campaignId))}/targeting`
  );
  return {
    ...data,
    targeting: normalizeAdTargeting(data.targeting),
    estimate: normalizeAdReachEstimate(data.estimate)
  };
}

/**
 * Live reach preview for the wizard. The campaign doesn't exist yet during
 * creation, so the per-campaign PUT cannot serve the estimate; this sibling
 * endpoint takes the same targeting body. (Contract deviation — flagged in
 * the build report: the spec only defines the per-campaign PUT/GET.)
 */
export async function previewAdTargetingEstimate(targeting: AdTargetingPayload) {
  const data = await pulseApi<AdTargetingResponse>("/api/pulse/ads/targeting/estimate", {
    method: "POST",
    body: JSON.stringify(targeting)
  });
  return { ...data, estimate: normalizeAdReachEstimate(data.estimate) };
}

/* ------------------------------------------------------------------ *
 * Audiences
 * ------------------------------------------------------------------ */

export type AdAudienceKind = "saved" | "custom" | "engagement" | "lookalike" | "exclusion";

export type AdAudience = {
  id: number;
  name: string;
  kind: AdAudienceKind | string;
  estimated_size?: number;
};

export type AdEngagementPreset = {
  key: string;
  name: string;
  size?: number;
};

export type AdAudiencesResponse = {
  ok?: boolean;
  audiences?: AdAudience[];
  engagement_presets?: AdEngagementPreset[];
};

export type AdAudienceCreatePayload = {
  account_id: number;
  name: string;
  kind: AdAudienceKind;
  definition?: Record<string, unknown>;
};

export function normalizeAdAudiences(audiences?: AdAudience[] | null): AdAudience[] {
  return (Array.isArray(audiences) ? audiences : [])
    .map((audience) => ({
      ...audience,
      id: Number(audience?.id || 0),
      name: String(audience?.name || "Untitled audience"),
      kind: String(audience?.kind || "saved"),
      estimated_size: Math.max(0, Math.floor(Number(audience?.estimated_size || 0)))
    }))
    .filter((audience) => audience.id > 0);
}

export async function listAdAudiences(accountId: number) {
  const data = await pulseApi<AdAudiencesResponse>(
    `/api/pulse/ads/audiences?account_id=${encodeURIComponent(String(accountId))}`
  );
  return {
    ...data,
    audiences: normalizeAdAudiences(data.audiences),
    engagement_presets: (Array.isArray(data.engagement_presets) ? data.engagement_presets : []).map((preset) => ({
      key: String(preset?.key || ""),
      name: String(preset?.name || ""),
      size: Math.max(0, Math.floor(Number(preset?.size || 0)))
    }))
  };
}

export async function createAdAudience(payload: AdAudienceCreatePayload) {
  return pulseApi<{ ok?: boolean; audience?: AdAudience }>("/api/pulse/ads/audiences", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

/* ------------------------------------------------------------------ *
 * Content inventory — the "use existing content" picker
 * ------------------------------------------------------------------ */

export const AD_CONTENT_KINDS = ["post", "reel", "video", "event", "listing"] as const;
export type AdContentKind = (typeof AD_CONTENT_KINDS)[number];

export type AdContentItem = {
  kind: AdContentKind | string;
  id: number;
  title: string;
  thumbnail_url?: string;
  created_at?: string;
  metrics?: { views?: number; likes?: number; comments?: number };
  eligible: boolean;
  ineligible_reason?: string;
};

export type AdContentInventoryResponse = { ok?: boolean; items?: AdContentItem[] };

export function normalizeAdContentItems(items?: AdContentItem[] | null): AdContentItem[] {
  return (Array.isArray(items) ? items : [])
    .map((item) => ({
      ...item,
      kind: String(item?.kind || "post"),
      id: Number(item?.id || 0),
      title: String(item?.title || "Untitled"),
      thumbnail_url: String(item?.thumbnail_url || ""),
      created_at: String(item?.created_at || ""),
      metrics: {
        views: Math.max(0, Math.floor(Number(item?.metrics?.views || 0))),
        likes: Math.max(0, Math.floor(Number(item?.metrics?.likes || 0))),
        comments: Math.max(0, Math.floor(Number(item?.metrics?.comments || 0)))
      },
      eligible: item?.eligible !== false,
      ineligible_reason: String(item?.ineligible_reason || "")
    }))
    .filter((item) => item.id > 0);
}

export async function getAdContentInventory(
  params: { kinds?: AdContentKind[]; limit?: number } = {}
) {
  const query = new URLSearchParams();
  const kinds = params.kinds && params.kinds.length ? params.kinds : AD_CONTENT_KINDS.slice();
  query.set("kinds", kinds.join(","));
  query.set("limit", String(params.limit || 25));
  const data = await pulseApi<AdContentInventoryResponse>(
    `/api/pulse/ads/content-inventory?${query.toString()}`
  );
  return { ...data, items: normalizeAdContentItems(data.items) };
}

/* ------------------------------------------------------------------ *
 * Creative + full campaign creation
 * ------------------------------------------------------------------ */

export type AdCreativeType =
  | "image"
  | "video"
  | "text"
  | "listing"
  | "post"
  | "reel"
  | "event"
  | "live_replay";

export const AD_CALL_TO_ACTIONS = [
  "Shop Now",
  "Learn More",
  "Send Message",
  "Sign Up",
  "Watch More",
  "Get Offer",
  "Book Now"
] as const;
export type AdCallToAction = (typeof AD_CALL_TO_ACTIONS)[number];

export type AdCreativePayload = {
  creative_type: AdCreativeType;
  title: string;
  headline: string;
  primary_text: string;
  body: string;
  call_to_action: string;
  destination_url: string;
  media_asset_id?: number;
  content_ref_type?: string;
  content_ref_id?: number;
};

export type AdSpecialCategory = "" | "credit" | "employment" | "housing" | "social" | "elections";

export type AdFullCampaignPayload = {
  /** Generated once per draft and persisted, so a retried publish is a no-op. */
  idempotency_key: string;
  ad_account_id: number;
  campaign: {
    campaign_name: string;
    objective: AdCanonicalObjective;
    budget_type: "daily" | "lifetime";
    daily_budget_cents: number;
    lifetime_budget_cents: number;
    start_at: string;
    end_at: string;
    special_category?: AdSpecialCategory;
  };
  targeting: AdTargetingPayload;
  /** Empty array = automatic placements. */
  placements: string[];
  creative: AdCreativePayload;
  submit: boolean;
};

export type AdFullCampaignResponse = {
  ok?: boolean;
  campaign?: { id?: number; status?: string; campaign_name?: string; objective?: string };
  creative?: { id?: number };
  targeting?: Partial<AdTargetingPayload>;
  blockers?: string[];
  /** True when the idempotency key matched an earlier successful create. */
  duplicate?: boolean;
};

export async function createFullAdCampaign(payload: AdFullCampaignPayload) {
  const data = await pulseApi<AdFullCampaignResponse>("/api/pulse/ads/campaigns/full", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return { ...data, blockers: (Array.isArray(data.blockers) ? data.blockers : []).map(String) };
}

/* ------------------------------------------------------------------ *
 * Ad media upload
 * ------------------------------------------------------------------ */

export type AdMediaUploadResult = {
  ok?: boolean;
  media_id?: number;
  media?: { id?: number; media_id?: number; url?: string };
};

/**
 * Uploads a picked asset to the ad account's media endpoint. The generic
 * uploader in `media/nativeMediaUpload.ts` is hardcoded to
 * `/api/pulse/media/upload`, so ad creatives get their own FormData post here;
 * `pulseApi` skips the JSON Content-Type for FormData bodies.
 */
export async function uploadAdMedia(
  accountId: number,
  asset: { uri: string; name?: string; mimeType?: string }
) {
  const form = new FormData();
  form.append("file", {
    uri: asset.uri,
    name: asset.name || "ad-media",
    type: asset.mimeType || "application/octet-stream"
  } as unknown as Blob);
  const data = await pulseApi<AdMediaUploadResult>(
    `/api/pulse/ads/accounts/${encodeURIComponent(String(accountId))}/media/upload`,
    { method: "POST", body: form }
  );
  return data;
}

export function adMediaUploadId(result?: AdMediaUploadResult | null): number {
  return Number(result?.media_id || result?.media?.id || result?.media?.media_id || 0);
}

/* ------------------------------------------------------------------ *
 * Small coercions
 * ------------------------------------------------------------------ */

function stringList(value: unknown): string[] {
  return (Array.isArray(value) ? value : []).map((item) => String(item || "").trim()).filter(Boolean);
}

function idList(value: unknown): number[] {
  return (Array.isArray(value) ? value : [])
    .map((item) => Math.floor(Number(item)))
    .filter((id) => Number.isFinite(id) && id > 0);
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const num = Math.floor(Number(value));
  if (!Number.isFinite(num)) return fallback;
  return Math.min(max, Math.max(min, num));
}
