/**
 * Pulse Briefings API — the native hub's only data source.
 *
 * Everything here is a thin wrapper over the production briefing routes:
 *
 *   GET   /api/pulse/briefings?limit&offset      history (sent/generated only)
 *   GET   /api/pulse/briefings/:id               one briefing + grounded facts
 *   GET   /api/pulse/briefings/preferences       canonical server preferences
 *   PATCH /api/pulse/briefings/preferences       preference writes
 *   GET   /api/pulse/briefings/status            delivery status + unseen count
 *   POST  /api/pulse/briefings/seen              clear the unread cursor
 *
 * The server is the single authority: the scheduler reads the same preference
 * row these calls write, history contains only briefings that really reached
 * (or were generated for) the user, and the unread badge is derived from the
 * server-side seen cursor — never from device-local state. Nothing in this
 * module fabricates a briefing.
 */

import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";

const BRIEFINGS_CACHE_KEY = "pulsesoc.native.briefings.firstpage";
const STATUS_CACHE_KEY = "pulsesoc.native.briefings.status";

/** Server frequency vocabulary. "smart" is the recommended default cadence. */
export type BriefingFrequency =
  | "off"
  | "important_only"
  | "every_6h"
  | "morning_evening"
  | "daily"
  | "smart";

export type BriefingListItem = {
  id: number;
  window_key: string;
  status: "sent" | "generated";
  title: string;
  body: string;
  summary_source?: string;
  locale?: string;
  generated_at?: string;
  sent_at?: string;
};

export type BriefingFacts = {
  generated_at?: string;
  timezone?: string;
  network?: {
    unread_messages?: number;
    friend_requests?: number;
    new_followers?: number;
    mentions?: number;
    comments?: number;
    reactions?: number;
    marketplace_orders?: number;
    security_alerts?: number;
    community_events?: number;
  } | null;
  crypto?: {
    available?: boolean;
    provider?: string;
    observed_at?: string;
    btc_price?: number;
    btc_change_24h?: number;
    eth_price?: number;
    eth_change_24h?: number;
    total_market_cap?: number;
    market_cap_change_24h_pct?: number;
    btc_dominance?: number;
    market_direction?: string;
    gainers?: Array<{ symbol: string; change_24h: number }>;
    losers?: Array<{ symbol: string; change_24h: number }>;
    trending?: Array<{ symbol: string; rank?: number }>;
    watchlist?: Array<{ symbol: string; price?: number; change_24h?: number }>;
    alert_proximity?: Array<{ symbol: string; threshold: number; distance_pct: number }>;
  } | null;
};

export type BriefingDetail = BriefingListItem & {
  facts: BriefingFacts;
  crypto_provider?: string;
  created_at?: string;
};

export type BriefingPreferences = {
  enabled: boolean;
  network_enabled: boolean;
  crypto_enabled: boolean;
  watchlist_enabled: boolean;
  frequency: BriefingFrequency;
  quiet_start: string;
  quiet_end: string;
};

export type BriefingDeliveryStatus = {
  enabled: boolean;
  frequency: BriefingFrequency;
  frequencies: BriefingFrequency[];
  quiet_start: string;
  quiet_end: string;
  /** IANA zone the server schedules against — shown, never silently UTC. */
  timezone: string;
  push_enabled: boolean;
  briefings_feature_enabled: boolean;
  last_briefing: {
    id: number;
    title: string;
    status: string;
    generated_at?: string;
    sent_at?: string;
    created_at?: string;
  } | null;
  /** Local-time evaluation estimate. Copy must say "around" — never a promise. */
  next_check_local: string | null;
  unseen_count: number;
};

export type BriefingHistoryPage = {
  briefings: BriefingListItem[];
  has_more: boolean;
  next_offset: number | null;
};

export const BRIEFING_HISTORY_PAGE_SIZE = 20;

export async function listBriefings(params: { limit?: number; offset?: number } = {}): Promise<BriefingHistoryPage> {
  const limit = params.limit || BRIEFING_HISTORY_PAGE_SIZE;
  const offset = params.offset || 0;
  const page = await pulseApi<{ ok: boolean } & BriefingHistoryPage>(
    `/api/pulse/briefings?limit=${encodeURIComponent(String(limit))}&offset=${encodeURIComponent(String(offset))}`
  );
  const normalized: BriefingHistoryPage = {
    briefings: Array.isArray(page.briefings) ? page.briefings : [],
    has_more: Boolean(page.has_more),
    next_offset: typeof page.next_offset === "number" ? page.next_offset : null
  };
  if (offset === 0) await writeJsonCache(BRIEFINGS_CACHE_KEY, normalized).catch(() => undefined);
  return normalized;
}

/** Offline fallback for the first page only. Real server rows, cached — never fabricated. */
export async function loadCachedBriefings(): Promise<BriefingHistoryPage | null> {
  return readJsonCache<BriefingHistoryPage>(BRIEFINGS_CACHE_KEY, (value) => ({
    briefings: Array.isArray(value?.briefings) ? value.briefings : [],
    has_more: Boolean(value?.has_more),
    next_offset: typeof value?.next_offset === "number" ? value.next_offset : null
  }));
}

export async function getBriefing(briefingId: number): Promise<BriefingDetail> {
  const payload = await pulseApi<{ ok: boolean; briefing: BriefingDetail }>(
    `/api/pulse/briefings/${encodeURIComponent(String(briefingId))}`
  );
  return payload.briefing;
}

export async function getBriefingPreferences(): Promise<BriefingPreferences> {
  const payload = await pulseApi<{ ok: boolean; preferences: BriefingPreferences }>(
    "/api/pulse/briefings/preferences"
  );
  return payload.preferences;
}

export async function updateBriefingPreferences(patch: Partial<BriefingPreferences>): Promise<BriefingPreferences> {
  const payload = await pulseApi<{ ok: boolean; preferences: BriefingPreferences }>(
    "/api/pulse/briefings/preferences",
    { method: "PATCH", body: JSON.stringify({ preferences: patch }) }
  );
  return payload.preferences;
}

export async function getBriefingStatus(): Promise<BriefingDeliveryStatus> {
  const payload = await pulseApi<{ ok: boolean; status: BriefingDeliveryStatus }>(
    "/api/pulse/briefings/status"
  );
  await writeJsonCache(STATUS_CACHE_KEY, payload.status).catch(() => undefined);
  return payload.status;
}

export async function loadCachedBriefingStatus(): Promise<BriefingDeliveryStatus | null> {
  return readJsonCache<BriefingDeliveryStatus>(STATUS_CACHE_KEY, (value) => value);
}

export async function markBriefingsSeen(): Promise<void> {
  await pulseApi<{ ok: boolean }>("/api/pulse/briefings/seen", { method: "POST", body: "{}" });
}
