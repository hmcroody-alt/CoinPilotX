import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const GROWTH_STATE_CACHE_KEY = "pulsesoc.native.growth.state";

export type GrowthWallet = {
  status?: string;
  credits_cents?: number;
  currency?: string;
};

export type GrowthAccount = {
  public_id?: string;
  status?: string;
  lifecycle_stage?: string;
  default_workspace_id?: number | string;
};

export type GrowthAnalytics = {
  container_public_id?: string;
  conversion_tracking_id?: string;
  status?: string;
};

export type GrowthSummary = {
  account?: GrowthAccount;
  wallet?: GrowthWallet;
  analytics?: GrowthAnalytics;
  growth_score?: number;
  preferences?: Record<string, unknown>;
  modules?: string[];
  audience_categories?: string[];
};

export type GrowthPortal = {
  ok?: boolean;
  summary?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  cards?: Array<Record<string, unknown>>;
  modules?: Array<Record<string, unknown>>;
  recommendations?: string[];
  message?: string;
};

export type GrowthState = {
  ok?: boolean;
  growth?: GrowthSummary;
  portal?: GrowthPortal;
  hero?: {
    title?: string;
    body?: string;
    cta?: string;
  };
  message?: string;
};

export async function getGrowthState() {
  const state = normalizeGrowthState(await pulseApi<GrowthState>("/api/pulse/growth"));
  await cacheGrowthState(state).catch(() => undefined);
  return state;
}

export async function loadCachedGrowthState() {
  return readJsonCache<GrowthState>(GROWTH_STATE_CACHE_KEY, normalizeGrowthState);
}

export async function cacheGrowthState(state: GrowthState) {
  await writeJsonCache(GROWTH_STATE_CACHE_KEY, normalizeGrowthState(state));
}

export async function openGrowthWebFallback(path = "/pulse/growth") {
  const target = /^https?:\/\//i.test(path) ? path : `${PULSE_API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  return {
    ok: false,
    target,
    status: "native_provider_boundary",
    message: "This action stays in Growth Center in the app until it is ready to use."
  };
}

export function normalizeGrowthState(input: GrowthState): GrowthState {
  const growth = input.growth || {};
  const wallet = growth.wallet || {};
  const account = growth.account || {};
  const analytics = growth.analytics || {};
  const portal = input.portal || {};
  return {
    ...input,
    growth: {
      ...growth,
      account: {
        ...account,
        status: String(account.status || "ready"),
        lifecycle_stage: String(account.lifecycle_stage || "provisioned")
      },
      wallet: {
        ...wallet,
        status: String(wallet.status || "inactive"),
        credits_cents: Number(wallet.credits_cents || 0),
        currency: String(wallet.currency || "usd")
      },
      analytics: {
        ...analytics,
        status: String(analytics.status || "ready")
      },
      growth_score: Number(growth.growth_score || 0),
      preferences: growth.preferences || {},
      modules: Array.isArray(growth.modules) ? growth.modules.map(String) : [],
      audience_categories: Array.isArray(growth.audience_categories) ? growth.audience_categories.map(String) : []
    },
    portal: {
      ...portal,
      cards: Array.isArray(portal.cards) ? portal.cards : [],
      modules: Array.isArray(portal.modules) ? portal.modules : [],
      recommendations: Array.isArray(portal.recommendations) ? portal.recommendations.map(String) : []
    },
    hero: input.hero || {
      title: "Grow your reach.",
      body: "PulseSoc keeps track of your growth.",
      cta: "Explore Growth"
    }
  };
}

export function growthMoney(cents?: number, currency = "usd") {
  const value = Number(cents || 0) / 100;
  return new Intl.NumberFormat("en-US", { style: "currency", currency: currency.toUpperCase() }).format(value);
}

export function promotionWebPath(contentType?: string, contentId?: number | string) {
  const params = new URLSearchParams();
  if (contentType) params.set("content_type", contentType);
  if (contentId) params.set("content_id", String(contentId));
  const query = params.toString();
  return `/pulse/promote${query ? `?${query}` : ""}`;
}
