/**
 * The data layer for the Insights screen.
 *
 * Two boundaries hold this module together, and both exist because Insights is
 * the reconciliation point for the seller surface — every number on it is owned
 * by another screen, and a dashboard that contradicts its sources is worse than
 * no dashboard.
 *
 * **Nothing is aggregated here.** Totals, comparisons, series buckets, source
 * splits and rankings all arrive already computed from
 * `GET /api/pulse/insights/seller/summary`, which sums the whole orders table
 * inside an explicit window. The obvious shortcut — reuse the seller-orders
 * endpoint the Store and Orders screens already call and add it up on the phone
 * — is wrong in a way that hides: that endpoint is `LIMIT 100` per table with no
 * date range, so a "90-day total" built from it is the sum of the newest hundred
 * rows, an understatement that grows as the seller sells more. The better the
 * store does, the more the dashboard lies.
 *
 * **A figure with no backend source is absent, not invented.** The server names
 * the metrics it cannot measure in `unavailable`; this module carries that list
 * through untouched (see {@link INSIGHTS_MOCK_DATA_GAPS}) so the screen can omit
 * those modules entirely. A zero renders as a measurement, and "0 store views"
 * would be a false one.
 *
 * This module returns numbers, not strings. Formatting — currency, percent,
 * abbreviation, dates — belongs to the localization utilities at the component
 * boundary, not here.
 */

import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi, PulseApiError } from "./pulseApi";
// The shared state and failure vocabulary. Insights names a failure with the
// same five causes and the same five sentences every other surface uses.
import { failureFrom, type FailureCopy } from "./stateLanguage";

/* ------------------------------------------------------------------ periods */

export const INSIGHTS_PERIODS = ["today", "7d", "30d", "90d"] as const;
export type InsightsPeriod = (typeof INSIGHTS_PERIODS)[number];
export const DEFAULT_INSIGHTS_PERIOD: InsightsPeriod = "7d";

/** Days in each period. Mirrors `PERIOD_DAYS` in `seller_analytics.py`. */
export const INSIGHTS_PERIOD_DAYS: Record<InsightsPeriod, number> = {
  today: 1,
  "7d": 7,
  "30d": 30,
  "90d": 90
};

export function isInsightsPeriod(value: unknown): value is InsightsPeriod {
  return typeof value === "string" && (INSIGHTS_PERIODS as readonly string[]).includes(value);
}

const PERIOD_STORAGE_KEY = "pulse.insights.period.v1";
const SUMMARY_CACHE_PREFIX = "pulse.insights.summary.v1.";

/**
 * The seller's last period choice, remembered across launches. A seller who
 * lives in 30d should not land on 7d every morning.
 *
 * Stored per-device rather than per-account because it is a view preference,
 * not account state, and a round trip to fetch it would delay first paint of
 * the very control it configures.
 */
export async function readSavedPeriod(): Promise<InsightsPeriod> {
  const saved = await readJsonCache<{ period?: string }>(PERIOD_STORAGE_KEY, (value) => value);
  return isInsightsPeriod(saved?.period) ? saved.period : DEFAULT_INSIGHTS_PERIOD;
}

export async function writeSavedPeriod(period: InsightsPeriod): Promise<void> {
  await writeJsonCache(PERIOD_STORAGE_KEY, { period }).catch(() => undefined);
}

/* -------------------------------------------------------------------- types */

/**
 * One bucket of the revenue/orders series.
 *
 * `date` is a `YYYY-MM-DD` calendar day in the *seller's* timezone, already
 * shifted server-side. It is deliberately not an instant: parsing it back into
 * a `Date` on the device would re-apply the device's offset and slide the whole
 * chart by a day for anyone travelling.
 */
export type InsightsBucket = {
  date: string;
  revenue_minor: number;
  orders: number;
};

/** `day` for Today/7d/30d, `week` once a period would exceed 30 points. */
export type InsightsBucketLabel = "day" | "week";

/**
 * Revenue and order count for a window.
 *
 * Two figures, not four. Unit counts and unique-buyer counts would need a line
 * table and a buyer id the transaction rows do not carry, and a "units" figure
 * that silently equalled the order count would be a fabricated metric.
 */
export type InsightsTotals = {
  revenue_minor: number;
  orders: number;
};

/**
 * A revenue slice by where the sale happened. The platform can tell Store from
 * Marketplace (the transaction records both a seller type and an item type); it
 * cannot tell you which of those an ad caused, which is why there is no `ads`
 * member here. See {@link INSIGHTS_MOCK_DATA_GAPS}.
 */
export type InsightsSource = {
  key: "store" | "marketplace";
  revenue_minor: number;
  orders: number;
};

/**
 * One ranked listing. Revenue and orders are aggregates; title, image and stock
 * are labels and inventory state read from the listing itself.
 *
 * `title` is `null` when the listing has been deleted since it sold — the row
 * still counts, because dropping it would understate a list headed "where the
 * money came from". `stock` is `null` when the listing does not track stock at
 * all, which is a different statement from zero and must not read "sold out".
 */
export type InsightsTopItem = {
  item_id: string;
  item_type: string;
  source: "store" | "marketplace";
  revenue_minor: number;
  orders: number;
  title: string | null;
  image_url: string | null;
  listing_status: string | null;
  stock: number | null;
  price_label: string | null;
};

/** A metric this platform has no source for, named so the UI can omit it. */
export type InsightsGap = { key: string; label: string; needs: string };

export type InsightsSummary = {
  period: InsightsPeriod;
  days: number;
  timezone_offset_minutes: number;
  /** Window edges, half-open, sitting on the seller's local midnight. */
  start: string;
  end: string;
  prior_start: string;
  prior_end: string;
  /**
   * Whether the seller existed and could have traded before this window.
   *
   * This separates "you earned nothing last week" from "you did not exist last
   * week". Without it a new seller's first sale reads as ▲100%, which is a
   * fabricated comparison against a period that never happened.
   */
  has_prior_period: boolean;
  currency: string;
  currencies: string[];
  totals: InsightsTotals;
  /** `null` when there is no prior period to compare against. */
  prior_totals: InsightsTotals | null;
  bucket: InsightsBucketLabel;
  series: InsightsBucket[];
  sources: InsightsSource[];
  top_items: InsightsTopItem[];
  followers: { gained: number; prior_gained: number | null };
  unavailable: InsightsGap[];
};

/* -------------------------------------------------------------- known gaps */

/**
 * The metrics the mission's design asks for that this platform cannot yet
 * measure. Kept in sync with `UNAVAILABLE_METRICS` in `seller_analytics.py`.
 *
 * Exported so it can be asserted in a test: if someone later fakes one of
 * these, the count changes and the test says so.
 *
 * MOCK-DATA: none of these render. This list is the *reason* they do not.
 */
export const INSIGHTS_MOCK_DATA_GAPS: readonly InsightsGap[] = [
  {
    key: "store_views",
    label: "Store and listing views",
    needs:
      "A view-tracking table for storefronts and listings. The post and video view " +
      "counters cover feed content only; listings carry no view counter and nothing " +
      "increments one."
  },
  {
    key: "ads_attribution",
    label: "Revenue attributed to ads",
    needs:
      "A business-scoped, period-scoped attribution read. The attribution engine is real " +
      "(four models, a lookback window) but its campaign and channel reports accept " +
      "neither a business id nor a date range, so no per-seller 'From ads' figure can be " +
      "taken from them."
  },
  {
    key: "on_time_dispatch",
    label: "On-time dispatch rate",
    needs: "A promised ship-by date and a recorded dispatch time on the order. Neither exists."
  },
  {
    key: "reply_rate",
    label: "Replies under the response threshold",
    needs: "A messaging metric that records first-response latency per conversation."
  },
  {
    key: "offers_answered",
    label: "Offers answered",
    needs: "A live offers table. The buyer-interest table has the right shape but is never written to."
  }
] as const;

/**
 * True when the server says it cannot measure `key`. Drives module omission.
 *
 * The distinction that matters here is between *no summary* and *a summary with
 * an empty list*, and they must not be collapsed into one truthiness check.
 *
 * With no summary — before the first response lands, or after a failure — the
 * safe assumption is that nothing is measurable, so the local ledger stands in
 * and the modules stay hidden. Rendering them optimistically would flash a zero,
 * and a zero is a measurement claim.
 *
 * With a summary in hand, its list is authoritative *including when it is
 * empty*: an empty list is the server saying it can now measure everything.
 * Falling back to the local ledger there would keep a live module hidden until
 * somebody shipped a new build, which is exactly the client deciding what the
 * platform can measure — the thing this module refuses to do.
 */
export function isGap(summary: InsightsSummary | null, key: string): boolean {
  const gaps = summary ? summary.unavailable : INSIGHTS_MOCK_DATA_GAPS;
  return gaps.some((gap) => gap.key === key);
}

/**
 * Whether the "From ads" row may render at all.
 *
 * Sellers make spend decisions on this number, so it ships only when a real
 * attribution model produced it. When it cannot, the source breakdown falls
 * back to the two rows the platform *can* prove — Store and Marketplace — and
 * never to a client-side heuristic dressed up as attribution.
 */
export function attributionAvailable(summary: InsightsSummary | null): boolean {
  return !isGap(summary, "ads_attribution");
}

/* -------------------------------------------------------------- comparison */

export type InsightsComparison =
  | { kind: "none"; reason: "no_prior_period" }
  | { kind: "none"; reason: "no_prior_value" }
  | { kind: "change"; direction: "up" | "down" | "flat"; ratio: number; priorValue: number };

/**
 * Compare a value with the immediately preceding period of equal length.
 *
 * `ratio` is a fraction (0.18 = 18%), not a percentage and not a string — the
 * caller formats it through the localization utilities. Two distinct refusals
 * are returned rather than a number: no prior period at all (the seller is new)
 * and a prior period that existed but was zero (any percentage against zero is
 * undefined). Both render as words on screen, never as ▲100%.
 */
export function compareToPrior(
  value: number,
  priorValue: number | null | undefined,
  hasPriorPeriod: boolean
): InsightsComparison {
  if (!hasPriorPeriod || priorValue === null || priorValue === undefined) {
    return { kind: "none", reason: "no_prior_period" };
  }
  if (priorValue === 0) return { kind: "none", reason: "no_prior_value" };
  const ratio = (value - priorValue) / Math.abs(priorValue);
  const direction = ratio > 0.0005 ? "up" : ratio < -0.0005 ? "down" : "flat";
  return { kind: "change", direction, ratio, priorValue };
}

/**
 * The period's revenue, in major units, paired with the currency it is in.
 *
 * Added for the Business Hub's "Today's sales" cell. This module deliberately
 * returns numbers rather than strings (see the header), so the hub still hands
 * the result to the app's shared `useFormatters().currency` exactly as the
 * Insights screen does — but the two decisions a consumer could get wrong, WHICH
 * field is "sales" and what unit it is in, are made here, once, by the owner.
 * The hub therefore performs no money arithmetic of its own.
 *
 * `null` when there is no summary. Never a zero standing in for an absent one:
 * "$0.00" and "—" are different claims, and only the source knows which is true.
 */
export function insightsRevenueMajor(
  summary: InsightsSummary | null
): { amount: number; currency: string } | null {
  if (!summary) return null;
  return { amount: summary.totals.revenue_minor / 100, currency: summary.currency || "USD" };
}

/** Revenue share of one source, as a fraction of the period's total. */
export function sourceShare(source: InsightsSource, sources: readonly InsightsSource[]): number {
  const total = sources.reduce((sum, entry) => sum + entry.revenue_minor, 0);
  return total > 0 ? source.revenue_minor / total : 0;
}

/* ------------------------------------------------------------ normalisation */

function num(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function normalizeSummary(raw: unknown, requested: InsightsPeriod): InsightsSummary {
  const data = (raw || {}) as Record<string, unknown>;
  const totals = (data.totals || {}) as Record<string, unknown>;
  const prior = data.prior_totals as Record<string, unknown> | null | undefined;
  const followers = (data.followers || {}) as Record<string, unknown>;
  const priorGained = followers.prior_gained;

  return {
    period: isInsightsPeriod(data.period) ? data.period : requested,
    days: num(data.days, INSIGHTS_PERIOD_DAYS[requested]),
    timezone_offset_minutes: num(data.timezone_offset_minutes),
    start: str(data.start),
    end: str(data.end),
    prior_start: str(data.prior_start),
    prior_end: str(data.prior_end),
    has_prior_period: data.has_prior_period === true,
    currency: str(data.currency, "USD"),
    currencies: Array.isArray(data.currencies) ? data.currencies.map((entry) => str(entry)) : [],
    totals: { revenue_minor: num(totals.revenue_minor), orders: num(totals.orders) },
    prior_totals: prior
      ? { revenue_minor: num(prior.revenue_minor), orders: num(prior.orders) }
      : null,
    bucket: data.bucket === "week" ? "week" : "day",
    series: Array.isArray(data.series)
      ? data.series.map((entry) => {
          const bucket = (entry || {}) as Record<string, unknown>;
          return {
            date: str(bucket.date),
            revenue_minor: num(bucket.revenue_minor),
            orders: num(bucket.orders)
          };
        })
      : [],
    sources: Array.isArray(data.sources)
      ? data.sources
          .map((entry) => {
            const source = (entry || {}) as Record<string, unknown>;
            return {
              key: source.key === "store" ? ("store" as const) : ("marketplace" as const),
              revenue_minor: num(source.revenue_minor),
              orders: num(source.orders)
            };
          })
          .filter((source) => source.revenue_minor > 0 || source.orders > 0)
      : [],
    top_items: Array.isArray(data.top_items)
      ? data.top_items.map((entry) => {
          const item = (entry || {}) as Record<string, unknown>;
          const stock = item.stock;
          return {
            item_id: str(item.item_id),
            item_type: str(item.item_type),
            source: item.source === "store" ? ("store" as const) : ("marketplace" as const),
            revenue_minor: num(item.revenue_minor),
            orders: num(item.orders),
            title: typeof item.title === "string" && item.title.trim() ? item.title : null,
            image_url: typeof item.image_url === "string" && item.image_url ? item.image_url : null,
            listing_status:
              typeof item.listing_status === "string" && item.listing_status
                ? item.listing_status
                : null,
            stock: stock === null || stock === undefined ? null : num(stock),
            price_label:
              typeof item.price_label === "string" && item.price_label ? item.price_label : null
          };
        })
      : [],
    followers: {
      gained: num(followers.gained),
      prior_gained: priorGained === null || priorGained === undefined ? null : num(priorGained)
    },
    // Trusted straight through. If the server ever stops naming a gap because it
    // has learned to measure it, the module appears; the client never decides.
    unavailable: Array.isArray(data.unavailable)
      ? data.unavailable.map((entry) => {
          const gap = (entry || {}) as Record<string, unknown>;
          return { key: str(gap.key), label: str(gap.label), needs: str(gap.needs) };
        })
      : INSIGHTS_MOCK_DATA_GAPS.map((gap) => ({ ...gap }))
  };
}

/* ------------------------------------------------------------------- loader */

export type InsightsLoad = {
  summary: InsightsSummary;
  /** True when this came from disk because the network failed. */
  fromCache: boolean;
  /** Epoch ms the cached copy was written. `null` for a fresh response. */
  cachedAt: number | null;
};

type CachedEnvelope = { savedAt: number; summary: InsightsSummary };

function cacheKey(period: InsightsPeriod) {
  return `${SUMMARY_CACHE_PREFIX}${period}`;
}

/**
 * The seller's UTC offset in minutes, in the sign the backend expects: minutes
 * to *add* to UTC to reach local time. `getTimezoneOffset` returns the opposite
 * sign, hence the negation — Los Angeles reports 420 and means -420.
 *
 * Period boundaries are cut at the seller's local midnight, so getting this
 * backwards would shift every window by up to a day and quietly disagree with
 * the Orders screen.
 */
export function localTimezoneOffsetMinutes(now: Date = new Date()): number {
  return -now.getTimezoneOffset();
}

/** The raw fetch. Throws `PulseApiError`; callers decide about the cache. */
export async function fetchInsightsSummary(
  period: InsightsPeriod,
  options: { topLimit?: number; now?: Date } = {}
): Promise<InsightsSummary> {
  const params = new URLSearchParams({
    period,
    tz_offset: String(localTimezoneOffsetMinutes(options.now)),
    top: String(Math.max(1, Math.min(options.topLimit ?? 5, 50)))
  });
  const payload = await pulseApi<{ ok?: boolean; insights?: unknown }>(
    `/api/pulse/insights/seller/summary?${params.toString()}`
  );
  return normalizeSummary(payload?.insights, period);
}

export async function readCachedInsights(period: InsightsPeriod): Promise<CachedEnvelope | null> {
  return readJsonCache<CachedEnvelope>(cacheKey(period), (value) => ({
    savedAt: num(value?.savedAt),
    summary: normalizeSummary(value?.summary, period)
  }));
}

/**
 * Load one period, falling back to the last good copy when the network is gone.
 *
 * Offline shows real figures with an honest "last updated" note rather than an
 * empty screen — but only for periods that were actually cached. A period with
 * no cached copy stays unavailable and says why; it is not silently served from
 * a different period's numbers.
 */
export async function loadInsights(
  period: InsightsPeriod,
  options: { topLimit?: number; now?: Date } = {}
): Promise<InsightsLoad> {
  try {
    const summary = await fetchInsightsSummary(period, options);
    await writeJsonCache<CachedEnvelope>(cacheKey(period), {
      savedAt: Date.now(),
      summary
    }).catch(() => undefined);
    return { summary, fromCache: false, cachedAt: null };
  } catch (error) {
    const cached = await readCachedInsights(period);
    if (cached) return { summary: cached.summary, fromCache: true, cachedAt: cached.savedAt };
    throw error;
  }
}

/** Which cached periods exist, so the picker can disable the rest when offline. */
export async function cachedPeriods(): Promise<InsightsPeriod[]> {
  const found = await Promise.all(
    INSIGHTS_PERIODS.map(async (period) => ((await readCachedInsights(period)) ? period : null))
  );
  return found.filter((period): period is InsightsPeriod => period !== null);
}

/* ------------------------------------------------------------ request gate */

export class InsightsStaleResponse extends Error {
  constructor() {
    super("A newer Insights request superseded this one.");
    this.name = "InsightsStaleResponse";
  }
}

export type InsightsRequestGate = {
  /**
   * Run `work` under a fresh token. Resolves only if no later `run` started in
   * the meantime; otherwise rejects with {@link InsightsStaleResponse}.
   */
  run<T>(work: () => Promise<T>): Promise<T>;
  /** Invalidate everything in flight — call from an unmount effect. */
  cancel(): void;
  /** True when `error` is just a superseded request and must not be shown. */
  isStale(error: unknown): boolean;
};

/**
 * A monotonic token guard for period switching.
 *
 * Tapping 7d then immediately 90d starts two requests, and nothing guarantees
 * they finish in that order. Without this, the slower 7d response lands last
 * and paints seven days of data under a highlighted 90d pill — the screen would
 * be lying about what it is showing, which is the specific failure this whole
 * screen exists to avoid.
 *
 * A token rather than `AbortController` because the loader also touches
 * AsyncStorage, and an aborted fetch would still let a stale cache read through.
 * The token is checked after every await, so a superseded request cannot write
 * to the UI no matter which stage it was in.
 */
export function createInsightsRequestGate(): InsightsRequestGate {
  let current = 0;
  return {
    async run<T>(work: () => Promise<T>): Promise<T> {
      const token = ++current;
      const result = await work();
      if (token !== current) throw new InsightsStaleResponse();
      return result;
    },
    cancel() {
      current += 1;
    },
    isStale(error: unknown) {
      return error instanceof InsightsStaleResponse;
    }
  };
}

/* ------------------------------------------------------------------ *
 * Cause-specific failure, and whether there is anything to export
 * ------------------------------------------------------------------ */

export const INSIGHTS_ERROR_CAUSES_FLAG = "EXPO_PUBLIC_INSIGHTS_ERROR_CAUSES";

/** True when a build has opted into cause-specific insights errors. Off by default. */
export function insightsErrorCausesEnabled(): boolean {
  return String(process.env[INSIGHTS_ERROR_CAUSES_FLAG] || "").trim() === "1";
}

export type InsightsFailure = FailureCopy & {
  /**
   * True when pressing the action would send the same request again. False for
   * the entitlement case, where a second identical attempt fails identically,
   * and for the sign-in case, which goes somewhere else entirely.
   */
  retries: boolean;
};

/**
 * Why insights did not load, and what to do about it.
 *
 * `insightsErrorMessage` above collapsed five situations into three sentences
 * and gave all of them the same treatment: a line of text and a Retry. Two of
 * those situations do not retry. Being offline and the service being down look
 * identical to it, though only one of them is fixed by reconnecting. And an
 * account without the entitlement was told to try again forever.
 *
 * The causes themselves are not decided here — they come from
 * {@link failureFrom}, which is the app-wide vocabulary from ADR-0003, so the
 * Store, Advertising and Insights all name the same failure the same way.
 */
export function insightsFailure(error: unknown, subject = "Your insights"): InsightsFailure {
  const copy = failureFrom(error, subject);
  return { ...copy, retries: copy.cause !== "entitlement" && copy.cause !== "authentication" };
}

/**
 * Whether an export would contain anything.
 *
 * Export was previously enabled whenever a summary existed, so a seller with no
 * trade in the window could press it and receive a file of nothing — and a
 * disabled pill was drawn at 45% opacity with no reason given, which reads as a
 * rendering fault rather than a decision.
 *
 * "Anything" means at least one order in the window. Revenue alone is not the
 * test: a period can hold a refund and net to zero revenue while still having
 * rows worth exporting, and orders is the count of rows the file would carry.
 */
export function insightsHasExportableData(summary: InsightsSummary | null): boolean {
  if (!summary) return false;
  return summary.totals.orders > 0 || summary.series.some((bucket) => bucket.orders > 0);
}

/**
 * The one sentence a disabled Export explains itself with, or `null` when it is
 * enabled. Never an empty string, so the caller cannot render a blank reason.
 */
export function insightsExportBlockedReason(input: {
  summary: InsightsSummary | null;
  loading?: boolean;
  failed?: boolean;
  fromCache?: boolean;
}): string | null {
  if (input.loading) return "Still loading your figures.";
  if (input.failed) return "Your insights didn't load, so there's nothing to export.";
  if (!input.summary) return "There's nothing to export yet.";
  if (input.fromCache) return "These figures are from your last visit. Reconnect to export them.";
  if (!insightsHasExportableData(input.summary)) {
    return "You had no orders in this period, so an export would be empty.";
  }
  return null;
}

/** A user-facing failure line. Named so retries can be per-module. */
export function insightsErrorMessage(error: unknown, subject: string): string {
  if (error instanceof PulseApiError && error.status === 401) {
    return "Sign in again to see your insights.";
  }
  if (error instanceof PulseApiError && error.status === 503) {
    return `${subject} didn't load. PulseSoc couldn't be reached.`;
  }
  return `${subject} didn't load.`;
}
