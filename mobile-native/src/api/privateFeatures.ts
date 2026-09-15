/**
 * Relationship Intelligence as a typed client vocabulary.
 *
 * This file carried five features — documents, people, briefings, shield and
 * the concierge desk. Four were withdrawn from the product and their routes no
 * longer exist, so their clients are gone from here rather than left behind as
 * callable functions aimed at 404s. A client kept "just in case" is worse than
 * a deleted one: it compiles, it is typed, it looks supported, and the only
 * way to find out it is dead is to ship a screen that calls it.
 *
 * Same contract as `privateOffice.ts`: every state below was decided on the
 * server, and this file carries the words across the wire without adding to
 * them. Refusals arrive as a tagged union rather than a thrown error because
 * the four refusals the server distinguishes are the product, not a failure
 * mode; a screen has to name which one it is rendering.
 *
 * **A refusal and an empty result never render the same.** Every `READY` here
 * means the server sent the payload and it said the member has nothing; a
 * payload we could not read is a refusal instead. See `sentList`.
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

/**
 * The list the server actually sent, or `null` if it did not send one.
 *
 * `asList` answers `[]` for a key that is missing, null, or the wrong type.
 * That is the right reading for an optional decoration and the wrong reading
 * for the array a screen renders as "you have nothing yet", because those two
 * sentences are different claims:
 *
 *   - "the server told us you have no documents" — true, and the member's own
 *     fact about their own vault;
 *   - "we could not find a document list in what came back" — a failure on our
 *     side, rendered to the member as a statement about their belongings.
 *
 * `pulseApi` already throws on a non-2xx, on `ok: false`, and on a body it
 * could not parse as JSON, so most of this class never reaches here. What is
 * left is the narrow, real case: a well-formed 200 whose payload is not the
 * one this function was written against — a route that changed shape, a proxy
 * or cache answering with a different document, a partial serialization. In
 * every one of those the honest answer is a refusal the member can retry, not
 * a confident empty state.
 *
 * The Office already draws exactly this line for scalars: `privateRecords`
 * omits an attention count rather than reporting zero for one it did not
 * receive, because "confident zeros over real obligations" is the failure that
 * shape exists to prevent. A confident empty list is the same failure.
 */
function sentList(body: Record<string, unknown>, key: string): unknown[] | null {
  return Array.isArray(body[key]) ? (body[key] as unknown[]) : null;
}

/**
 * What a 200 that did not carry its payload is worth.
 *
 * `ERROR` rather than `UNAVAILABLE`: the server was reachable and answered.
 * We could not read the answer. Both render a retry, but only one of them is
 * true, and the state word is what a bug report will be written from.
 */
const UNREADABLE: PrivateFeatureRefusal = { state: "ERROR", message: "" };

/**
 * Translate a thrown API error into the tagged refusal the server intended.
 *
 * Exported so the Private Conversations client shares this one translation
 * rather than copying it. Two copies drift the moment the server adds a state,
 * and the screen reading the stale copy renders a generic "something went
 * wrong" for a refusal the product has a precise word for.
 */
export function privateFeatureRefusal(error: unknown): PrivateFeatureRefusal {
  return refusal(error);
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
    const people = sentList(body, "people");
    if (people === null) return UNREADABLE;
    return { state: "READY", people: people.map(parsePerson) };
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
    // No `person` block parses to node 0 with a blank name — a profile screen
    // for somebody who is not in the directory.
    if (asCount(person.node_id) <= 0) return UNREADABLE;
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
