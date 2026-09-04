/**
 * Market Intelligence client — the typed shape of a server-side opinion.
 *
 * ## This file computes nothing
 *
 * Every score, band, level, label and sentence here was produced by
 * `services/market_intelligence` from data the board had already fetched. The
 * client's whole job is to keep that payload intact on the way in. A second
 * scoring implementation in TypeScript would drift from the first the week
 * somebody tuned a threshold, and the two would disagree on screen — the card
 * saying WAIT while the drill-down explained an ACCUMULATE.
 *
 * ## Null is the third answer
 *
 * A score of `null` does not mean zero and does not mean low. It means the
 * server could not measure it — usually because the asset has too little price
 * history. `Number(null)` is `0`, and a `0` rendered in a slot labelled
 * "Opportunity" is a confident claim that the asset is worthless. So every
 * numeric field arrives through `nullableNumber` and every band, level and state
 * through `nullableText`, and the rendering layer turns null into `--`.
 *
 * That is also why `confidence` travels beside almost every reading. KNOWN means
 * measured, INFERRED means derived from something measured, UNAVAILABLE means we
 * looked and could not tell — and the last of those must never be drawn the same
 * way as a low number.
 *
 * ## Two depths, two endpoints
 *
 * The list depth rides on `/api/pulse/market/snapshot`, which already returns
 * fifty rows in one request; `intelligence` is another field on a row the screen
 * was fetching anyway, so an indicator per card costs zero extra requests. The
 * full depth is a second request for the *one* asset a user opened. Making the
 * deep layers part of the snapshot would have put fifty analyses on the wire so
 * that one could be read.
 */

import { pulseApi } from "./pulseApi";

/** How sure the server is about one reading. Never collapse these into a boolean. */
export type Confidence = "KNOWN" | "INFERRED" | "UNAVAILABLE";

/**
 * The verdict states, plus the one that is not a verdict.
 *
 * `DATA_UNAVAILABLE` is in the union on purpose. It is what the server returns
 * when there is not enough history to say anything, and a client that mapped it
 * onto WAIT would turn an outage into advice.
 */
export type ActionState =
  | "STRONG_ACCUMULATION"
  | "ACCUMULATE"
  | "HOLD"
  | "WAIT"
  | "WAIT_FOR_PULLBACK"
  | "WAIT_FOR_CONFIRMATION"
  | "BREAKOUT_WATCH"
  | "PULLBACK_WATCH"
  | "REVERSAL_WATCH"
  | "TAKE_PARTIAL_PROFIT"
  | "REDUCE"
  | "EXIT"
  | "AVOID"
  | "DO_NOT_CHASE"
  | "HIGH_RISK"
  | "DATA_UNAVAILABLE";

/** Drives colour only. The server picks it so tone and state cannot disagree. */
export type ActionTone = "positive" | "neutral" | "caution" | "watch" | "negative" | "muted";

export type RiskLevel = "LOW" | "MODERATE" | "HIGH" | "EXTREME";

/** One sentence of justification, with a stable code for tests and telemetry. */
export type Reason = { code: string; text: string; confidence: Confidence };

export type ActionVerdict = {
  state: ActionState | null;
  label: string;
  tone: ActionTone;
  /** "holder" | "non_holder" | "unknown" — the question the verdict answers. */
  perspective: string;
  reasons: Reason[];
  confidence: Confidence;
};

/** A 0–100 reading, or null when it could not be computed. `band` mirrors it. */
export type QualityScore = {
  score: number | null;
  band: string | null;
  confidence: Confidence;
};

export type DataQuality = {
  level: "INSUFFICIENT" | "LIMITED" | "FULL" | null;
  pricePoints: number | null;
  priceBasis: string;
  volumeHistory: boolean;
  note: string;
};

/** What a card shows. Small enough to ride on all fifty rows. */
export type AssetIntelligence = {
  symbol: string;
  action: ActionVerdict;
  opportunity: QualityScore;
  entry: QualityScore;
  risk: { level: RiskLevel | null; confidence: Confidence };
  dataQuality: DataQuality;
  disclaimer: string;
  /** At list depth this is the top two action reasons, already trimmed server-side. */
  why: Reason[];
};

export type MarketRegime = {
  state: "RISK_ON" | "NEUTRAL" | "RISK_OFF" | null;
  label: string | null;
  advancers: number | null;
  decliners: number | null;
  breadthPct: number | null;
  medianChangePct: number | null;
  btcDominance: number | null;
  detail: string;
  basis: string;
  confidence: Confidence;
};

export type RotationGroup = {
  key: string;
  avgChangePct: number | null;
  count: number;
  confidence: Confidence;
};

export type MarketRotation = {
  groups: RotationGroup[];
  leader: string | null;
  basis: string;
  confidence: Confidence;
};

export type TimeframeRow = {
  key: string;
  label: string;
  direction: "UP" | "DOWN" | "FLAT" | null;
  changePct: number | null;
  thresholdPct: number | null;
  /** "last 143h of hourly closes" — said out loud so nobody reads it as a feed. */
  basis: string;
  confidence: Confidence;
};

export type TrendAlignment = {
  direction: "UP" | "DOWN" | "FLAT" | null;
  alignmentPct: number | null;
  /** The denominator. 100% over two rows is not 100% over four. */
  measured: number;
  total: number;
  agree: number | null;
  confidence: Confidence;
};

export type Extension = {
  zScore: number | null;
  vsMeanPct: number | null;
  state: string | null;
  lookbackHours: number | null;
  confidence: Confidence;
};

export type Levels = {
  support: number | null;
  resistance: number | null;
  supportDistancePct: number | null;
  resistanceDistancePct: number | null;
  rangeHigh: number | null;
  rangeLow: number | null;
  positionInRangePct: number | null;
  /** Names the closes-only derivation, so a pivot is never shown as a traded high. */
  basis: string;
  confidence: Confidence;
};

export type Volatility = { dailyPct: number | null; band: RiskLevel | null; confidence: Confidence };

export type Liquidity = {
  turnoverPct: number | null;
  band: string | null;
  volume24h: number | null;
  marketCap: number | null;
  confidence: Confidence;
};

export type RelativeStrength = {
  vsBoardPct: number | null;
  vsBenchmarkPct: number | null;
  state: string | null;
  confidence: Confidence;
};

export type VolumeAnomaly = {
  ratio: number | null;
  state: string | null;
  samples: number;
  median: number | null;
  note: string;
  confidence: Confidence;
};

/** Conditional and level-based throughout. `type: null` means no setup, not a weak one. */
export type Setup = {
  type: string | null;
  label: string | null;
  status: string | null;
  trigger: string | null;
  entryZone: number[] | null;
  invalidation: number | null;
  target1: number | null;
  target2: number | null;
  riskReward: number | null;
  note: string;
  confidence: Confidence;
};

export type RiskFactor = {
  key: string;
  label: string;
  level: RiskLevel | null;
  detail: string;
  confidence: Confidence;
};

export type RiskDetail = {
  level: RiskLevel | null;
  factors: RiskFactor[];
  measuredFactors: number;
  confidence: Confidence;
};

export type AnomalyFinding = { key: string; label: string; detail: string; confidence: Confidence };

export type Anomalies = {
  findings: AnomalyFinding[];
  /** Travels with the findings so they cannot be rendered as opportunities alone. */
  caveat: string;
  confidence: Confidence;
};

export type Evidence = {
  priceSeries: { points: number | null; granularity: string; source: string };
  volumeSeries: { samples: number | null; source: string | null };
  boardContext: { medianChangePct: number | null; benchmarkChangePct: number | null; assets: number | null };
  observedAt: string | null;
  /** What this analysis cannot see. Shown, not buried. */
  limits: string[];
};

export type HoldingContext = {
  known: boolean;
  quantity: number | null;
  unrealizedPnlPct: number | null;
  portfolioWeightPct: number | null;
  note: string;
};

export type PositionSizing = {
  available: boolean;
  riskPerUnitPct: number | null;
  suggestedAllocationPct: number | null;
  suggestedAmount: number | null;
  portfolioValue: number | null;
  riskBudgetPct: number | null;
  invalidation: number | null;
  riskAdjusted: boolean;
  /** Never omitted by the renderer: this is arithmetic on a budget, not advice. */
  caveat: string;
  note: string;
  confidence: Confidence;
};

export type AssetIntelligenceDetail = AssetIntelligence & {
  ok: boolean;
  /** False when the portfolio could not be read — different from "owns nothing". */
  personalized: boolean;
  whyDetail: { action: Reason[]; opportunity: Reason[]; entry: Reason[] };
  timeframes: { rows: TimeframeRow[]; alignment: TrendAlignment; note: string };
  structure: {
    extension: Extension;
    levels: Levels;
    volatility: Volatility;
    liquidity: Liquidity;
    relativeStrength: RelativeStrength;
  };
  setup: Setup;
  riskDetail: RiskDetail;
  anomalies: Anomalies;
  volume: VolumeAnomaly;
  evidence: Evidence;
  holding: HoldingContext;
  sizing: PositionSizing;
  regime: MarketRegime | null;
};

export type PortfolioRisk = {
  ok: boolean;
  available: boolean;
  concentration: {
    level: RiskLevel | null;
    topSymbol: string | null;
    topWeightPct: number | null;
    positions: number | null;
    weights: { symbol: string; weightPct: number | null }[];
    detail: string;
    confidence: Confidence;
  } | null;
  clusters: { pair: string[]; r: number | null; combinedWeightPct: number | null; level: RiskLevel | null }[];
  overlap: { detail: string; level: RiskLevel | null } | null;
  positions: number | null;
  measuredPairs: number | null;
  basis: string;
  note: string;
  confidence: Confidence | null;
};

// ---------------------------------------------------------------------------
// Normalizers
//
// Written defensively rather than trustingly. This payload crosses a version
// boundary — an older app talks to a newer server and vice versa — and the
// failure mode of an optimistic normalizer is a crash inside a render, on a
// screen whose price data was perfectly fine.
// ---------------------------------------------------------------------------

type Loose = Record<string, unknown>;

function obj(value: unknown): Loose {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Loose) : {};
}

function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** A number, or null when the server had nothing. Never a substituted zero. */
function num(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function text(value: unknown, fallback = ""): string {
  if (value === null || value === undefined) return fallback;
  const out = String(value).trim();
  return out || fallback;
}

function nullableText(value: unknown): string | null {
  const out = text(value);
  return out || null;
}

/**
 * Anything unrecognised becomes UNAVAILABLE.
 *
 * Defaulting to KNOWN would let a field this client does not understand be
 * presented as a measured fact, which is the exact failure this whole layer
 * exists to prevent.
 */
function confidence(value: unknown): Confidence {
  const out = text(value).toUpperCase();
  return out === "KNOWN" || out === "INFERRED" ? out : "UNAVAILABLE";
}

function riskLevel(value: unknown): RiskLevel | null {
  const out = text(value).toUpperCase();
  return out === "LOW" || out === "MODERATE" || out === "HIGH" || out === "EXTREME" ? out : null;
}

function normalizeReasons(value: unknown): Reason[] {
  return arr(value)
    .map((entry) => {
      const row = obj(entry);
      return { code: text(row.code), text: text(row.text), confidence: confidence(row.confidence) };
    })
    .filter((reason) => Boolean(reason.text));
}

function normalizeAction(value: unknown): ActionVerdict {
  const row = obj(value);
  const state = text(row.state).toUpperCase();
  return {
    state: (state || null) as ActionState | null,
    label: text(row.label, state),
    tone: (text(row.tone, "neutral") as ActionTone) || "neutral",
    perspective: text(row.perspective, "unknown"),
    reasons: normalizeReasons(row.reasons),
    confidence: confidence(row.confidence)
  };
}

function normalizeQuality(value: unknown): QualityScore {
  const row = obj(value);
  return { score: num(row.score), band: nullableText(row.band), confidence: confidence(row.confidence) };
}

function normalizeDataQuality(value: unknown): DataQuality {
  const row = obj(value);
  const level = text(row.level).toUpperCase();
  return {
    level: (level === "INSUFFICIENT" || level === "LIMITED" || level === "FULL" ? level : null) as DataQuality["level"],
    pricePoints: num(row.pricePoints),
    priceBasis: text(row.priceBasis),
    volumeHistory: Boolean(row.volumeHistory),
    note: text(row.note)
  };
}

export function normalizeIntelligence(value: unknown): AssetIntelligence | null {
  // A row whose analysis failed server-side carries `intelligence: null`. That
  // is a real state — the row still has a real price — so it stays null here
  // rather than becoming an empty verdict the card would try to draw.
  if (!value || typeof value !== "object") return null;
  const row = obj(value);
  const risk = obj(row.risk);
  return {
    symbol: text(row.symbol).toUpperCase(),
    action: normalizeAction(row.action),
    opportunity: normalizeQuality(row.opportunity),
    entry: normalizeQuality(row.entry),
    risk: { level: riskLevel(risk.level), confidence: confidence(risk.confidence) },
    dataQuality: normalizeDataQuality(row.dataQuality),
    disclaimer: text(row.disclaimer),
    why: normalizeReasons(row.why)
  };
}

export function normalizeRegime(value: unknown): MarketRegime | null {
  if (!value || typeof value !== "object") return null;
  const row = obj(value);
  const state = text(row.state).toUpperCase();
  return {
    state: (state === "RISK_ON" || state === "NEUTRAL" || state === "RISK_OFF" ? state : null) as MarketRegime["state"],
    label: nullableText(row.label),
    advancers: num(row.advancers),
    decliners: num(row.decliners),
    breadthPct: num(row.breadthPct),
    medianChangePct: num(row.medianChangePct),
    btcDominance: num(row.btcDominance),
    detail: text(row.detail),
    basis: text(row.basis),
    confidence: confidence(row.confidence)
  };
}

export function normalizeRotation(value: unknown): MarketRotation | null {
  if (!value || typeof value !== "object") return null;
  const row = obj(value);
  return {
    groups: arr(row.groups).map((entry) => {
      const group = obj(entry);
      return {
        key: text(group.key),
        avgChangePct: num(group.avgChangePct),
        count: num(group.count) ?? 0,
        confidence: confidence(group.confidence)
      };
    }),
    leader: nullableText(row.leader),
    basis: text(row.basis),
    confidence: confidence(row.confidence)
  };
}

function normalizeTimeframes(value: unknown) {
  const row = obj(value);
  const alignment = obj(row.alignment);
  const direction = (raw: unknown) => {
    const out = text(raw).toUpperCase();
    return (out === "UP" || out === "DOWN" || out === "FLAT" ? out : null) as "UP" | "DOWN" | "FLAT" | null;
  };
  return {
    rows: arr(row.rows).map((entry) => {
      const item = obj(entry);
      return {
        key: text(item.key),
        label: text(item.label),
        direction: direction(item.direction),
        changePct: num(item.changePct),
        thresholdPct: num(item.thresholdPct),
        basis: text(item.basis),
        confidence: confidence(item.confidence)
      };
    }),
    alignment: {
      direction: direction(alignment.direction),
      alignmentPct: num(alignment.alignmentPct),
      measured: num(alignment.measured) ?? 0,
      total: num(alignment.total) ?? 0,
      agree: num(alignment.agree),
      confidence: confidence(alignment.confidence)
    },
    note: text(row.note)
  };
}

function normalizeSetup(value: unknown): Setup {
  const row = obj(value);
  const zone = arr(row.entryZone)
    .map(num)
    .filter((point): point is number => point !== null);
  return {
    type: nullableText(row.type),
    label: nullableText(row.label),
    status: nullableText(row.status),
    trigger: nullableText(row.trigger),
    entryZone: zone.length === 2 ? zone : null,
    invalidation: num(row.invalidation),
    target1: num(row.target1),
    target2: num(row.target2),
    riskReward: num(row.riskReward),
    note: text(row.note),
    confidence: confidence(row.confidence)
  };
}

function normalizeRiskDetail(value: unknown): RiskDetail {
  const row = obj(value);
  return {
    level: riskLevel(row.level),
    factors: arr(row.factors).map((entry) => {
      const factor = obj(entry);
      return {
        key: text(factor.key),
        label: text(factor.label),
        level: riskLevel(factor.level),
        detail: text(factor.detail),
        confidence: confidence(factor.confidence)
      };
    }),
    measuredFactors: num(row.measuredFactors) ?? 0,
    confidence: confidence(row.confidence)
  };
}

function normalizeVolume(value: unknown): VolumeAnomaly {
  const row = obj(value);
  return {
    ratio: num(row.ratio),
    state: nullableText(row.state),
    samples: num(row.samples) ?? 0,
    median: num(row.median),
    note: text(row.note),
    confidence: confidence(row.confidence)
  };
}

/**
 * The deep payload for one asset.
 *
 * `why` is renamed to `whyDetail` on the way in because the two depths use the
 * same key for different shapes — a flat list on a row, three keyed lists here.
 * A component that received either under one name would have to type-test it at
 * render time, and the version where that test is wrong ships an empty section.
 */
export function normalizeIntelligenceDetail(value: unknown): AssetIntelligenceDetail | null {
  const base = normalizeIntelligence(value);
  if (!base) return null;
  const row = obj(value);
  const why = obj(row.why);
  const structure = obj(row.structure);
  const extension = obj(structure.extension);
  const levels = obj(structure.levels);
  const volatility = obj(structure.volatility);
  const liquidity = obj(structure.liquidity);
  const strength = obj(structure.relativeStrength);
  const anomalies = obj(row.anomalies);
  const evidence = obj(row.evidence);
  const priceSeries = obj(evidence.priceSeries);
  const volumeSeries = obj(evidence.volumeSeries);
  const board = obj(evidence.boardContext);
  const holding = obj(row.holding);
  const sizing = obj(row.sizing);

  return {
    ...base,
    // At full depth `why` is the keyed object, so the flat list the row shape
    // carries is rebuilt from the action reasons rather than left empty.
    why: normalizeReasons(why.action),
    ok: Boolean(row.ok),
    personalized: Boolean(row.personalized),
    whyDetail: {
      action: normalizeReasons(why.action),
      opportunity: normalizeReasons(why.opportunity),
      entry: normalizeReasons(why.entry)
    },
    timeframes: normalizeTimeframes(row.timeframes),
    structure: {
      extension: {
        zScore: num(extension.zScore),
        vsMeanPct: num(extension.vsMeanPct),
        state: nullableText(extension.state),
        lookbackHours: num(extension.lookbackHours),
        confidence: confidence(extension.confidence)
      },
      levels: {
        support: num(levels.support),
        resistance: num(levels.resistance),
        supportDistancePct: num(levels.supportDistancePct),
        resistanceDistancePct: num(levels.resistanceDistancePct),
        rangeHigh: num(levels.rangeHigh),
        rangeLow: num(levels.rangeLow),
        positionInRangePct: num(levels.positionInRangePct),
        basis: text(levels.basis),
        confidence: confidence(levels.confidence)
      },
      volatility: {
        dailyPct: num(volatility.dailyPct),
        band: riskLevel(volatility.band),
        confidence: confidence(volatility.confidence)
      },
      liquidity: {
        turnoverPct: num(liquidity.turnoverPct),
        band: nullableText(liquidity.band),
        volume24h: num(liquidity.volume24h),
        marketCap: num(liquidity.marketCap),
        confidence: confidence(liquidity.confidence)
      },
      relativeStrength: {
        vsBoardPct: num(strength.vsBoardPct),
        vsBenchmarkPct: num(strength.vsBenchmarkPct),
        state: nullableText(strength.state),
        confidence: confidence(strength.confidence)
      }
    },
    setup: normalizeSetup(row.setup),
    riskDetail: normalizeRiskDetail(row.riskDetail),
    anomalies: {
      findings: arr(anomalies.findings).map((entry) => {
        const finding = obj(entry);
        return {
          key: text(finding.key),
          label: text(finding.label),
          detail: text(finding.detail),
          confidence: confidence(finding.confidence)
        };
      }),
      caveat: text(anomalies.caveat),
      confidence: confidence(anomalies.confidence)
    },
    volume: normalizeVolume(row.volume),
    evidence: {
      priceSeries: {
        points: num(priceSeries.points),
        granularity: text(priceSeries.granularity),
        source: text(priceSeries.source)
      },
      volumeSeries: { samples: num(volumeSeries.samples), source: nullableText(volumeSeries.source) },
      boardContext: {
        medianChangePct: num(board.medianChangePct),
        benchmarkChangePct: num(board.benchmarkChangePct),
        assets: num(board.assets)
      },
      observedAt: nullableText(evidence.observedAt),
      limits: arr(evidence.limits)
        .map((limit) => text(limit))
        .filter(Boolean)
    },
    holding: {
      known: Boolean(holding.known),
      quantity: num(holding.quantity),
      unrealizedPnlPct: num(holding.unrealizedPnlPct),
      portfolioWeightPct: num(holding.portfolioWeightPct),
      note: text(holding.note)
    },
    sizing: {
      available: Boolean(sizing.available),
      riskPerUnitPct: num(sizing.riskPerUnitPct),
      suggestedAllocationPct: num(sizing.suggestedAllocationPct),
      suggestedAmount: num(sizing.suggestedAmount),
      portfolioValue: num(sizing.portfolioValue),
      riskBudgetPct: num(sizing.riskBudgetPct),
      invalidation: num(sizing.invalidation),
      riskAdjusted: Boolean(sizing.riskAdjusted),
      caveat: text(sizing.caveat),
      note: text(sizing.note),
      confidence: confidence(sizing.confidence)
    },
    regime: normalizeRegime(row.regime)
  };
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/**
 * The full drill-down for one open asset.
 *
 * Called when a user expands the intelligence panel, not when the screen mounts.
 * The deep layers exist for the asset somebody actually opened, and fetching
 * them on mount would spend a request per glance.
 *
 * `riskBudgetPct` is the share of the portfolio the member is willing to lose on
 * being wrong. It is passed through rather than defaulted silently because the
 * sizing arithmetic is meaningless without it, and picking a number on the
 * member's behalf would be the product making a risk decision for them.
 */
export async function getAssetIntelligence(symbol: string, riskBudgetPct?: number) {
  const query = riskBudgetPct === undefined ? "" : `?riskBudgetPct=${encodeURIComponent(String(riskBudgetPct))}`;
  return normalizeIntelligenceDetail(
    await pulseApi<unknown>(
      `/api/pulse/market/assets/${encodeURIComponent(symbol.toUpperCase())}/intelligence${query}`
    )
  );
}

/** Concentration, correlation clusters and exposure overlap for the caller. */
export async function getPortfolioRisk(): Promise<PortfolioRisk> {
  const response = obj(await pulseApi<unknown>("/api/pulse/market/portfolio/risk"));
  const concentration = obj(response.concentration);
  return {
    ok: Boolean(response.ok),
    available: Boolean(response.available),
    concentration: response.concentration
      ? {
          level: riskLevel(concentration.level),
          topSymbol: nullableText(concentration.topSymbol),
          topWeightPct: num(concentration.topWeightPct),
          positions: num(concentration.positions),
          weights: arr(concentration.weights).map((entry) => {
            const weight = obj(entry);
            return { symbol: text(weight.symbol).toUpperCase(), weightPct: num(weight.weightPct) };
          }),
          detail: text(concentration.detail),
          confidence: confidence(concentration.confidence)
        }
      : null,
    clusters: arr(response.clusters).map((entry) => {
      const cluster = obj(entry);
      return {
        pair: arr(cluster.pair).map((item) => text(item).toUpperCase()),
        r: num(cluster.r),
        combinedWeightPct: num(cluster.combinedWeightPct),
        level: riskLevel(cluster.level)
      };
    }),
    overlap: response.overlap
      ? { detail: text(obj(response.overlap).detail), level: riskLevel(obj(response.overlap).level) }
      : null,
    positions: num(response.positions),
    measuredPairs: num(response.measuredPairs),
    basis: text(response.basis),
    note: text(response.note),
    confidence: response.confidence ? confidence(response.confidence) : null
  };
}

// ---------------------------------------------------------------------------
// Display helpers
//
// The single place a null reading becomes "--", so no screen has to remember to
// check before formatting — and no screen can accidentally render a missing
// score as 0.
// ---------------------------------------------------------------------------

export const UNKNOWN_READING = "--";

export function formatScore(score: number | null): string {
  return score === null ? UNKNOWN_READING : String(Math.round(score));
}

export function formatPct(value: number | null, digits = 1): string {
  return value === null ? UNKNOWN_READING : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function formatPlainPct(value: number | null, digits = 1): string {
  return value === null ? UNKNOWN_READING : `${value.toFixed(digits)}%`;
}

export function formatRatio(value: number | null, digits = 2): string {
  return value === null ? UNKNOWN_READING : `${value.toFixed(digits)}x`;
}

/**
 * A level price, at the precision the asset actually needs.
 *
 * A support at 0.00004182 rendered to two decimals is 0.00 — which is not a
 * rounded level, it is a different claim entirely.
 */
export function formatLevel(value: number | null): string {
  if (value === null) return UNKNOWN_READING;
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 0 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 8;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** Title-cases an enum for display: `WAIT_FOR_PULLBACK` → `Wait for pullback`. */
export function humanizeState(state: string | null): string {
  if (!state) return UNKNOWN_READING;
  const words = state.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
