/**
 * Private Office — the native client for the canonical server surface.
 *
 * ## Why this file parses instead of casting
 *
 * Every state this module returns was decided on the server, in
 * `services/private_office/office.py` and `feature_matrix.py`. The client's job
 * is to carry those words across the wire without adding to them. So the
 * parsers below narrow unknown JSON into closed unions and drop anything they
 * do not recognise, rather than defaulting an unfamiliar word to something
 * plausible. A future server state this build has never heard of must surface
 * as "we do not know", not as "unavailable" — the second is a claim.
 *
 * ## Why `reason` matters more than `availability`
 *
 * The canonical availability vocabulary has four words, and it collapses two
 * genuinely different promises into one of them: a capability nobody has built
 * and a capability that cannot answer at all until an outside provider is
 * connected both arrive as NOT_IMPLEMENTED. The distinction survives in
 * `implementation`, and the server pre-renders it into `reason`. The screens
 * branch on `reason`. Re-deriving it here from `implementation` would make this
 * file a second authority on a question the server already answered.
 *
 * ## Why a degraded read is not an error
 *
 * `/api/private-office/overview` answers 200 with `ok: false` when the tier
 * resolver could not produce a trustworthy answer, and `pulseApi` turns that
 * into a throw. We catch it and return `ENTRY_UNKNOWN` rather than letting it
 * reach a screen as a red error. "We could not confirm your access" and "you do
 * not have access" are different sentences, and the person most likely to see
 * the first is the person who paid.
 */

import { PulseApiError, pulseApi } from "./pulseApi";

/* --- vocabulary --------------------------------------------------------- */

/** Mirrors `services/private_office/feature_matrix.py` availability states. */
export type PrivateFeatureAvailability =
  | "ENTITLED"
  | "NOT_ENTITLED"
  | "FEATURE_DISABLED"
  | "NOT_IMPLEMENTED"
  | "UNKNOWN";

/** Mirrors `office._child_state`'s `reason`. The word the UI actually renders. */
export type PrivateFeatureReason =
  | "AVAILABLE"
  | "PROVIDER_REQUIRED"
  | "NOT_IMPLEMENTED"
  | "TEMPORARILY_DISABLED"
  | "UPGRADE_REQUIRED"
  | "UNKNOWN";

/** Mirrors `office.product_state`'s `state`. */
export type PrivateOfficeEntryState =
  | "ENTRY_AVAILABLE"
  | "ENTRY_UPGRADE_REQUIRED"
  | "ENTRY_UNAVAILABLE"
  | "ENTRY_UNKNOWN";

const AVAILABILITY_WORDS: readonly PrivateFeatureAvailability[] = [
  "ENTITLED",
  "NOT_ENTITLED",
  "FEATURE_DISABLED",
  "NOT_IMPLEMENTED"
];

const REASON_WORDS: readonly PrivateFeatureReason[] = [
  "AVAILABLE",
  "PROVIDER_REQUIRED",
  "NOT_IMPLEMENTED",
  "TEMPORARILY_DISABLED",
  "UPGRADE_REQUIRED"
];

const ENTRY_STATES: readonly PrivateOfficeEntryState[] = [
  "ENTRY_AVAILABLE",
  "ENTRY_UPGRADE_REQUIRED",
  "ENTRY_UNAVAILABLE",
  "ENTRY_UNKNOWN"
];

/* --- shapes ------------------------------------------------------------- */

export type PrivateOfficeChild = {
  featureId: string;
  availability: PrivateFeatureAvailability;
  implementation: string;
  minimumTier: string;
  reason: PrivateFeatureReason;
  /** True only for ENTITLED. The single question a tile may ask. */
  opens: boolean;
};

export type PrivateOfficeProductState = {
  featureId: string;
  state: PrivateOfficeEntryState;
  effectiveTier: string;
  available: PrivateOfficeChild[];
  unavailable: PrivateOfficeChild[];
  /** The cheapest tier that unlocks something built. Null when nothing would. */
  upgradeTier: string | null;
};

export type PrivateDomainCount = {
  domain: string;
  count: number;
  /** Carried from the server rather than inferred from `count === 0`. */
  empty: boolean;
};

export type PrivateOfficeOverview = {
  /** False when the tier resolver was degraded. Branch on this first. */
  resolved: boolean;
  office: PrivateOfficeProductState;
  domains: PrivateDomainCount[];
  verifiedAt: string;
};

export type PrivateFactProvenance = {
  sourceType: string;
  sourceId: string;
  hasSourceDocument: boolean;
  provenanceType: string;
  verification: string;
  observedAt: string;
  confidence: number;
};

export type PrivateFact = {
  id: number;
  factType: string;
  value: string;
  valueType: string;
  domain: string;
  sensitivity: string;
  observedAt: string;
  lifecycleState: string;
  provenance: PrivateFactProvenance;
  freshness: { stale: boolean; ageDays: number | null; horizonDays: number | null };
};

/**
 * Why the facts read has its own result union instead of throwing.
 *
 * The four refusals the server distinguishes — unavailable, not implemented,
 * feature disabled, not entitled — are the whole point of the surface. Turned
 * into one thrown Error they become one error banner, which is exactly the
 * collapse the feature matrix exists to prevent. So the caller gets a tagged
 * result and has to name which case it is rendering.
 */
export type PrivateFactsResult =
  | { state: "READY"; facts: PrivateFact[]; domain: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "ERROR"; message: string };

export const UNKNOWN_OFFICE: PrivateOfficeProductState = {
  featureId: "private_office",
  state: "ENTRY_UNKNOWN",
  effectiveTier: "",
  available: [],
  unavailable: [],
  upgradeTier: null
};

export const UNKNOWN_OVERVIEW: PrivateOfficeOverview = {
  resolved: false,
  office: UNKNOWN_OFFICE,
  domains: [],
  verifiedAt: ""
};

/* --- parsing ------------------------------------------------------------ */

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asOneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  const word = asText(value).trim().toUpperCase();
  return (allowed as readonly string[]).includes(word) ? (word as T) : fallback;
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseChild(raw: unknown): PrivateOfficeChild {
  const row = asRecord(raw);
  return {
    featureId: asText(row.feature_id),
    availability: asOneOf(row.availability, AVAILABILITY_WORDS, "UNKNOWN"),
    implementation: asText(row.implementation),
    minimumTier: asText(row.minimum_tier),
    reason: asOneOf(row.reason, REASON_WORDS, "UNKNOWN"),
    // `opens` is read, never derived. A client that computed it from
    // `availability` would be one refactor away from making an unbuilt row
    // tappable.
    opens: row.opens === true
  };
}

function parseChildren(raw: unknown): PrivateOfficeChild[] {
  return Array.isArray(raw) ? raw.map(parseChild).filter((child) => child.featureId) : [];
}

export function parseProductState(raw: unknown): PrivateOfficeProductState {
  const row = asRecord(raw);
  if (!asText(row.state)) return UNKNOWN_OFFICE;
  const upgrade = asText(row.upgrade_tier).trim();
  return {
    featureId: asText(row.feature_id) || "private_office",
    state: asOneOf(row.state, ENTRY_STATES, "ENTRY_UNKNOWN"),
    effectiveTier: asText(row.effective_tier),
    available: parseChildren(row.available),
    unavailable: parseChildren(row.unavailable),
    upgradeTier: upgrade || null
  };
}

function parseDomains(raw: unknown): PrivateDomainCount[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => {
      const row = asRecord(entry);
      const domain = asText(row.domain);
      const count = asFiniteNumber(row.count) ?? 0;
      return { domain, count, empty: row.empty === true };
    })
    .filter((row) => row.domain);
}

export function parseOverview(payload: unknown): PrivateOfficeOverview {
  const body = asRecord(payload);
  // `ok` is the resolver's confidence, not the transport's. An overview with
  // ok:false carries a real product block (ENTRY_UNKNOWN) and must still be
  // parsed, so the screen can say "we could not confirm" with structure behind
  // it rather than rendering nothing.
  const office = parseProductState(body.private_office);
  const resolved = body.ok === true && office.state !== "ENTRY_UNKNOWN";
  return {
    resolved,
    office: resolved ? office : { ...office, state: office.state },
    domains: resolved ? parseDomains(body.domains) : [],
    verifiedAt: asText(body.verified_at)
  };
}

function parseProvenance(raw: unknown): PrivateFactProvenance {
  const row = asRecord(raw);
  return {
    sourceType: asText(row.source_type),
    sourceId: asText(row.source_id),
    hasSourceDocument: row.has_source_document === true,
    provenanceType: asText(row.provenance_type),
    verification: asText(row.verification),
    observedAt: asText(row.observed_at),
    confidence: asFiniteNumber(row.confidence) ?? 0
  };
}

export function parseFact(raw: unknown): PrivateFact {
  const row = asRecord(raw);
  const freshness = asRecord(row.freshness);
  return {
    id: asFiniteNumber(row.id) ?? 0,
    factType: asText(row.fact_type),
    value: asText(row.value),
    valueType: asText(row.value_type),
    domain: asText(row.domain),
    sensitivity: asText(row.sensitivity),
    observedAt: asText(row.observed_at),
    lifecycleState: asText(row.lifecycle_state),
    provenance: parseProvenance(row.provenance),
    freshness: {
      stale: freshness.stale === true,
      ageDays: asFiniteNumber(freshness.age_days),
      horizonDays: asFiniteNumber(freshness.horizon_days)
    }
  };
}

/* --- reads -------------------------------------------------------------- */

export const PRIVATE_OFFICE_OVERVIEW_PATH = "/api/private-office/overview";
export const PRIVATE_OFFICE_FACTS_PATH = "/api/private-office/facts";

/**
 * The Private Office entry state and this member's per-domain counts.
 *
 * Never throws. Anything it cannot trust becomes ENTRY_UNKNOWN, which the
 * screen renders as "temporarily unavailable" rather than as a denial.
 */
export async function getPrivateOfficeOverview(): Promise<PrivateOfficeOverview> {
  try {
    return parseOverview(await pulseApi<unknown>(PRIVATE_OFFICE_OVERVIEW_PATH));
  } catch (error) {
    if (error instanceof PulseApiError && error.details) {
      // A 503 from this endpoint still carries the product block; the counts
      // are what failed, not the entitlement answer.
      const parsed = parseOverview(error.details);
      if (parsed.office.state !== "ENTRY_UNKNOWN") return { ...parsed, resolved: false };
    }
    return UNKNOWN_OVERVIEW;
  }
}

/** One domain's facts, or the specific reason the server refused. */
export async function getPrivateFacts(domain?: string): Promise<PrivateFactsResult> {
  const query = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  try {
    const body = asRecord(await pulseApi<unknown>(`${PRIVATE_OFFICE_FACTS_PATH}${query}`));
    const rows = Array.isArray(body.facts) ? body.facts : [];
    return { state: "READY", facts: rows.map(parseFact), domain: asText(body.domain) };
  } catch (error) {
    if (!(error instanceof PulseApiError)) {
      return { state: "ERROR", message: "" };
    }
    const details = asRecord(error.details);
    const serverState = asText(details.state).trim().toUpperCase();

    if (serverState === "NOT_ENTITLED") {
      return { state: "NOT_ENTITLED", minimumTier: asText(details.minimum_tier) };
    }
    if (serverState === "FEATURE_DISABLED") return { state: "FEATURE_DISABLED" };
    if (serverState === "NOT_IMPLEMENTED") return { state: "NOT_IMPLEMENTED" };
    // 503 covers both the degraded resolver and an unreadable store. Both mean
    // "we could not look", which must never be drawn as an empty store.
    if (serverState === "UNAVAILABLE" || error.status === 503 || error.status === 504) {
      return { state: "UNAVAILABLE" };
    }
    return { state: "ERROR", message: error.message || "" };
  }
}
