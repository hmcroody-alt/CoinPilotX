/**
 * The ONE client-side entitlement authority.
 *
 * Nothing in this app may look at `plan`, `premium_status`,
 * `subscription_status`, `lifetime_premium` or `is_premium` and decide for
 * itself what a member is entitled to. Every one of those fields is an input
 * to the server's resolver, not a conclusion, and a client that reads them
 * becomes a *second* authority that will eventually disagree with the first.
 * The user is the one who finds out — usually as a paying member staring at a
 * locked screen, or a lapsed one still holding a feature.
 *
 * That is not hypothetical here. Before this module existed the native app had
 * six independent deciders using four mutually inconsistent arrays:
 *
 *     ProfileHeader          ["active","premium","founder","lifetime"]
 *     AppNavigator           ["active","premium","founder"]          <- no lifetime
 *     LiveHostSessionScreen  ["active","verified","pro","premium"]
 *     api/premium.ts         ["active","trialing","premium","founder"]
 *
 * while the *server* uses `{active, founder, lifetime, trial}`. So a lifetime
 * member got a badge on their profile and none in the navigation drawer, and
 * `"trial"` was premium to the backend and free to every screen. This module
 * replaces all of it with one call to the canonical endpoint.
 *
 *     GET /api/private-office/entitlement
 *
 * See `services/private_office/tiers.py` for the server side, which is where
 * the ladder is actually decided.
 *
 * Failing closed without lying
 * ----------------------------
 * A resolve that did not complete grants nothing, but it must not be rendered
 * as "you are on Free" — that is a different lie, told to exactly the person
 * who paid. `state` therefore distinguishes:
 *
 *     "resolved"    the server answered and the answer is trustworthy
 *     "unavailable" we do not know; grant nothing, say nothing about the tier
 *
 * `tierSatisfies` and `isEntitled` return false in both cases. Anything that
 * renders copy must branch on `state` first.
 */

import { pulseApi } from "../api/pulseApi";

/** The ladder, lowest to highest. Index is the rank. */
export const TIER_ORDER = ["FREE", "PREMIUM", "PRIVATE", "PRIVATE_OFFICE"] as const;

export type Tier = (typeof TIER_ORDER)[number];

/** Mirrors the server's `feature_matrix` availability vocabulary. */
export type FeatureAvailability =
  | "ENTITLED"
  | "NOT_ENTITLED"
  | "FEATURE_DISABLED"
  | "NOT_IMPLEMENTED"
  | "UNKNOWN";

/**
 * Membership status as the server reports it. `account_hold` and `unavailable`
 * are deliberately part of the same vocabulary: a suspended member and an
 * unreachable resolver are both "not entitled right now", but they are not the
 * same thing to say out loud, and the client needs to tell them apart.
 */
export type TierStatus =
  | "none"
  | "active"
  | "grace"
  | "grandfathered"
  | "account_hold"
  | "unavailable";

export interface TierAnswer {
  /** "resolved" only when the server said its own resolve was trustworthy. */
  state: "resolved" | "unavailable";
  effectiveTier: Tier;
  status: TierStatus;
  /** Grant provenance, verbatim from the server. Display/diagnostics only. */
  source: string;
  /** ISO string, or null for a grant that does not expire (e.g. lifetime). */
  expiresAt: string | null;
  features: Record<string, FeatureAvailability>;
  /** ISO timestamp of the resolve, from the server's clock, not the device's. */
  verifiedAt: string;
}

/**
 * The answer used whenever the server did not give us one: no access, and
 * honest about not knowing. Exported so callers can render an initial state
 * without inventing a tier while the first request is in flight.
 */
export const UNKNOWN_TIER: TierAnswer = {
  state: "unavailable",
  effectiveTier: "FREE",
  status: "unavailable",
  source: "",
  expiresAt: null,
  features: {},
  verifiedAt: ""
};

const TIER_RANK: Record<string, number> = TIER_ORDER.reduce(
  (acc, tier, index) => ({ ...acc, [tier]: index }),
  {} as Record<string, number>
);

const STATUS_VALUES: TierStatus[] = [
  "none",
  "active",
  "grace",
  "grandfathered",
  "account_hold",
  "unavailable"
];

const AVAILABILITY_VALUES: FeatureAvailability[] = [
  "ENTITLED",
  "NOT_ENTITLED",
  "FEATURE_DISABLED",
  "NOT_IMPLEMENTED"
];

/** Rank of a tier name. Anything unrecognised ranks as FREE — fail closed. */
export function tierRank(tier: string): number {
  return TIER_RANK[String(tier || "").trim().toUpperCase()] ?? 0;
}

/**
 * Does this answer reach `minimum`?
 *
 * An unavailable answer is false for every minimum above FREE. Callers that
 * need to tell "denied" from "unknown" must read `answer.state` themselves.
 */
export function tierSatisfies(answer: TierAnswer | null, minimum: Tier): boolean {
  if (!answer || answer.state !== "resolved") return false;
  return tierRank(answer.effectiveTier) >= tierRank(minimum);
}

/**
 * Availability of one feature.
 *
 * "UNKNOWN" is returned both when the resolve failed and when the server did
 * not mention the feature at all. Neither is a licence to render the feature:
 * a client that treated a missing key as available would expose whatever the
 * server has not yet learned to gate.
 */
export function featureAvailability(
  answer: TierAnswer | null,
  featureId: string
): FeatureAvailability {
  if (!answer || answer.state !== "resolved") return "UNKNOWN";
  return answer.features[featureId] || "UNKNOWN";
}

/** The single question a gate should ask. True only for a live entitlement. */
export function isEntitled(answer: TierAnswer | null, featureId: string): boolean {
  return featureAvailability(answer, featureId) === "ENTITLED";
}

/**
 * Is this member above FREE at all?
 *
 * This is the replacement for every `["active","premium",…].includes(...)` the
 * app used to run. It answers from the resolved ladder, so `lifetime`,
 * `trial`, `grace` and `grandfathered` all land wherever the server put them
 * and cannot land in two places at once.
 */
export function isMember(answer: TierAnswer | null): boolean {
  return tierSatisfies(answer, "PREMIUM");
}

function asTier(value: unknown): Tier {
  const text = String(value || "").trim().toUpperCase();
  return (TIER_ORDER as readonly string[]).includes(text) ? (text as Tier) : "FREE";
}

function asStatus(value: unknown): TierStatus {
  const text = String(value || "").trim().toLowerCase();
  return (STATUS_VALUES as string[]).includes(text) ? (text as TierStatus) : "none";
}

function asFeatures(value: unknown): Record<string, FeatureAvailability> {
  if (!value || typeof value !== "object") return {};
  const out: Record<string, FeatureAvailability> = {};
  for (const [featureId, entry] of Object.entries(value as Record<string, unknown>)) {
    const availability = String(
      (entry && typeof entry === "object"
        ? (entry as Record<string, unknown>).availability
        : entry) || ""
    ).toUpperCase();
    // An availability word we do not recognise is dropped rather than mapped to
    // a default. A default of ENTITLED would open a gate on a typo; a default
    // of NOT_ENTITLED would silently hide a feature a newer server shipped.
    // Dropping it makes the key read UNKNOWN, which is what it is.
    if ((AVAILABILITY_VALUES as string[]).includes(availability)) {
      out[featureId] = availability as FeatureAvailability;
    }
  }
  return out;
}

/**
 * Turn the endpoint's JSON into a `TierAnswer`.
 *
 * Split out from the fetch so the Stage 22 truth table can be asserted without
 * a network, and so a malformed payload is a *parse* decision made in one
 * documented place rather than optional chaining scattered across screens.
 */
export function parseTierAnswer(payload: unknown): TierAnswer {
  if (!payload || typeof payload !== "object") return UNKNOWN_TIER;
  const body = payload as Record<string, unknown>;

  // `ok` reflects whether the ANSWER is trustworthy, not whether HTTP worked.
  // The server returns 200 with ok=false during a degraded resolve precisely so
  // this branch exists on the client.
  if (body.ok !== true || body.resolver_state !== "ok") return UNKNOWN_TIER;

  return {
    state: "resolved",
    effectiveTier: asTier(body.effective_tier),
    status: asStatus(body.status),
    source: String(body.source || ""),
    expiresAt: body.expires_at ? String(body.expires_at) : null,
    features: asFeatures(body.features),
    verifiedAt: String(body.verified_at || "")
  };
}

export const CANONICAL_TIER_PATH = "/api/private-office/entitlement";

/**
 * Ask the server what the signed-in member is entitled to.
 *
 * Never throws. A network failure, a 401, a 500 and a degraded resolve all
 * produce `UNKNOWN_TIER`, because from a gate's point of view they are the same
 * event: we did not get an answer, so we grant nothing. Screens that need to
 * explain themselves read `state`.
 */
export async function fetchCanonicalTier(): Promise<TierAnswer> {
  try {
    const payload = await pulseApi<unknown>(CANONICAL_TIER_PATH);
    return parseTierAnswer(payload);
  } catch {
    return UNKNOWN_TIER;
  }
}
