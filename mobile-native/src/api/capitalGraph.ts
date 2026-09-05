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
