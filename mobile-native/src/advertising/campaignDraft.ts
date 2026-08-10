/**
 * The campaign-creation draft: shape, defaults, per-step validation and the
 * `POST /campaigns/full` payload assembly.
 *
 * Everything here is pure — no React, no storage, no network — mirroring
 * `marketplace/listingDraft.ts` so the whole creation contract is unit-testable
 * without rendering the wizard. Persistence lives in `campaignDraftStore.ts`;
 * the screen only moves data between the two.
 *
 * The idempotency key is minted once, when the draft is created, and survives
 * normalization: a publish retry after a network failure reuses the same key,
 * so the server can answer `duplicate: true` instead of double-creating.
 */

import {
  AD_CALL_TO_ACTIONS,
  AD_CANONICAL_OBJECTIVES,
  AD_PLACEMENT_KEYS,
  AdCallToAction,
  AdCanonicalObjective,
  AdContentKind,
  AdCreativeType,
  AdFullCampaignPayload,
  AdSpecialCategory,
  AdTargetingPayload,
  isAdCanonicalObjective,
  isAdPlacementKey,
  normalizeAdTargeting
} from "../api/adsOs";
import { AD_DAILY_BUDGET_MAX_CENTS, AD_LIFETIME_BUDGET_MAX_CENTS } from "../api/businessOs";

export type CampaignWizardStep =
  | "objective"
  | "setup"
  | "audience"
  | "placements"
  | "creative"
  | "budget"
  | "review";

export const CAMPAIGN_WIZARD_STEPS: CampaignWizardStep[] = [
  "objective",
  "setup",
  "audience",
  "placements",
  "creative",
  "budget",
  "review"
];

export const PRIMARY_TEXT_MAX_LENGTH = 125;
export const HEADLINE_MAX_LENGTH = 40;

export const AD_SPECIAL_CATEGORIES: AdSpecialCategory[] = [
  "",
  "credit",
  "employment",
  "housing",
  "social",
  "elections"
];

export const MIN_TARGET_AGE = 13;
export const MAX_TARGET_AGE = 65;

/* ------------------------------------------------------------------ *
 * Objective metadata
 * ------------------------------------------------------------------ */

/**
 * Per-objective wizard behavior. `titleKey`/`captionKey`/`optimizationKey` are
 * i18n suffixes under `commerce:adsWizard.`; the implied-config flags drive the
 * objective-specific validation in `validateCampaignStep`.
 */
export type ObjectiveMeta = {
  icon: string;
  titleKey: string;
  captionKey: string;
  /** Display-only optimization goal shown on the Budget & Delivery step. */
  optimizationKey: string;
  /** CTA preselected when the objective is chosen. */
  defaultCallToAction: AdCallToAction;
  /** website_traffic: a destination URL is mandatory. */
  requiresDestinationUrl?: boolean;
  /** marketplace_sales: needs a listing creative OR a destination URL. */
  requiresListingOrDestination?: boolean;
};

export const AD_OBJECTIVE_METADATA: Record<AdCanonicalObjective, ObjectiveMeta> = {
  awareness: {
    icon: "megaphone-outline",
    titleKey: "objectiveAwareness",
    captionKey: "objectiveAwarenessCaption",
    optimizationKey: "optimizationReach",
    defaultCallToAction: "Learn More"
  },
  engagement: {
    icon: "heart-outline",
    titleKey: "objectiveEngagement",
    captionKey: "objectiveEngagementCaption",
    optimizationKey: "optimizationEngagement",
    defaultCallToAction: "Learn More"
  },
  video_views: {
    icon: "play-circle-outline",
    titleKey: "objectiveVideoViews",
    captionKey: "objectiveVideoViewsCaption",
    optimizationKey: "optimizationVideoViews",
    defaultCallToAction: "Watch More"
  },
  website_traffic: {
    icon: "globe-outline",
    titleKey: "objectiveWebsiteTraffic",
    captionKey: "objectiveWebsiteTrafficCaption",
    optimizationKey: "optimizationClicks",
    defaultCallToAction: "Learn More",
    requiresDestinationUrl: true
  },
  messages: {
    icon: "chatbubble-ellipses-outline",
    titleKey: "objectiveMessages",
    captionKey: "objectiveMessagesCaption",
    optimizationKey: "optimizationConversations",
    defaultCallToAction: "Send Message"
  },
  marketplace_sales: {
    icon: "pricetags-outline",
    titleKey: "objectiveMarketplaceSales",
    captionKey: "objectiveMarketplaceSalesCaption",
    optimizationKey: "optimizationSales",
    defaultCallToAction: "Shop Now",
    requiresListingOrDestination: true
  },
  app_activity: {
    icon: "phone-portrait-outline",
    titleKey: "objectiveAppActivity",
    captionKey: "objectiveAppActivityCaption",
    optimizationKey: "optimizationAppActivity",
    defaultCallToAction: "Sign Up"
  },
  lead_generation: {
    icon: "person-add-outline",
    titleKey: "objectiveLeadGeneration",
    captionKey: "objectiveLeadGenerationCaption",
    optimizationKey: "optimizationLeads",
    defaultCallToAction: "Sign Up"
  },
  event_promotion: {
    icon: "calendar-outline",
    titleKey: "objectiveEventPromotion",
    captionKey: "objectiveEventPromotionCaption",
    optimizationKey: "optimizationEventResponses",
    defaultCallToAction: "Book Now"
  },
  profile_growth: {
    icon: "trending-up-outline",
    titleKey: "objectiveProfileGrowth",
    captionKey: "objectiveProfileGrowthCaption",
    optimizationKey: "optimizationFollows",
    defaultCallToAction: "Learn More"
  },
  live_promotion: {
    icon: "radio-outline",
    titleKey: "objectiveLivePromotion",
    captionKey: "objectiveLivePromotionCaption",
    optimizationKey: "optimizationLiveViewers",
    defaultCallToAction: "Watch More"
  }
};

/**
 * Every optimization goal any objective can imply. The Budget & Delivery step
 * lets the advertiser pick one; the value is draft-local only — the backend's
 * campaign create accepts no such field — so it colors the wizard summary and
 * nothing else. Values are i18n suffixes under `commerce:adsWizard.`.
 */
export const AD_OPTIMIZATION_GOAL_KEYS = [
  "optimizationReach",
  "optimizationEngagement",
  "optimizationVideoViews",
  "optimizationClicks",
  "optimizationConversations",
  "optimizationSales",
  "optimizationAppActivity",
  "optimizationLeads",
  "optimizationEventResponses",
  "optimizationFollows",
  "optimizationLiveViewers"
] as const;

export type AdOptimizationGoalKey = (typeof AD_OPTIMIZATION_GOAL_KEYS)[number];

export function isAdOptimizationGoalKey(value: unknown): value is AdOptimizationGoalKey {
  return AD_OPTIMIZATION_GOAL_KEYS.includes(value as AdOptimizationGoalKey);
}

/* ------------------------------------------------------------------ *
 * Draft shape
 * ------------------------------------------------------------------ */

export type CampaignSetupDraft = {
  name: string;
  /** Marketplace ad vs Post ad — drives the creative step's defaults. */
  adSurface: "marketplace" | "post";
  budgetType: "daily" | "lifetime";
  /** USD amount as typed, e.g. "25" or "25.50". Converted to cents at build. */
  budgetAmount: string;
  /** YYYY-MM-DD, entered as text (no date-picker dependency). */
  startDate: string;
  /** YYYY-MM-DD or empty for open-ended. Required for lifetime budgets. */
  endDate: string;
  specialCategory: AdSpecialCategory;
};

export type CampaignAudienceDraft = {
  countries: string[];
  languages: string[];
  minAge: number;
  maxAge: number;
  deviceType: "all" | "mobile" | "desktop";
  interests: string[];
  keywords: string[];
  audienceMode: "everyone" | "followers" | "non_followers" | "engaged";
  savedAudienceIds: number[];
  excludedAudienceIds: number[];
};

export type CampaignPlacementsDraft = {
  mode: "automatic" | "manual";
  keys: string[];
};

export type CampaignCreativeDraft = {
  source: "existing" | "new";
  /** For "new": whether the seller is uploading an image or a video. */
  newMediaKind: "image" | "video";
  contentRefType: AdContentKind | "";
  contentRefId: number;
  contentTitle: string;
  contentThumbnailUrl: string;
  mediaAssetId: number;
  mediaPreviewUri: string;
  title: string;
  headline: string;
  primaryText: string;
  body: string;
  callToAction: AdCallToAction;
  destinationUrl: string;
};

/**
 * Client-side delivery preferences. Nothing here reaches the server — see
 * `AD_OPTIMIZATION_GOAL_KEYS`. `""` means "use the objective's default".
 */
export type CampaignDeliveryDraft = {
  optimizationGoal: AdOptimizationGoalKey | "";
};

export type CampaignDraft = {
  version: 1;
  step: CampaignWizardStep;
  /** Minted at creation, persisted, reused on publish retry. */
  idempotencyKey: string;
  updatedAt: string;
  accountId: number;
  objective: AdCanonicalObjective | null;
  setup: CampaignSetupDraft;
  audience: CampaignAudienceDraft;
  placements: CampaignPlacementsDraft;
  creative: CampaignCreativeDraft;
  delivery: CampaignDeliveryDraft;
};

export function createCampaignIdempotencyKey(): string {
  return `cmp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}-${Math.random()
    .toString(36)
    .slice(2, 10)}`;
}

export function createCampaignDraft(): CampaignDraft {
  return {
    version: 1,
    step: "objective",
    idempotencyKey: createCampaignIdempotencyKey(),
    updatedAt: new Date().toISOString(),
    accountId: 0,
    objective: null,
    setup: {
      name: "",
      adSurface: "post",
      budgetType: "daily",
      budgetAmount: "",
      startDate: "",
      endDate: "",
      specialCategory: ""
    },
    audience: {
      countries: [],
      languages: [],
      minAge: MIN_TARGET_AGE,
      maxAge: MAX_TARGET_AGE,
      deviceType: "all",
      interests: [],
      keywords: [],
      audienceMode: "everyone",
      savedAudienceIds: [],
      excludedAudienceIds: []
    },
    placements: {
      mode: "automatic",
      keys: []
    },
    creative: {
      source: "existing",
      newMediaKind: "image",
      contentRefType: "",
      contentRefId: 0,
      contentTitle: "",
      contentThumbnailUrl: "",
      mediaAssetId: 0,
      mediaPreviewUri: "",
      title: "",
      headline: "",
      primaryText: "",
      body: "",
      callToAction: "Learn More",
      destinationUrl: ""
    },
    delivery: {
      optimizationGoal: ""
    }
  };
}

/**
 * Defensive merge over a possibly stale or truncated persisted draft. Absent or
 * malformed branches fall back to defaults; the idempotency key is the one
 * field that must survive verbatim when present.
 */
export function normalizeCampaignDraft(value: Partial<CampaignDraft> | null | undefined): CampaignDraft {
  const base = createCampaignDraft();
  if (!value || typeof value !== "object") return base;
  const objective = isAdCanonicalObjective(value.objective) ? value.objective : null;
  const step: CampaignWizardStep = CAMPAIGN_WIZARD_STEPS.includes(value.step as CampaignWizardStep)
    ? objective || value.step === "objective"
      ? (value.step as CampaignWizardStep)
      : "objective"
    : "objective";
  const setup = value.setup || ({} as Partial<CampaignSetupDraft>);
  const audience = value.audience || ({} as Partial<CampaignAudienceDraft>);
  const placements = value.placements || ({} as Partial<CampaignPlacementsDraft>);
  const creative = value.creative || ({} as Partial<CampaignCreativeDraft>);
  const delivery = value.delivery || ({} as Partial<CampaignDeliveryDraft>);
  const callToAction = AD_CALL_TO_ACTIONS.includes(creative.callToAction as AdCallToAction)
    ? (creative.callToAction as AdCallToAction)
    : base.creative.callToAction;
  const minAge = clampAge(audience.minAge, MIN_TARGET_AGE);
  const maxAge = clampAge(audience.maxAge, MAX_TARGET_AGE);
  return {
    ...base,
    step,
    idempotencyKey:
      typeof value.idempotencyKey === "string" && value.idempotencyKey.trim()
        ? value.idempotencyKey.trim()
        : base.idempotencyKey,
    updatedAt: String(value.updatedAt || base.updatedAt),
    accountId: Math.max(0, Math.floor(Number(value.accountId || 0))) || 0,
    objective,
    setup: {
      name: String(setup.name || ""),
      adSurface: setup.adSurface === "marketplace" ? "marketplace" : "post",
      budgetType: setup.budgetType === "lifetime" ? "lifetime" : "daily",
      budgetAmount: String(setup.budgetAmount || ""),
      startDate: String(setup.startDate || ""),
      endDate: String(setup.endDate || ""),
      specialCategory: AD_SPECIAL_CATEGORIES.includes(setup.specialCategory as AdSpecialCategory)
        ? (setup.specialCategory as AdSpecialCategory)
        : ""
    },
    audience: {
      countries: stringList(audience.countries),
      languages: stringList(audience.languages),
      minAge: Math.min(minAge, maxAge),
      maxAge: Math.max(minAge, maxAge),
      deviceType:
        audience.deviceType === "mobile" || audience.deviceType === "desktop" ? audience.deviceType : "all",
      interests: stringList(audience.interests),
      keywords: stringList(audience.keywords),
      audienceMode:
        audience.audienceMode === "followers" ||
        audience.audienceMode === "non_followers" ||
        audience.audienceMode === "engaged"
          ? audience.audienceMode
          : "everyone",
      savedAudienceIds: idList(audience.savedAudienceIds),
      excludedAudienceIds: idList(audience.excludedAudienceIds)
    },
    placements: {
      mode: placements.mode === "manual" ? "manual" : "automatic",
      keys: stringList(placements.keys).filter(isAdPlacementKey)
    },
    creative: {
      source: creative.source === "new" ? "new" : "existing",
      newMediaKind: creative.newMediaKind === "video" ? "video" : "image",
      contentRefType: isContentKind(creative.contentRefType) ? creative.contentRefType : "",
      contentRefId: Math.max(0, Math.floor(Number(creative.contentRefId || 0))) || 0,
      contentTitle: String(creative.contentTitle || ""),
      contentThumbnailUrl: String(creative.contentThumbnailUrl || ""),
      mediaAssetId: Math.max(0, Math.floor(Number(creative.mediaAssetId || 0))) || 0,
      mediaPreviewUri: String(creative.mediaPreviewUri || ""),
      title: String(creative.title || ""),
      headline: String(creative.headline || "").slice(0, HEADLINE_MAX_LENGTH),
      primaryText: String(creative.primaryText || "").slice(0, PRIMARY_TEXT_MAX_LENGTH),
      body: String(creative.body || ""),
      callToAction,
      destinationUrl: String(creative.destinationUrl || "")
    },
    delivery: {
      optimizationGoal: isAdOptimizationGoalKey(delivery.optimizationGoal) ? delivery.optimizationGoal : ""
    }
  };
}

/**
 * The optimization goal shown on Budget & Delivery: the advertiser's explicit
 * pick if there is one, else the objective's default, else reach.
 */
export function campaignOptimizationGoal(draft: CampaignDraft): AdOptimizationGoalKey {
  if (draft.delivery.optimizationGoal) return draft.delivery.optimizationGoal;
  const key = draft.objective ? AD_OBJECTIVE_METADATA[draft.objective].optimizationKey : "";
  return isAdOptimizationGoalKey(key) ? key : "optimizationReach";
}

function isContentKind(value: unknown): value is AdContentKind {
  return value === "post" || value === "reel" || value === "video" || value === "event" || value === "listing";
}

function clampAge(value: unknown, fallback: number): number {
  const num = Math.floor(Number(value));
  if (!Number.isFinite(num)) return fallback;
  return Math.min(MAX_TARGET_AGE, Math.max(MIN_TARGET_AGE, num));
}

function stringList(value: unknown): string[] {
  return (Array.isArray(value) ? value : []).map((item) => String(item || "").trim()).filter(Boolean);
}

function idList(value: unknown): number[] {
  return (Array.isArray(value) ? value : [])
    .map((item) => Math.floor(Number(item)))
    .filter((id) => Number.isFinite(id) && id > 0);
}

/** True when the draft carries anything an advertiser would mind losing. */
export function campaignDraftHasContent(draft: CampaignDraft): boolean {
  return Boolean(
    draft.objective ||
      draft.setup.name.trim() ||
      draft.setup.budgetAmount.trim() ||
      draft.creative.primaryText.trim() ||
      draft.creative.headline.trim() ||
      draft.creative.contentRefId > 0 ||
      draft.creative.mediaAssetId > 0
  );
}

/* ------------------------------------------------------------------ *
 * Validation
 * ------------------------------------------------------------------ */

export type CampaignDraftIssue = { field: string; messageKey: string };

const ERROR = (field: string, key: string): CampaignDraftIssue => ({
  field,
  messageKey: `commerce:adsWizard.${key}`
});

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function parseBudgetCents(amount: string): number {
  const value = Number(String(amount || "").trim());
  if (!Number.isFinite(value) || value <= 0) return 0;
  return Math.round(value * 100);
}

/** Validates ONE wizard step. `review` runs every step's checks. */
export function validateCampaignStep(step: CampaignWizardStep, draft: CampaignDraft): CampaignDraftIssue[] {
  if (step === "review") {
    return [
      ...validateCampaignStep("objective", draft),
      ...validateCampaignStep("setup", draft),
      ...validateCampaignStep("audience", draft),
      ...validateCampaignStep("placements", draft),
      ...validateCampaignStep("creative", draft)
    ];
  }
  const issues: CampaignDraftIssue[] = [];
  switch (step) {
    case "objective": {
      if (!draft.objective) issues.push(ERROR("objective", "errorObjective"));
      break;
    }
    case "setup": {
      if (!draft.setup.name.trim()) issues.push(ERROR("name", "errorName"));
      const cents = parseBudgetCents(draft.setup.budgetAmount);
      if (cents <= 0) {
        issues.push(ERROR("budgetAmount", "errorBudget"));
      } else if (draft.setup.budgetType === "daily" && cents > AD_DAILY_BUDGET_MAX_CENTS) {
        issues.push(ERROR("budgetAmount", "errorBudgetMax"));
      } else if (draft.setup.budgetType === "lifetime" && cents > AD_LIFETIME_BUDGET_MAX_CENTS) {
        issues.push(ERROR("budgetAmount", "errorBudgetMax"));
      }
      if (!DATE_RE.test(draft.setup.startDate.trim())) issues.push(ERROR("startDate", "errorStartDate"));
      const endDate = draft.setup.endDate.trim();
      if (draft.setup.budgetType === "lifetime" && !endDate) {
        issues.push(ERROR("endDate", "errorEndDateRequired"));
      } else if (endDate) {
        if (!DATE_RE.test(endDate)) {
          issues.push(ERROR("endDate", "errorEndDate"));
        } else if (DATE_RE.test(draft.setup.startDate.trim()) && endDate <= draft.setup.startDate.trim()) {
          issues.push(ERROR("endDate", "errorEndAfterStart"));
        }
      }
      break;
    }
    case "audience": {
      if (draft.audience.minAge > draft.audience.maxAge) issues.push(ERROR("ageRange", "errorAgeRange"));
      break;
    }
    case "placements": {
      if (draft.placements.mode === "manual" && draft.placements.keys.length === 0) {
        issues.push(ERROR("placements", "errorPlacements"));
      }
      break;
    }
    case "creative": {
      const creative = draft.creative;
      if (creative.source === "existing") {
        if (creative.contentRefId <= 0 || !creative.contentRefType) {
          issues.push(ERROR("contentRef", "errorContentRef"));
        }
      } else if (creative.mediaAssetId <= 0) {
        issues.push(ERROR("media", "errorMedia"));
      }
      if (!creative.primaryText.trim()) issues.push(ERROR("primaryText", "errorPrimaryText"));
      if (creative.primaryText.length > PRIMARY_TEXT_MAX_LENGTH) {
        issues.push(ERROR("primaryText", "errorPrimaryTextLength"));
      }
      if (!creative.headline.trim()) issues.push(ERROR("headline", "errorHeadline"));
      if (creative.headline.length > HEADLINE_MAX_LENGTH) issues.push(ERROR("headline", "errorHeadlineLength"));
      const meta = draft.objective ? AD_OBJECTIVE_METADATA[draft.objective] : null;
      const url = creative.destinationUrl.trim();
      if (meta?.requiresDestinationUrl && !isHttpUrl(url)) {
        issues.push(ERROR("destinationUrl", "errorDestination"));
      }
      if (
        meta?.requiresListingOrDestination &&
        !(creative.contentRefType === "listing" && creative.contentRefId > 0) &&
        !isHttpUrl(url)
      ) {
        issues.push(ERROR("destinationUrl", "errorMarketplaceCreative"));
      }
      if (url && !isHttpUrl(url)) issues.push(ERROR("destinationUrl", "errorDestinationFormat"));
      break;
    }
    case "budget": {
      // The step edits `setup`'s budget/schedule fields; re-run just those
      // checks so a bad edit blocks here instead of surfacing at review.
      const budgetFields = new Set(["budgetAmount", "endDate"]);
      issues.push(...validateCampaignStep("setup", draft).filter((issue) => budgetFields.has(issue.field)));
      break;
    }
  }
  return issues;
}

function isHttpUrl(value: string): boolean {
  return /^https?:\/\/\S+\.\S+/.test(value.trim());
}

export function campaignDraftIssueFor(issues: CampaignDraftIssue[], field: string): string {
  return issues.find((issue) => issue.field === field)?.messageKey || "";
}

/* ------------------------------------------------------------------ *
 * Payload assembly
 * ------------------------------------------------------------------ */

function creativeTypeFor(draft: CampaignDraft): AdCreativeType {
  const creative = draft.creative;
  if (creative.source === "existing" && creative.contentRefType) {
    switch (creative.contentRefType) {
      case "listing":
        return "listing";
      case "post":
        return "post";
      case "reel":
        return "reel";
      case "event":
        return "event";
      case "video":
        return "video";
    }
  }
  if (creative.source === "new") return creative.newMediaKind === "video" ? "video" : "image";
  return "text";
}

export function buildCampaignTargetingPayload(draft: CampaignDraft): AdTargetingPayload {
  return normalizeAdTargeting({
    countries: draft.audience.countries,
    languages: draft.audience.languages,
    min_age: draft.audience.minAge,
    max_age: draft.audience.maxAge,
    device_type: draft.audience.deviceType,
    interests: draft.audience.interests,
    keywords: draft.audience.keywords,
    audience_mode: draft.audience.audienceMode,
    saved_audience_ids: draft.audience.savedAudienceIds,
    excluded_audience_ids: draft.audience.excludedAudienceIds
  });
}

export function buildFullCampaignPayload(draft: CampaignDraft, options: { submit: boolean }): AdFullCampaignPayload {
  const cents = parseBudgetCents(draft.setup.budgetAmount);
  const lifetime = draft.setup.budgetType === "lifetime";
  const dailyCents = lifetime ? 0 : Math.min(cents, AD_DAILY_BUDGET_MAX_CENTS);
  const lifetimeCents = lifetime ? Math.min(cents, AD_LIFETIME_BUDGET_MAX_CENTS) : 0;
  const creative = draft.creative;
  const payload: AdFullCampaignPayload = {
    idempotency_key: draft.idempotencyKey,
    ad_account_id: draft.accountId,
    campaign: {
      campaign_name: draft.setup.name.trim(),
      objective: draft.objective || "awareness",
      budget_type: draft.setup.budgetType,
      daily_budget_cents: dailyCents,
      lifetime_budget_cents: lifetimeCents,
      start_at: draft.setup.startDate.trim(),
      end_at: draft.setup.endDate.trim()
    },
    targeting: buildCampaignTargetingPayload(draft),
    placements: draft.placements.mode === "manual" ? draft.placements.keys.filter(isAdPlacementKey) : [],
    creative: {
      creative_type: creativeTypeFor(draft),
      title: creative.title.trim() || draft.setup.name.trim(),
      headline: creative.headline.trim(),
      primary_text: creative.primaryText.trim(),
      body: creative.body.trim(),
      call_to_action: creative.callToAction,
      destination_url: creative.destinationUrl.trim()
    },
    submit: options.submit
  };
  if (draft.setup.specialCategory) payload.campaign.special_category = draft.setup.specialCategory;
  if (creative.source === "new" && creative.mediaAssetId > 0) {
    payload.creative.media_asset_id = creative.mediaAssetId;
  }
  if (creative.source === "existing" && creative.contentRefId > 0 && creative.contentRefType) {
    payload.creative.content_ref_type = creative.contentRefType;
    payload.creative.content_ref_id = creative.contentRefId;
  }
  return payload;
}

/** Placement labels are i18n suffixes under `commerce:adsWizard.`. */
export const AD_PLACEMENT_LABEL_KEYS: Record<(typeof AD_PLACEMENT_KEYS)[number], string> = {
  feed_inline: "placementFeed",
  feed_inline_ufo_mobile: "placementMobileFeed",
  marketplace_sponsor: "placementMarketplace",
  search_sponsored_result: "placementSearch",
  video_pre_roll: "placementVideos",
  profile_sponsor: "placementProfile",
  status_interstitial: "placementStatus",
  pulse_radio_sponsor: "placementPulseRadio"
};

export const OBJECTIVE_LIST = AD_CANONICAL_OBJECTIVES;
