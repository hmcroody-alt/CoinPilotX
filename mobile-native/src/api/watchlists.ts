/**
 * Watchlists + asset detail client.
 *
 * ## Why every numeric field is `number | null`
 *
 * The usual normalizer in this folder writes `Number(input.x || 0)`, which is
 * right for counts and wrong here. A price the provider could not supply would
 * become `0`, and `$0.00` next to `0.00%` reads on screen as a fact about the
 * market rather than as an absence. So the server sends null for "we do not
 * know", and this file's job is to keep null intact all the way to the view,
 * where it renders as "--".
 *
 * That is also why there is no client-side default for `change_24h`, no
 * last-known-price fallback, and no computing a percentage from two prices we
 * happen to have lying around. If the server did not say it, we do not show it.
 *
 * ## Caching
 *
 * The screen renders from cache first and revalidates, so opening Watchlists is
 * instant on a warm start. Cached numbers are still real numbers that were true
 * when written — never invented — but they are old, so the view labels them via
 * `market.updated_at` and the stale flag rather than presenting them as live.
 */

import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";
import type { AlertRule } from "./alerts";
import { normalizeAlertRules } from "./alerts";

const WATCHLIST_MARKET_CACHE_KEY = "pulsesoc.native.crypto.watchlists.market";
const ASSET_DETAIL_CACHE_PREFIX = "pulsesoc.native.crypto.asset.";
const ASSET_HISTORY_CACHE_PREFIX = "pulsesoc.native.crypto.history.";

/** The ranges the backend knows how to answer. The server still decides which
 *  of them a given asset actually supports; this is only the union. */
export const HISTORY_RANGES = ["1H", "24H", "7D", "1M", "1Y", "ALL"] as const;
export type HistoryRange = (typeof HISTORY_RANGES)[number];

export type AssetQuote = {
  symbol: string;
  name: string;
  image: string;
  price: number | null;
  change_24h: number | null;
  price_change_24h: number | null;
  market_cap: number | null;
  market_cap_rank: number | null;
  volume_24h: number | null;
  circulating_supply: number | null;
  max_supply: number | null;
  sparkline: number[];
  has_market_data: boolean;
  favorite?: boolean;
};

export type WatchlistAsset = AssetQuote & {
  id: number;
  watchlist_id: number;
  position: number;
  notes: string;
  favorite: boolean;
  alert_count: number;
};

export type Watchlist = {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
  asset_count: number;
  /** Mean 24h change across the assets we actually have a price for, or null. */
  average_change_24h: number | null;
  priced_asset_count: number;
  assets: WatchlistAsset[];
};

export type MarketStatus = {
  source: string;
  updated_at: string;
  warning: string;
  ready: boolean;
};

export type WatchlistMarketView = {
  ok?: boolean;
  watchlists: Watchlist[];
  favorites: WatchlistAsset[];
  market: MarketStatus;
};

export type AssetMembership = {
  watchlist_id: number;
  watchlist_name: string;
  asset_id: number;
};

export type AssetDetail = {
  ok?: boolean;
  asset: AssetQuote;
  watchlists: AssetMembership[];
  alerts: AlertRule[];
  /** Only the chart ranges this asset can truthfully answer. */
  ranges: HistoryRange[];
  market: MarketStatus;
};

export type HistoryPoint = { t: number; price: number };

export type AssetHistory = {
  ok?: boolean;
  symbol: string;
  range: HistoryRange;
  points: HistoryPoint[];
  source: string;
  warning: string;
  stale?: boolean;
};

export type WatchlistMutation = {
  ok?: boolean;
  message?: string;
  watchlist_id?: number;
  name?: string;
  assets_removed?: number;
};

/**
 * What the wire is actually allowed to carry.
 *
 * `Partial<T>` only loosens the top level: it still asserts that a `market` key,
 * if present, holds a complete `MarketStatus`. But the server sends `market: {}`
 * during an outage, and half-populated rows are the normal case this whole
 * module exists to absorb. Typing the inputs as `Partial` would bake in exactly
 * the assumption the normalizers are here to remove, and would make the
 * degraded-payload tests unwriteable without casts that hide the problem.
 */
type Raw<T> = T extends (infer U)[] ? Raw<U>[] : T extends object ? { [K in keyof T]?: Raw<T[K]> } : T;

/**
 * A number, or null if the server had nothing.
 *
 * `Number(null)` is 0 and `Number(undefined)` is NaN; both would render as a
 * price. Only a genuinely finite number survives.
 */
function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function plainNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeSparkline(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  // `Number(null)` is `0` and survives a finiteness check, so a gap in the
  // provider's series would be drawn as a dip to zero. Dropping the gap shifts
  // the remaining points along the x axis a little, which is the right trade:
  // a sparkline is a shape, not a timed series, and a false crash is a much
  // louder error than a slightly compressed one.
  return value
    .map((point) => nullableNumber(point))
    .filter((point): point is number => point !== null);
}

export function normalizeAssetQuote(input: Raw<AssetQuote> = {}): AssetQuote {
  return {
    ...input,
    symbol: String(input.symbol || "").toUpperCase(),
    name: String(input.name || input.symbol || "").trim(),
    image: String(input.image || ""),
    price: nullableNumber(input.price),
    change_24h: nullableNumber(input.change_24h),
    price_change_24h: nullableNumber(input.price_change_24h),
    market_cap: nullableNumber(input.market_cap),
    market_cap_rank: nullableNumber(input.market_cap_rank),
    volume_24h: nullableNumber(input.volume_24h),
    circulating_supply: nullableNumber(input.circulating_supply),
    max_supply: nullableNumber(input.max_supply),
    sparkline: normalizeSparkline(input.sparkline),
    has_market_data: Boolean(input.has_market_data),
    favorite: Boolean(input.favorite)
  };
}

function normalizeWatchlistAsset(input: Raw<WatchlistAsset> = {}): WatchlistAsset {
  return {
    ...normalizeAssetQuote(input),
    id: plainNumber(input.id),
    watchlist_id: plainNumber(input.watchlist_id),
    position: plainNumber(input.position),
    notes: String(input.notes || ""),
    favorite: Boolean(input.favorite),
    alert_count: plainNumber(input.alert_count)
  };
}

function normalizeMarketStatus(input: Raw<MarketStatus> = {}): MarketStatus {
  return {
    source: String(input.source || "unavailable"),
    updated_at: String(input.updated_at || ""),
    warning: String(input.warning || ""),
    ready: Boolean(input.ready)
  };
}

function normalizeWatchlist(input: Raw<Watchlist> = {}): Watchlist {
  const assets = (input.assets || []).map(normalizeWatchlistAsset);
  return {
    ...input,
    id: plainNumber(input.id),
    name: String(input.name || "").trim(),
    created_at: String(input.created_at || ""),
    updated_at: String(input.updated_at || ""),
    // Trust the server's own count rather than assets.length: they agree today,
    // and if they ever stop, the server is the one that can see the rows.
    asset_count: plainNumber(input.asset_count, assets.length),
    average_change_24h: nullableNumber(input.average_change_24h),
    priced_asset_count: plainNumber(input.priced_asset_count),
    assets
  };
}

export function normalizeWatchlistMarketView(input: Raw<WatchlistMarketView> = {}): WatchlistMarketView {
  return {
    ...input,
    watchlists: (input.watchlists || []).map(normalizeWatchlist).filter((watchlist) => watchlist.id > 0),
    favorites: (input.favorites || []).map(normalizeWatchlistAsset),
    market: normalizeMarketStatus(input.market || {})
  };
}

// `alerts` is held at the alerts module's own `AlertRule` rather than being
// loosened by `Raw`, because that type is already permissive and it is not this
// module's to reinterpret — recursing into it would start describing the alert
// contract from here, which is the drift the shared normalizer exists to stop.
export function normalizeAssetDetail(
  input: Raw<Omit<AssetDetail, "alerts">> & { alerts?: AlertRule[] } = {}
): AssetDetail {
  const ranges = (input.ranges || []).filter((range): range is HistoryRange =>
    (HISTORY_RANGES as readonly string[]).includes(String(range))
  );
  return {
    ...input,
    asset: normalizeAssetQuote(input.asset || {}),
    watchlists: (input.watchlists || []).map((membership) => ({
      watchlist_id: plainNumber(membership.watchlist_id),
      watchlist_name: String(membership.watchlist_name || ""),
      asset_id: plainNumber(membership.asset_id)
    })),
    // The canonical alert normalizer, so an alert looks identical here and on
    // the alerts screen. Two shapes for one rule is how they drift apart.
    alerts: normalizeAlertRules(input.alerts || []),
    ranges,
    market: normalizeMarketStatus(input.market || {})
  };
}

export function normalizeAssetHistory(input: Raw<AssetHistory> = {}): AssetHistory {
  // `Number(null)` is `0`, and `0` passes a finiteness check, so the obvious
  // spelling of this filter silently keeps every gap in the series and plots it
  // at zero. On a line chart that is a worse lie than it is in a table: it draws
  // a spike down to nothing and back, which reads as a real crash rather than as
  // a missing sample. A point we cannot place is dropped, and the line spans the
  // gap instead.
  const points = (input.points || [])
    .map((point) => ({ t: nullableNumber(point?.t), price: nullableNumber(point?.price) }))
    .filter((point): point is HistoryPoint => point.t !== null && point.price !== null);
  return {
    ...input,
    symbol: String(input.symbol || "").toUpperCase(),
    range: (String(input.range || "24H").toUpperCase() as HistoryRange),
    points,
    source: String(input.source || "unavailable"),
    warning: String(input.warning || ""),
    stale: Boolean(input.stale)
  };
}

function normalizeMutation(input: Raw<WatchlistMutation> = {}): WatchlistMutation {
  return { ...input, ok: Boolean(input.ok), message: String(input.message || "") };
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/**
 * Every watchlist with live prices attached, in one request.
 *
 * One call for the whole screen is the point: the server joins the user's lists
 * to a single market snapshot, so fifty rows cost the provider one request
 * rather than fifty, and two lists holding BTC ask about BTC once.
 */
export async function getWatchlistMarketView() {
  const view = normalizeWatchlistMarketView(await pulseApi<WatchlistMarketView>("/api/crypto/watchlists/market"));
  await writeJsonCache(WATCHLIST_MARKET_CACHE_KEY, view).catch(() => undefined);
  return view;
}

export async function loadCachedWatchlistMarketView() {
  return readJsonCache<WatchlistMarketView>(WATCHLIST_MARKET_CACHE_KEY, normalizeWatchlistMarketView);
}

export async function getAssetDetail(symbol: string) {
  const detail = normalizeAssetDetail(
    await pulseApi<AssetDetail>(`/api/crypto/assets/${encodeURIComponent(symbol.toUpperCase())}`)
  );
  await writeJsonCache(`${ASSET_DETAIL_CACHE_PREFIX}${symbol.toUpperCase()}`, detail).catch(() => undefined);
  return detail;
}

export async function loadCachedAssetDetail(symbol: string) {
  return readJsonCache<AssetDetail>(`${ASSET_DETAIL_CACHE_PREFIX}${symbol.toUpperCase()}`, normalizeAssetDetail);
}

export async function getAssetHistory(symbol: string, range: HistoryRange = "24H") {
  const history = normalizeAssetHistory(
    await pulseApi<AssetHistory>(
      `/api/crypto/assets/${encodeURIComponent(symbol.toUpperCase())}/history?range=${encodeURIComponent(range)}`
    )
  );
  // Only a series with real points is worth keeping: caching an empty
  // "unavailable" answer would make a transient outage look permanent.
  if (history.points.length) {
    await writeJsonCache(`${ASSET_HISTORY_CACHE_PREFIX}${symbol.toUpperCase()}.${range}`, history).catch(() => undefined);
  }
  return history;
}

export async function loadCachedAssetHistory(symbol: string, range: HistoryRange = "24H") {
  return readJsonCache<AssetHistory>(
    `${ASSET_HISTORY_CACHE_PREFIX}${symbol.toUpperCase()}.${range}`,
    normalizeAssetHistory
  );
}

export async function searchAssets(query: string, limit = 25) {
  const response = await pulseApi<{ ok?: boolean; assets?: Partial<AssetQuote>[] }>(
    `/api/crypto/assets/search?q=${encodeURIComponent(query)}&limit=${encodeURIComponent(String(limit))}`
  );
  return (response.assets || []).map(normalizeAssetQuote).filter((asset) => Boolean(asset.symbol));
}

// ---------------------------------------------------------------------------
// Mutations
//
// None of these take a user id. Ownership is the authenticated session on the
// server; a client-supplied id would be a request to act as someone else.
// ---------------------------------------------------------------------------

export async function createWatchlist(name: string) {
  return normalizeMutation(
    await pulseApi<WatchlistMutation>("/api/crypto/watchlists", {
      method: "POST",
      body: JSON.stringify({ name })
    })
  );
}

export async function renameWatchlist(watchlistId: number, name: string) {
  return normalizeMutation(
    await pulseApi<WatchlistMutation>(`/api/crypto/watchlists/${encodeURIComponent(String(watchlistId))}`, {
      method: "PATCH",
      body: JSON.stringify({ name })
    })
  );
}

export async function deleteWatchlist(watchlistId: number) {
  return normalizeMutation(
    await pulseApi<WatchlistMutation>(`/api/crypto/watchlists/${encodeURIComponent(String(watchlistId))}`, {
      method: "DELETE"
    })
  );
}

export async function addWatchlistAsset(watchlistId: number, assetSymbol: string, notes = "") {
  return normalizeMutation(
    await pulseApi<WatchlistMutation>(`/api/crypto/watchlists/${encodeURIComponent(String(watchlistId))}/assets`, {
      method: "POST",
      body: JSON.stringify({ assetSymbol, notes })
    })
  );
}

export async function removeWatchlistAsset(watchlistId: number, assetId: number) {
  return normalizeMutation(
    await pulseApi<WatchlistMutation>(
      `/api/crypto/watchlists/${encodeURIComponent(String(watchlistId))}/assets/${encodeURIComponent(String(assetId))}`,
      { method: "DELETE" }
    )
  );
}

/**
 * Toggle a favourite.
 *
 * Server-side on purpose: a favourite is part of the account, so it has to
 * survive a reinstall and appear on a second device. Device-local storage would
 * quietly fail both.
 */
export async function setFavoriteAsset(symbol: string, favorite: boolean) {
  return normalizeMutation(
    await pulseApi<WatchlistMutation>("/api/crypto/favorites", {
      method: "POST",
      body: JSON.stringify({ symbol: symbol.toUpperCase(), favorite })
    })
  );
}

// ---------------------------------------------------------------------------
// Display helpers
//
// These are the single place "we do not know" becomes "--", so no screen has to
// remember to check for null before formatting.
// ---------------------------------------------------------------------------

export const UNKNOWN_VALUE = "--";

export function formatPrice(value: number | null): string {
  if (value === null) return UNKNOWN_VALUE;
  const digits = Math.abs(value) >= 1 ? 2 : 6;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function formatPercent(value: number | null): string {
  if (value === null) return UNKNOWN_VALUE;
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function formatSignedPrice(value: number | null): string {
  if (value === null) return UNKNOWN_VALUE;
  return `${value >= 0 ? "+" : "-"}${formatPrice(Math.abs(value))}`;
}

/** Compact market cap / volume / supply. Never rounds an unknown to zero. */
export function formatCompact(value: number | null): string {
  if (value === null) return UNKNOWN_VALUE;
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(2)}K`;
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function formatSupply(value: number | null, symbol: string): string {
  if (value === null) return UNKNOWN_VALUE;
  return `${formatCompact(value)} ${symbol}`.trim();
}

export function formatRank(value: number | null): string {
  return value === null ? UNKNOWN_VALUE : `#${value}`;
}
