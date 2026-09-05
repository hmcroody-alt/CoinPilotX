/**
 * The five shipped Private Office features — documents, people, briefings,
 * shield and the concierge desk — as one typed client vocabulary.
 *
 * Same contract as `privateOffice.ts`: every state below was decided on the
 * server, and this file carries the words across the wire without adding to
 * them. Refusals arrive as a tagged union rather than a thrown error because
 * the four refusals the server distinguishes are the product, not a failure
 * mode; a screen has to name which one it is rendering.
 *
 * Two truth blocks ride on their payloads and must never be dropped in
 * parsing: the shield posture's `external` block (what no outside provider
 * has checked — absence of findings is not external safety) and the concierge
 * `desk` block (UNSTAFFED when nobody is on the roster — the client must
 * never imply a human who does not exist).
 */

import { PulseApiError, pulseApi } from "./pulseApi";
import { officeRequestHeaders } from "../privateOffice/officeLock";

/* --- shared vocabulary --------------------------------------------------- */

export type PrivateFeatureRefusal =
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

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

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function refusal(error: unknown): PrivateFeatureRefusal {
  if (!(error instanceof PulseApiError)) return { state: "ERROR", message: "" };
  const details = asRecordObject(error.details);
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

/* ========================================================================= */
/* Document Intelligence                                                     */
/* ========================================================================= */

export const PRIVATE_DOCUMENTS_PATH = "/api/private-office/documents";

export type PrivateDocument = {
  id: number;
  title: string;
  originalName: string;
  extension: string;
  mimeType: string;
  sizeBytes: number;
  /** EXTRACTED | NO_CLAIMS | PROVIDER_REQUIRED | FAILED — the server's word. */
  extractionState: string;
  extractionNote: string;
  domain: string;
  sensitivity: string;
  createdAt: string;
  updatedAt: string;
};

export type PrivateClaim = {
  id: number;
  documentId: number;
  factType: string;
  valueType: string;
  proposedValue: string;
  locator: string;
  domain: string;
  /** PROPOSED | ACCEPTED | REJECTED. */
  status: string;
  factId: number;
  createdAt: string;
  reviewedAt: string;
};

export function parsePrivateDocument(raw: unknown): PrivateDocument {
  const row = asRecordObject(raw);
  return {
    id: asCount(row.id),
    title: asText(row.title),
    originalName: asText(row.original_name),
    extension: asText(row.extension),
    mimeType: asText(row.mime_type),
    sizeBytes: asCount(row.size_bytes),
    extractionState: asText(row.extraction_state),
    extractionNote: asText(row.extraction_note),
    domain: asText(row.domain),
    sensitivity: asText(row.sensitivity),
    createdAt: asText(row.created_at),
    updatedAt: asText(row.updated_at)
  };
}

function parseClaim(raw: unknown): PrivateClaim {
  const row = asRecordObject(raw);
  return {
    id: asCount(row.id),
    documentId: asCount(row.document_id),
    factType: asText(row.fact_type),
    valueType: asText(row.value_type),
    proposedValue: asText(row.proposed_value),
    locator: asText(row.locator),
    domain: asText(row.domain),
    status: asText(row.status),
    factId: asCount(row.fact_id),
    createdAt: asText(row.created_at),
    reviewedAt: asText(row.reviewed_at)
  };
}

export type PrivateDocumentsResult =
  | { state: "READY"; documents: PrivateDocument[] }
  | PrivateFeatureRefusal;

export async function getPrivateDocuments(): Promise<PrivateDocumentsResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_DOCUMENTS_PATH, { headers: await officeRequestHeaders() })
    );
    return { state: "READY", documents: asList(body.documents).map(parsePrivateDocument) };
  } catch (error) {
    return refusal(error);
  }
}

export type PrivateDocumentDetailResult =
  | { state: "READY"; document: PrivateDocument; claims: PrivateClaim[] }
  | { state: "NOT_FOUND" }
  | PrivateFeatureRefusal;

export async function getPrivateDocument(id: number): Promise<PrivateDocumentDetailResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_DOCUMENTS_PATH}/${id}`, {
        headers: await officeRequestHeaders()
      })
    );
    return {
      state: "READY",
      document: parsePrivateDocument(body.document),
      claims: asList(body.claims).map(parseClaim)
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      // A feature-gate 404 (NOT_IMPLEMENTED / FEATURE_DISABLED) is not a
      // missing document; only an ungated 404 means the row does not exist.
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    return refusal(error);
  }
}

export type PrivateDocumentUpload = {
  uri: string;
  name: string;
  mimeType: string;
  title?: string;
};

export type PrivateDocumentUploadResult =
  | {
      state: "SAVED";
      document: PrivateDocument;
      duplicate: boolean;
      extraction: { state: string; note: string; claimsProposed: number };
    }
  | { state: "REJECTED"; message: string }
  | PrivateFeatureRefusal;

/** Upload one document. Multipart; extraction runs synchronously server-side. */
export async function uploadPrivateDocument(
  upload: PrivateDocumentUpload
): Promise<PrivateDocumentUploadResult> {
  const form = new FormData();
  form.append("file", {
    uri: upload.uri,
    name: upload.name,
    type: upload.mimeType || "application/octet-stream"
    // React Native's FormData file part is not in the DOM lib's types.
  } as unknown as Blob);
  if (upload.title) form.append("title", upload.title);
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_DOCUMENTS_PATH, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: form
      })
    );
    const extraction = asRecordObject(body.extraction);
    return {
      state: "SAVED",
      document: parsePrivateDocument(body.document),
      duplicate: body.duplicate === true,
      extraction: {
        state: asText(extraction.state),
        note: asText(extraction.note),
        claimsProposed: asCount(extraction.claims_proposed)
      }
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return refusal(error);
  }
}

export type PrivateClaimReviewResult =
  | { state: "OK" }
  | { state: "NOT_FOUND" }
  | { state: "REJECTED"; message: string }
  | PrivateFeatureRefusal;

/** Accept or reject one PROPOSED claim. Accepting records the fact server-side. */
export async function reviewPrivateClaim(
  claimId: number,
  decision: "accept" | "reject"
): Promise<PrivateClaimReviewResult> {
  try {
    await pulseApi<unknown>(`/api/private-office/claims/${claimId}/review`, {
      method: "POST",
      headers: await officeRequestHeaders(),
      body: JSON.stringify({ decision })
    });
    return { state: "OK" };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return refusal(error);
  }
}

/* ========================================================================= */
/* Relationship Intelligence                                                 */
/* ========================================================================= */

export const PRIVATE_PEOPLE_PATH = "/api/private-office/relationships";

export type PrivatePerson = {
  nodeId: number;
  name: string;
  role: string;
  domain: string;
  sensitivity: string;
  createdAt: string;
  openCommitments: number;
  connections: number;
};

export type PrivatePersonFact = {
  id: number;
  factType: string;
  value: string;
  valueType: string;
  provenanceType: string;
  observedAt: string;
};

export type PrivatePersonTimelineItem = {
  at: string;
  kind: string;
  label: string;
};

export type PrivatePersonProfile = {
  nodeId: number;
  name: string;
  role: string;
  domain: string;
  sensitivity: string;
  createdAt: string;
  facts: PrivatePersonFact[];
  commitments: { id: number; recordType: string; title: string; status: string; dueAt: string }[];
  timeline: PrivatePersonTimelineItem[];
};

function parsePerson(raw: unknown): PrivatePerson {
  const row = asRecordObject(raw);
  return {
    nodeId: asCount(row.node_id),
    name: asText(row.name),
    role: asText(row.role),
    domain: asText(row.domain),
    sensitivity: asText(row.sensitivity),
    createdAt: asText(row.created_at),
    openCommitments: asCount(row.open_commitments),
    connections: asCount(row.connections)
  };
}

export type PrivatePeopleResult =
  | { state: "READY"; people: PrivatePerson[] }
  | PrivateFeatureRefusal;

export async function getPrivatePeople(): Promise<PrivatePeopleResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_PEOPLE_PATH, { headers: await officeRequestHeaders() })
    );
    return { state: "READY", people: asList(body.people).map(parsePerson) };
  } catch (error) {
    return refusal(error);
  }
}

export type PrivatePersonProfileResult =
  | { state: "READY"; profile: PrivatePersonProfile }
  | { state: "NOT_FOUND" }
  | PrivateFeatureRefusal;

export async function getPrivatePersonProfile(
  nodeId: number
): Promise<PrivatePersonProfileResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_PEOPLE_PATH}/${nodeId}`, {
        headers: await officeRequestHeaders()
      })
    );
    const person = asRecordObject(body.person);
    return {
      state: "READY",
      profile: {
        nodeId: asCount(person.node_id),
        name: asText(person.name),
        role: asText(person.role),
        domain: asText(person.domain),
        sensitivity: asText(person.sensitivity),
        createdAt: asText(person.created_at),
        facts: asList(person.facts).map((fact) => {
          const row = asRecordObject(fact);
          return {
            id: asCount(row.id),
            factType: asText(row.fact_type),
            value:
              typeof row.typed_value === "string" ? row.typed_value : String(row.typed_value ?? ""),
            valueType: asText(row.value_type),
            provenanceType: asText(row.provenance_type),
            observedAt: asText(row.observed_at)
          };
        }),
        commitments: asList(person.commitments).map((entry) => {
          const row = asRecordObject(entry);
          return {
            id: asCount(row.id),
            recordType: asText(row.record_type),
            title: asText(row.title),
            status: asText(row.effective_status) || asText(row.status),
            dueAt: asText(row.due_at)
          };
        }),
        timeline: asList(person.timeline).map((entry) => {
          const row = asRecordObject(entry);
          return { at: asText(row.at), kind: asText(row.kind), label: asText(row.label) };
        })
      }
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    return refusal(error);
  }
}

export type PrivatePersonWriteResult =
  | { state: "SAVED"; person: PrivatePerson }
  | { state: "REJECTED"; message: string }
  | PrivateFeatureRefusal;

/** Add one person. The server never merges; a duplicate name is two people. */
export async function addPrivatePerson(draft: {
  name: string;
  role?: string;
}): Promise<PrivatePersonWriteResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_PEOPLE_PATH, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: JSON.stringify({ name: draft.name, ...(draft.role ? { role: draft.role } : {}) })
      })
    );
    return { state: "SAVED", person: parsePerson(body.person) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return refusal(error);
  }
}

/* ========================================================================= */
/* Private Briefings                                                         */
/* ========================================================================= */

export const PRIVATE_BRIEFINGS_PATH = "/api/private-office/briefings";

export type PrivateBriefingSummary = {
  id: number;
  title: string;
  generatedAt: string;
  itemCount: number;
};

export type PrivateBriefingItem = {
  id: number;
  section: string;
  label: string;
  detail: string;
};

export type PrivateBriefingDetail = PrivateBriefingSummary & {
  sections: { section: string; items: PrivateBriefingItem[] }[];
};

function parseBriefingSummary(raw: unknown): PrivateBriefingSummary {
  const row = asRecordObject(raw);
  return {
    id: asCount(row.id),
    title: asText(row.title),
    generatedAt: asText(row.generated_at),
    itemCount: asCount(row.item_count)
  };
}

function parseBriefingDetail(raw: unknown): PrivateBriefingDetail {
  const row = asRecordObject(raw);
  return {
    ...parseBriefingSummary(raw),
    sections: asList(row.sections).map((entry) => {
      const section = asRecordObject(entry);
      return {
        section: asText(section.section),
        items: asList(section.items).map((item) => {
          const itemRow = asRecordObject(item);
          return {
            id: asCount(itemRow.id),
            section: asText(itemRow.section),
            label: asText(itemRow.label),
            detail: asText(itemRow.detail)
          };
        })
      };
    })
  };
}

export type PrivateBriefingsResult =
  | { state: "READY"; briefings: PrivateBriefingSummary[] }
  | PrivateFeatureRefusal;

export async function getPrivateBriefings(): Promise<PrivateBriefingsResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_BRIEFINGS_PATH, { headers: await officeRequestHeaders() })
    );
    return { state: "READY", briefings: asList(body.briefings).map(parseBriefingSummary) };
  } catch (error) {
    return refusal(error);
  }
}

export type PrivateBriefingDetailResult =
  | { state: "READY"; briefing: PrivateBriefingDetail }
  | { state: "NOT_FOUND" }
  | PrivateFeatureRefusal;

export async function getPrivateBriefing(id: number): Promise<PrivateBriefingDetailResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_BRIEFINGS_PATH}/${id}`, {
        headers: await officeRequestHeaders()
      })
    );
    return { state: "READY", briefing: parseBriefingDetail(body.briefing) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    return refusal(error);
  }
}

export type PrivateBriefingGenerateResult =
  | { state: "GENERATED"; briefing: PrivateBriefingDetail }
  | PrivateFeatureRefusal;

/** Generate a briefing now, from the member's own records and nothing else. */
export async function generatePrivateBriefing(): Promise<PrivateBriefingGenerateResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_BRIEFINGS_PATH, {
        method: "POST",
        headers: await officeRequestHeaders()
      })
    );
    return { state: "GENERATED", briefing: parseBriefingDetail(body.briefing) };
  } catch (error) {
    return refusal(error);
  }
}

/* ========================================================================= */
/* Private Shield                                                            */
/* ========================================================================= */

export const PRIVATE_SHIELD_PATH = "/api/private-office/shield";

export type ShieldExternalCoverage = {
  /** False until a real provider is integrated. Never inferred client-side. */
  monitored: boolean;
  state: string;
  note: string;
};

export type ShieldPosture = {
  openFindings: number;
  bySeverity: Record<string, number>;
  /** What the internal scan looks at — named, so "clear" has a scope. */
  checks: string[];
  /**
   * What no external provider has checked. Rendered verbatim and never
   * summarized into reassurance: absence of findings is not external safety.
   */
  external: ShieldExternalCoverage[];
};

export type ShieldFinding = {
  id: number;
  kind: string;
  severity: string;
  title: string;
  detail: string;
  status: string;
  firstSeenAt: string;
  lastSeenAt: string;
  resolutionNote: string;
};

function parsePosture(raw: unknown): ShieldPosture {
  const row = asRecordObject(raw);
  const severities = asRecordObject(row.by_severity);
  const external = asRecordObject(row.external);
  return {
    openFindings: asCount(row.open_findings),
    bySeverity: Object.fromEntries(
      Object.entries(severities).map(([severity, count]) => [severity, asCount(count)])
    ),
    checks: asList(row.checks).map(asText).filter(Boolean),
    external: Object.values(external).map((entry) => {
      const coverage = asRecordObject(entry);
      return {
        monitored: coverage.monitored === true,
        state: asText(coverage.state),
        note: asText(coverage.note)
      };
    })
  };
}

function parseFinding(raw: unknown): ShieldFinding {
  const row = asRecordObject(raw);
  return {
    id: asCount(row.id),
    kind: asText(row.kind),
    severity: asText(row.severity),
    title: asText(row.title),
    detail: asText(row.detail),
    status: asText(row.status),
    firstSeenAt: asText(row.first_seen_at),
    lastSeenAt: asText(row.last_seen_at),
    resolutionNote: asText(row.resolution_note)
  };
}

export type ShieldHomeResult =
  | { state: "READY"; posture: ShieldPosture; findings: ShieldFinding[] }
  | PrivateFeatureRefusal;

/** Posture plus the open findings, in two reads behind one gate. */
export async function getShieldHome(): Promise<ShieldHomeResult> {
  try {
    const headers = await officeRequestHeaders();
    const [postureBody, findingsBody] = await Promise.all([
      pulseApi<unknown>(PRIVATE_SHIELD_PATH, { headers }),
      pulseApi<unknown>(`${PRIVATE_SHIELD_PATH}/findings`, { headers })
    ]);
    return {
      state: "READY",
      posture: parsePosture(asRecordObject(postureBody).posture),
      findings: asList(asRecordObject(findingsBody).findings).map(parseFinding)
    };
  } catch (error) {
    return refusal(error);
  }
}

export type ShieldScanResult = { state: "SCANNED" } | PrivateFeatureRefusal;

export async function runShieldScan(): Promise<ShieldScanResult> {
  try {
    await pulseApi<unknown>(`${PRIVATE_SHIELD_PATH}/scan`, {
      method: "POST",
      headers: await officeRequestHeaders()
    });
    return { state: "SCANNED" };
  } catch (error) {
    return refusal(error);
  }
}

export type ShieldFindingWriteResult =
  | { state: "OK"; finding: ShieldFinding }
  | { state: "NOT_FOUND" }
  | { state: "REJECTED"; message: string }
  | PrivateFeatureRefusal;

export async function setShieldFindingStatus(
  findingId: number,
  status: "ACKNOWLEDGED" | "RESOLVED" | "DISMISSED",
  note?: string
): Promise<ShieldFindingWriteResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_SHIELD_PATH}/findings/${findingId}`, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: JSON.stringify({ status, ...(note ? { note } : {}) })
      })
    );
    return { state: "OK", finding: parseFinding(body.finding) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return refusal(error);
  }
}

/* ========================================================================= */
/* Human Concierge                                                           */
/* ========================================================================= */

export const PRIVATE_CONCIERGE_PATH = "/api/private-office/concierge";

export type ConciergeDesk = {
  /**
   * Read, never derived, never softened. False means no human is on the
   * roster and the screen says so — the one claim this feature exists to
   * never fake.
   */
  staffed: boolean;
  operatorCount: number;
  note: string;
};

export type ConciergeRequest = {
  id: number;
  title: string;
  description: string;
  status: string;
  category: string;
  priority: string;
  deadlineAt: string;
  completedAt: string;
  createdAt: string;
  updatedAt: string;
};

export type ConciergeMessage = {
  id: number;
  /** MEMBER | OPERATOR — the server's word for who wrote it. */
  author: string;
  body: string;
  createdAt: string;
};

export const CONCIERGE_CATEGORIES = [
  "GENERAL",
  "TRAVEL",
  "LEGAL",
  "FINANCIAL",
  "PERSONAL",
  "RESEARCH",
  "ADMIN"
] as const;

export const CONCIERGE_PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT"] as const;

function parseDesk(raw: unknown): ConciergeDesk {
  const row = asRecordObject(raw);
  return {
    staffed: row.staffed === true,
    operatorCount: asCount(row.operator_count),
    note: asText(row.note)
  };
}

function parseConciergeRequest(raw: unknown): ConciergeRequest {
  const row = asRecordObject(raw);
  return {
    id: asCount(row.id),
    title: asText(row.title),
    description: asText(row.description),
    status: asText(row.status),
    category: asText(row.category),
    priority: asText(row.priority),
    deadlineAt: asText(row.deadline_at),
    completedAt: asText(row.completed_at),
    createdAt: asText(row.created_at),
    updatedAt: asText(row.updated_at)
  };
}

function parseMessage(raw: unknown): ConciergeMessage {
  const row = asRecordObject(raw);
  return {
    id: asCount(row.id),
    author: asText(row.author),
    body: asText(row.body),
    createdAt: asText(row.created_at)
  };
}

export type ConciergeHomeResult =
  | { state: "READY"; desk: ConciergeDesk; requests: ConciergeRequest[] }
  | PrivateFeatureRefusal;

export async function getConciergeHome(): Promise<ConciergeHomeResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_CONCIERGE_PATH, { headers: await officeRequestHeaders() })
    );
    return {
      state: "READY",
      desk: parseDesk(body.desk),
      requests: asList(body.requests).map(parseConciergeRequest)
    };
  } catch (error) {
    return refusal(error);
  }
}

export type ConciergeThreadResult =
  | { state: "READY"; request: ConciergeRequest; thread: ConciergeMessage[]; desk: ConciergeDesk }
  | { state: "NOT_FOUND" }
  | PrivateFeatureRefusal;

export async function getConciergeRequest(requestId: number): Promise<ConciergeThreadResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_CONCIERGE_PATH}/requests/${requestId}`, {
        headers: await officeRequestHeaders()
      })
    );
    return {
      state: "READY",
      request: parseConciergeRequest(body.request),
      thread: asList(body.thread).map(parseMessage),
      desk: parseDesk(body.desk)
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    return refusal(error);
  }
}

export type ConciergeRequestDraft = {
  title: string;
  description?: string;
  category?: string;
  priority?: string;
};

export type ConciergeSubmitResult =
  | { state: "SAVED"; request: ConciergeRequest; desk: ConciergeDesk }
  | { state: "REJECTED"; message: string }
  | PrivateFeatureRefusal;

export async function submitConciergeRequest(
  draft: ConciergeRequestDraft
): Promise<ConciergeSubmitResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_CONCIERGE_PATH}/requests`, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: JSON.stringify({
          title: draft.title,
          ...(draft.description ? { description: draft.description } : {}),
          ...(draft.category ? { category: draft.category } : {}),
          ...(draft.priority ? { priority: draft.priority } : {})
        })
      })
    );
    return {
      state: "SAVED",
      request: parseConciergeRequest(body.request),
      desk: parseDesk(body.desk)
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return refusal(error);
  }
}

export type ConciergeMessageResult =
  | { state: "SENT"; message: ConciergeMessage }
  | { state: "NOT_FOUND" }
  | { state: "REJECTED"; message: string }
  | PrivateFeatureRefusal;

export async function sendConciergeMessage(
  requestId: number,
  messageBody: string
): Promise<ConciergeMessageResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_CONCIERGE_PATH}/requests/${requestId}/messages`, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: JSON.stringify({ body: messageBody })
      })
    );
    return { state: "SENT", message: parseMessage(body.message_sent) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return refusal(error);
  }
}

export type ConciergeCancelResult =
  | { state: "OK"; request: ConciergeRequest }
  | { state: "NOT_FOUND" }
  | { state: "REJECTED"; message: string }
  | PrivateFeatureRefusal;

export async function cancelConciergeRequest(requestId: number): Promise<ConciergeCancelResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_CONCIERGE_PATH}/requests/${requestId}/cancel`, {
        method: "POST",
        headers: await officeRequestHeaders()
      })
    );
    return { state: "OK", request: parseConciergeRequest(body.request) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") return { state: "NOT_FOUND" };
    }
    if (error instanceof PulseApiError && error.status === 400) {
      const details = asRecordObject(error.details);
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return refusal(error);
  }
}
