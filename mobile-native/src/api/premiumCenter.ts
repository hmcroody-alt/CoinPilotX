/**
 * Premium Status Center client — the canonical membership answer.
 *
 * This module talks to `GET /api/premium/status-center`, which is the *only*
 * authority on whether an account is Premium. Everything below is transport and
 * presentation mapping; nothing here decides membership, and nothing here may
 * start deciding it later.
 *
 * Why not `api/premium.ts`
 * -----------------------
 * That module predates the canonical entitlements migration. It derives
 * membership client-side (`normalizePremiumStatus` ORs four fields together) and
 * hand-writes a benefit list with fixed labels — the exact two behaviours the
 * server-side Status Center was built to end. It still backs the legacy
 * `/api/premium/status` surface, so it is left alone rather than rewritten, but
 * no new Premium surface should read from it.
 *
 * The server decides which experience you get
 * -------------------------------------------
 * `membership.mode` arrives already decided, and the screen renders whichever
 * layout that mode names. The client never inspects a receipt, a date, or an
 * entitlement row to work out whether someone is Premium — if it did, the app
 * and the server would eventually disagree, and the member would believe the
 * number on their own screen.
 *
 * The cache is a display cache, not an authority
 * ----------------------------------------------
 * `loadCachedPremiumCenter` exists so the tile does not flash "free" at a paying
 * member on a cold start. It may only ever paint a label. Purchase, restore and
 * management all re-read live server state first — see `PREMIUM_CACHE_CONTRACT`.
 */

import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";

const PREMIUM_CENTER_CACHE_KEY = "pulsesoc.native.premium.center";

/**
 * Load-bearing comment, asserted by `premiumCenter.test.ts`: the cached payload
 * is allowed to decide what the tile *says* and nothing else. Any future code
 * that gates a capability, a purchase or a management action on cached state is
 * a client-side entitlement authority, which is the thing this whole subsystem
 * exists to prevent.
 */
export const PREMIUM_CACHE_CONTRACT = "display-only";

/** Membership mode as decided by `services/business_os/entitlements/premium.py`. */
export type PremiumMembershipMode =
  | "owner_lifetime"
  | "active"
  | "grace"
  | "grandfathered"
  | "suspended"
  | "revoked"
  | "legacy"
  | "legacy_fallback"
  | "inactive"
  | "none";

export type PremiumMembership = {
  is_premium: boolean;
  /** Premium *and* not paused by an account hold. The usable-access answer. */
  usable_now: boolean;
  mode: PremiumMembershipMode;
  /** Which authority decided. Diagnostic; never shown to the member. */
  decided_by: string;
  on_hold: boolean;
  account_status: string | null;
  /**
   * Why the server answered as it did, from the closed enum in
   * `premium.REASONS`. Diagnostic, like `decided_by` — the copy a member reads
   * comes from `premiumExperience`, not from this string.
   */
  reason: string;
  /**
   * Does this membership have an end at all?
   *
   * A server FACT, not a client deduction, and the distinction matters. A
   * permanent membership can have a lapsed provider subscription sitting behind
   * it — history that really happened — and from the row alone it is
   * indistinguishable from a membership that ran out. Deducing "expired" from
   * the presence of that row is what would put a renewal prompt in front of
   * someone whose access cannot lapse.
   */
  lifetime: boolean;
};

export type PremiumFounder = {
  is_founder: boolean;
  founder_number: number;
  /** Locked founder price in cents, when the membership record carries one. */
  price_cents: number | null;
};

/**
 * The state the subscription card switches on.
 *
 * Distinct from `status`, which is the provider's own word for the same thing.
 * `status` is not safe to display: rows written before the Apple adapter had a
 * fixed vocabulary can hold anything, so the server closes this set and maps
 * everything it does not recognise to `unknown`.
 */
export type PremiumSubscriptionState =
  | "active"
  | "trialing"
  | "grace"
  | "billing_retry"
  | "canceled"
  | "expired"
  | "revoked"
  | "paused"
  | "unknown";

/**
 * Safe billing facts. The server returns no subscription id, no transaction id
 * and no receipt, so there is nothing here a screen could leak.
 *
 * `renews_at` and `expires_at` are deliberately two fields rather than one date
 * plus a flag. They are never both set: the same instant means "you will be
 * charged" or "your access stops", and which one it is has already been decided
 * server-side so no screen can render the wrong verb next to the right date.
 */
export type PremiumSubscription = {
  provider: string;
  plan_key: string;
  billing_period: string;
  /** The provider's own status word. Diagnostic — render `state` instead. */
  status: string;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  state: PremiumSubscriptionState;
  auto_renew: boolean;
  renews_at: string | null;
  expires_at: string | null;
  /**
   * The Apple product identifier behind this subscription, derived from the
   * plan key the server set from a verified `productId`. The app needs it to ask
   * StoreKit for *this member's* localized price — which is why no price, and no
   * currency, is ever sent from the server.
   */
  product_id: string | null;
  /** Verified original purchase date. `null` means omit the row, not "unknown". */
  original_purchase_at: string | null;
};

const SUBSCRIPTION_STATES: readonly PremiumSubscriptionState[] = [
  "active", "trialing", "grace", "billing_retry",
  "canceled", "expired", "revoked", "paused", "unknown"
];

/**
 * One advertised benefit. The server has already filtered this list to
 * capabilities a gate genuinely enforces, so the app renders what it is given
 * and never adds to it.
 */
export type PremiumBenefit = {
  key: string;
  label: string;
  status: string;
  included: boolean;
  active_now: boolean;
  beta: boolean;
  /** Present only when a real metered grant exists. Absent means unmetered. */
  allowance?: {
    limit: number;
    period: string | null;
    used: number | null;
    remaining: number | null;
  };
};

export type PremiumNotice = { code: string; message: string };

export type PremiumCenter = {
  ok: boolean;
  membership: PremiumMembership;
  founder: PremiumFounder;
  subscription: PremiumSubscription | null;
  benefits: PremiumBenefit[];
  /** Granted but not yet enforced. Shown as "not yet", never as a benefit. */
  not_yet: Array<{ key: string; label: string; status: string }>;
  notices: PremiumNotice[];
  not_verification: string;
};

/**
 * Which layout the member gets. Derived from server fields only.
 *
 * `expired` and `none` both lead to the same purchase surface and differ only in
 * copy — someone whose subscription lapsed should not be greeted as though they
 * had never subscribed.
 */
export type PremiumExperience =
  | "founder"
  | "lifetime"
  | "active"
  | "grace"
  | "hold"
  | "expired"
  | "none";

export async function getPremiumCenter(): Promise<PremiumCenter> {
  const payload = normalizePremiumCenter(await pulseApi<Partial<PremiumCenter>>("/api/premium/status-center"));
  await cachePremiumCenter(payload).catch(() => undefined);
  return payload;
}

export async function loadCachedPremiumCenter(): Promise<PremiumCenter | null> {
  return readJsonCache<PremiumCenter>(PREMIUM_CENTER_CACHE_KEY, normalizePremiumCenter);
}

export async function cachePremiumCenter(payload: PremiumCenter): Promise<void> {
  await writeJsonCache(PREMIUM_CENTER_CACHE_KEY, payload);
}

/**
 * Fill in missing fields without ever inventing membership.
 *
 * Every unknown resolves to the *unentitled* answer. A malformed or truncated
 * payload must never read as Premium: the failure that costs the company money
 * is granting access nobody paid for, and the failure that costs a member
 * nothing is a screen that says "not yet" until it can load properly.
 */
export function normalizePremiumCenter(input: Partial<PremiumCenter> | null | undefined): PremiumCenter {
  const membership = input?.membership;
  const founder = input?.founder;
  const subscription = input?.subscription;
  return {
    ok: Boolean(input?.ok),
    membership: {
      is_premium: Boolean(membership?.is_premium),
      usable_now: Boolean(membership?.usable_now),
      mode: (membership?.mode || "none") as PremiumMembershipMode,
      decided_by: String(membership?.decided_by || ""),
      on_hold: Boolean(membership?.on_hold),
      account_status: membership?.account_status ?? null,
      reason: String(membership?.reason || ""),
      // Unentitled by default, like every other field here: a truncated payload
      // must not be able to assert a permanent membership.
      lifetime: Boolean(membership?.lifetime)
    },
    founder: {
      is_founder: Boolean(founder?.is_founder),
      founder_number: Number(founder?.founder_number || 0),
      price_cents: typeof founder?.price_cents === "number" ? founder.price_cents : null
    },
    subscription: subscription ? normalizeSubscription(subscription) : null,
    benefits: Array.isArray(input?.benefits) ? input.benefits : [],
    not_yet: Array.isArray(input?.not_yet) ? input.not_yet : [],
    notices: Array.isArray(input?.notices) ? input.notices : [],
    not_verification: String(input?.not_verification || "")
  };
}

/**
 * Fill in the subscription facts without ever upgrading a silence into a claim.
 *
 * Two defaults carry real weight. An unrecognised `state` becomes `unknown`
 * rather than `active`, so a payload this build does not understand shows a
 * cautious card instead of asserting a live membership. And `auto_renew` falls
 * back to the inverse of `cancel_at_period_end` rather than to `true`, so an
 * older server that predates the field still produces the honest verb.
 */
function normalizeSubscription(input: Partial<PremiumSubscription>): PremiumSubscription {
  const state = SUBSCRIPTION_STATES.includes(input.state as PremiumSubscriptionState)
    ? (input.state as PremiumSubscriptionState)
    : "unknown";
  const cancelAtPeriodEnd = Boolean(input.cancel_at_period_end);
  const autoRenew = typeof input.auto_renew === "boolean" ? input.auto_renew : !cancelAtPeriodEnd;
  const periodEnd = input.current_period_end || null;
  return {
    provider: String(input.provider || ""),
    plan_key: String(input.plan_key || ""),
    billing_period: String(input.billing_period || ""),
    status: String(input.status || ""),
    current_period_end: periodEnd,
    cancel_at_period_end: cancelAtPeriodEnd,
    state,
    auto_renew: autoRenew,
    // Recomputed rather than trusted, so the two can never both be set — the
    // one bug that would put "Renews" above the date a member loses access.
    renews_at: autoRenew && state !== "expired" ? input.renews_at ?? periodEnd : null,
    expires_at: !autoRenew || state === "expired" ? input.expires_at ?? periodEnd : null,
    product_id: input.product_id || null,
    original_purchase_at: input.original_purchase_at || null
  };
}

/**
 * Usage Center — "Your Premium this month".
 *
 * Talks to `GET /api/premium/usage-center`. Every value the server sends is a
 * live count from the domain table the feature itself reads
 * (`usage_summary.py`); a source the server could not measure is OMITTED from
 * `signals` and named in `omitted`. The client renders exactly what arrives and
 * never zero-fills, estimates, or invents a number — same golden rule as the
 * benefit list. NOT cached: a usage count shown stale is a small lie.
 */
export type PremiumUsageSignal = {
  key: string;
  /** The entitlement capability this signal belongs to. */
  capability: string;
  label: string;
  kind: "count" | "state";
  scope: "current" | "month" | "day";
  /** count -> number; state -> the state string or null when unset. */
  value: number | string | null;
  free_limit?: number;
  beyond_free_limit?: boolean;
  in_use?: boolean;
};

export type PremiumUsageRecommendation = {
  capability: string;
  /** The signal key this recommendation was derived from — never speculative. */
  signal: string;
  reason: string;
  title: string;
  body: string;
};

export type PremiumUsageCenter = {
  ok: boolean;
  membership: { is_premium: boolean; usable_now: boolean; on_hold: boolean };
  usage: {
    month: string;
    signals: PremiumUsageSignal[];
    /** Sources that could not be measured this request. Shown as absent. */
    omitted: string[];
    recommendations: PremiumUsageRecommendation[];
    provenance: string;
  };
  not_verification: string;
};

export async function getPremiumUsageCenter(): Promise<PremiumUsageCenter> {
  return normalizePremiumUsageCenter(
    await pulseApi<Partial<PremiumUsageCenter>>("/api/premium/usage-center")
  );
}

/**
 * Fail-closed normalize, same posture as `normalizePremiumCenter`: a malformed
 * payload yields empty lists (nothing rendered), never fabricated counts.
 */
export function normalizePremiumUsageCenter(
  input: Partial<PremiumUsageCenter> | null | undefined
): PremiumUsageCenter {
  const usage = input?.usage;
  const signals = Array.isArray(usage?.signals)
    ? usage.signals.filter(
        (s): s is PremiumUsageSignal =>
          Boolean(s && typeof s.key === "string" && typeof s.label === "string") &&
          (s.kind === "count" ? typeof s.value === "number" : true)
      )
    : [];
  const recommendations = Array.isArray(usage?.recommendations)
    ? usage.recommendations.filter(
        (r): r is PremiumUsageRecommendation =>
          Boolean(r && typeof r.title === "string" && typeof r.signal === "string")
      )
    : [];
  return {
    ok: Boolean(input?.ok),
    membership: {
      is_premium: Boolean(input?.membership?.is_premium),
      usable_now: Boolean(input?.membership?.usable_now),
      on_hold: Boolean(input?.membership?.on_hold)
    },
    usage: {
      month: String(usage?.month || ""),
      signals,
      omitted: Array.isArray(usage?.omitted) ? usage.omitted.map(String) : [],
      recommendations,
      provenance: String(usage?.provenance || "")
    },
    not_verification: String(input?.not_verification || "")
  };
}

/**
 * The layout this member gets.
 *
 * Order matters. Founder is checked first because a Founder who also holds a
 * paid subscription is still a Founder, and being shown a plain "Active" screen
 * would read as having lost the status. The account hold is checked before
 * `active` for the opposite reason: a member whose access is paused must not be
 * told everything is fine.
 *
 * `lifetime` sits between them. Above `hold` it would tell a suspended account
 * everything is fine; below `grace` or `active` it would never fire, because a
 * permanent membership is also a premium one and `active` would swallow it —
 * and "Active" is the layout with the renewal date on it.
 */
export function premiumExperience(payload: PremiumCenter | null): PremiumExperience {
  if (!payload) return "none";
  if (payload.founder.is_founder || payload.membership.mode === "grandfathered") return "founder";
  if (payload.membership.on_hold) return "hold";
  if (payload.membership.lifetime && payload.membership.is_premium) return "lifetime";
  if (payload.membership.mode === "grace") return "grace";
  if (payload.membership.is_premium) return "active";
  // Someone with a subscription row who is no longer premium has lapsed; someone
  // with no row never subscribed. Both buy from the same screen, different words.
  return payload.subscription ? "expired" : "none";
}

/**
 * Micro-status for the Profile OS tile, or `null` for no badge at all.
 *
 * `null` is what prevents the flicker the brief calls out. On a cold start the
 * tile renders gold with no status word rather than asserting "free", so a
 * paying member never watches their membership appear to vanish and come back.
 * Absence of a badge says nothing; the word "Free" would have said something
 * wrong.
 */
export function premiumTileState(payload: PremiumCenter | null): "active" | "founder" | "grace" | null {
  if (!payload) return null;
  const experience = premiumExperience(payload);
  if (experience === "founder") return "founder";
  if (experience === "grace") return "grace";
  // A permanent membership reads as "active" on the tile. The tile has three
  // words and no room for a fourth, and of the three this is the only one that
  // is true: the membership is on, and it is not a Founder number.
  if (experience === "active" || experience === "lifetime") return "active";
  return null;
}
