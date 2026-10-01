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
import { officeRequestHeaders, setOfficeUnlocked } from "../privateOffice/officeLock";

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
  /**
   * The second lock (Stage 15). True means the member is entitled but this
   * request carried no valid unlock grant — the overview arrives with entry
   * state only and no counts. `setupRequired` distinguishes "no passcode has
   * ever been created" (render the setup flow) from "locked" (render unlock).
   */
  locked: boolean;
  setupRequired: boolean;
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
  /** 423: entitled, but no valid unlock grant rode on this request. */
  | { state: "LOCKED"; setupRequired: boolean }
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
  verifiedAt: "",
  locked: false,
  setupRequired: false
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
    verifiedAt: asText(body.verified_at),
    // Read, never derived: the server says whether the second lock stood in
    // the way of this response. A client that inferred it from empty domains
    // would draw an empty office as a locked one.
    locked: body.locked === true,
    setupRequired: body.setup_required === true
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
    return parseOverview(
      await pulseApi<unknown>(PRIVATE_OFFICE_OVERVIEW_PATH, {
        headers: await officeRequestHeaders()
      })
    );
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
    const body = asRecord(
      await pulseApi<unknown>(`${PRIVATE_OFFICE_FACTS_PATH}${query}`, {
        headers: await officeRequestHeaders()
      })
    );
    const rows = Array.isArray(body.facts) ? body.facts : [];
    return { state: "READY", facts: rows.map(parseFact), domain: asText(body.domain) };
  } catch (error) {
    if (!(error instanceof PulseApiError)) {
      return { state: "ERROR", message: "" };
    }
    const details = asRecord(error.details);
    const serverState = asText(details.state).trim().toUpperCase();

    // The second lock (Stage 15-16). Checked before the entitlement words:
    // a 423 carries the one instruction that matters — unlock, or set up.
    if (serverState === "PRIVATE_OFFICE_LOCKED" || error.status === 423) {
      return { state: "LOCKED", setupRequired: details.setup_required === true };
    }
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

/* --- writes: the member records a fact ----------------------------------- */

/**
 * The two vocabularies a creation form has to offer before any data exists.
 * They mirror `services/private_office/model.py`; the server re-validates and
 * rejects anything it does not recognise, so a drift here fails loudly rather
 * than storing a mislabeled fact.
 */
export const FACT_DOMAINS = [
  "GENERAL",
  "FINANCIAL",
  "LEGAL",
  "HEALTH",
  "FAMILY",
  "IDENTITY",
  "SECURITY"
] as const;

export const FACT_VALUE_TYPES = [
  "STRING",
  "NUMBER",
  "MONEY",
  "PERCENT",
  "DATE",
  "BOOLEAN"
] as const;

export type PrivateFactDraft = {
  domain: string;
  factType: string;
  value: string;
  valueType: string;
  sensitivity?: string;
};

export type PrivateFactWriteResult =
  | { state: "SAVED"; status: string; factId: string }
  /** The writer's own validation, verbatim — it is written for a person. */
  | { state: "REJECTED"; message: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };

/**
 * Record one fact through `POST /api/private-office/facts`.
 *
 * The owner comes from the session and provenance is fixed server-side at
 * USER_ASSERTED — there is nothing this client could send to claim otherwise,
 * and no field here pretends there is.
 */
export async function createPrivateFact(draft: PrivateFactDraft): Promise<PrivateFactWriteResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(PRIVATE_OFFICE_FACTS_PATH, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: JSON.stringify({
          domain: draft.domain,
          fact_type: draft.factType,
          value: draft.value,
          value_type: draft.valueType,
          ...(draft.sensitivity ? { sensitivity: draft.sensitivity } : {})
        })
      })
    );
    return { state: "SAVED", status: asText(body.status), factId: asText(body.fact_id) };
  } catch (error) {
    if (!(error instanceof PulseApiError)) {
      return { state: "ERROR", message: "" };
    }
    const details = asRecord(error.details);
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
    if (error.status === 400) {
      return { state: "REJECTED", message: asText(details.message) || error.message || "" };
    }
    return { state: "ERROR", message: error.message || "" };
  }
}

/* --- second lock: security API ------------------------------------------- */

export const PRIVATE_OFFICE_SECURITY_PATH = "/api/private-office/security";

export type OfficeSecurityStatus = {
  /**
   * `UPGRADE_REQUIRED` is deliberately distinct from `UNAVAILABLE`.
   *
   * `_gate` in `services/private_office_routes.py` answers 403 for "a real
   * capability out of reach" and 503 for "we could not look", and its own
   * comment explains why those must not share a shape: the person most likely
   * to hit a degraded resolve is the person who paid. This client used to
   * collapse both into `UNAVAILABLE`, which threw the distinction away on
   * arrival and told a member whose subscription had lapsed that the network
   * was down. The server said "renew"; the screen said "we couldn't reach the
   * server". Keep these two apart.
   */
  state: "READY" | "UNAVAILABLE" | "UPGRADE_REQUIRED";
  passcodeSet: boolean;
  setupRequired: boolean;
  /** Seconds until another attempt is allowed. 0 when not cooling down. */
  cooldownSeconds: number;
  biometricPreference: "enabled" | "disabled" | "unset";
  /** Whether the grant THIS device presented is currently valid. */
  unlocked: boolean;
};

/**
 * Every mutation below answers with a tagged result rather than a throw, for
 * the same reason `PrivateFactsResult` does: the refusals are the product.
 * `COOLDOWN` carries the server's own countdown so the client renders the
 * server's clock and never invents one (Stage 9 — the limit is server-side).
 */
export type OfficeSecurityWriteResult =
  | { state: "OK" }
  | { state: "POLICY"; reason: string }
  | { state: "ALREADY_SET" }
  | { state: "NOT_SET" }
  | { state: "WRONG_PASSCODE" }
  | { state: "COOLDOWN"; retryAfterSeconds: number }
  | { state: "REVERIFY_FAILED" }
  | { state: "UNAVAILABLE" }
  | { state: "ERROR"; message: string };

export type OfficeUnlockResult =
  | { state: "UNLOCKED" }
  | Exclude<OfficeSecurityWriteResult, { state: "OK" }>;

function writeFailure(error: unknown): OfficeSecurityWriteResult {
  if (!(error instanceof PulseApiError)) return { state: "ERROR", message: "" };
  const details = asRecord(error.details);
  const code = asText(details.error).trim().toLowerCase();
  if (code === "cooldown") {
    return {
      state: "COOLDOWN",
      retryAfterSeconds: asFiniteNumber(details.retry_after_seconds) ?? 0
    };
  }
  if (code === "wrong_passcode") return { state: "WRONG_PASSCODE" };
  if (code === "passcode_policy" || code === "confirm_mismatch") {
    return { state: "POLICY", reason: asText(details.reason) || code };
  }
  if (code === "passcode_already_set") return { state: "ALREADY_SET" };
  if (code === "passcode_not_set") return { state: "NOT_SET" };
  if (code === "reverification_failed") return { state: "REVERIFY_FAILED" };
  if (error.status === 503 || error.status === 504) return { state: "UNAVAILABLE" };
  return { state: "ERROR", message: error.message || "" };
}

/** Setup state and cooldown for the signed-in member. Never throws. */
export async function getOfficeSecurityStatus(): Promise<OfficeSecurityStatus> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(`${PRIVATE_OFFICE_SECURITY_PATH}/status`, {
        headers: await officeRequestHeaders()
      })
    );
    const preference = asText(body.biometric_preference).trim().toLowerCase();
    return {
      state: "READY",
      passcodeSet: body.passcode_set === true,
      setupRequired: body.setup_required === true,
      cooldownSeconds: asFiniteNumber(body.cooldown_seconds) ?? 0,
      biometricPreference:
        preference === "enabled" || preference === "disabled" ? preference : "unset",
      unlocked: body.unlocked === true
    };
  } catch (error) {
    // A 403 is not a failure to reach the server -- it is the server's answer.
    // `_gate` returns it only for a real capability the member's tier does not
    // reach, having already spent 503 on "we could not look" and 404 on "there
    // is nothing to sell". Reading `status` here is what lets the gate offer a
    // renew path instead of a retry button that can never succeed.
    const upgradeRequired = error instanceof PulseApiError && error.status === 403;
    return {
      state: upgradeRequired ? "UPGRADE_REQUIRED" : "UNAVAILABLE",
      passcodeSet: false,
      setupRequired: false,
      cooldownSeconds: 0,
      biometricPreference: "unset",
      unlocked: false
    };
  }
}

/** First-entry passcode creation (Stages 1-3). The value crosses once, in the body. */
export async function setupOfficePasscode(
  passcode: string,
  confirmPasscode: string
): Promise<OfficeSecurityWriteResult> {
  try {
    await pulseApi<unknown>(`${PRIVATE_OFFICE_SECURITY_PATH}/setup`, {
      method: "POST",
      headers: await officeRequestHeaders(),
      body: JSON.stringify({ passcode, confirm_passcode: confirmPasscode })
    });
    return { state: "OK" };
  } catch (error) {
    return writeFailure(error);
  }
}

/**
 * Prove the passcode; on success the server's grant is stowed in memory via
 * `setOfficeUnlocked`, and every subsequent Office read carries it. This is the
 * single unlock path — Face ID resolves to a passcode and lands here too.
 */
export async function unlockOffice(passcode: string, userId: number): Promise<OfficeUnlockResult> {
  try {
    const body = asRecord(
      await pulseApi<unknown>(`${PRIVATE_OFFICE_SECURITY_PATH}/unlock`, {
        method: "POST",
        headers: await officeRequestHeaders(),
        body: JSON.stringify({ passcode })
      })
    );
    const grant = asText(body.grant_token);
    if (!grant) return { state: "ERROR", message: "" };
    setOfficeUnlocked(grant, asText(body.expires_at), userId);
    return { state: "UNLOCKED" };
  } catch (error) {
    const failure = writeFailure(error);
    return failure.state === "OK" ? { state: "ERROR", message: "" } : failure;
  }
}

/**
 * Server-side lock. `allDevices` revokes every live grant for this member
 * (Stage 25's "Lock now"); otherwise only the presented grant dies. The local
 * token is dropped by the caller via `lockOfficeLocally` either way.
 */
export async function lockOffice(allDevices: boolean): Promise<OfficeSecurityWriteResult> {
  try {
    await pulseApi<unknown>(`${PRIVATE_OFFICE_SECURITY_PATH}/lock`, {
      method: "POST",
      headers: await officeRequestHeaders(),
      body: JSON.stringify(allDevices ? { all: true } : {})
    });
    return { state: "OK" };
  } catch (error) {
    return writeFailure(error);
  }
}

/** Rotate the passcode. Success revokes every grant on every device (Stage 12). */
export async function changeOfficePasscode(
  currentPasscode: string,
  newPasscode: string,
  confirmPasscode: string
): Promise<OfficeSecurityWriteResult> {
  try {
    await pulseApi<unknown>(`${PRIVATE_OFFICE_SECURITY_PATH}/change`, {
      method: "POST",
      headers: await officeRequestHeaders(),
      body: JSON.stringify({
        current_passcode: currentPasscode,
        new_passcode: newPasscode,
        confirm_passcode: confirmPasscode
      })
    });
    return { state: "OK" };
  } catch (error) {
    return writeFailure(error);
  }
}

/**
 * Forgotten passcode (Stage 11): the ACCOUNT PASSWORD is the elevated proof.
 * A logged-in session alone is precisely what the second lock distrusts.
 */
export async function resetOfficePasscode(
  accountPassword: string,
  newPasscode: string,
  confirmPasscode: string
): Promise<OfficeSecurityWriteResult> {
  try {
    await pulseApi<unknown>(`${PRIVATE_OFFICE_SECURITY_PATH}/reset`, {
      method: "POST",
      headers: await officeRequestHeaders(),
      body: JSON.stringify({
        account_password: accountPassword,
        new_passcode: newPasscode,
        confirm_passcode: confirmPasscode
      })
    });
    return { state: "OK" };
  } catch (error) {
    return writeFailure(error);
  }
}

/** Record the Face ID preference server-side — truthful settings, never an unlock. */
export async function setOfficeBiometricPreference(
  enabled: boolean
): Promise<OfficeSecurityWriteResult> {
  try {
    await pulseApi<unknown>(`${PRIVATE_OFFICE_SECURITY_PATH}/biometric`, {
      method: "POST",
      headers: await officeRequestHeaders(),
      body: JSON.stringify({ enabled })
    });
    return { state: "OK" };
  } catch (error) {
    return writeFailure(error);
  }
}
