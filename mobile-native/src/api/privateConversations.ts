/**
 * Private Conversations — the client for the Private Office *view* of a
 * canonical Messenger thread.
 *
 * What this file is not: a messaging client. There is exactly one message
 * ledger (`comm_v2_messages`) and one attachment authority
 * (`message_attachments`), and this module reaches both through the same
 * canonical service every other Messenger surface uses. It therefore reuses
 * `MessengerMessage` and `normalizeMessages` from `messenger.ts` rather than
 * defining a parallel message shape — a second message type is how two screens
 * start disagreeing about what a message is.
 *
 * What this file adds is the Office *classification* that rides alongside a
 * conversation: its scope, its sensitivity, its cross-domain links, and the
 * capability truth a screen must read before it renders any claim about
 * security.
 *
 * On encryption. There is no cryptographic end-to-end encryption on this path.
 * The server says so in every payload (`end_to_end_encrypted: false`) and this
 * parser preserves that answer *by defaulting to false when the field is
 * absent*. Reading a missing field as "true" — or letting a screen assume it —
 * is precisely the failure Stage 53 forbids, so the default is the safe one and
 * a test asserts it.
 */

import { PulseApiError, pulseApi } from "./pulseApi";
import { officeRequestHeaders } from "../privateOffice/officeLock";
import { privateFeatureRefusal, type PrivateFeatureRefusal } from "./privateFeatures";
import {
  normalizeConversations,
  normalizeMessages,
  type MessengerConversation,
  type MessengerMessage,
  type RawConversation
} from "./messenger";

export const PRIVATE_CONVERSATIONS_PATH = "/api/private-office/conversations";

/* --- vocabulary ---------------------------------------------------------- */

/**
 * The four Office scopes. These are a classification *on* a canonical
 * conversation, not four new conversation types: the server maps them onto the
 * existing `direct` / `group` / `room` vocabulary, so ordinary Messenger keeps
 * understanding these threads.
 */
export const PRIVATE_CONVERSATION_SCOPES = [
  "DIRECT",
  "GROUP",
  "ORGANIZATION_ROOM",
  "PROJECT_ROOM"
] as const;

export type PrivateConversationScope = (typeof PRIVATE_CONVERSATION_SCOPES)[number];

export const PRIVATE_CONVERSATION_LINK_TYPES = [
  "DOCUMENT",
  "RECORD",
  "FACT",
  "MEETING",
  "ORGANIZATION_NODE",
  "PROJECT"
] as const;

/**
 * The sensitivity ladder, mirroring `services/private_office/model.py`.
 *
 * Duplicated as a literal union because TypeScript needs one to type the
 * picker, and the server does not publish the list in `capabilities`. The
 * duplication is bounded by how it is used: this list drives *which levels can
 * be chosen*, never *how a stored level is displayed*. A value the server
 * invents later still renders on screen (the info screen falls back to the raw
 * string); only the picker lags, which is a cosmetic gap rather than a thread
 * that silently appears unclassified.
 */
export const PRIVATE_CONVERSATION_SENSITIVITIES = [
  "PUBLIC",
  "INTERNAL",
  "CONFIDENTIAL",
  "HIGHLY_SENSITIVE",
  "RESTRICTED"
] as const;

export type PrivateConversationSensitivity =
  (typeof PRIVATE_CONVERSATION_SENSITIVITIES)[number];

export type PrivateConversationLinkType =
  (typeof PRIVATE_CONVERSATION_LINK_TYPES)[number];

/* --- shared parsing helpers ---------------------------------------------- */

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
 * Read a boolean that is only true when the server actually said so.
 *
 * Used for every security-relevant flag. `Boolean(undefined)` is already false,
 * but writing the intent out means a later refactor cannot quietly turn an
 * absent field into a permissive default.
 */
function asStrictTrue(value: unknown): boolean {
  return value === true;
}

function asScope(value: unknown): PrivateConversationScope | "" {
  const word = asText(value).trim().toUpperCase();
  return (PRIVATE_CONVERSATION_SCOPES as readonly string[]).includes(word)
    ? (word as PrivateConversationScope)
    : "";
}

function asLinkType(value: unknown): PrivateConversationLinkType | "" {
  const word = asText(value).trim().toUpperCase();
  return (PRIVATE_CONVERSATION_LINK_TYPES as readonly string[]).includes(word)
    ? (word as PrivateConversationLinkType)
    : "";
}

/* --- classification ------------------------------------------------------ */

export type PrivateConversationClassification = {
  officeScope: PrivateConversationScope | "";
  sensitivity: string;
  organizationNodeId: number;
  operationsProjectId: number;
  meetingId: number;
  archived: boolean;
  createdAt: string;
  /** True only when the server classified this thread as an Office thread. */
  isPrivateOffice: boolean;
  /**
   * Always false today. Kept as data rather than a constant so that if real
   * cryptographic E2EE is ever built, the screens read the server's answer
   * instead of a hardcoded one — and until then they read `false`.
   */
  endToEndEncrypted: boolean;
};

export function parsePrivateConversationClassification(
  raw: unknown
): PrivateConversationClassification {
  const row = asRecordObject(raw);
  return {
    officeScope: asScope(row.office_scope),
    sensitivity: asText(row.sensitivity),
    organizationNodeId: asCount(row.organization_node_id),
    operationsProjectId: asCount(row.operations_project_id),
    meetingId: asCount(row.meeting_id),
    archived: asStrictTrue(row.archived),
    createdAt: asText(row.created_at),
    isPrivateOffice: asStrictTrue(row.is_private_office),
    endToEndEncrypted: asStrictTrue(row.end_to_end_encrypted)
  };
}

/* --- links --------------------------------------------------------------- */

export type PrivateConversationLink = {
  linkType: PrivateConversationLinkType | "";
  targetId: string;
  label: string;
  createdAt: string;
};

export function parsePrivateConversationLink(raw: unknown): PrivateConversationLink {
  const row = asRecordObject(raw);
  return {
    linkType: asLinkType(row.link_type),
    targetId: asText(row.target_id) || String(asCount(row.target_id) || ""),
    label: asText(row.label) || asText(row.title),
    createdAt: asText(row.created_at)
  };
}

/* --- capability truth ---------------------------------------------------- */

export type PrivateConversationCapabilities = {
  featureId: string;
  enabled: boolean;
  messageLedger: string;
  attachmentAuthority: string;
  rtcProvider: string;
  scopes: string[];
  linkTypes: string[];
  /** False. See the module docstring — this is asserted, not assumed. */
  endToEndEncrypted: boolean;
  encryptionNote: string;
  disappearingMessages: boolean;
};

export function parsePrivateConversationCapabilities(
  raw: unknown
): PrivateConversationCapabilities {
  const row = asRecordObject(raw);
  return {
    featureId: asText(row.feature_id),
    enabled: asStrictTrue(row.enabled),
    messageLedger: asText(row.message_ledger),
    attachmentAuthority: asText(row.attachment_authority),
    rtcProvider: asText(row.rtc_provider),
    scopes: asList(row.scopes).map(asText).filter(Boolean),
    linkTypes: asList(row.link_types).map(asText).filter(Boolean),
    endToEndEncrypted: asStrictTrue(row.end_to_end_encrypted),
    encryptionNote: asText(row.encryption_note),
    disappearingMessages: asStrictTrue(row.disappearing_messages)
  };
}

/* --- a thread in the list ------------------------------------------------ */

export type PrivateConversationSummary = {
  /** The canonical conversation. Not an Office-side copy or alias. */
  conversation: MessengerConversation;
  classification: PrivateConversationClassification;
  links: PrivateConversationLink[];
};

function parseSummary(raw: unknown): PrivateConversationSummary {
  const row = asRecordObject(raw);
  // The row IS the canonical conversation, with two Office keys added. Passing
  // it through the canonical normalizer keeps one definition of what a
  // conversation looks like on this client.
  const [conversation] = normalizeConversationRow(row);
  return {
    conversation,
    classification: parsePrivateConversationClassification(row.private_office),
    links: asList(row.links).map(parsePrivateConversationLink)
  };
}

/**
 * Normalize exactly one raw conversation through the canonical path.
 *
 * `normalizeConversations` is the shared implementation; calling it with a
 * single-item array is deliberate, because writing a one-row variant here
 * would be a second normalizer.
 */
function normalizeConversationRow(row: Record<string, unknown>): MessengerConversation[] {
  const normalized = normalizeConversations([row as RawConversation]);
  return normalized.length ? normalized : ([row] as unknown as MessengerConversation[]);
}

/* --- results ------------------------------------------------------------- */

export type PrivateConversationListResult =
  | {
      state: "READY";
      conversations: PrivateConversationSummary[];
      count: number;
      capabilities: PrivateConversationCapabilities;
    }
  | PrivateFeatureRefusal;

export async function listPrivateConversations(
  scope?: PrivateConversationScope
): Promise<PrivateConversationListResult> {
  try {
    const query = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_CONVERSATIONS_PATH}${query}`, {
        headers: await officeRequestHeaders()
      })
    );
    const conversations = asList(body.conversations).map(parseSummary);
    return {
      state: "READY",
      conversations,
      // Trust the parsed length over the server's count for what the screen
      // renders: a count that disagrees with the list is how a UI claims
      // "3 threads" above an empty list.
      count: conversations.length,
      capabilities: parsePrivateConversationCapabilities(body.capabilities)
    };
  } catch (error) {
    return privateFeatureRefusal(error);
  }
}

export type PrivateConversationCapabilitiesResult =
  | { state: "READY"; capabilities: PrivateConversationCapabilities }
  | PrivateFeatureRefusal;

export async function getPrivateConversationCapabilities(): Promise<PrivateConversationCapabilitiesResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(`${PRIVATE_CONVERSATIONS_PATH}/capabilities`, {
        headers: await officeRequestHeaders()
      })
    );
    return {
      state: "READY",
      capabilities: parsePrivateConversationCapabilities(body.capabilities)
    };
  } catch (error) {
    return privateFeatureRefusal(error);
  }
}

export type PrivateConversationDetail = {
  conversation: MessengerConversation;
  classification: PrivateConversationClassification;
  links: PrivateConversationLink[];
  capabilities: PrivateConversationCapabilities;
};

export type PrivateConversationDetailResult =
  | ({ state: "READY" } & PrivateConversationDetail)
  | { state: "NOT_FOUND" }
  | PrivateFeatureRefusal;

export async function getPrivateConversation(
  ref: string | number
): Promise<PrivateConversationDetailResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(
        `${PRIVATE_CONVERSATIONS_PATH}/${encodeURIComponent(String(ref))}`,
        { headers: await officeRequestHeaders() }
      )
    );
    const [conversation] = normalizeConversationRow(asRecordObject(body.conversation));
    return {
      state: "READY",
      conversation,
      classification: parsePrivateConversationClassification(body.private_office),
      links: asList(body.links).map(parsePrivateConversationLink),
      capabilities: parsePrivateConversationCapabilities(body.capabilities)
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) {
      const details = asRecordObject(error.details);
      const word = asText(details.state).trim().toUpperCase();
      // A gate 404 is a refusal, not a missing thread. Only an ungated 404
      // means this conversation is not an Office conversation.
      if (word !== "NOT_IMPLEMENTED" && word !== "FEATURE_DISABLED") {
        return { state: "NOT_FOUND" };
      }
    }
    return privateFeatureRefusal(error);
  }
}

export type PrivateConversationCreateInput = {
  officeScope: PrivateConversationScope;
  title?: string;
  participantIds?: number[];
  sensitivity?: string;
  organizationNodeId?: number;
  operationsProjectId?: number;
};

export type PrivateConversationCreateResult =
  | {
      state: "CREATED";
      conversationId: number;
      conversation: MessengerConversation;
      classification: PrivateConversationClassification;
    }
  | { state: "REJECTED"; code: string; message: string }
  | PrivateFeatureRefusal;

export async function createPrivateConversation(
  input: PrivateConversationCreateInput
): Promise<PrivateConversationCreateResult> {
  try {
    const payload: Record<string, unknown> = { office_scope: input.officeScope };
    if (input.title) payload.title = input.title;
    if (input.participantIds?.length) payload.participant_ids = input.participantIds;
    if (input.sensitivity) payload.sensitivity = input.sensitivity;
    if (input.organizationNodeId) payload.organization_node_id = input.organizationNodeId;
    if (input.operationsProjectId) payload.operations_project_id = input.operationsProjectId;

    const body = asRecordObject(
      await pulseApi<unknown>(PRIVATE_CONVERSATIONS_PATH, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await officeRequestHeaders()) },
        body: JSON.stringify(payload)
      })
    );
    const [conversation] = normalizeConversationRow(asRecordObject(body.conversation));
    return {
      state: "CREATED",
      conversationId: asCount(body.conversation_id),
      conversation,
      classification: parsePrivateConversationClassification(body.private_office)
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status >= 400 && error.status < 500) {
      const details = asRecordObject(error.details);
      const code = asText(details.code);
      // A named refusal from the classification layer (bad scope, missing
      // binding) is a product answer the screen must show verbatim, not a
      // generic failure.
      if (code && error.status !== 423) {
        return { state: "REJECTED", code, message: error.message || "" };
      }
    }
    return privateFeatureRefusal(error);
  }
}

/* --- delegated message operations ---------------------------------------- */

export type PrivateConversationMessagesResult =
  | { state: "READY"; messages: MessengerMessage[] }
  | PrivateFeatureRefusal;

/**
 * Read the thread. The server delegates straight to the canonical messaging
 * service and passes its envelope through unchanged, so the rows arriving here
 * are ordinary Messenger messages and are normalized by the ordinary
 * normalizer.
 */
export async function getPrivateConversationMessages(
  ref: string | number,
  params: { limit?: number; beforeId?: number } = {}
): Promise<PrivateConversationMessagesResult> {
  try {
    const search = new URLSearchParams();
    if (params.limit) search.set("limit", String(params.limit));
    if (params.beforeId) search.set("before_id", String(params.beforeId));
    const query = search.toString() ? `?${search.toString()}` : "";

    const body = asRecordObject(
      await pulseApi<unknown>(
        `${PRIVATE_CONVERSATIONS_PATH}/${encodeURIComponent(String(ref))}/messages${query}`,
        { headers: await officeRequestHeaders() }
      )
    );
    const rows = asList(body.messages) as MessengerMessage[];
    const conversationId = asCount(body.conversation_id);
    return { state: "READY", messages: normalizeMessages(rows, conversationId) };
  } catch (error) {
    return privateFeatureRefusal(error);
  }
}

export type PrivateConversationSendResult =
  | { state: "SENT"; message: MessengerMessage | null }
  | { state: "REJECTED"; code: string; message: string }
  | PrivateFeatureRefusal;

export async function sendPrivateConversationMessage(
  ref: string | number,
  payload: { body: string; clientMessageId?: string; attachmentIds?: number[] }
): Promise<PrivateConversationSendResult> {
  try {
    const wire: Record<string, unknown> = { body: payload.body };
    // The canonical ledger de-duplicates on this id. Sending it means a retry
    // after a dropped response returns the original message instead of a
    // duplicate.
    if (payload.clientMessageId) wire.client_message_id = payload.clientMessageId;
    if (payload.attachmentIds?.length) wire.attachment_ids = payload.attachmentIds;

    const body = asRecordObject(
      await pulseApi<unknown>(
        `${PRIVATE_CONVERSATIONS_PATH}/${encodeURIComponent(String(ref))}/messages`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...(await officeRequestHeaders()) },
          body: JSON.stringify(wire)
        }
      )
    );
    const raw = asRecordObject(body.message);
    const [message] = normalizeMessages(
      [raw as MessengerMessage],
      asCount(body.conversation_id)
    );
    return { state: "SENT", message: message ?? null };
  } catch (error) {
    if (error instanceof PulseApiError && error.status >= 400 && error.status < 500) {
      const details = asRecordObject(error.details);
      const code = asText(details.code);
      if (code && error.status !== 423) {
        return { state: "REJECTED", code, message: error.message || "" };
      }
    }
    return privateFeatureRefusal(error);
  }
}

/** Fire-and-forget signals. A failure here must never block the thread. */
export async function markPrivateConversationRead(ref: string | number): Promise<boolean> {
  try {
    await pulseApi<unknown>(
      `${PRIVATE_CONVERSATIONS_PATH}/${encodeURIComponent(String(ref))}/read`,
      { method: "POST", headers: await officeRequestHeaders() }
    );
    return true;
  } catch {
    return false;
  }
}

export async function setPrivateConversationTyping(
  ref: string | number,
  typing: boolean
): Promise<boolean> {
  try {
    await pulseApi<unknown>(
      `${PRIVATE_CONVERSATIONS_PATH}/${encodeURIComponent(String(ref))}/typing`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await officeRequestHeaders()) },
        body: JSON.stringify({ typing })
      }
    );
    return true;
  } catch {
    return false;
  }
}

/* --- classification writes ----------------------------------------------- */

export type PrivateConversationSensitivityResult =
  | { state: "SAVED"; classification: PrivateConversationClassification }
  | { state: "REJECTED"; code: string; message: string }
  | PrivateFeatureRefusal;

export async function setPrivateConversationSensitivity(
  ref: string | number,
  sensitivity: string
): Promise<PrivateConversationSensitivityResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(
        `${PRIVATE_CONVERSATIONS_PATH}/${encodeURIComponent(String(ref))}/sensitivity`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...(await officeRequestHeaders()) },
          body: JSON.stringify({ sensitivity })
        }
      )
    );
    return {
      state: "SAVED",
      classification: parsePrivateConversationClassification(body.private_office)
    };
  } catch (error) {
    if (error instanceof PulseApiError && error.status >= 400 && error.status < 500) {
      const details = asRecordObject(error.details);
      const code = asText(details.code);
      if (code && error.status !== 423) {
        return { state: "REJECTED", code, message: error.message || "" };
      }
    }
    return privateFeatureRefusal(error);
  }
}

export type PrivateConversationLinkResult =
  | { state: "SAVED"; links: PrivateConversationLink[] }
  | { state: "REJECTED"; code: string; message: string }
  | PrivateFeatureRefusal;

async function writeLink(
  ref: string | number,
  method: "POST" | "DELETE",
  linkType: PrivateConversationLinkType,
  targetId: string | number
): Promise<PrivateConversationLinkResult> {
  try {
    const body = asRecordObject(
      await pulseApi<unknown>(
        `${PRIVATE_CONVERSATIONS_PATH}/${encodeURIComponent(String(ref))}/links`,
        {
          method,
          headers: { "Content-Type": "application/json", ...(await officeRequestHeaders()) },
          body: JSON.stringify({ link_type: linkType, target_id: targetId })
        }
      )
    );
    return { state: "SAVED", links: asList(body.links).map(parsePrivateConversationLink) };
  } catch (error) {
    if (error instanceof PulseApiError && error.status >= 400 && error.status < 500) {
      const details = asRecordObject(error.details);
      const code = asText(details.code);
      if (code && error.status !== 423) {
        return { state: "REJECTED", code, message: error.message || "" };
      }
    }
    return privateFeatureRefusal(error);
  }
}

/**
 * Link this conversation to a canonical object elsewhere in the Office.
 *
 * A link is a *reference*, never a copy: linking a document does not move
 * bytes, and linking a fact does not assert it. The owning domain stays the
 * only writer of the thing being linked.
 */
export function linkPrivateConversation(
  ref: string | number,
  linkType: PrivateConversationLinkType,
  targetId: string | number
): Promise<PrivateConversationLinkResult> {
  return writeLink(ref, "POST", linkType, targetId);
}

export function unlinkPrivateConversation(
  ref: string | number,
  linkType: PrivateConversationLinkType,
  targetId: string | number
): Promise<PrivateConversationLinkResult> {
  return writeLink(ref, "DELETE", linkType, targetId);
}
