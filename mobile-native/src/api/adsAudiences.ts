/**
 * Audiences data layer.
 *
 * Binds the audience family under `/api/pulse/ads/audiences`:
 *
 *   • `GET  /audiences?account_id=` → `pulse_ads_os.list_audiences`
 *     (`{audiences, engagement_presets}` — presets carry live estimated sizes).
 *   • `POST /audiences` → `create_audience`. Rejects `kind: "lookalike"` with a
 *     sentence pointing at the lookalike endpoint; that split is preserved here.
 *   • `POST /audiences/lookalike` → `pulse_ads_audiences.create_lookalike`
 *     (seed must have ≥ 100 members; breadth 1–20%, default 5).
 *   • `GET  /audiences/<id>` → `audience_detail` (adds `estimate {estimated_size,
 *     band}`, `warnings[]`, `referenced_by_campaigns[]`).
 *   • `POST /audiences/<id>/update` → `update_audience` (name/definition;
 *     archived audiences answer 409).
 *   • `POST /audiences/<id>/archive` → `archive_audience`. The server does NOT
 *     block archiving an in-use audience — {@link audienceArchiveWarning} is the
 *     client-side check that turns that into an informed confirmation.
 *
 * Size bands mirror `pulse_ads_audiences`: narrow < 1,000 < good < broad.
 */
import { pulseApi } from "./pulseApi";

const nonNegInt = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

/* ------------------------------------------------------------------ *
 * Shapes
 * ------------------------------------------------------------------ */

export type AdAudienceBand = "narrow" | "good" | "broad";

export type AdAudience = {
  id: number;
  account_id: number;
  name: string;
  /** "saved" | "custom" | "lookalike" — anything else passes through as text. */
  kind: string;
  definition: Record<string, unknown>;
  estimated_size: number;
  archived: boolean;
  created_at: string;
  updated_at: string;
};

export function normalizeAdAudience(value?: Partial<AdAudience> | null): AdAudience {
  return {
    id: nonNegInt(value?.id),
    account_id: nonNegInt(value?.account_id),
    name: String(value?.name || ""),
    kind: String(value?.kind || "saved"),
    definition:
      value?.definition && typeof value.definition === "object"
        ? (value.definition as Record<string, unknown>)
        : {},
    estimated_size: nonNegInt(value?.estimated_size),
    // SQLite stores 0/1.
    archived: value?.archived === true || Number(value?.archived) === 1,
    created_at: String(value?.created_at || ""),
    updated_at: String(value?.updated_at || "")
  };
}

/** The band the backend would assign; used for the size chip on list rows. */
export function audienceSizeBand(estimatedSize: number): AdAudienceBand {
  if (estimatedSize < 1000) return "narrow";
  if (estimatedSize > 5_000_000) return "broad";
  return "good";
}

export type AdEngagementPreset = { key: string; name: string; estimated_size: number };

export type AdAudienceList = {
  audiences: AdAudience[];
  engagement_presets: AdEngagementPreset[];
};

export async function listAdAccountAudiences(accountId: number): Promise<AdAudienceList> {
  const data = await pulseApi<{
    audiences?: Partial<AdAudience>[];
    engagement_presets?: Partial<AdEngagementPreset>[];
  }>(`/api/pulse/ads/audiences?account_id=${encodeURIComponent(String(accountId))}`);
  return {
    audiences: (Array.isArray(data.audiences) ? data.audiences : [])
      .map(normalizeAdAudience)
      .filter((audience) => audience.id > 0),
    engagement_presets: (Array.isArray(data.engagement_presets) ? data.engagement_presets : []).map(
      (preset) => ({
        key: String(preset?.key || ""),
        name: String(preset?.name || ""),
        estimated_size: nonNegInt(preset?.estimated_size)
      })
    )
  };
}

/* ------------------------------------------------------------------ *
 * Detail
 * ------------------------------------------------------------------ */

export type AdAudienceCampaignRef = {
  campaign_id: number;
  campaign_name: string;
  status: string;
  /** "included" and/or "excluded". */
  roles: string[];
};

export type AdAudienceDetail = AdAudience & {
  estimate: { estimated_size: number; band: AdAudienceBand };
  warnings: string[];
  referenced_by_campaigns: AdAudienceCampaignRef[];
};

export function normalizeAdAudienceDetail(value?: Record<string, unknown> | null): AdAudienceDetail {
  const base = normalizeAdAudience(value as Partial<AdAudience>);
  const estimate = (value?.estimate || {}) as { estimated_size?: number; band?: string };
  const band = String(estimate.band || "").toLowerCase();
  return {
    ...base,
    estimate: {
      estimated_size: nonNegInt(estimate.estimated_size ?? base.estimated_size),
      band: band === "narrow" || band === "broad" ? band : "good"
    },
    warnings: (Array.isArray(value?.warnings) ? (value!.warnings as unknown[]) : [])
      .map((warning) => String(warning || ""))
      .filter((warning) => warning.length > 0),
    referenced_by_campaigns: (Array.isArray(value?.referenced_by_campaigns)
      ? (value!.referenced_by_campaigns as Array<Record<string, unknown>>)
      : []
    )
      .map((ref) => ({
        campaign_id: nonNegInt(ref?.campaign_id),
        campaign_name: String(ref?.campaign_name || ""),
        status: String(ref?.status || ""),
        roles: (Array.isArray(ref?.roles) ? (ref.roles as unknown[]) : [])
          .map((role) => String(role || ""))
          .filter((role) => role.length > 0)
      }))
      .filter((ref) => ref.campaign_id > 0)
  };
}

export async function getAdAudienceDetail(audienceId: number): Promise<AdAudienceDetail> {
  const data = await pulseApi<{ audience?: Record<string, unknown> }>(
    `/api/pulse/ads/audiences/${encodeURIComponent(String(audienceId))}`
  );
  return normalizeAdAudienceDetail(data.audience);
}

/* ------------------------------------------------------------------ *
 * Writes
 * ------------------------------------------------------------------ */

/** Engagement sources the backend validates; a client select must not invent one. */
export const AD_AUDIENCE_SOURCES = [
  "engaged_with_content",
  "video_viewers",
  "marketplace_engagers",
  "previous_customers",
  "profile_engagers",
  "live_engagers"
] as const;

export type AdAudienceSource = (typeof AD_AUDIENCE_SOURCES)[number];

export const AD_AUDIENCE_DEFAULT_WINDOW_DAYS = 30;
export const AD_AUDIENCE_MAX_WINDOW_DAYS = 365;
export const AD_LOOKALIKE_MIN_SEED = 100;

export async function createAdAudience(payload: {
  account_id: number;
  name: string;
  kind: "custom" | "saved";
  definition: { source: AdAudienceSource; window_days: number };
}): Promise<AdAudience> {
  const data = await pulseApi<{ audience?: Partial<AdAudience> }>("/api/pulse/ads/audiences", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return normalizeAdAudience(data.audience);
}

export async function createAdLookalikeAudience(payload: {
  account_id: number;
  name: string;
  seed_audience_id: number;
  /** 1–20, default 5. Percent of the population to reach. */
  breadth_pct?: number;
}): Promise<AdAudience> {
  const data = await pulseApi<{ audience?: Partial<AdAudience> }>(
    "/api/pulse/ads/audiences/lookalike",
    { method: "POST", body: JSON.stringify(payload) }
  );
  return normalizeAdAudience(data.audience);
}

export async function updateAdAudience(
  audienceId: number,
  changes: { name?: string; definition?: Record<string, unknown> }
): Promise<AdAudience> {
  const data = await pulseApi<{ audience?: Partial<AdAudience> }>(
    `/api/pulse/ads/audiences/${encodeURIComponent(String(audienceId))}/update`,
    { method: "POST", body: JSON.stringify(changes) }
  );
  return normalizeAdAudience(data.audience);
}

export async function archiveAdAudience(audienceId: number): Promise<{ archived: boolean }> {
  const data = await pulseApi<{ audience_id?: number; archived?: boolean }>(
    `/api/pulse/ads/audiences/${encodeURIComponent(String(audienceId))}/archive`,
    { method: "POST", body: JSON.stringify({}) }
  );
  return { archived: data.archived === true };
}

/* ------------------------------------------------------------------ *
 * Client-side guards
 * ------------------------------------------------------------------ */

/**
 * Which campaigns still reference this audience. The server archives without
 * checking, so the confirmation dialog is the only place the advertiser learns
 * a delivering campaign is about to lose its targeting definition. Non-archived
 * statuses only — an archived campaign referencing an archived audience is not
 * a conflict anyone needs to resolve.
 */
export function audienceArchiveWarning(detail: AdAudienceDetail): AdAudienceCampaignRef[] {
  return detail.referenced_by_campaigns.filter(
    (ref) => !["archived", "deleted", "completed"].includes(ref.status.toLowerCase())
  );
}

/** Seeds a lookalike can legally be built from. */
export function eligibleLookalikeSeeds(audiences: AdAudience[]): AdAudience[] {
  return audiences.filter(
    (audience) =>
      !audience.archived &&
      audience.kind !== "lookalike" &&
      audience.estimated_size >= AD_LOOKALIKE_MIN_SEED
  );
}
