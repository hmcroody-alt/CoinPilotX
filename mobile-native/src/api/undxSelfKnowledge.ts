import { pulseApi } from "./pulseApi";

/**
 * Server-authoritative UNDX self-knowledge.
 *
 * The native app must NOT hard-code who builds PulseSoc or which UNDX actions are
 * available — that is decided on the server (see
 * `services/undx_self_knowledge.py :: self_knowledge`) and delivered inside the
 * `GET /api/pulse-ai/conversation` bootstrap payload. This module gives native
 * surfaces a typed, read-only view of that payload plus pure selectors, so a
 * screen can render live capability status without inventing or caching its own
 * copy of the truth.
 *
 * Shapes here mirror the Python composer exactly. Everything is optional-tolerant:
 * an older backend that predates `self_knowledge` simply yields `null`, and
 * callers must treat "unknown" as "not available" rather than assuming a
 * capability works.
 */

export type UndxExecutionMode = "READ" | "EXECUTE";
export type UndxCapabilityStatus = "AVAILABLE";

export type UndxCapabilityView = {
  capability_id: string;
  description: string;
  domain: string;
  status: UndxCapabilityStatus;
  executionMode: UndxExecutionMode;
  requiresConfirmation: boolean;
  requiresVerification: boolean;
  receiptRequired: boolean;
};

export type UndxCapabilityCounts = {
  total: number;
  read_only: number;
  write: number;
  requires_confirmation: number;
  by_domain: Record<string, number>;
};

export type UndxCompanyFacts = {
  version: number;
  legal_name: string;
  primary_product: string;
  founder: { name: string; title: string };
  product_category: string[];
};

export type UndxSelfKnowledge = {
  assistant: { name: string; description: string };
  company: UndxCompanyFacts;
  canonical: { company_explanation: string; pulsesoc_definition: string };
  capabilities: {
    counts: UndxCapabilityCounts;
    available: UndxCapabilityView[];
  };
  honesty: { never_fabricates: string[]; capability_rule: string };
  version: { company_identity: number };
};

type ConversationBootstrap = {
  self_knowledge?: UndxSelfKnowledge | null;
};

/**
 * Fetch the UNDX conversation bootstrap and return only its self-knowledge block.
 * Returns `null` when the backend does not supply one — callers must degrade to a
 * "capabilities unknown" state, never to a fabricated capability list.
 */
export async function fetchUndxSelfKnowledge(): Promise<UndxSelfKnowledge | null> {
  const data = await pulseApi<ConversationBootstrap>("/api/pulse-ai/conversation");
  return data?.self_knowledge ?? null;
}

/** True only when the server lists this capability as genuinely executable. */
export function isCapabilityAvailable(
  knowledge: UndxSelfKnowledge | null,
  capabilityId: string
): boolean {
  if (!knowledge) return false;
  return knowledge.capabilities.available.some(
    (view) => view.capability_id === capabilityId && view.status === "AVAILABLE"
  );
}

/** Capability ids the server currently advertises as executable. */
export function availableCapabilityIds(knowledge: UndxSelfKnowledge | null): string[] {
  if (!knowledge) return [];
  return knowledge.capabilities.available.map((view) => view.capability_id);
}

/** Group the advertised capabilities by their dotted-id domain, sorted by id. */
export function capabilitiesByDomain(
  knowledge: UndxSelfKnowledge | null
): Record<string, UndxCapabilityView[]> {
  const grouped: Record<string, UndxCapabilityView[]> = {};
  if (!knowledge) return grouped;
  for (const view of knowledge.capabilities.available) {
    (grouped[view.domain] ??= []).push(view);
  }
  for (const domain of Object.keys(grouped)) {
    grouped[domain].sort((a, b) => a.capability_id.localeCompare(b.capability_id));
  }
  return grouped;
}

/** Capabilities that must be confirmed before UNDX executes them. */
export function capabilitiesRequiringConfirmation(
  knowledge: UndxSelfKnowledge | null
): UndxCapabilityView[] {
  if (!knowledge) return [];
  return knowledge.capabilities.available.filter((view) => view.requiresConfirmation);
}

/**
 * A capability the model might name but that the server does NOT list is, by the
 * registry's allowlist invariant, not executable yet. Native surfaces should use
 * this to keep UNDX honest instead of trusting prose.
 */
export function isCapabilityClaimHonest(
  knowledge: UndxSelfKnowledge | null,
  capabilityId: string
): boolean {
  return isCapabilityAvailable(knowledge, capabilityId);
}
