/**
 * Capital Graph — the member's own holdings, coverage, structure and documents,
 * over the canonical read-only routes.
 *
 * Same contract as `privateOffice.ts` and `privateRecords.ts`: every state
 * returned here was decided by the server in `services/private_office_routes.py`,
 * a tagged result per call, never a thrown Error that collapses "we could not
 * look" into "there is nothing here".
 *
 * Two shapes deserve naming. There is no aggregate value anywhere in this
 * payload — the server refuses to total an estate whose parts have different
 * truth states, and this client must not compute one either. And `complete`
 * is the honest replacement: "3 properties" may only be said while it is true,
 * "3 properties so far" otherwise.
 */

import { PulseApiError, pulseApi } from "./pulseApi";
import { PrivateFact, parseFact } from "./privateOffice";
import { officeRequestHeaders } from "../privateOffice/officeLock";

/* --- vocabulary --------------------------------------------------------- */

/** Mirrors `services/private_office/capital_graph.py` VIEWS. */
export const CAPITAL_VIEWS = ["holdings", "coverage", "structure", "documents"] as const;

export type CapitalView = (typeof CAPITAL_VIEWS)[number];

export function asCapitalView(value: unknown): CapitalView | null {
  const word = typeof value === "string" ? value.trim().toLowerCase() : "";
  return (CAPITAL_VIEWS as readonly string[]).includes(word) ? (word as CapitalView) : null;
}

/** Mirrors TRUTH_STATES: how well one subject is known, weakest wins. */
export const TRUTH_STATES = [
  "KNOWN",
  "INFERRED",
  "ESTIMATED",
  "STALE",
  "MISSING",
  "CONFLICTING",
  "PRO_REVIEW"
] as const;

export type CapitalTruthState = (typeof TRUTH_STATES)[number];

/* --- shapes ------------------------------------------------------------- */

export type CapitalNode = {
  id: number;
  nodeType: string;
  externalRef: string;
  lifecycleState: string;
  sensitivity: string;
  domain: string;
  createdAt: string;
  updatedAt: string;
  truth: string;
  factCount: number;
};

export type CapitalEdgeProvenance = {
  sourceType: string;
  sourceId: string;
  hasSourceDocument: boolean;
  provenanceType: string;
  verification: string;
};

export type CapitalEdge = {
  id: number;
  sourceNodeId: number;
  targetNodeId: number;
  relationType: string;
  lifecycleState: string;
  createdAt: string;
  updatedAt: string;
  provenance: CapitalEdgeProvenance;
};

/** One edge from a subject's point of view, with the far end named. */
export type CapitalRelationship = CapitalEdge & {
  direction: "in" | "out";
  other: CapitalNode;
};

export type CapitalConflictSide = {
  factId: number;
  value: string;
  valueType: string;
  provenanceType: string;
  verification: string;
  observedAt: string;
  stale: boolean;
};

export type CapitalConflict = {
  conflictId: string;
  subjectId: string;
  factType: string;
  reason: string;
  competing: CapitalConflictSide[];
};

export type CapitalStaleFlag = {
  factId: number;
  factType: string;
  ageDays: number | null;
  horizonDays: number | null;
};

export type CapitalGraph = {
  view: CapitalView;
  nodes: CapitalNode[];
  edges: CapitalEdge[];
  facts: PrivateFact[];
  conflicts: CapitalConflict[];
  stale: CapitalStaleFlag[];
  /** Node counts by node_type. Counts of things, never of money. */
  counted: Record<string, number>;
  /** Node counts by truth state; every state pre-declared by the server. */
  truthCounts: Record<string, number>;
  /** Whether what is shown is all of it. Gates "N things" vs "N so far". */
  complete: boolean;
};

export type CapitalGraphResult =
  | { state: "READY"; graph: CapitalGraph }
  /** 403 with a policy reason: the question was refused, not empty. */
  | { state: "DENIED"; reason: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

export type CapitalEntityResult =
  | {
      state: "READY";
      entity: CapitalNode;
      /** The immediate neighbourhood, without the subject repeated. */
      related: CapitalNode[];
      graph: CapitalGraph;
    }
  /** Absent, someone else's, or out of view — identical on purpose. */
  | { state: "NOT_FOUND" }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

/**
 * One projected holding. `quantity`, `costBasis`, `price`, `value` and
 * `pnlValue` are `null` when unknown — a holding without a live quote has no
 * value, not a value of zero, mirroring the server's `_value_holding` rule.
 */
export type CapitalPortfolioAsset = {
  nodeId: number;
  symbol: string;
  name: string;
  quantity: number | null;
  lotCount: number;
  costBasis: number | null;
  price: number | null;
  value: number | null;
  pnlValue: number | null;
  priced: boolean;
  change24h: number | null;
  /** When the member's ledger last asserted this holding. */
  projectedAt: string;
};

export type CapitalPortfolioTotals = {
  /** Only present while every asset is priced; otherwise null, never partial. */
  value: number | null;
  cost: number | null;
  pnlValue: number | null;
  complete: boolean;
  assets: number;
  priced: number;
  unpricedSymbols: string[];
  basisKnown: number;
};

/** The price feed's own account of itself — used to label freshness honestly. */
export type CapitalPortfolioPrices = {
  source: string;
  observedEpoch: number | null;
  ageSeconds: number | null;
  warning: string;
};

/** How far behind the ledger the projection may be. */
export type CapitalPortfolioSync = {
  pending: number;
  failed: number;
  enabled: boolean;
};

export type CapitalPortfolio = {
  assets: CapitalPortfolioAsset[];
  totals: CapitalPortfolioTotals;
  prices: CapitalPortfolioPrices;
  sync: CapitalPortfolioSync;
};

export type CapitalPortfolioResult =
  | { state: "READY"; portfolio: CapitalPortfolio }
  | { state: "DENIED"; reason: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

export type CapitalRelationshipsResult =
  | { state: "READY"; entity: CapitalNode; relationships: CapitalRelationship[]; complete: boolean }
  | { state: "NOT_FOUND" }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

/* --- parsing ------------------------------------------------------------ */

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asId(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function asMaybeCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asCountMap(value: unknown): Record<string, number> {
  const raw = asRecord(value);
  const out: Record<string, number> = {};
  Object.entries(raw).forEach(([key, count]) => {
    if (typeof count === "number" && Number.isFinite(count)) out[key] = count;
  });
  return out;
}

export function parseCapitalNode(raw: unknown): CapitalNode {
  const row = asRecord(raw);
  return {
    id: asId(row.id),
    nodeType: asText(row.node_type),
    externalRef: asText(row.external_ref),
    lifecycleState: asText(row.lifecycle_state),
    sensitivity: asText(row.sensitivity),
    domain: asText(row.domain),
    createdAt: asText(row.created_at),
    updatedAt: asText(row.updated_at),
    truth: asText(row.truth),
    factCount: asId(row.fact_count)
  };
}

function parseProvenance(raw: unknown): CapitalEdgeProvenance {
  const row = asRecord(raw);
  return {
    sourceType: asText(row.source_type),
    sourceId: asText(row.source_id),
    hasSourceDocument: row.has_source_document === true,
    provenanceType: asText(row.provenance_type),
    verification: asText(row.verification)
  };
}

export function parseCapitalEdge(raw: unknown): CapitalEdge {
  const row = asRecord(raw);
  return {
    id: asId(row.id),
    sourceNodeId: asId(row.source_node_id),
    targetNodeId: asId(row.target_node_id),
    relationType: asText(row.relation_type),
    lifecycleState: asText(row.lifecycle_state),
    createdAt: asText(row.created_at),
    updatedAt: asText(row.updated_at),
    provenance: parseProvenance(row.provenance)
  };
}

function parseRelationship(raw: unknown): CapitalRelationship {
  const row = asRecord(raw);
  return {
    ...parseCapitalEdge(raw),
    direction: asText(row.direction) === "in" ? "in" : "out",
    other: parseCapitalNode(row.other)
  };
}

function parseConflict(raw: unknown): CapitalConflict {
  const row = asRecord(raw);
  const competing = Array.isArray(row.competing) ? row.competing : [];
  return {
    conflictId: asText(row.conflict_id),
    subjectId: asText(row.subject_id),
    factType: asText(row.fact_type),
    reason: asText(row.reason),
    competing: competing.map((entry) => {
      const side = asRecord(entry);
      return {
        factId: asId(side.fact_id),
        value: asText(side.value),
        valueType: asText(side.value_type),
        provenanceType: asText(side.provenance_type),
        verification: asText(side.verification),
        observedAt: asText(side.observed_at),
        stale: side.stale === true
      };
    })
  };
}

function parseStaleFlag(raw: unknown): CapitalStaleFlag {
  const row = asRecord(raw);
  return {
    factId: asId(row.fact_id),
    factType: asText(row.fact_type),
    ageDays: asMaybeCount(row.age_days),
    horizonDays: asMaybeCount(row.horizon_days)
  };
}

export function parseCapitalGraph(raw: unknown, view: CapitalView): CapitalGraph {
  const row = asRecord(raw);
  const list = (value: unknown) => (Array.isArray(value) ? value : []);
  return {
    view: asCapitalView(row.view) ?? view,
    nodes: list(row.nodes).map(parseCapitalNode),
    edges: list(row.edges).map(parseCapitalEdge),
    facts: list(row.facts).map(parseFact),
    conflicts: list(row.conflicts).map(parseConflict),
    stale: list(row.stale).map(parseStaleFlag),
    counted: asCountMap(row.counted),
    truthCounts: asCountMap(row.truth_counts),
    // Read, never derived: only the server knows whether it truncated.
    complete: row.complete === true
  };
}

function asMaybeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parsePortfolioAsset(raw: unknown): CapitalPortfolioAsset {
  const row = asRecord(raw);
  return {
    nodeId: asId(row.node_id),
    symbol: asText(row.symbol),
    name: asText(row.name),
    quantity: asMaybeNumber(row.quantity),
    lotCount: asId(row.lot_count),
    costBasis: asMaybeNumber(row.cost_basis),
    price: asMaybeNumber(row.price),
    value: asMaybeNumber(row.value),
    pnlValue: asMaybeNumber(row.pnl_value),
    priced: row.priced === true,
    change24h: asMaybeNumber(row.change_24h),
    projectedAt: asText(row.projected_at)
  };
}

export function parseCapitalPortfolio(raw: unknown): CapitalPortfolio {
  const row = asRecord(raw);
  const totals = asRecord(row.totals);
  const prices = asRecord(row.prices);
  const sync = asRecord(row.sync);
  return {
    assets: (Array.isArray(row.assets) ? row.assets : []).map(parsePortfolioAsset),
    totals: {
      value: asMaybeNumber(totals.value),
      cost: asMaybeNumber(totals.cost),
      pnlValue: asMaybeNumber(totals.pnl_value),
      // Read, never derived: only the server knows whether every asset priced.
      complete: totals.complete === true,
      assets: asId(totals.assets),
      priced: asId(totals.priced),
      unpricedSymbols: (Array.isArray(totals.unpriced_symbols)
        ? totals.unpriced_symbols
        : []
      ).map(asText),
      basisKnown: asId(totals.basis_known)
    },
    prices: {
      source: asText(prices.source),
      observedEpoch: asMaybeNumber(prices.observed_epoch),
      ageSeconds: asMaybeNumber(prices.age_seconds),
      warning: asText(prices.warning)
    },
    sync: {
      pending: asId(sync.pending),
      failed: asId(sync.failed),
      enabled: sync.enabled === true
    }
  };
}

/* --- refusals ----------------------------------------------------------- */

type Refusal =
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

function refusal(error: unknown): Refusal {
  if (!(error instanceof PulseApiError)) return { state: "ERROR", message: "" };
  const details = asRecord(error.details);
  const serverState = asText(details.state).trim().toUpperCase();
  if (serverState === "PRIVATE_OFFICE_LOCKED" || serverState === "LOCKED" || error.status === 423) {
    return { state: "LOCKED", setupRequired: details.setup_required === true };
  }
  if (serverState === "NOT_ENTITLED") {
    return { state: "NOT_ENTITLED", minimumTier: asText(details.minimum_tier) };
  }
  if (serverState === "FEATURE_DISABLED") return { state: "FEATURE_DISABLED" };
  if (serverState === "NOT_IMPLEMENTED") return { state: "NOT_IMPLEMENTED" };
  if (serverState === "UNAVAILABLE" || error.status === 503 || error.status === 504) {
    return { state: "UNAVAILABLE" };
  }
  return { state: "ERROR", message: error.message || "" };
}

/**
 * The owner-scoped capital reads add one arm to the shared refusals.
 *
 * Every route behind `_office_lock_gate` answers a failed owner check with
 * `403 {state: "denied", reason: {...}}`, which is a different fact from an
 * entitlement refusal or an outage: the question was understood and refused.
 * It must stay distinguishable so the screen can say so rather than draw an
 * empty balance sheet.
 */
type CapitalRefusal = Refusal | { state: "DENIED"; reason: string };

function capitalRefusal(error: unknown): CapitalRefusal {
  if (error instanceof PulseApiError && error.status === 403) {
    const details = asRecord(error.details);
    if (asText(details.state).trim().toUpperCase() === "DENIED") {
      return { state: "DENIED", reason: asText(asRecord(details.reason).reason) };
    }
  }
  return refusal(error);
}

/** 404 on the entity routes: absent and foreign arrive identically. */
function isNotFound(error: unknown): boolean {
  return (
    error instanceof PulseApiError &&
    error.status === 404 &&
    asText(asRecord(error.details).state).trim().toUpperCase() === "NOT_FOUND"
  );
}

/* --- reads -------------------------------------------------------------- */

export const CAPITAL_GRAPH_PATH = "/api/private-office/capital-graph";
export const CAPITAL_ENTITY_PATH = "/api/private-office/entities";

/** One view's graph, or the specific reason the server refused. */
export async function getCapitalGraph(view: CapitalView): Promise<CapitalGraphResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(`${CAPITAL_GRAPH_PATH}?view=${view}`, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", graph: parseCapitalGraph(body.capital_graph, view) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 403) {
      const details = asRecord(error.details);
      if (asText(details.state).trim().toUpperCase() === "DENIED") {
        return { state: "DENIED", reason: asText(details.reason) };
      }
    }
    return refusal(error);
  }
}

export const CAPITAL_PORTFOLIO_PATH = "/api/private-office/capital-graph/portfolio";

/**
 * The Portfolio node: projected holdings priced live at read time.
 *
 * The server never stores a price and never totals an incomplete set, so
 * `totals.value === null` with `complete: false` means "we will not pretend" —
 * render the per-asset rows and say which symbols went unpriced.
 */
export async function getCapitalPortfolio(): Promise<CapitalPortfolioResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(CAPITAL_PORTFOLIO_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", portfolio: parseCapitalPortfolio(body.portfolio) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 403) {
      const details = asRecord(error.details);
      if (asText(details.state).trim().toUpperCase() === "DENIED") {
        return { state: "DENIED", reason: asText(asRecord(details.reason).reason) };
      }
    }
    return refusal(error);
  }
}

/** One entity and its immediate neighbourhood, in one view. */
export async function getCapitalEntity(
  nodeId: number,
  view: CapitalView
): Promise<CapitalEntityResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(`${CAPITAL_ENTITY_PATH}/${nodeId}?view=${view}`, {
        headers: await officeRequestHeaders()
      })
    );
    const graph = parseCapitalGraph(body.capital_graph, view);
    const payload = asRecord(body.capital_graph);
    return {
      state: "READY",
      entity: parseCapitalNode(body.entity),
      related: (Array.isArray(payload.related) ? payload.related : []).map(parseCapitalNode),
      graph
    };
  } catch (error) {
    if (isNotFound(error)) return { state: "NOT_FOUND" };
    return refusal(error);
  }
}

/* --- command center ------------------------------------------------------
 *
 * `overview` and `exposure` are the SAME server computation: the exposure route
 * projects a subset of the overview payload precisely so the two screens can
 * never disagree about which holding is largest. They are parsed by shared
 * helpers here for the same reason — two parsers would reintroduce the drift
 * the server went out of its way to prevent.
 *
 * Every money field goes through `asMaybeNumber`, which yields `null` rather
 * than `0` for an absent key. That matters most on the refusal payload, where
 * the server sends `net_position: {}`: a parser that defaulted to zero would
 * turn "we would not say" into "you have nothing", which is the single lie this
 * surface exists to avoid.
 */

/** One coverage dimension, carrying its own arithmetic. */
export type CapitalCoverageDimension = {
  known: number;
  countable: number;
  /** null when nothing was countable — not 0, which would read as "0% known". */
  ratio: number | null;
};

/** One slice of a concentration chart. */
export type CapitalConcentration = {
  key: string;
  label: string;
  value: number | null;
  /** Share of the *priced* total. null when there is no total to divide by. */
  share: number | null;
};

/** A gap in the picture, explained (§37: what / why / source). */
export type CapitalReviewItem = {
  kind: string;
  subject: string;
  detail: string;
  source: string;
};

export type CapitalAssetsBlock = {
  pricedValue: number | null;
  currency: string;
  count: number;
  priced: number;
  unpriced: number;
  unpricedSymbols: string[];
  basisKnown: number;
  knownCost: number | null;
  complete: boolean;
};

/** One currency's slice of the liability side, as the server bucketed it. */
export type CapitalCurrencyBucket = {
  amount: number | null;
  count: number;
};

export type CapitalLiabilitiesBlock = {
  knownAmount: number | null;
  currency: string;
  count: number;
  quantified: number;
  unquantified: number;
  foreignCurrency: number;
  unspecifiedCurrency: number;
  /**
   * Every currency the member's obligations are denominated in, keyed by code.
   *
   * `knownAmount` is the base-currency bucket alone; everything else here was
   * excluded from the net position because the server will not invent an FX
   * rate. This map is the only place that says *how much* was set aside, so a
   * screen that reports "2 liabilities excluded" without it is naming a count
   * while withholding the magnitude.
   */
  byCurrency: Record<string, CapitalCurrencyBucket>;
  complete: boolean;
  truncated: boolean;
};

export type CapitalNetPosition = {
  /**
   * Priced assets minus quantified liabilities — NOT net worth.
   *
   * Never render this without `complete` and `incompleteReasons` beside it.
   * When `complete` is false the server has excluded something it could not
   * compare, and `incompleteReasons` names what. `no_liabilities_recorded` is
   * in that list on purpose: no debt on file is not the same as no debt.
   */
  estimated: number | null;
  currency: string;
  knownAssets: number | null;
  knownLiabilities: number | null;
  complete: boolean;
  incompleteReasons: string[];
  excluded: {
    unpricedAssets: number;
    unquantifiedLiabilities: number;
    foreignCurrencyLiabilities: number;
    unspecifiedCurrencyLiabilities: number;
  };
  basis: string;
  disclaimer: string;
};

export type CapitalCoverage = {
  dimensions: Record<string, CapitalCoverageDimension>;
  /** null when no dimension was scoreable. Never silently 0. */
  score: number | null;
  scoredDimensions: string[];
  formula: string;
};

export type CapitalConcentrations = {
  assets: CapitalConcentration[];
  assetBasis: string;
  assetTotal: number | null;
  assetsRanked: number;
  assetsUnrankedTail: number;
  liabilities: CapitalConcentration[];
  liabilityBasis: string;
  liabilityTotal: number | null;
  currency: string;
};

export type CapitalPrices = {
  source: string;
  observedEpoch: number | null;
  ageSeconds: number | null;
  warning: string;
};

export type CapitalOverview = {
  assets: CapitalAssetsBlock;
  liabilities: CapitalLiabilitiesBlock;
  netPosition: CapitalNetPosition;
  coverage: CapitalCoverage;
  concentrations: CapitalConcentrations;
  needsReview: CapitalReviewItem[];
  /** May exceed `needsReview.length`: the server caps what it sends. */
  needsReviewTotal: number;
  prices: CapitalPrices;
  generatedAt: string;
};

/** The exposure route's strict subset of the overview payload. */
export type CapitalExposure = {
  concentrations: CapitalConcentrations;
  assets: CapitalAssetsBlock;
  liabilities: CapitalLiabilitiesBlock;
  coverage: CapitalCoverage;
  prices: CapitalPrices;
  generatedAt: string;
};

export type CapitalOverviewResult =
  | { state: "READY"; overview: CapitalOverview }
  | { state: "DENIED"; reason: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

export type CapitalExposureResult =
  | { state: "READY"; exposure: CapitalExposure }
  | { state: "DENIED"; reason: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

function parseCapitalPrices(raw: unknown): CapitalPrices {
  const row = asRecord(raw);
  return {
    source: asText(row.source),
    observedEpoch: asMaybeNumber(row.observed_epoch),
    ageSeconds: asMaybeNumber(row.age_seconds),
    warning: asText(row.warning)
  };
}

function parseCoverageDimension(raw: unknown): CapitalCoverageDimension {
  const row = asRecord(raw);
  return {
    known: asId(row.known),
    countable: asId(row.countable),
    ratio: asMaybeNumber(row.ratio)
  };
}

function parseConcentration(raw: unknown): CapitalConcentration {
  const row = asRecord(raw);
  return {
    key: asText(row.key),
    label: asText(row.label),
    value: asMaybeNumber(row.value),
    share: asMaybeNumber(row.share)
  };
}

function parseAssetsBlock(raw: unknown): CapitalAssetsBlock {
  const row = asRecord(raw);
  return {
    pricedValue: asMaybeNumber(row.priced_value),
    currency: asText(row.currency),
    count: asId(row.count),
    priced: asId(row.priced),
    unpriced: asId(row.unpriced),
    unpricedSymbols: (Array.isArray(row.unpriced_symbols) ? row.unpriced_symbols : []).map(asText),
    basisKnown: asId(row.basis_known),
    knownCost: asMaybeNumber(row.known_cost),
    complete: row.complete === true
  };
}

function parseLiabilitiesBlock(raw: unknown): CapitalLiabilitiesBlock {
  const row = asRecord(raw);
  return {
    knownAmount: asMaybeNumber(row.known_amount),
    currency: asText(row.currency),
    count: asId(row.count),
    quantified: asId(row.quantified),
    unquantified: asId(row.unquantified),
    foreignCurrency: asId(row.foreign_currency),
    unspecifiedCurrency: asId(row.unspecified_currency),
    byCurrency: parseCurrencyBuckets(row.by_currency),
    complete: row.complete === true,
    truncated: row.truncated === true
  };
}

function parseCurrencyBuckets(raw: unknown): Record<string, CapitalCurrencyBucket> {
  const rows = asRecord(raw);
  const buckets: Record<string, CapitalCurrencyBucket> = {};
  for (const code of Object.keys(rows)) {
    const bucket = asRecord(rows[code]);
    buckets[code] = { amount: asMaybeNumber(bucket.amount), count: asId(bucket.count) };
  }
  return buckets;
}

function parseCoverage(raw: unknown): CapitalCoverage {
  const row = asRecord(raw);
  const rawDimensions = asRecord(row.dimensions);
  const dimensions: Record<string, CapitalCoverageDimension> = {};
  for (const name of Object.keys(rawDimensions)) {
    dimensions[name] = parseCoverageDimension(rawDimensions[name]);
  }
  return {
    dimensions,
    score: asMaybeNumber(row.score),
    scoredDimensions: (Array.isArray(row.scored_dimensions) ? row.scored_dimensions : []).map(
      asText
    ),
    formula: asText(row.formula)
  };
}

function parseConcentrations(raw: unknown): CapitalConcentrations {
  const row = asRecord(raw);
  return {
    assets: (Array.isArray(row.assets) ? row.assets : []).map(parseConcentration),
    assetBasis: asText(row.asset_basis),
    assetTotal: asMaybeNumber(row.asset_total),
    assetsRanked: asId(row.assets_ranked),
    assetsUnrankedTail: asId(row.assets_unranked_tail),
    liabilities: (Array.isArray(row.liabilities) ? row.liabilities : []).map(parseConcentration),
    liabilityBasis: asText(row.liability_basis),
    liabilityTotal: asMaybeNumber(row.liability_total),
    currency: asText(row.currency)
  };
}

function parseReviewItem(raw: unknown): CapitalReviewItem {
  const row = asRecord(raw);
  return {
    kind: asText(row.kind),
    subject: asText(row.subject),
    detail: asText(row.detail),
    source: asText(row.source)
  };
}

function parseNetPosition(raw: unknown): CapitalNetPosition {
  const row = asRecord(raw);
  const excluded = asRecord(row.excluded);
  return {
    estimated: asMaybeNumber(row.estimated),
    currency: asText(row.currency),
    knownAssets: asMaybeNumber(row.known_assets),
    knownLiabilities: asMaybeNumber(row.known_liabilities),
    // Read, never derived. A client that recomputed this from the reason list
    // would start disagreeing with the server the moment a reason is added.
    complete: row.complete === true,
    incompleteReasons: (Array.isArray(row.incomplete_reasons) ? row.incomplete_reasons : []).map(
      asText
    ),
    excluded: {
      unpricedAssets: asId(excluded.unpriced_assets),
      unquantifiedLiabilities: asId(excluded.unquantified_liabilities),
      foreignCurrencyLiabilities: asId(excluded.foreign_currency_liabilities),
      unspecifiedCurrencyLiabilities: asId(excluded.unspecified_currency_liabilities)
    },
    basis: asText(row.basis),
    disclaimer: asText(row.disclaimer)
  };
}

export function parseCapitalOverview(raw: unknown): CapitalOverview {
  const row = asRecord(raw);
  return {
    assets: parseAssetsBlock(row.assets),
    liabilities: parseLiabilitiesBlock(row.liabilities),
    netPosition: parseNetPosition(row.net_position),
    coverage: parseCoverage(row.coverage),
    concentrations: parseConcentrations(row.concentrations),
    needsReview: (Array.isArray(row.needs_review) ? row.needs_review : []).map(parseReviewItem),
    needsReviewTotal: asId(row.needs_review_total),
    prices: parseCapitalPrices(row.prices),
    generatedAt: asText(row.generated_at)
  };
}

export function parseCapitalExposure(raw: unknown): CapitalExposure {
  const row = asRecord(raw);
  return {
    concentrations: parseConcentrations(row.concentrations),
    assets: parseAssetsBlock(row.assets),
    liabilities: parseLiabilitiesBlock(row.liabilities),
    coverage: parseCoverage(row.coverage),
    prices: parseCapitalPrices(row.prices),
    generatedAt: asText(row.generated_at)
  };
}

export const CAPITAL_OVERVIEW_PATH = "/api/private-office/capital-graph/overview";
export const CAPITAL_EXPOSURE_PATH = "/api/private-office/capital-graph/exposure";

/** The Capital Command Center: assets, liabilities, net position, coverage. */
export async function getCapitalOverview(): Promise<CapitalOverviewResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(CAPITAL_OVERVIEW_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", overview: parseCapitalOverview(body.overview) };
  } catch (error) {
    return capitalRefusal(error);
  }
}

/** Concentration over the subset whose value is actually known. */
export async function getCapitalExposure(): Promise<CapitalExposureResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(CAPITAL_EXPOSURE_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", exposure: parseCapitalExposure(body.exposure) };
  } catch (error) {
    return capitalRefusal(error);
  }
}

/** The edges touching one entity, with the far end named. */
export async function getCapitalRelationships(
  nodeId: number,
  view: CapitalView
): Promise<CapitalRelationshipsResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(`${CAPITAL_ENTITY_PATH}/${nodeId}/relationships?view=${view}`, {
        headers: await officeRequestHeaders()
      })
    );
    const rows = Array.isArray(body.relationships) ? body.relationships : [];
    return {
      state: "READY",
      entity: parseCapitalNode(body.entity),
      relationships: rows.map(parseRelationship),
      complete: body.complete === true
    };
  } catch (error) {
    if (isNotFound(error)) return { state: "NOT_FOUND" };
    return refusal(error);
  }
}

/* --- obligations, cash flow, integrity ---------------------------------- */

/**
 * The last three capital-graph reads the app could not reach.
 *
 * All three answer with a figure *and* the reason that figure may be partial,
 * and in all three the partiality flag is the load-bearing field:
 *
 *   - `obligations.totals.known_amount` is null whenever more than one
 *     currency is present, because there is no FX rate to sum across and the
 *     server will not invent one. `currency` is "" in that case. A client that
 *     read null as 0 would report a debt-free member.
 *   - `cash_flow.buckets[].amount` is null for the same reason, per bucket.
 *     `basis.inflows` states that nothing has been netted against earnings —
 *     PulseSoc has no income ledger — and a screen labelled "cash flow" that
 *     omits it is describing outflows while implying a balance.
 *   - `integrity.healthy` is false when a check could not run, not only when a
 *     fault was found, and is null when the read was refused. Deriving it from
 *     `findings.length` would report a clean bill of health over a scan that
 *     never happened. It is read, never computed.
 */

/** Provenance as the fact store stamps it; wider than the edge variant. */
export type CapitalEvidence = {
  factIds: number[];
  provenance: CapitalEdgeProvenance | null;
};

export type CapitalLiabilityRow = {
  nodeId: number;
  rootId: number;
  title: string;
  kind: string;
  /** null when the record store never stated one — never 0. */
  amount: number | null;
  currency: string;
  quantified: boolean;
  dueAt: string | null;
  projectedAt: string | null;
  freshness: CapitalFreshness | null;
  evidence: CapitalEvidence;
};

export type CapitalFreshness = {
  stale: boolean;
  ageDays: number | null;
  horizonDays: number | null;
};

export type CapitalObligationTotals = {
  /**
   * Populated only when a single currency answers for every quantified row.
   * null means "not summable", which is not the same as zero owed.
   */
  knownAmount: number | null;
  currency: string;
  byCurrency: Record<string, CapitalCurrencyBucket>;
  currencies: string[];
  count: number;
  quantified: number;
  unquantified: number;
  unspecifiedCurrency: number;
  complete: boolean;
  truncated: boolean;
  limit: number;
};

export type CapitalProjectionSync = {
  projected: boolean;
  obligations: number;
  retired: number;
  skipped: number;
};

export type CapitalObligations = {
  liabilities: CapitalLiabilityRow[];
  totals: CapitalObligationTotals;
  sync: CapitalProjectionSync;
};

export type CapitalCashFlowRow = {
  nodeId: number;
  rootId: number;
  title: string;
  kind: string;
  amount: number | null;
  currency: string;
  dueAt: string | null;
  daysUntil: number | null;
  overdue: boolean;
  bucket: string;
  evidence: CapitalEvidence;
};

/** One time bucket. `amount` is null when there is no single currency to sum. */
export type CapitalCashFlowBucket = {
  amount: number | null;
  count: number;
};

export type CapitalCashFlowTotals = {
  currency: string;
  /** null, not 0, when nothing could be summed. */
  scheduledAmount: number | null;
  scheduledCount: number;
  obligationsSeen: number;
  truncated: boolean;
  complete: boolean;
  excludedCount: number;
  /**
   * A published invariant that must always be 0. The server exposes it rather
   * than asserting it privately so that a break in the single-currency
   * contract is visible here too.
   */
  mixedCurrencyRows: number;
};

export type CapitalCashFlowExcluded = {
  undated: number;
  unquantified: number;
  undatedAndUnquantified: number;
};

export type CapitalCashFlowBucketDefinition = {
  name: string;
  fromDays: number | null;
  toDays: number | null;
};

export type CapitalCashFlowBasis = {
  /** States that these are outflows only. Render it; do not paraphrase it. */
  inflows: string;
  recurrence: string;
  buckets: CapitalCashFlowBucketDefinition[];
};

export type CapitalCashFlow = {
  generatedAt: string;
  schedule: CapitalCashFlowRow[];
  buckets: Record<string, CapitalCashFlowBucket>;
  totals: CapitalCashFlowTotals;
  excluded: CapitalCashFlowExcluded;
  basis: CapitalCashFlowBasis;
  sync: CapitalProjectionSync;
};

export type CapitalIntegrityFinding = {
  check: string;
  subject: string;
  subjectId: string;
  detail: string;
  /** "invariant" is a structural violation; "drift" is a divergence. */
  severity: string;
};

export type CapitalIntegrityTotals = {
  findings: number;
  invariantViolations: number;
  checksRun: number;
  checksTotal: number;
  /** Named checks that could not run. Non-empty forces `healthy` false. */
  inconclusive: string[];
  truncated: boolean;
  complete: boolean;
};

export type CapitalIntegrityBasis = {
  /** The endpoint repairs nothing, and says so. */
  repair: string;
  scope: string;
  checks: string[];
};

export type CapitalIntegrity = {
  /**
   * true only when every check ran and every check was clean.
   *
   * null when the answer was withheld. Never derive this from `findings` —
   * zero findings over a check that never ran is unexamined, not healthy.
   */
  healthy: boolean | null;
  findings: CapitalIntegrityFinding[];
  /** Per-check state: "clean" | "findings" | "inconclusive". */
  checks: Record<string, string>;
  examined: { edges: number; nodes: number };
  totals: CapitalIntegrityTotals;
  basis: CapitalIntegrityBasis;
};

export type CapitalObligationsResult =
  | { state: "READY"; obligations: CapitalObligations }
  | { state: "DENIED"; reason: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

export type CapitalCashFlowResult =
  | { state: "READY"; cashFlow: CapitalCashFlow }
  | { state: "DENIED"; reason: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

export type CapitalIntegrityResult =
  | { state: "READY"; integrity: CapitalIntegrity }
  | { state: "DENIED"; reason: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

function parseFreshness(raw: unknown): CapitalFreshness | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  return {
    stale: row.stale === true,
    ageDays: asMaybeNumber(row.age_days),
    horizonDays: asMaybeNumber(row.horizon_days)
  };
}

function parseEvidence(raw: unknown): CapitalEvidence {
  const row = asRecord(raw);
  const provenance = row.provenance;
  return {
    factIds: (Array.isArray(row.fact_ids) ? row.fact_ids : []).map(asId),
    // Absent provenance stays null: "we did not record where this came from"
    // must not render as an empty-but-present source.
    provenance:
      provenance && typeof provenance === "object" && !Array.isArray(provenance)
        ? parseProvenance(provenance)
        : null
  };
}

function parseLiabilityRow(raw: unknown): CapitalLiabilityRow {
  const row = asRecord(raw);
  return {
    nodeId: asId(row.node_id),
    rootId: asId(row.root_id),
    title: asText(row.title),
    kind: asText(row.kind),
    amount: asMaybeNumber(row.amount),
    currency: asText(row.currency),
    // Read from the wire. Deriving it from `amount !== null` would let a
    // parser bug and a genuine absence look identical.
    quantified: row.quantified === true,
    dueAt: asMaybeText(row.due_at),
    projectedAt: asMaybeText(row.projected_at),
    freshness: parseFreshness(row.freshness),
    evidence: parseEvidence(row.evidence)
  };
}

function asMaybeText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function parseObligationTotals(raw: unknown): CapitalObligationTotals {
  const row = asRecord(raw);
  return {
    knownAmount: asMaybeNumber(row.known_amount),
    currency: asText(row.currency),
    byCurrency: parseCurrencyBuckets(row.by_currency),
    currencies: (Array.isArray(row.currencies) ? row.currencies : []).map(asText),
    count: asId(row.count),
    quantified: asId(row.quantified),
    unquantified: asId(row.unquantified),
    unspecifiedCurrency: asId(row.unspecified_currency),
    complete: row.complete === true,
    truncated: row.truncated === true,
    limit: asId(row.limit)
  };
}

function parseProjectionSync(raw: unknown): CapitalProjectionSync {
  const row = asRecord(raw);
  return {
    projected: row.projected === true,
    obligations: asId(row.obligations),
    retired: asId(row.retired),
    skipped: asId(row.skipped)
  };
}

export function parseCapitalObligations(raw: unknown): CapitalObligations {
  const row = asRecord(raw);
  return {
    liabilities: (Array.isArray(row.liabilities) ? row.liabilities : []).map(parseLiabilityRow),
    totals: parseObligationTotals(row.totals),
    sync: parseProjectionSync(row.sync)
  };
}

function parseCashFlowRow(raw: unknown): CapitalCashFlowRow {
  const row = asRecord(raw);
  return {
    nodeId: asId(row.node_id),
    rootId: asId(row.root_id),
    title: asText(row.title),
    kind: asText(row.kind),
    amount: asMaybeNumber(row.amount),
    currency: asText(row.currency),
    dueAt: asMaybeText(row.due_at),
    // null rather than 0: "due today" and "we do not know when" are not the
    // same row on a timeline.
    daysUntil: asMaybeNumber(row.days_until),
    overdue: row.overdue === true,
    bucket: asText(row.bucket),
    evidence: parseEvidence(row.evidence)
  };
}

function parseCashFlowBuckets(raw: unknown): Record<string, CapitalCashFlowBucket> {
  const rows = asRecord(raw);
  const buckets: Record<string, CapitalCashFlowBucket> = {};
  for (const name of Object.keys(rows)) {
    const bucket = asRecord(rows[name]);
    buckets[name] = { amount: asMaybeNumber(bucket.amount), count: asId(bucket.count) };
  }
  return buckets;
}

function parseCashFlowTotals(raw: unknown): CapitalCashFlowTotals {
  const row = asRecord(raw);
  return {
    currency: asText(row.currency),
    scheduledAmount: asMaybeNumber(row.scheduled_amount),
    scheduledCount: asId(row.scheduled_count),
    obligationsSeen: asId(row.obligations_seen),
    truncated: row.truncated === true,
    complete: row.complete === true,
    excludedCount: asId(row.excluded_count),
    mixedCurrencyRows: asId(row.mixed_currency_rows)
  };
}

function parseCashFlowBasis(raw: unknown): CapitalCashFlowBasis {
  const row = asRecord(raw);
  return {
    inflows: asText(row.inflows),
    recurrence: asText(row.recurrence),
    buckets: (Array.isArray(row.buckets) ? row.buckets : []).map((entry) => {
      const bucket = asRecord(entry);
      return {
        name: asText(bucket.name),
        fromDays: asMaybeNumber(bucket.from_days),
        toDays: asMaybeNumber(bucket.to_days)
      };
    })
  };
}

export function parseCapitalCashFlow(raw: unknown): CapitalCashFlow {
  const row = asRecord(raw);
  const excluded = asRecord(row.excluded);
  return {
    generatedAt: asText(row.generated_at),
    schedule: (Array.isArray(row.schedule) ? row.schedule : []).map(parseCashFlowRow),
    buckets: parseCashFlowBuckets(row.buckets),
    totals: parseCashFlowTotals(row.totals),
    excluded: {
      undated: asId(excluded.undated),
      unquantified: asId(excluded.unquantified),
      undatedAndUnquantified: asId(excluded.undated_and_unquantified)
    },
    basis: parseCashFlowBasis(row.basis),
    sync: parseProjectionSync(row.sync)
  };
}

function parseIntegrityFinding(raw: unknown): CapitalIntegrityFinding {
  const row = asRecord(raw);
  return {
    check: asText(row.check),
    subject: asText(row.subject),
    subjectId: asText(row.subject_id),
    severity: asText(row.severity),
    detail: asText(row.detail)
  };
}

export function parseCapitalIntegrity(raw: unknown): CapitalIntegrity {
  const row = asRecord(raw);
  const totals = asRecord(row.totals);
  const basis = asRecord(row.basis);
  const examined = asRecord(row.examined);
  const checks: Record<string, string> = {};
  const rawChecks = asRecord(row.checks);
  for (const name of Object.keys(rawChecks)) checks[name] = asText(rawChecks[name]);
  return {
    // Strictly tri-state. Anything that is not a literal boolean is "the
    // server did not say", which must not collapse into "not healthy" or,
    // far worse, into "healthy".
    healthy: typeof row.healthy === "boolean" ? row.healthy : null,
    findings: (Array.isArray(row.findings) ? row.findings : []).map(parseIntegrityFinding),
    checks,
    examined: { edges: asId(examined.edges), nodes: asId(examined.nodes) },
    totals: {
      findings: asId(totals.findings),
      invariantViolations: asId(totals.invariant_violations),
      checksRun: asId(totals.checks_run),
      checksTotal: asId(totals.checks_total),
      inconclusive: (Array.isArray(totals.inconclusive) ? totals.inconclusive : []).map(asText),
      truncated: totals.truncated === true,
      complete: totals.complete === true
    },
    basis: {
      repair: asText(basis.repair),
      scope: asText(basis.scope),
      checks: (Array.isArray(basis.checks) ? basis.checks : []).map(asText)
    }
  };
}

export const CAPITAL_OBLIGATIONS_PATH = "/api/private-office/capital-graph/obligations";
export const CAPITAL_CASH_FLOW_PATH = "/api/private-office/capital-graph/cash-flow";
export const CAPITAL_INTEGRITY_PATH = "/api/private-office/capital-graph/integrity";

/** Projected liabilities, summed only where a single currency allows it. */
export async function getCapitalObligations(): Promise<CapitalObligationsResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(CAPITAL_OBLIGATIONS_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", obligations: parseCapitalObligations(body.obligations) };
  } catch (error) {
    return capitalRefusal(error);
  }
}

/** When recorded obligations fall due. Outflows only — see `basis.inflows`. */
export async function getCapitalCashFlow(): Promise<CapitalCashFlowResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(CAPITAL_CASH_FLOW_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", cashFlow: parseCapitalCashFlow(body.cash_flow) };
  } catch (error) {
    return capitalRefusal(error);
  }
}

/** Read-only structural diagnostics. Repairs nothing; see `basis.repair`. */
export async function getCapitalIntegrity(): Promise<CapitalIntegrityResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(CAPITAL_INTEGRITY_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", integrity: parseCapitalIntegrity(body.integrity) };
  } catch (error) {
    return capitalRefusal(error);
  }
}
