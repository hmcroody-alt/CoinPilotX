/**
 * The reports table is an advertiser's receipt, so the two honesty rules the
 * module enforces are pinned here rather than trusted:
 *
 *   1. `results_available` must default to FALSE. A missing flag rendered as a
 *      zero under "Results" tells an advertiser their money bought nothing when
 *      the truth is that nobody counted.
 *   2. `cpc_cents` (and cost-per-result, and ROAS) must be null — not 0 — when
 *      there is nothing to divide by. "$0.00 per click" claims free clicks.
 *
 * The CSV builder's escaping is also pinned: one campaign named with a comma
 * must not shift every column after it.
 */

import {
  adReportRange,
  buildAdReportCsv,
  isValidReportDate,
  normalizeAdReport,
  normalizeAdReportBreakdown,
  normalizeAdReportRow
} from "../adsReports";
import type { AdReportRow } from "../adsReports";

const row = (overrides: Partial<AdReportRow> = {}): AdReportRow =>
  normalizeAdReportRow({
    key: "campaign:3",
    label: "Launch",
    campaign_id: 3,
    spend_cents: 12500,
    impressions: 4000,
    reach: 3000,
    clicks: 50,
    ctr: 0.0125,
    cpc_cents: 250,
    results: 50,
    results_metric: "clicks",
    results_available: true,
    ...overrides
  });

describe("normalizeAdReportBreakdown", () => {
  it("accepts only the backend's breakdown keys and falls back to campaign", () => {
    expect(normalizeAdReportBreakdown("placement")).toBe("placement");
    expect(normalizeAdReportBreakdown("DATE")).toBe("date");
    expect(normalizeAdReportBreakdown("bogus")).toBe("campaign");
    expect(normalizeAdReportBreakdown(null)).toBe("campaign");
  });
});

describe("normalizeAdReportRow", () => {
  it("reads results as unavailable unless the server explicitly says true", () => {
    expect(row({ results_available: undefined }).results_available).toBe(false);
    expect(row({ results_available: "yes" as never }).results_available).toBe(false);
    expect(row({ results_available: true }).results_available).toBe(true);
  });

  it("keeps derived money figures null when there is nothing to divide by", () => {
    const empty = row({ cpc_cents: 0, cost_per_result_cents: 0, roas: 0 });
    expect(empty.cpc_cents).toBeNull();
    expect(empty.cost_per_result_cents).toBeNull();
    expect(empty.roas).toBeNull();
    expect(row({ cpc_cents: undefined }).cpc_cents).toBeNull();
  });

  it("marks estimated reach so the table can label it, and never goes negative", () => {
    expect(row({ reach_estimated: true }).reach_estimated).toBe(true);
    expect(row({ reach_estimated: undefined }).reach_estimated).toBe(false);
    const clamped = row({ spend_cents: -100 as never, clicks: -3 as never, ctr: -0.5 as never });
    expect(clamped.spend_cents).toBe(0);
    expect(clamped.clicks).toBe(0);
    expect(clamped.ctr).toBe(0);
  });

  it("nulls the ids that are absent instead of inventing id 0", () => {
    const bare = normalizeAdReportRow({ key: "totals" });
    expect(bare.campaign_id).toBeNull();
    expect(bare.adset_id).toBeNull();
    expect(bare.creative_id).toBeNull();
    expect(bare.placement_key).toBeNull();
  });
});

describe("normalizeAdReport", () => {
  it("drops keyless rows and keeps the metadata notes verbatim", () => {
    const report = normalizeAdReport({
      rows: [{ key: "campaign:3", label: "Launch" }, { key: "" }] as never,
      totals: { key: "totals", label: "Totals" } as never,
      breakdown: "adset",
      metadata: {
        start: "2026-08-01",
        end: "2026-08-07",
        notes: ["Reach is estimated.", ""],
        attribution: { model: "last_click", window_days: 7, note: "n" }
      } as never
    });
    expect(report.rows).toHaveLength(1);
    expect(report.breakdown).toBe("adset");
    expect(report.metadata.notes).toEqual(["Reach is estimated."]);
    expect(report.metadata.attribution.window_days).toBe(7);
  });

  it("degrades an empty payload without throwing", () => {
    const report = normalizeAdReport(undefined);
    expect(report.rows).toEqual([]);
    expect(report.breakdown).toBe("campaign");
    expect(report.totals.results_available).toBe(false);
  });
});

describe("adReportRange", () => {
  it("builds a trailing window that includes today", () => {
    const range = adReportRange("7d", new Date(2026, 7, 9));
    expect(range).toEqual({ start: "2026-08-03", end: "2026-08-09" });
  });

  it("crosses month boundaries correctly", () => {
    const range = adReportRange("14d", new Date(2026, 7, 5));
    expect(range).toEqual({ start: "2026-07-23", end: "2026-08-05" });
  });

  it("leaves custom entirely to the caller", () => {
    expect(adReportRange("custom")).toEqual({ start: null, end: null });
  });
});

describe("isValidReportDate", () => {
  it("accepts YYYY-MM-DD only", () => {
    expect(isValidReportDate("2026-08-09")).toBe(true);
    expect(isValidReportDate("2026-8-9")).toBe(false);
    expect(isValidReportDate("09/08/2026")).toBe(false);
    expect(isValidReportDate("")).toBe(false);
  });
});

describe("buildAdReportCsv", () => {
  it("escapes commas and quotes so one label cannot shift the columns", () => {
    const report = normalizeAdReport({
      rows: [{ key: "campaign:1", label: 'Launch, "big" one', spend_cents: 100 }] as never,
      totals: { key: "" } as never
    });
    const csv = buildAdReportCsv(report);
    const lines = csv.split("\n");
    expect(lines[1].startsWith('"Launch, ""big"" one",100,')).toBe(true);
    // Header and row must agree on the column count despite the quoted comma.
    const columns = lines[0].split(",").length;
    expect(lines[1].replace(/"[^"]*"/g, "x").split(",").length).toBe(columns);
  });

  it("exports unavailable metrics as words and blanks, never zeros", () => {
    const report = normalizeAdReport({
      rows: [
        { key: "campaign:1", label: "Launch", results: 9, results_available: false, cpc_cents: 0 }
      ] as never,
      totals: { key: "" } as never
    });
    const line = buildAdReportCsv(report).split("\n")[1];
    expect(line).toContain("not measured");
    expect(line).not.toContain(",9,");
  });

  it("appends totals last, and only when the server sent them", () => {
    const withTotals = normalizeAdReport({
      rows: [{ key: "campaign:1", label: "Launch" }] as never,
      totals: { key: "totals", label: "Totals" } as never
    });
    const lines = buildAdReportCsv(withTotals).split("\n");
    expect(lines).toHaveLength(3);
    expect(lines[2].startsWith("Totals,")).toBe(true);

    const withoutTotals = normalizeAdReport({
      rows: [{ key: "campaign:1", label: "Launch" }] as never
    });
    expect(buildAdReportCsv(withoutTotals).split("\n")).toHaveLength(2);
  });
});
