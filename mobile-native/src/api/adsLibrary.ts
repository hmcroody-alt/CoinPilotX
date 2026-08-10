/**
 * Creative library data layer (wave 2 — the browsable library).
 *
 * Binds `services/pulse_ads_library.py`:
 *
 *   • `GET  /api/pulse/ads/library?filter=` → `library_overview`
 *     (`{filter, creatives, assets, counts: {all, images, videos, posts}}`).
 *     Buckets: images = {image}; videos = {video, reel, live_replay}; posts =
 *     everything content-backed (text, post, listing, event, hologram, audio).
 *   • `GET  /api/pulse/ads/library/<id>` → `asset_detail` (adds copy fields,
 *     previews, mixed moderation history, appeals, and the server's own
 *     `editable` verdict).
 *   • `POST /api/pulse/ads/creative/<id>/metadata` → `update_creative_metadata`.
 *     Only `draft`/`rejected` creatives are editable, only the six copy fields,
 *     and any edit resets moderation to `draft` — the creative must be
 *     resubmitted. The screen must warn before the tap, not after.
 *   • `POST /api/pulse/ads/creative/<id>/use-in-campaign` →
 *     `duplicate_creative_to_campaign` (same-account only; closed campaigns
 *     409; the copy starts as a draft named "{title} copy").
 *
 * Existing lifecycle actions (submit/duplicate/archive/delete_draft) stay in
 * `adsCreatives.ts` — this module does not duplicate them.
 */
import { pulseApi } from "./pulseApi";

const nonNegInt = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

/* ------------------------------------------------------------------ *
 * Filters
 * ------------------------------------------------------------------ */

export const AD_LIBRARY_FILTERS = ["all", "images", "videos", "posts"] as const;

export type AdLibraryFilter = (typeof AD_LIBRARY_FILTERS)[number];

export function normalizeAdLibraryFilter(value?: string | null): AdLibraryFilter {
  const key = String(value || "").toLowerCase();
  return (AD_LIBRARY_FILTERS as readonly string[]).includes(key) ? (key as AdLibraryFilter) : "all";
}

/* ------------------------------------------------------------------ *
 * Items
 * ------------------------------------------------------------------ */

export type AdLibraryPolicyFlag = {
  flag_type: string;
  severity: string;
  details: string;
  created_at: string;
};

export type AdLibraryCampaignRef = {
  campaign_id: number;
  campaign_name: string;
  campaign_status: string;
  adset_id: number | null;
};

export type AdLibraryItem = {
  id: number;
  ad_account_id: number;
  creative_type: string;
  /** Server-assigned bucket: "images" | "videos" | "posts". */
  bucket: string;
  title: string;
  status: string;
  moderation_status: string;
  rejection_reason: string;
  policy_flags: AdLibraryPolicyFlag[];
  campaign: AdLibraryCampaignRef | null;
  media_url: string;
  thumbnail_url: string;
  playback_url: string;
  media_ready: boolean;
  performance: { impressions: number; clicks: number; ctr: number };
  created_at: string;
  updated_at: string;
};

export function normalizeAdLibraryItem(value?: Record<string, unknown> | null): AdLibraryItem {
  const performance = (value?.performance || {}) as Record<string, unknown>;
  const campaign = value?.campaign as Record<string, unknown> | null | undefined;
  const campaignId = nonNegInt(campaign?.campaign_id);
  const adsetId = nonNegInt(campaign?.adset_id);
  return {
    id: nonNegInt(value?.id),
    ad_account_id: nonNegInt(value?.ad_account_id),
    creative_type: String(value?.creative_type || ""),
    bucket: String(value?.bucket || "posts"),
    title: String(value?.title || ""),
    status: String(value?.status || "draft"),
    moderation_status: String(value?.moderation_status || "draft"),
    rejection_reason: String(value?.rejection_reason || ""),
    policy_flags: (Array.isArray(value?.policy_flags)
      ? (value!.policy_flags as Array<Record<string, unknown>>)
      : []
    ).map((flag) => ({
      flag_type: String(flag?.flag_type || ""),
      severity: String(flag?.severity || ""),
      details: String(flag?.details || ""),
      created_at: String(flag?.created_at || "")
    })),
    campaign:
      campaign && campaignId > 0
        ? {
            campaign_id: campaignId,
            campaign_name: String(campaign.campaign_name || ""),
            campaign_status: String(campaign.campaign_status || ""),
            adset_id: adsetId > 0 ? adsetId : null
          }
        : null,
    media_url: String(value?.media_url || ""),
    thumbnail_url: String(value?.thumbnail_url || ""),
    playback_url: String(value?.playback_url || ""),
    media_ready: value?.media_ready === true || Number(value?.media_ready) === 1,
    performance: {
      impressions: nonNegInt(performance.impressions),
      clicks: nonNegInt(performance.clicks),
      ctr: Math.max(0, Number(performance.ctr) || 0)
    },
    created_at: String(value?.created_at || ""),
    updated_at: String(value?.updated_at || "")
  };
}

export type AdLibraryCounts = { all: number; images: number; videos: number; posts: number };

export type AdLibraryOverview = {
  filter: AdLibraryFilter;
  creatives: AdLibraryItem[];
  counts: AdLibraryCounts;
};

export async function getAdLibrary(filter: AdLibraryFilter = "all"): Promise<AdLibraryOverview> {
  const data = await pulseApi<{
    filter?: string;
    creatives?: Array<Record<string, unknown>>;
    counts?: Partial<AdLibraryCounts>;
  }>(`/api/pulse/ads/library?filter=${encodeURIComponent(filter)}`);
  return {
    filter: normalizeAdLibraryFilter(data.filter),
    creatives: (Array.isArray(data.creatives) ? data.creatives : [])
      .map(normalizeAdLibraryItem)
      .filter((item) => item.id > 0),
    counts: {
      all: nonNegInt(data.counts?.all),
      images: nonNegInt(data.counts?.images),
      videos: nonNegInt(data.counts?.videos),
      posts: nonNegInt(data.counts?.posts)
    }
  };
}

/* ------------------------------------------------------------------ *
 * Asset detail
 * ------------------------------------------------------------------ */

export type AdModerationHistoryEntry = {
  /** "moderation_queue" | "review_board" — the two tables the history merges. */
  source: string;
  status: string;
  notes: string;
  created_at: string;
};

export type AdLibraryAssetDetail = AdLibraryItem & {
  body: string;
  headline: string;
  primary_text: string;
  call_to_action: string;
  destination_url: string;
  moderation_history: AdModerationHistoryEntry[];
  /** The server's own verdict — true only for draft/rejected creatives. */
  editable: boolean;
};

export function normalizeAdLibraryAssetDetail(
  value?: Record<string, unknown> | null
): AdLibraryAssetDetail {
  const base = normalizeAdLibraryItem(value);
  return {
    ...base,
    body: String(value?.body || ""),
    headline: String(value?.headline || ""),
    primary_text: String(value?.primary_text || ""),
    call_to_action: String(value?.call_to_action || ""),
    destination_url: String(value?.destination_url || ""),
    moderation_history: (Array.isArray(value?.moderation_history)
      ? (value!.moderation_history as Array<Record<string, unknown>>)
      : []
    ).map((entry) => ({
      source: String(entry?.source || ""),
      // The two sources name their verdict column differently.
      status: String(entry?.status || entry?.review_status || ""),
      notes: String(entry?.notes || entry?.review_reason || ""),
      created_at: String(entry?.created_at || "")
    })),
    editable: value?.editable === true
  };
}

export async function getAdLibraryAsset(creativeId: number): Promise<AdLibraryAssetDetail> {
  const data = await pulseApi<{ creative?: Record<string, unknown> }>(
    `/api/pulse/ads/library/${encodeURIComponent(String(creativeId))}`
  );
  return normalizeAdLibraryAssetDetail(data.creative);
}

/* ------------------------------------------------------------------ *
 * Writes
 * ------------------------------------------------------------------ */

/** The six fields the backend accepts; anything else is silently dropped there. */
export type AdCreativeMetadataPatch = Partial<{
  title: string;
  body: string;
  headline: string;
  primary_text: string;
  call_to_action: string;
  destination_url: string;
}>;

/**
 * Edits reset moderation to `draft` server-side — the creative leaves review
 * and must be resubmitted. Callers confirm that consequence before posting.
 */
export async function updateAdCreativeMetadata(
  creativeId: number,
  patch: AdCreativeMetadataPatch
): Promise<AdLibraryAssetDetail> {
  const data = await pulseApi<{ creative?: Record<string, unknown> }>(
    `/api/pulse/ads/creative/${encodeURIComponent(String(creativeId))}/metadata`,
    { method: "POST", body: JSON.stringify(patch) }
  );
  return normalizeAdLibraryAssetDetail(data.creative);
}

/** Copies the creative into another campaign as a fresh draft ("{title} copy"). */
export async function useAdCreativeInCampaign(
  creativeId: number,
  campaignId: number,
  adsetId?: number
): Promise<AdLibraryAssetDetail> {
  const data = await pulseApi<{ creative?: Record<string, unknown> }>(
    `/api/pulse/ads/creative/${encodeURIComponent(String(creativeId))}/use-in-campaign`,
    {
      method: "POST",
      body: JSON.stringify(
        adsetId ? { campaign_id: campaignId, adset_id: adsetId } : { campaign_id: campaignId }
      )
    }
  );
  return normalizeAdLibraryAssetDetail(data.creative);
}
