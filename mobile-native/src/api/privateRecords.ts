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

/* --- overview ----------------------------------------------------------- */

/**
 * One row of the ranked attention queue. `primaryReason` is read from the
 * server rather than recomputed here: a screen that derives its own reason can
 * display one thing while the order it was given reflects another.
 */
export type AttentionItem = {
  id: number;
  recordType: string;
  title: string;
  status: string;
  effectiveStatus: string;
  /** The type's own deadline field, already resolved server-side. */
  dueAt: string;
  deadlineField: string;
  priority: string;
  severity: string;
  domain: string;
  updatedAt: string;
  primaryReason: string;
  reasons: string[];
};

/**
 * A number the server could count, or the marker it sends when the current
 * model cannot answer the question at all. Kept in the type so a screen cannot
 * render an unanswerable figure as `0` without the compiler objecting — which
 * is the entire point, because a fabricated zero is indistinguishable from a
 * real one to the person reading it.
 */
export type OverviewCount = number | "UNSUPPORTED";

/** One thing that happened, from the audit trail. */
export type OverviewActivity = {
  action: string;
  recordType: string;
  recordId: string;
  /** Whether the member did it themselves, rather than a provider. */
  byOwner: boolean;
  outcome: string;
  at: string;
};

export type PrivateOverview = {
  asOf: string;
  needsAttention: number;
  dueToday: number;
  dueThisWeek: number;
  overdue: number;
  pendingDecisions: number;
  openRequests: number;
  awaitingResponse: number;
  activeRisks: number;
  activeHighRisks: number;
  activeOpportunities: number;
  recentlyCompleted: number;
  expiringOpportunities: OverviewCount;
  counts: Partial<Record<PrivateRecordView, number>>;
  attention: AttentionItem[];
  /** The true size of the queue, which may exceed `attention.length`. */
  attentionTotal: number;
  attentionTruncated: boolean;
  recentActivity: OverviewActivity[];
  recentWindowDays: number;
  /** Reason name -> why the model cannot report it. */
  unsupported: Record<string, string>;
};

export type PrivateOverviewResult =
  | { state: "READY"; overview: PrivateOverview }
  /** A successful read of a ledger with nothing outstanding. */
  | { state: "EMPTY"; overview: PrivateOverview }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "NOT_FOUND" }
  | { state: "ERROR"; message: string };

export const PRIVATE_OVERVIEW_PATH = "/api/private-office/operations/overview";

function asOverviewCount(value: unknown): OverviewCount {
  if (typeof value === "string" && value.trim().toUpperCase() === "UNSUPPORTED") {
    return "UNSUPPORTED";
  }
  return asCount(value);
}

export function parseAttentionItem(raw: unknown): AttentionItem {
  const row = asRecordObject(raw);
  const reasons = Array.isArray(row.reasons)
    ? row.reasons.map(asText).filter(Boolean)
    : [];
  return {
    id: asCount(row.id),
    recordType: asText(row.record_type),
    title: asText(row.title),
    status: asText(row.status),
    effectiveStatus: asText(row.effective_status) || asText(row.status),
    dueAt: asText(row.due_at),
    deadlineField: asText(row.deadline_field),
    priority: asText(row.priority),
    severity: asText(row.severity),
    domain: asText(row.domain),
    updatedAt: asText(row.updated_at),
    // Falls back to the strongest reason present rather than to a placeholder:
    // an item on the queue with no reason at all would be the one thing this
    // model promises cannot happen.
    primaryReason: asText(row.primary_reason) || reasons[0] || "",
    reasons
  };
}

function parseActivity(raw: unknown): OverviewActivity {
  const row = asRecordObject(raw);
  return {
    action: asText(row.action),
    recordType: asText(row.record_type),
    recordId: asText(row.record_id),
    byOwner: row.by_owner === true,
    outcome: asText(row.outcome),
    at: asText(row.at)
  };
}

export function parseOverview(raw: unknown): PrivateOverview {
  const body = asRecordObject(raw);
  const queue = asRecordObject(body.attention);
  const items = Array.isArray(queue.items) ? queue.items : [];
  const rawCounts = asRecordObject(body.counts);
  const counts: Partial<Record<PrivateRecordView, number>> = {};
  RECORD_VIEWS.forEach((view) => {
    if (view in rawCounts) counts[view] = asCount(rawCounts[view]);
  });
  const rawUnsupported = asRecordObject(body.unsupported);
  const unsupported: Record<string, string> = {};
  Object.keys(rawUnsupported).forEach((key) => {
    unsupported[key] = asText(rawUnsupported[key]);
  });
  const activity = Array.isArray(body.recent_activity) ? body.recent_activity : [];
  return {
    asOf: asText(body.as_of),
    needsAttention: asCount(body.needs_attention),
    dueToday: asCount(body.due_today),
    dueThisWeek: asCount(body.due_this_week),
    overdue: asCount(body.overdue),
    pendingDecisions: asCount(body.pending_decisions),
    openRequests: asCount(body.open_requests),
    awaitingResponse: asCount(body.awaiting_response),
    activeRisks: asCount(body.active_risks),
    activeHighRisks: asCount(body.active_high_risks),
    activeOpportunities: asCount(body.active_opportunities),
    recentlyCompleted: asCount(body.recently_completed),
    expiringOpportunities: asOverviewCount(body.expiring_opportunities),
    counts,
    attention: items.map(parseAttentionItem),
    // The server's own total, not `items.length`. The list is a page; treating
    // its length as the count would agree with the header right up until the
    // member has more than a page of problems.
    attentionTotal: asCount(queue.total),
    attentionTruncated: queue.truncated === true,
    recentActivity: activity.map(parseActivity),
    recentWindowDays: asCount(body.recent_window_days),
    unsupported
  };
}

/**
 * The executive summary across all six primitives, or the specific reason the
 * server refused. Never throws, and never degrades a refusal into a page of
 * zeros: "nothing needs you" and "we could not look" are opposite claims and
 * the caller has to be able to tell them apart.
 */
export async function getPrivateOverview(): Promise<PrivateOverviewResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_OVERVIEW_PATH, {
        headers: await officeRequestHeaders()
      })
    );
    const overview = parseOverview(body.overview);
    // EMPTY is claimed only on a successful read. It is a statement about the
    // member's ledger, which means it can only be made by something that
    // managed to look at it.
    const anything =
      overview.needsAttention > 0 ||
      overview.recentlyCompleted > 0 ||
      RECORD_VIEWS.some((view) => (overview.counts[view] ?? 0) > 0);
    return { state: anything ? "READY" : "EMPTY", overview };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      // The route is absent on this deployment — an optional route pack that
      // failed to register looks exactly like this, and reporting it as a
      // generic error would send someone hunting for a data problem.
      return { state: "NOT_FOUND" };
    }
    return refusal(error);
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
  outcome?: string,
  reopen?: boolean
): Promise<PrivateRecordWriteResult> {
  const body: Record<string, string | boolean> = { status };
  const settled = asText(outcome).trim();
  if (settled) body.outcome = settled;
  // Undoing a closure is a separate act of intent, so it travels as its own
  // flag and only when it is literally `true`. The server applies the same
  // test; sending it always — even as `false` — would put a reopen field on
  // every ordinary status move and invite a stale form value into it.
  if (reopen === true) body.reopen = true;
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
