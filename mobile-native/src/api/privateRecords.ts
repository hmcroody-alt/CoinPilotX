/**
 * Private Office Operations — the six record views, over the canonical routes.
 *
 * Same contract as `privateOffice.ts`, and for the same reasons: every state
 * this module returns was decided by the server in
 * `services/private_office_routes.py`, and the refusals are the product. A
 * tagged result per call, never a thrown Error that collapses "we could not
 * look" into "there is nothing here".
 *
 * The write paths mirror the server's narrowness. Creation sends only the
 * fields the route's allowlist admits — the owner comes from the session and
 * the source is pinned to USER on the server, so this client does not even
 * have a place to put either. Status moves send a status (and, for decisions,
 * an outcome) and nothing else, because `records.update_record` accepts
 * nothing else: the substance of a record cannot be rewritten from a phone.
 */

import { PulseApiError, pulseApi } from "./pulseApi";
import { officeRequestHeaders } from "../privateOffice/officeLock";

/* --- vocabulary --------------------------------------------------------- */

/** Mirrors `services/private_office/retrieval.py` RECORD_VIEWS. */
export const RECORD_VIEWS = [
  "obligations",
  "events",
  "decisions",
  "requests",
  "risks",
  "opportunities"
] as const;

export type PrivateRecordView = (typeof RECORD_VIEWS)[number];

export function asRecordView(value: unknown): PrivateRecordView | null {
  const word = typeof value === "string" ? value.trim().toLowerCase() : "";
  return (RECORD_VIEWS as readonly string[]).includes(word)
    ? (word as PrivateRecordView)
    : null;
}

/* --- shapes ------------------------------------------------------------- */

export type PrivateRecord = {
  id: number;
  recordType: string;
  title: string;
  status: string;
  /** What is true right now (OVERDUE, DUE_SOON) vs. what was last decided. */
  effectiveStatus: string;
  domain: string;
  sensitivity: string;
  sourceType: string;
  createdAt: string;
  updatedAt: string;
  /** The view's long field: summary, description, context — server-named. */
  body: string;
  /** Decisions only: the question the record answers. */
  question: string;
  /** Decisions only: the recorded outcome, once decided. */
  outcome: string;
  dueAt: string;
  occurredAt: string;
  amount: string;
};

export type PrivateRecordsResult =
  | {
      state: "READY";
      view: PrivateRecordView;
      records: PrivateRecord[];
      openCount: number;
      /** The server's status vocabulary for this view, in its order. */
      statuses: string[];
    }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

export type PrivateRecordWriteResult =
  | { state: "OK"; record: PrivateRecord | null }
  /** The writer's own validation, verbatim — it is written for a person. */
  | { state: "REJECTED"; message: string }
  | { state: "NOT_FOUND" }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

export type PrivateAttention = {
  state: "READY" | "UNAVAILABLE" | "LOCKED" | "REFUSED";
  /** Open records per view. Empty when state is not READY. */
  counts: Partial<Record<PrivateRecordView, number>>;
  /** The obligations due soonest, inside the server's stated horizon. */
  dueSoon: PrivateRecord[];
};

/* --- parsing ------------------------------------------------------------ */

function asRecordObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function parsePrivateRecord(raw: unknown): PrivateRecord {
  const row = asRecordObject(raw);
  return {
    id: asCount(row.id),
    recordType: asText(row.record_type),
    title: asText(row.title),
    status: asText(row.status),
    effectiveStatus: asText(row.effective_status) || asText(row.status),
    domain: asText(row.domain),
    sensitivity: asText(row.sensitivity),
    sourceType: asText(row.source_type),
    createdAt: asText(row.created_at),
    updatedAt: asText(row.updated_at),
    // One long field per view, server-named: `description` for requests,
    // `summary` for everything else.
    body: asText(row.summary) || asText(row.description),
    question: asText(row.question),
    outcome: asText(row.outcome),
    dueAt: asText(row.due_at),
    occurredAt: asText(row.occurred_at),
    amount: asText(row.amount)
  };
}

function refusal(
  error: unknown
):
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string } {
  if (!(error instanceof PulseApiError)) return { state: "ERROR", message: "" };
  const details = asRecordObject(error.details);
  const serverState = asText(details.state).trim().toUpperCase();
  if (serverState === "PRIVATE_OFFICE_LOCKED" || error.status === 423) {
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

/* --- reads -------------------------------------------------------------- */

export const PRIVATE_RECORDS_PATH = "/api/private-office/records";
export const PRIVATE_ATTENTION_PATH = "/api/private-office/attention";

/** One view's records, or the specific reason the server refused. */
export async function getPrivateRecords(
  view: PrivateRecordView
): Promise<PrivateRecordsResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_RECORDS_PATH}/${view}`, {
        headers: await officeRequestHeaders()
      })
    );
    const rows = Array.isArray(body.records) ? body.records : [];
    const statuses = Array.isArray(body.statuses)
      ? body.statuses.map(asText).filter(Boolean)
      : [];
    return {
      state: "READY",
      view,
      records: rows.map(parsePrivateRecord),
      openCount: asCount(body.open_count),
      statuses
    };
  } catch (error) {
    return refusal(error);
  }
}

/**
 * Open counts per view and the obligations due soonest. Never throws; the
 * caller renders REFUSED/UNAVAILABLE as "we could not look", never as zeros —
 * confident zeros over real obligations are the failure this shape prevents.
 */
export async function getPrivateAttention(): Promise<PrivateAttention> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_ATTENTION_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    const rawCounts = asRecordObject(body.counts);
    const counts: Partial<Record<PrivateRecordView, number>> = {};
    RECORD_VIEWS.forEach((view) => {
      if (view in rawCounts) counts[view] = asCount(rawCounts[view]);
    });
    const dueSoon = Array.isArray(body.due_soon) ? body.due_soon : [];
    return { state: "READY", counts, dueSoon: dueSoon.map(parsePrivateRecord) };
  } catch (error) {
    const why = refusal(error);
    return {
      state:
        why.state === "LOCKED"
          ? "LOCKED"
          : why.state === "UNAVAILABLE" || why.state === "ERROR"
            ? "UNAVAILABLE"
            : "REFUSED",
      counts: {},
      dueSoon: []
    };
  }
}

/* --- writes ------------------------------------------------------------- */

/**
 * The fields a member may send when recording something. A subset of the
 * route's own allowlist — everything else (owner, source, provenance,
 * relevance) is decided server-side and deliberately absent here.
 */
export type PrivateRecordDraft = {
  title?: string;
  summary?: string;
  description?: string;
  question?: string;
  obligation_type?: string;
  event_type?: string;
  occurred_at?: string;
  category?: string;
  risk_type?: string;
  opportunity_type?: string;
  due_at?: string;
};

/** Record one obligation, event, decision, request, risk or opportunity. */
export async function createPrivateRecord(
  view: PrivateRecordView,
  draft: PrivateRecordDraft
): Promise<PrivateRecordWriteResult> {
  const body: Record<string, string> = {};
  Object.entries(draft).forEach(([key, value]) => {
    const text = asText(value).trim();
    if (text) body[key] = text;
  });
  try {
    const answer = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_RECORDS_PATH}/${view}`, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: JSON.stringify(body)
      })
    );
    return {
      state: "OK",
      record: answer.record ? parsePrivateRecord(answer.record) : null
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) };
    }
    return refusal(error);
  }
}

/** Move one record's status; decisions may carry their outcome with it. */
export async function setPrivateRecordStatus(
  view: PrivateRecordView,
  recordId: number,
  status: string,
  outcome?: string
): Promise<PrivateRecordWriteResult> {
  const body: Record<string, string> = { status };
  const settled = asText(outcome).trim();
  if (settled) body.outcome = settled;
  try {
    const answer = asRecordObject(
      await pulseApi<unknown>(
        `${PRIVATE_RECORDS_PATH}/${view}/${recordId}/status`,
        {
          method: "POST",
          headers: await officeRequestHeaders(),
          body: JSON.stringify(body)
        }
      )
    );
    return {
      state: "OK",
      record: answer.record ? parsePrivateRecord(answer.record) : null
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      // "Not yours" and "never existed" arrive identically, on purpose.
      return { state: "NOT_FOUND" };
    }
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) };
    }
    return refusal(error);
  }
}
