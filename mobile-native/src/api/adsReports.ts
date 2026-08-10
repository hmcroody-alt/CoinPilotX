/**
 * Ads reporting data layer.
 *
 * Binds `GET /api/pulse/ads/reports` → `services/pulse_ads_reporting.build_report`.
 * Row fields mirror the backend's flat serializer verbatim — they are not
 * guessed. Two honesty rules are enforced here rather than per screen:
 *
 *   1. `results_available: false` means the objective's result metric has no
 *      measurement source. The screen must say "not measured", never render a
 *      zero — a fake zero under "Results" tells an advertiser their money
 *      bought nothing when the truth is that nobody counted.
 *   2. `reach_estimated: true` marks reach as modelled, not observed. It is
 *      surfaced so the table can label it, not silently blended.
 *
 * The CSV builder is also here so its escaping can be pinned by tests: a
 * campaign named `Launch, "big" one` must not shift every column after it.
 */
import { pulseApi } from "./pulseApi";

export const AD_REPORT_BREAKDOWNS = [
  "campaign",
  "adset",
  "ad",
  "placement",
  "date",
  "objective"
] as const;

export type AdReportBreakdown = (typeof AD_REPORT_BREAKDOWNS)[number];

export function normalizeAdReportBreakdown(value?: string | null): AdReportBreakdown {
  const key = String(value || "").toLowerCase();
  return (AD_REPORT_BREAKDOWNS as readonly string[]).includes(key)
    ? (key as AdReportBreakdown)
    : "campaign";
}

export type AdReportRow = {
  key: string;
  label: string;
  campaign_id: number | null;
  adset_id: number | null;
  creative_id: number | null;
  placement_key: string | null;
  spend_cents: number;
  impressions: number;
  reach: number;
  /** True when reach is modelled from impressions rather than counted. */
  reach_estimated: boolean;
  frequency: number;
  clicks: number;
  /** Fraction 0–1, mirrors backend `round(clicks/impressions, 4)`. */
  ctr: number;
  /** Null when there are no clicks — a CPC of $0.00 would claim free clicks. */
  cpc_cents: number | null;
  results: number;
  results_metric: string;
  /** False = the metric has no measurement source; render "not measured", not 0. */
  results_available: boolean;
  cost_per_result_cents: number | null;
  purchases: number;
  revenue_cents: number;
  roas: number | null;
};

const nonNegInt = (value: unknown): number => Math.max(0, Math.round(Number(value) || 0));

const idOrNull = (value: unknown): number | null => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : null;
};

const positiveOrNull = (value: unknown): number | null => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

export function normalizeAdReportRow(value?: Partial<AdReportRow> | null): AdReportRow {
  return {
    key: String(value?.key || ""),
    label: String(value?.label || ""),
    campaign_id: idOrNull(value?.campaign_id),
    adset_id: idOrNull(value?.adset_id),
    creative_id: idOrNull(value?.creative_id),
    placement_key: value?.placement_key ? String(value.placement_key) : null,
    spend_cents: nonNegInt(value?.spend_cents),
    impressions: nonNegInt(value?.impressions),
    reach: nonNegInt(value?.reach),
    reach_estimated: value?.reach_estimated === true,
    frequency: Math.max(0, Number(value?.frequency) || 0),
    clicks: nonNegInt(value?.clicks),
    ctr: Math.max(0, Number(value?.ctr) || 0),
    cpc_cents: positiveOrNull(value?.cpc_cents) == null ? null : nonNegInt(value?.cpc_cents),
    results: nonNegInt(value?.results),
    results_metric: String(value?.results_metric || ""),
    // Missing flag reads as "not measured" — the safe direction. Only an
    // explicit true lets a screen print the number as a fact.
    results_available: value?.results_available === true,
    cost_per_result_cents:
      positiveOrNull(value?.cost_per_result_cents) == null
        ? null
        : nonNegInt(value?.cost_per_result_cents),
    purchases: nonNegInt(value?.purchases),
    revenue_cents: nonNegInt(value?.revenue_cents),
    roas: positiveOrNull(value?.roas)
  };
}

export type AdReportAttribution = { model: string; window_days: number; note: string };

export type AdReportMeta = {
  start: string;
  end: string;
  breakdown: AdReportBreakdown;
  campaign_id: number | null;
  attribution: AdReportAttribution;
  notes: string[];
  generated_at: string;
};

export type AdReport = {
  rows: AdReportRow[];
  totals: AdReportRow;
  breakdown: AdReportBreakdown;
  start: string;
  end: string;
  metadata: AdReportMeta;
};

export function normalizeAdReport(value?: Partial<AdReport> | null): AdReport {
  const metadata = (value?.metadata || {}) as Partial<AdReportMeta>;
  const attribution = (metadata.attribution || {}) as Partial<AdReportAttribution>;
  const breakdown = normalizeAdReportBreakdown(value?.breakdown || metadata.breakdown);
  return {
    rows: (Array.isArray(value?.rows) ? value!.rows : [])
      .map(normalizeAdReportRow)
      .filter((row) => row.key.length > 0),
    totals: normalizeAdReportRow(value?.totals),
    breakdown,
    start: String(value?.start || metadata.start || ""),
    end: String(value?.end || metadata.end || ""),
    metadata: {
      start: String(metadata.start || value?.start || ""),
      end: String(metadata.end || value?.end || ""),
      breakdown,
      campaign_id: idOrNull(metadata.campaign_id),
      attribution: {
        model: String(attribution.model || ""),
        window_days: nonNegInt(attribution.window_days),
        note: String(attribution.note || "")
      },
      notes: (Array.isArray(metadata.notes) ? metadata.notes : [])
        .map((note) => String(note || ""))
        .filter((note) => note.length > 0),
      generated_at: String(metadata.generated_at || "")
    }
  };
}

export type AdReportQuery = {
  start?: string;
  end?: string;
  breakdown?: AdReportBreakdown;
  campaignId?: number;
};

export async function getAdReport(accountId: number, query: AdReportQuery = {}): Promise<AdReport> {
  const params = new URLSearchParams();
  params.set("account_id", String(accountId));
  if (query.start) params.set("start", query.start);
  if (query.end) params.set("end", query.end);
  params.set("breakdown", query.breakdown || "campaign");
  if (query.campaignId) params.set("campaign_id", String(query.campaignId));
  const data = await pulseApi<Partial<AdReport>>(`/api/pulse/ads/reports?${params.toString()}`);
  return normalizeAdReport(data);
}

/* ------------------------------------------------------------------ *
 * Date-range presets
 * ------------------------------------------------------------------ */

export type AdReportRangePreset = "7d" | "14d" | "30d" | "custom";

export const AD_REPORT_RANGE_PRESETS: AdReportRangePreset[] = ["7d", "14d", "30d", "custom"];

const isoDay = (date: Date): string => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
};

/**
 * Trailing window ending today (inclusive), matching the backend's default of
 * "trailing N days". `custom` returns nulls — the caller supplies both dates.
 */
export function adReportRange(
  preset: AdReportRangePreset,
  now: Date = new Date()
): { start: string | null; end: string | null } {
  if (preset === "custom") return { start: null, end: null };
  const days = preset === "7d" ? 7 : preset === "14d" ? 14 : 30;
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const start = new Date(end);
  start.setDate(start.getDate() - (days - 1));
  return { start: isoDay(start), end: isoDay(end) };
}

/** Loose YYYY-MM-DD check for the custom range inputs. */
export function isValidReportDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isFinite(parsed.getTime());
}

/* ------------------------------------------------------------------ *
 * CSV export
 * ------------------------------------------------------------------ */

const CSV_COLUMNS: Array<{ header: string; value: (row: AdReportRow) => string }> = [
  { header: "label", value: (row) => row.label },
  { header: "spend_cents", value: (row) => String(row.spend_cents) },
  { header: "impressions", value: (row) => String(row.impressions) },
  { header: "reach", value: (row) => (row.reach_estimated ? `${row.reach} (estimated)` : String(row.reach)) },
  { header: "frequency", value: (row) => String(row.frequency) },
  { header: "clicks", value: (row) => String(row.clicks) },
  { header: "ctr", value: (row) => String(row.ctr) },
  { header: "cpc_cents", value: (row) => (row.cpc_cents == null ? "" : String(row.cpc_cents)) },
  {
    header: "results",
    value: (row) => (row.results_available ? String(row.results) : "not measured")
  },
  { header: "results_metric", value: (row) => row.results_metric },
  {
    header: "cost_per_result_cents",
    value: (row) => (row.cost_per_result_cents == null ? "" : String(row.cost_per_result_cents))
  },
  { header: "purchases", value: (row) => String(row.purchases) },
  { header: "revenue_cents", value: (row) => String(row.revenue_cents) },
  { header: "roas", value: (row) => (row.roas == null ? "" : String(row.roas)) }
];

function csvCell(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/**
 * Client-built CSV: header, one line per row, totals last. Unavailable metrics
 * export as "not measured" / empty rather than zero, for the same reason the
 * table renders them that way — a spreadsheet is still a report.
 */
export function buildAdReportCsv(report: AdReport): string {
  const lines: string[] = [];
  lines.push(["breakdown", ...CSV_COLUMNS.map((column) => column.header).slice(1)].join(","));
  const emit = (row: AdReportRow) =>
    lines.push(CSV_COLUMNS.map((column) => csvCell(column.value(row))).join(","));
  report.rows.forEach(emit);
  if (report.totals.key) emit(report.totals);
  return lines.join("\n");
}
