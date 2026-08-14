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
};

export type PremiumFounder = {
  is_founder: boolean;
  founder_number: number;
  /** Locked founder price in cents, when the membership record carries one. */
  price_cents: number | null;
};

/**
 * Safe billing facts. The server returns no subscription id, no transaction id
 * and no receipt, so there is nothing here a screen could leak.
 */
export type PremiumSubscription = {
  provider: string;
  plan_key: string;
  billing_period: string;
  status: string;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
};

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
      account_status: membership?.account_status ?? null
    },
    founder: {
      is_founder: Boolean(founder?.is_founder),
      founder_number: Number(founder?.founder_number || 0),
      price_cents: typeof founder?.price_cents === "number" ? founder.price_cents : null
    },
    subscription: subscription
      ? {
          provider: String(subscription.provider || ""),
          plan_key: String(subscription.plan_key || ""),
          billing_period: String(subscription.billing_period || ""),
          status: String(subscription.status || ""),
          current_period_end: subscription.current_period_end || null,
          cancel_at_period_end: Boolean(subscription.cancel_at_period_end)
        }
      : null,
    benefits: Array.isArray(input?.benefits) ? input.benefits : [],
    not_yet: Array.isArray(input?.not_yet) ? input.not_yet : [],
    notices: Array.isArray(input?.notices) ? input.notices : [],
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
 */
export function premiumExperience(payload: PremiumCenter | null): PremiumExperience {
  if (!payload) return "none";
  if (payload.founder.is_founder || payload.membership.mode === "grandfathered") return "founder";
  if (payload.membership.on_hold) return "hold";
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
  if (experience === "active") return "active";
  return null;
}
