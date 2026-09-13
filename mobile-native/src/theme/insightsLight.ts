/**
 * Insights palette — the shared seller light theme plus the handful of tokens a
 * chart needs and a list does not.
 *
 * It spreads `storeLight` rather than redefining it, so a change to the page
 * background or the hairline reaches this screen too and Insights cannot drift
 * into being a second design system.
 *
 * The source colours are not decorative. Across the business surfaces green
 * means Store, neutral gray means Marketplace, and gold means ads or money — the
 * same three meanings the Advertising and Orders screens use. A seller who has
 * learned that gray is Marketplace should not have to relearn it here, so the
 * breakdown bars and the ranked-row accents take their colour from the source,
 * never from position in the list.
 *
 * Store and Marketplace were blue and violet before the black/white/green lock.
 * A two-colour breakdown bar is the one place that collapse actually costs
 * something, because a stacked bar has no room for a label inside each segment.
 * The legend beside it names both sources, and the ranked rows underneath state
 * the source in words, so the bar is never the only place the split is readable.
 */

import { storeLight } from "./storeLight";

export const insightsLight = {
  ...storeLight,

  chart: {
    /**
     * Revenue is the primary series, so it takes the same green the rest of the
     * seller surface already uses for a positive money state — it is
     * `storeLight.status.success`, quoted here for the chart's benefit rather
     * than forked.
     */
    revenue: storeLight.status.success,
    /**
     * Orders sit behind revenue at reduced opacity. Two lines at equal weight
     * read as a competition; the second series is context for the first.
     */
    orders: "#0A7050",
    ordersOpacity: 0.85,
    /** Three of these. Any more and the plot becomes a grid with a line on it. */
    gridline: "#ECEEEE",
    /** The dot on the latest revenue point — "you are here". */
    latest: storeLight.status.success,
    axisLabel: storeLight.text.muted
  },

  source: {
    /** Green — Store, app-wide. */
    store: { from: "#2EA47C", to: "#0A7050" },
    /** Neutral gray — Marketplace, app-wide. */
    marketplace: { from: "#8A9691", to: "#4A5250" },
    /** Gold — ads and money, app-wide. Reserved; no ads row ships today. */
    ads: { from: "#FFD97A", to: "#FFA41C" },
    track: "#EFF1F1"
  },

  ring: {
    track: "#EFF1F1",
    /** Bands, not a gradient: a ring's colour states which band it is in. */
    excellent: storeLight.status.success,
    good: "#0A7050",
    warn: storeLight.accent.star
  },

  tip: {
    from: "#EEF7F4",
    to: "#FFFFFF",
    border: "#BFE0D3"
  },

  /** Rank 1 only. Ranks 2+ are numbered in muted text — gold means "the one". */
  rankGold: storeLight.accent.star
} as const;

export type InsightsSourceKey = "store" | "marketplace" | "ads";

export function sourceColors(key: InsightsSourceKey) {
  return insightsLight.source[key];
}

/**
 * Health-ring bands.
 *
 * 90 / 75 is a proposal, not a platform standard — nothing in this codebase
 * defines fulfilment thresholds, and the three metrics these rings were designed
 * for have no data source yet (see `INSIGHTS_MOCK_DATA_GAPS`). Flagged in the
 * report as needing a product decision before any ring ships.
 */
export function ringBand(percent: number): "excellent" | "good" | "warn" {
  if (percent >= 90) return "excellent";
  if (percent >= 75) return "good";
  return "warn";
}

/** The words screen readers hear after the number. Colour is never the only cue. */
export function ringBandLabel(percent: number): string {
  const band = ringBand(percent);
  return band === "excellent" ? "excellent" : band === "good" ? "on track" : "needs attention";
}
