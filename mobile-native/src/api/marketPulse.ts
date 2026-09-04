/**
 * Market Pulse client — the live crypto command center's only data path.
 *
 * ## The phone never talks to CoinGecko
 *
 * Every read here goes to `/api/pulse/market/*`, which reads the shared market
 * foundation the dashboard board and Pulse Briefings already poll. One provider
 * call per window serves every user, so user count does not scale provider
 * spend. There is deliberately no CoinGecko host, no API key and no coin-id
 * table in this file: a symbol is not a CoinGecko id, and resolving one to the
 * other is server work. A second resolver in native code would drift from the
 * first the day a coin is relisted under a new id.
 *
 * ## Absence is null, all the way to the pixel
 *
 * `Number(null)` is `0`, and `$0.00` beside `0.00%` reads on screen as a fact
 * about the market rather than as a gap in the data. So the server sends null
 * for "we do not know" and this file's whole job on the way in is to keep that
 * null intact for `formatPrice` to render as "--". There is no last-known-price
 * fallback and no percentage computed from two numbers we happen to hold.
 *
 * ## Freshness is a server claim, not a client guess
 *
 * `freshness.live` is the only field a screen may consult before drawing a LIVE
 * dot, and `ageSeconds` is measured from when the provider answered — not from
 * when this request was served, so a cache hit cannot reset "Updated 12s ago"
 * to zero. A client that timed its own fetch would report the age of the
 * request instead of the age of the price.
 *
 * ## Writes are not here
 *
 * Adding to a watchlist and creating an alert go through the existing crypto
 * API (`api/watchlists.ts`, `api/alerts.ts`). There is one watchlist store and
 * one alert engine in this product, and Market Pulse is a reader of both.
 */

import { readJsonCache, writeJsonCache } from "../core/cache";
import {
  AssetIntelligence,
  MarketRegime,
  MarketRotation,
  normalizeIntelligence,
  normalizeRegime,
  normalizeRotation
} from "./marketIntelligence";
import { pulseApi } from "./pulseApi";

const SNAPSHOT_CACHE_PREFIX = "pulsesoc.native.marketpulse.snapshot.";

/**
 * The chips, in display order.
 *
 * Only categories the canonical provider data can actually answer. "trending"
 * is CoinGecko's own search-trending signal rather than a sort of the price
 * board — labelling today's biggest mover "trending" would be a claim the
 * provider never made.
 */
export const MARKET_CATEGORIES = ["all", "gainers", "losers", "trending", "watchlist"] as const;
export type MarketCategory = (typeof MARKET_CATEGORIES)[number];

/**
 * Chart ranges the backend can answer, in the spelling the chart shows.
 *
 * 30D and 90D are aliases the server resolves to its stored 1M/3M keys, so no
 * existing caller, cache entry or saved preference has to change spelling for
 * the chart to read the way a trader expects. ALL is bounded server-side by the
 * provider plan; the client never asks for `max`.
 */
export const PULSE_RANGES = ["1H", "24H", "7D", "30D", "90D", "1Y", "ALL"] as const;
export type PulseRange = (typeof PULSE_RANGES)[number];

/** One asset, in PulseSoc's own shape. No CoinGecko field name survives. */
export type MarketAsset = {
  id: string;
  symbol: string;
  name: string;
  rank: number | null;
  price: number | null;
  change24h: number | null;
  marketCap: number | null;
  volume24h: number | null;
  image: string;
  sparkline: number[];
  updatedAt: string | null;
  /** Per-account, merged server-side inside one authenticated request. */
  watching: boolean;
  favorite: boolean;
  alertCount: number;
  /** Set only on rows that came from the provider's trending list. */
  trending: boolean;
  /**
   * The server's verdict for this row, or null when it could not form one.
   *
   * Null is a real state and is not the same as a cautious verdict: the row's
   * price is still true, the analysis simply had too little history. A card
   * renders nothing rather than a placeholder opinion.
   */
  intelligence: AssetIntelligence | null;
};

export type MarketFreshness = {
  ageSeconds: number | null;
  observedAt: string | null;
  source: string;
  /** The single field a screen may consult before claiming to be live. */
  live: boolean;
  stale: boolean;
  degraded: boolean;
  warning: string;
};

export type GlobalMetrics = {
  available: boolean;
  provider: string;
  observedAt: string | null;
  stale: boolean;
  totalMarketCap: number | null;
  totalVolume24h: number | null;
  btcDominance: number | null;
  ethDominance: number | null;
  marketCapChange24hPct: number | null;
  marketDirection: string;
};

export type MarketSnapshot = {
  ok: boolean;
  category: MarketCategory;
  assets: MarketAsset[];
  freshness: MarketFreshness;
  global: GlobalMetrics;
  /** Present on the trending payload so nobody mistakes it for a price sort. */
  basis: string;
  /**
   * False when the per-account overlay could not be read. The market list is
   * still true without the badges, so the screen stays up and simply does not
   * claim to know what the user is watching.
   */
  personalized: boolean;
  /**
   * Conditions across the whole board, computed from breadth the snapshot
   * already contains — no extra provider call and no paid breadth feed. Null
   * when the board was too small to measure it.
   */
  regime: MarketRegime | null;
  rotation: MarketRotation | null;
};

export type PulseHistoryPoint = { t: number; price: number };

export type PulseHistory = {
  ok: boolean;
  symbol: string;
  range: string;
  points: PulseHistoryPoint[];
  source: string;
  warning: string;
  stale: boolean;
};

type Raw<T> = T extends (infer U)[] ? Raw<U>[] : T extends object ? { [K in keyof T]?: Raw<T[K]> } : T;

/** A number, or null when the server had nothing. `0` is a price; null is not. */
function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function nullableText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text ? text : null;
}

function sparklinePoints(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  // A gap in the provider's series is dropped rather than plotted. `Number(null)`
  // is `0` and passes a finiteness check, so the obvious spelling of this filter
  // would draw every gap as a crash to zero — a much louder lie than a slightly
  // compressed shape.
  return value.map(nullableNumber).filter((point): point is number => point !== null);
}

export function normalizeMarketAsset(input: Raw<MarketAsset> = {}): MarketAsset {
  const symbol = String(input.symbol || "").toUpperCase();
  return {
    id: String(input.id || symbol.toLowerCase()),
    symbol,
    name: String(input.name || symbol),
    rank: nullableNumber(input.rank),
    price: nullableNumber(input.price),
    change24h: nullableNumber(input.change24h),
    marketCap: nullableNumber(input.marketCap),
    volume24h: nullableNumber(input.volume24h),
    image: String(input.image || ""),
    sparkline: sparklinePoints(input.sparkline),
    updatedAt: nullableText(input.updatedAt),
    watching: Boolean(input.watching),
    favorite: Boolean(input.favorite),
    // A badge count, not a price: 0 is the honest answer for "no alerts".
    alertCount: Math.max(0, Number(input.alertCount) || 0),
    trending: Boolean(input.trending),
    // Owned by `marketIntelligence.ts`, because the same shape arrives from two
    // endpoints and one normalizer for it is one place to keep it honest.
    intelligence: normalizeIntelligence(input.intelligence)
  };
}

export function normalizeFreshness(input: Raw<MarketFreshness> = {}): MarketFreshness {
  return {
    ageSeconds: nullableNumber(input.ageSeconds),
    observedAt: nullableText(input.observedAt),
    source: String(input.source || "unavailable"),
    live: Boolean(input.live),
    // An unreadable payload is treated as stale rather than as fresh. The
    // failure mode of the opposite default is a LIVE dot over old numbers.
    stale: input.stale === undefined ? true : Boolean(input.stale),
    degraded: Boolean(input.degraded),
    warning: String(input.warning || "")
  };
}

export function normalizeGlobalMetrics(input: Raw<GlobalMetrics> = {}): GlobalMetrics {
  return {
    available: Boolean(input.available),
    provider: String(input.provider || ""),
    observedAt: nullableText(input.observedAt),
    stale: input.stale === undefined ? true : Boolean(input.stale),
    totalMarketCap: nullableNumber(input.totalMarketCap),
    totalVolume24h: nullableNumber(input.totalVolume24h),
    btcDominance: nullableNumber(input.btcDominance),
    ethDominance: nullableNumber(input.ethDominance),
    marketCapChange24hPct: nullableNumber(input.marketCapChange24hPct),
    marketDirection: String(input.marketDirection || "unknown")
  };
}

function isCategory(value: unknown): value is MarketCategory {
  return (MARKET_CATEGORIES as readonly string[]).includes(String(value));
}

export function normalizeSnapshot(input: Raw<MarketSnapshot> = {}): MarketSnapshot {
  return {
    ok: Boolean(input.ok),
    category: isCategory(input.category) ? (input.category as MarketCategory) : "all",
    assets: (input.assets || []).map(normalizeMarketAsset).filter((asset) => Boolean(asset.symbol)),
    freshness: normalizeFreshness(input.freshness || {}),
    global: normalizeGlobalMetrics(input.global || {}),
    basis: String(input.basis || ""),
    personalized: Boolean(input.personalized),
    regime: normalizeRegime(input.regime),
    rotation: normalizeRotation(input.rotation)
  };
}

export function normalizeHistory(input: Raw<PulseHistory> = {}): PulseHistory {
  const points = (input.points || [])
    .map((point) => ({ t: nullableNumber(point?.t), price: nullableNumber(point?.price) }))
    .filter((point): point is PulseHistoryPoint => point.t !== null && point.price !== null);
  return {
    ok: Boolean(input.ok) && points.length > 0,
    symbol: String(input.symbol || "").toUpperCase(),
    range: String(input.range || "24H").toUpperCase(),
    points,
    source: String(input.source || "unavailable"),
    warning: String(input.warning || ""),
    stale: Boolean(input.stale)
  };
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/**
 * Everything the screen needs on open, in one request.
 *
 * Pull-to-refresh calls exactly this. It does not carry a bypass flag, because
 * the cache, the single-flight and the credit guard it would bypass are the
 * only reasons the provider bill does not scale with how hard users pull.
 */
export async function getMarketSnapshot(category: MarketCategory = "all", limit = 50) {
  const snapshot = normalizeSnapshot(
    await pulseApi<MarketSnapshot>(
      `/api/pulse/market/snapshot?category=${encodeURIComponent(category)}&limit=${encodeURIComponent(String(limit))}`
    )
  );
  // Only a snapshot with rows is worth keeping. Caching an empty outage payload
  // would make a thirty-second provider blip look permanent on next launch.
  if (snapshot.assets.length) {
    await writeJsonCache(`${SNAPSHOT_CACHE_PREFIX}${category}`, snapshot).catch(() => undefined);
  }
  return snapshot;
}

export async function loadCachedMarketSnapshot(category: MarketCategory = "all") {
  return readJsonCache<MarketSnapshot>(`${SNAPSHOT_CACHE_PREFIX}${category}`, normalizeSnapshot);
}

/** The global strip alone — a cheaper foreground refresh than the whole board. */
export async function getGlobalMetrics() {
  const response = await pulseApi<{ ok?: boolean; global?: Raw<GlobalMetrics> }>("/api/pulse/market/global");
  return normalizeGlobalMetrics(response.global || {});
}

/**
 * Search the same board that supplies prices.
 *
 * Every hit is therefore openable. Offering a coin the price engine has never
 * heard of would hand the user a detail screen that can only read "Unavailable".
 */
export async function searchMarketAssets(query: string, limit = 25) {
  const response = await pulseApi<{ ok?: boolean; assets?: Raw<MarketAsset>[] }>(
    `/api/pulse/market/search?q=${encodeURIComponent(query)}&limit=${encodeURIComponent(String(limit))}`
  );
  return (response.assets || []).map(normalizeMarketAsset).filter((asset) => Boolean(asset.symbol));
}

/** Real price history for one asset and range. Never a synthesised series. */
export async function getPulseHistory(symbol: string, range: PulseRange = "24H") {
  return normalizeHistory(
    await pulseApi<PulseHistory>(
      `/api/pulse/market/assets/${encodeURIComponent(symbol.toUpperCase())}/history?range=${encodeURIComponent(range)}`
    )
  );
}

// ---------------------------------------------------------------------------
// Display helpers
//
// The single place "we do not know" becomes "--", so no screen has to remember
// to check for null before formatting.
// ---------------------------------------------------------------------------

export const UNKNOWN = "--";

export function formatBigMoney(value: number | null): string {
  if (value === null) return UNKNOWN;
  const abs = Math.abs(value);
  if (abs >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function formatDominance(value: number | null): string {
  return value === null ? UNKNOWN : `${value.toFixed(1)}%`;
}

/**
 * "Updated 12s ago" — or nothing at all when the age is unknown.
 *
 * A null age means the server could not say when the price was observed, and
 * "Updated just now" would be the app inventing the one number this label
 * exists to report.
 */
export function formatAge(ageSeconds: number | null): string {
  if (ageSeconds === null || ageSeconds < 0) return "";
  if (ageSeconds < 60) return `Updated ${Math.floor(ageSeconds)}s ago`;
  if (ageSeconds < 3600) return `Updated ${Math.floor(ageSeconds / 60)}m ago`;
  if (ageSeconds < 86400) return `Updated ${Math.floor(ageSeconds / 3600)}h ago`;
  return `Updated ${Math.floor(ageSeconds / 86400)}d ago`;
}
